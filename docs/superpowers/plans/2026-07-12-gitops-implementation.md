# GitOps Implementation Plan: evcc + teslamate on APPUiO Cloud

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a GitOps repository that deploys evcc, teslamate, tesla-http-proxy, and oauth2-proxy to APPUiO Cloud with SOPS-encrypted secrets and CI-driven deployment.

**Architecture:** Plain Kubernetes/OpenShift manifests organized in per-app directories. GitHub Actions decrypts SOPS secrets and runs `oc apply`. A shared tesla-http-proxy handles Tesla Fleet API auth, and a shared oauth2-proxy handles Google OAuth for both web UIs.

**Tech Stack:** Kubernetes manifests, SOPS + age, GitHub Actions, OpenShift Routes, VSHNPostgreSQL CRD

## Global Constraints

- All Docker Hub images must use the `dockerhub.vshn.net` proxy prefix (e.g., `dockerhub.vshn.net/evcc/evcc:latest`)
- Encrypted secret files use the naming convention `secret.sops.yaml`
- All manifests target the user's APPUiO Cloud namespace
- Routes use `*.apps.exoscale-ch-gva-2-0.appuio.cloud` subdomains
- Age private key file: `age-key.txt` at repo root, gitignored

---

### Task 1: Repository scaffolding — .gitignore, .sops.yaml, age keypair

**Files:**
- Create: `.gitignore`
- Create: `.sops.yaml`
- Create: `age-key.txt` (generated, gitignored)

**Interfaces:**
- Consumes: nothing
- Produces: `.sops.yaml` config used by all subsequent tasks that create `secret.sops.yaml` files; `age-key.txt` used for encryption/decryption

- [ ] **Step 1: Create `.gitignore`**

```
age-key.txt
*.dec.yaml
```

- [ ] **Step 2: Generate age keypair**

```bash
age-keygen -o age-key.txt 2>&1 | tee age-pubkey.txt
```

Note the public key from the output (starts with `age1...`). It will be used in the next step.

- [ ] **Step 3: Create `.sops.yaml`**

Replace `age1...` with the actual public key from step 2:

```yaml
creation_rules:
  - encrypted_regex: "^(data|stringData)$"
    age: "age1..."
```

This tells SOPS to only encrypt the `data` and `stringData` fields in Kubernetes Secret manifests, leaving `metadata`, `apiVersion`, etc. in plaintext for readability.

- [ ] **Step 4: Clean up pubkey file and commit**

```bash
rm age-pubkey.txt
git add .gitignore .sops.yaml
git commit -m "feat: add repo scaffolding — .gitignore, .sops.yaml"
```

---

### Task 2: tesla-http-proxy manifests

**Files:**
- Create: `tesla-http-proxy/deployment.yaml`
- Create: `tesla-http-proxy/service.yaml`
- Create: `tesla-http-proxy/secret.sops.yaml`

**Interfaces:**
- Consumes: `.sops.yaml` for encryption config
- Produces: Service `tesla-http-proxy` on port 443 (cluster-internal), used by evcc and teslamate deployments

- [ ] **Step 1: Create `tesla-http-proxy/deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tesla-http-proxy
  labels:
    app: tesla-http-proxy
spec:
  replicas: 1
  selector:
    matchLabels:
      app: tesla-http-proxy
  template:
    metadata:
      labels:
        app: tesla-http-proxy
    spec:
      containers:
        - name: tesla-http-proxy
          image: dockerhub.vshn.net/tesla/vehicle-command:0.4.1
          command:
            - tesla-http-proxy
            - -tls-key
            - /config/tls-key.pem
            - -cert
            - /config/tls-cert.pem
            - -key-file
            - /config/fleet-key.pem
            - -port
            - "4443"
          ports:
            - containerPort: 4443
              name: https
          volumeMounts:
            - name: config
              mountPath: /config
              readOnly: true
      volumes:
        - name: config
          secret:
            secretName: tesla-http-proxy
```

- [ ] **Step 2: Create `tesla-http-proxy/service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: tesla-http-proxy
spec:
  selector:
    app: tesla-http-proxy
  ports:
    - port: 443
      targetPort: 4443
      name: https
```

- [ ] **Step 3: Create plaintext secret, then encrypt with SOPS**

Create the plaintext secret first as `tesla-http-proxy/secret.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tesla-http-proxy
type: Opaque
stringData:
  tls-key.pem: |
    REPLACE_WITH_TLS_PRIVATE_KEY
  tls-cert.pem: |
    REPLACE_WITH_TLS_CERTIFICATE
  fleet-key.pem: |
    REPLACE_WITH_FLEET_API_PRIVATE_KEY
```

Then encrypt and remove plaintext:

```bash
sops -e tesla-http-proxy/secret.yaml > tesla-http-proxy/secret.sops.yaml
rm tesla-http-proxy/secret.yaml
```

- [ ] **Step 4: Commit**

```bash
git add tesla-http-proxy/
git commit -m "feat: add tesla-http-proxy manifests"
```

---

### Task 3: teslamate manifests (Deployment, Service, PostgreSQL CRD)

**Files:**
- Create: `teslamate/deployment.yaml`
- Create: `teslamate/service.yaml`
- Create: `teslamate/postgresql.yaml`
- Create: `teslamate/route.yaml`
- Create: `teslamate/secret.sops.yaml`

**Interfaces:**
- Consumes: Service `tesla-http-proxy` (port 443) from Task 2; `.sops.yaml` for encryption
- Produces: Service `teslamate` on port 4000, Route `teslamate` on `teslamate-<namespace>.apps.exoscale-ch-gva-2-0.appuio.cloud`

- [ ] **Step 1: Create `teslamate/postgresql.yaml`**

```yaml
apiVersion: vshn.appcat.vshn.io/v1
kind: VSHNPostgreSQL
metadata:
  name: teslamate-db
spec:
  parameters:
    service:
      majorVersion: "17"
      serviceLevel: besteffort
    size:
      plan: standard-2
    backup:
      enabled: true
      schedule: "0 2 * * *"
      retention: 6
  writeConnectionSecretToRef:
    name: teslamate-db-credentials
```

- [ ] **Step 2: Create `teslamate/deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: teslamate
  labels:
    app: teslamate
spec:
  replicas: 1
  selector:
    matchLabels:
      app: teslamate
  template:
    metadata:
      labels:
        app: teslamate
    spec:
      containers:
        - name: teslamate
          image: dockerhub.vshn.net/teslamate/teslamate:latest
          ports:
            - containerPort: 4000
              name: http
          env:
            - name: DATABASE_HOST
              valueFrom:
                secretKeyRef:
                  name: teslamate-db-credentials
                  key: POSTGRESQL_HOST
            - name: DATABASE_PORT
              valueFrom:
                secretKeyRef:
                  name: teslamate-db-credentials
                  key: POSTGRESQL_PORT
            - name: DATABASE_NAME
              valueFrom:
                secretKeyRef:
                  name: teslamate-db-credentials
                  key: POSTGRESQL_DB
            - name: DATABASE_USER
              valueFrom:
                secretKeyRef:
                  name: teslamate-db-credentials
                  key: POSTGRESQL_USER
            - name: DATABASE_PASS
              valueFrom:
                secretKeyRef:
                  name: teslamate-db-credentials
                  key: POSTGRESQL_PASSWORD
            - name: ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: teslamate
                  key: encryption-key
            - name: DISABLE_MQTT
              value: "true"
```

- [ ] **Step 3: Create `teslamate/service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: teslamate
spec:
  selector:
    app: teslamate
  ports:
    - port: 4000
      targetPort: 4000
      name: http
```

- [ ] **Step 4: Create `teslamate/route.yaml`**

```yaml
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: teslamate
spec:
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
  to:
    kind: Service
    name: oauth2-proxy
    weight: 100
  port:
    targetPort: http
```

Note: The Route points to the `oauth2-proxy` Service, not directly to teslamate. The hostname will be auto-assigned by OpenShift as `teslamate-<namespace>.apps.exoscale-ch-gva-2-0.appuio.cloud`.

- [ ] **Step 5: Create plaintext secret, then encrypt**

Create `teslamate/secret.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: teslamate
type: Opaque
stringData:
  encryption-key: "REPLACE_WITH_RANDOM_SECRET"
```

Generate a random key and encrypt:

```bash
# Generate a real encryption key:
# ENCRYPTION_KEY=$(openssl rand -base64 32)
# Then put it in the secret before encrypting
sops -e teslamate/secret.yaml > teslamate/secret.sops.yaml
rm teslamate/secret.yaml
```

- [ ] **Step 6: Commit**

```bash
git add teslamate/
git commit -m "feat: add teslamate manifests with VSHNPostgreSQL"
```

---

### Task 4: evcc manifests (Deployment, Service, PVC, ConfigMap)

**Files:**
- Create: `evcc/deployment.yaml`
- Create: `evcc/service.yaml`
- Create: `evcc/pvc.yaml`
- Create: `evcc/configmap.yaml`
- Create: `evcc/route.yaml`
- Create: `evcc/secret.sops.yaml`

**Interfaces:**
- Consumes: Service `tesla-http-proxy` (port 443) from Task 2; `.sops.yaml` for encryption
- Produces: Service `evcc` on port 7070, Route `evcc` on `evcc-<namespace>.apps.exoscale-ch-gva-2-0.appuio.cloud`

- [ ] **Step 1: Create `evcc/pvc.yaml`**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: evcc-data
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
```

- [ ] **Step 2: Create `evcc/configmap.yaml`**

A minimal evcc config — Tesla vehicle configured to use the tesla-http-proxy:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: evcc-config
data:
  evcc.yaml: |
    network:
      schema: http
      host: 0.0.0.0
      port: 7070
    log: info
```

Note: Most evcc configuration (vehicles, chargers, site) is done through the web UI and persisted in the SQLite database. The config file provides only the base server settings.

- [ ] **Step 3: Create `evcc/deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: evcc
  labels:
    app: evcc
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: evcc
  template:
    metadata:
      labels:
        app: evcc
    spec:
      containers:
        - name: evcc
          image: dockerhub.vshn.net/evcc/evcc:latest
          ports:
            - containerPort: 7070
              name: http
          volumeMounts:
            - name: data
              mountPath: /root/.evcc
            - name: config
              mountPath: /etc/evcc.yaml
              subPath: evcc.yaml
              readOnly: true
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: evcc-data
        - name: config
          configMap:
            name: evcc-config
```

- [ ] **Step 4: Create `evcc/service.yaml`**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: evcc
spec:
  selector:
    app: evcc
  ports:
    - port: 7070
      targetPort: 7070
      name: http
```

- [ ] **Step 5: Create `evcc/route.yaml`**

```yaml
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: evcc
spec:
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
  to:
    kind: Service
    name: oauth2-proxy
    weight: 100
  port:
    targetPort: http
```

Note: Route points to oauth2-proxy. Hostname auto-assigned as `evcc-<namespace>.apps.exoscale-ch-gva-2-0.appuio.cloud`.

- [ ] **Step 6: Create plaintext secret, then encrypt**

Create `evcc/secret.yaml` (placeholder for any future Tesla-specific secrets evcc may need):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: evcc
type: Opaque
stringData:
  placeholder: "true"
```

```bash
sops -e evcc/secret.yaml > evcc/secret.sops.yaml
rm evcc/secret.yaml
```

- [ ] **Step 7: Commit**

```bash
git add evcc/
git commit -m "feat: add evcc manifests with PVC and ConfigMap"
```

---

### Task 5: oauth2-proxy manifests

**Files:**
- Create: `oauth2-proxy/deployment.yaml`
- Create: `oauth2-proxy/service.yaml`
- Create: `oauth2-proxy/secret.sops.yaml`

**Interfaces:**
- Consumes: Services `evcc` (port 7070) and `teslamate` (port 4000) from Tasks 3-4; Route hostnames from Tasks 3-4
- Produces: Service `oauth2-proxy` on port 4180, used as the target for both Routes

- [ ] **Step 1: Create `oauth2-proxy/deployment.yaml`**

The `--upstream` flag cannot do host-based routing natively. Instead, we run oauth2-proxy as an auth gateway and use OpenShift Routes to direct traffic. Each Route points to oauth2-proxy, which authenticates, then proxies to the correct upstream based on the `X-Forwarded-Host` header.

For simplicity, we'll use two oauth2-proxy instances or a single instance with a reverse proxy sidecar. The simplest working approach: **one oauth2-proxy per app** sharing the same Secret (Google OAuth credentials). This avoids complex routing logic.

Alternative simpler approach: use oauth2-proxy in **auth-request mode** where the Route's backend is the actual app, and oauth2-proxy only handles `/oauth2/` paths. However, OpenShift Routes don't support auth subrequests.

Practical approach: **one oauth2-proxy deployment with the evcc upstream as default**, and a second small deployment for teslamate. Both share the same Google OAuth Secret.

Actually, the cleanest approach for OpenShift: run **one oauth2-proxy per Route**, both using the same SOPS-encrypted secret for Google OAuth config. This is two Deployments but zero routing complexity.

Create `oauth2-proxy/deployment-evcc.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: oauth2-proxy-evcc
  labels:
    app: oauth2-proxy-evcc
spec:
  replicas: 1
  selector:
    matchLabels:
      app: oauth2-proxy-evcc
  template:
    metadata:
      labels:
        app: oauth2-proxy-evcc
    spec:
      containers:
        - name: oauth2-proxy
          image: dockerhub.vshn.net/oauth2-proxy/oauth2-proxy:v7.9.0
          args:
            - --provider=google
            - --upstream=http://evcc:7070
            - --http-address=0.0.0.0:4180
            - --reverse-proxy=true
            - --email-domain=*
            - --authenticated-emails-file=/config/authenticated-emails
          ports:
            - containerPort: 4180
              name: http
          envFrom:
            - secretRef:
                name: oauth2-proxy
          volumeMounts:
            - name: config
              mountPath: /config
              readOnly: true
      volumes:
        - name: config
          secret:
            secretName: oauth2-proxy-emails
```

Create `oauth2-proxy/deployment-teslamate.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: oauth2-proxy-teslamate
  labels:
    app: oauth2-proxy-teslamate
spec:
  replicas: 1
  selector:
    matchLabels:
      app: oauth2-proxy-teslamate
  template:
    metadata:
      labels:
        app: oauth2-proxy-teslamate
    spec:
      containers:
        - name: oauth2-proxy
          image: dockerhub.vshn.net/oauth2-proxy/oauth2-proxy:v7.9.0
          args:
            - --provider=google
            - --upstream=http://teslamate:4000
            - --http-address=0.0.0.0:4180
            - --reverse-proxy=true
            - --email-domain=*
            - --authenticated-emails-file=/config/authenticated-emails
          ports:
            - containerPort: 4180
              name: http
          envFrom:
            - secretRef:
                name: oauth2-proxy
          volumeMounts:
            - name: config
              mountPath: /config
              readOnly: true
      volumes:
        - name: config
          secret:
            secretName: oauth2-proxy-emails
```

- [ ] **Step 2: Create `oauth2-proxy/service-evcc.yaml` and `oauth2-proxy/service-teslamate.yaml`**

`oauth2-proxy/service-evcc.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: oauth2-proxy-evcc
spec:
  selector:
    app: oauth2-proxy-evcc
  ports:
    - port: 4180
      targetPort: 4180
      name: http
```

`oauth2-proxy/service-teslamate.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: oauth2-proxy-teslamate
spec:
  selector:
    app: oauth2-proxy-teslamate
  ports:
    - port: 4180
      targetPort: 4180
      name: http
```

- [ ] **Step 3: Update Routes in Task 3 and Task 4 to point to correct oauth2-proxy services**

Update `evcc/route.yaml` to target `oauth2-proxy-evcc`:

```yaml
  to:
    kind: Service
    name: oauth2-proxy-evcc
```

Update `teslamate/route.yaml` to target `oauth2-proxy-teslamate`:

```yaml
  to:
    kind: Service
    name: oauth2-proxy-teslamate
```

- [ ] **Step 4: Create plaintext secrets, then encrypt**

Create `oauth2-proxy/secret.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: oauth2-proxy
type: Opaque
stringData:
  OAUTH2_PROXY_CLIENT_ID: "REPLACE_WITH_GOOGLE_CLIENT_ID"
  OAUTH2_PROXY_CLIENT_SECRET: "REPLACE_WITH_GOOGLE_CLIENT_SECRET"
  OAUTH2_PROXY_COOKIE_SECRET: "REPLACE_WITH_COOKIE_SECRET"
```

Generate the cookie secret with: `openssl rand -base64 32 | tr -- '+/' '-_'`

Create `oauth2-proxy/emails-secret.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: oauth2-proxy-emails
type: Opaque
stringData:
  authenticated-emails: |
    user1@gmail.com
    user2@gmail.com
```

Encrypt both:

```bash
sops -e oauth2-proxy/secret.yaml > oauth2-proxy/secret.sops.yaml
sops -e oauth2-proxy/emails-secret.yaml > oauth2-proxy/emails-secret.sops.yaml
rm oauth2-proxy/secret.yaml oauth2-proxy/emails-secret.yaml
```

- [ ] **Step 5: Commit**

```bash
git add oauth2-proxy/ evcc/route.yaml teslamate/route.yaml
git commit -m "feat: add oauth2-proxy manifests for evcc and teslamate"
```

---

### Task 6: GitHub Actions CI/CD workflow

**Files:**
- Create: `.github/workflows/deploy.yaml`

**Interfaces:**
- Consumes: All manifests from Tasks 1-5; GitHub Actions secrets `SOPS_AGE_KEY` and `APPUIO_TOKEN`
- Produces: Automated deployment pipeline triggered on push to `main`

- [ ] **Step 1: Create `.github/workflows/deploy.yaml`**

```yaml
name: Deploy to APPUiO Cloud

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install sops
        uses: mdgreenwald/mozilla-sops-action@v1

      - name: Install age
        run: |
          curl -sSfLo age.tar.gz https://github.com/FiloSottile/age/releases/download/v1.2.1/age-v1.2.1-linux-amd64.tar.gz
          tar -xzf age.tar.gz
          sudo mv age/age /usr/local/bin/
          sudo mv age/age-keygen /usr/local/bin/

      - name: Install oc CLI
        uses: redhat-actions/openshift-tools-installer@v1
        with:
          oc: "4"

      - name: Login to APPUiO Cloud
        run: oc login --server=https://api.exoscale-ch-gva-2-0.appuio.cloud:6443 --token=${{ secrets.APPUIO_TOKEN }}

      - name: Decrypt secrets
        env:
          SOPS_AGE_KEY: ${{ secrets.SOPS_AGE_KEY }}
        run: |
          for f in $(find . -name '*.sops.yaml'); do
            sops -d -i "$f"
          done

      - name: Apply tesla-http-proxy
        run: oc apply -f tesla-http-proxy/

      - name: Apply teslamate
        run: oc apply -f teslamate/

      - name: Apply evcc
        run: oc apply -f evcc/

      - name: Apply oauth2-proxy
        run: oc apply -f oauth2-proxy/
```

- [ ] **Step 2: Commit**

```bash
git add .github/
git commit -m "feat: add GitHub Actions deploy workflow"
```

---

### Task 7: CLAUDE.md

**Files:**
- Create: `CLAUDE.md`

**Interfaces:**
- Consumes: All prior tasks
- Produces: Documentation for future Claude Code sessions

- [ ] **Step 1: Create `CLAUDE.md`**

```markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

GitOps repository deploying evcc (EV charging), teslamate (Tesla data logging), tesla-http-proxy (Fleet API gateway), and oauth2-proxy (Google OAuth) to APPUiO Cloud (shared OpenShift).

## Architecture

Plain Kubernetes/OpenShift manifests in per-app directories. No Helm, no Kustomize. GitHub Actions decrypts SOPS secrets and runs `oc apply` on push to main.

- **tesla-http-proxy**: shared Fleet API proxy for signed vehicle commands (cluster-internal only)
- **evcc**: charging control with SQLite on PVC, web UI exposed via oauth2-proxy
- **teslamate**: data logging with managed VSHNPostgreSQL, web UI exposed via oauth2-proxy
- **oauth2-proxy**: two instances (one per app) sharing Google OAuth credentials

## Secrets

Encrypted with SOPS + age. Private key in `age-key.txt` (gitignored).

- Edit: `sops <app>/secret.sops.yaml`
- Create: write plaintext Secret YAML, run `sops -e secret.yaml > secret.sops.yaml`, delete plaintext
- Only `data` and `stringData` fields are encrypted (configured in `.sops.yaml`)

## Deploy

Automatic on push to main via GitHub Actions. Manual:

```bash
oc login --server=https://api.exoscale-ch-gva-2-0.appuio.cloud:6443 --token=...
for f in $(find . -name '*.sops.yaml'); do sops -d -i "$f"; done
oc apply -f tesla-http-proxy/
oc apply -f teslamate/
oc apply -f evcc/
oc apply -f oauth2-proxy/
```

## Docker Images

All images use `dockerhub.vshn.net` proxy prefix to bypass Docker Hub pull limits.

## Database

teslamate uses VSHNPostgreSQL (AppCat managed). The CRD auto-creates a `teslamate-db-credentials` Secret with connection details. No manual DB credential management.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "feat: add CLAUDE.md"
```

---

### Task 8: Renovate configuration

**Files:**
- Create: `renovate.json`

**Interfaces:**
- Consumes: All manifest files with Docker image references; `.github/workflows/deploy.yaml` with action versions
- Produces: Automated dependency update PRs

- [ ] **Step 1: Create `renovate.json`**

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    "config:recommended"
  ],
  "registryAliases": {
    "dockerhub.vshn.net": "docker.io"
  },
  "packageRules": [
    {
      "matchUpdateTypes": ["minor", "patch"],
      "automerge": true
    },
    {
      "matchManagers": ["github-actions"],
      "automerge": true
    }
  ]
}
```

The `registryAliases` config tells Renovate that `dockerhub.vshn.net` is a proxy for Docker Hub so it can resolve upstream versions. Minor/patch updates and GitHub Actions updates are auto-merged.

- [ ] **Step 2: Commit**

```bash
git add renovate.json
git commit -m "feat: add Renovate config with aggressive auto-merge"
```
