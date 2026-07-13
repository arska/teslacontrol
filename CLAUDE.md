# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

GitOps repository deploying evcc (EV charging), teslamate (Tesla data logging), tesla-http-proxy (Fleet API gateway), and oauth2-proxy (Google OAuth) to APPUiO Cloud (shared OpenShift). Service name: **WattLens**.

## Architecture

Plain Kubernetes/OpenShift manifests in per-app directories. No Helm, no Kustomize. GitHub Actions decrypts SOPS secrets and runs `oc apply` on push to main.

- **tesla-http-proxy**: shared Fleet API proxy for signed vehicle commands (cluster-internal only)
- **evcc**: charging control with SQLite on PVC, web UI exposed via oauth2-proxy
- **teslamate**: data logging with managed VSHNPostgreSQL, web UI exposed via oauth2-proxy
- **oauth2-proxy**: two instances (one per app) sharing Google OAuth credentials
- **tesla-public-key**: nginx serving the Fleet API public key at `/.well-known/appspecific/com.tesla.3p.public-key.pem`
- **monitoring**: PrometheusRule + AlertmanagerConfig with Telegram alerts

## Deployment Environment

- **Cluster**: APPUiO Cloud (shared OpenShift)
- **Namespace**: `arska-teslacontrol`
- **Custom domains**: `evcc.aukia.com`, `teslamate.aukia.com`, `fleet.aukia.com`
- DNS CNAMEs point to `cname.exoscale-ch-gva-2-0.appuio.cloud`

## Ingress and TLS

Uses Kubernetes Ingress (not OpenShift Routes) — APPUiO Cloud requires Ingress for cert-manager/Let's Encrypt integration. Routes do not support cert-manager.

Annotate with `cert-manager.io/cluster-issuer: letsencrypt-production` for automatic TLS. Use `letsencrypt-staging` for testing to avoid rate limits.

## OpenShift Constraints

Containers run as **non-root**. Key implications:
- nginx: use `nginxinc/nginx-unprivileged` (not `nginx:alpine`)
- evcc: set `HOME=/home/evcc` env var and mount PVC at `/home/evcc/.evcc` (not `/root/.evcc`)
- Ports must be >= 1024

## Secrets

Encrypted with SOPS + age. Private key in `age-key.txt` (gitignored).

- Edit: `sops <app>/secret.sops.yaml`
- Create: write plaintext Secret YAML, run `sops -e secret.yaml > secret.sops.yaml`, delete plaintext
- Only `data` and `stringData` fields are encrypted (configured in `.sops.yaml`)
- evcc config (`evcc.yaml`) lives inside `evcc/secret.sops.yaml` (not a ConfigMap) because it contains the Telegram bot token

## Deploy

Automatic on push to main via GitHub Actions. Manual:

```bash
oc login --server=https://api.exoscale-ch-gva-2-0.appuio.cloud:6443 --token=...
oc project arska-teslacontrol
for f in $(find . -name '*.sops.yaml' -not -name '.sops.yaml'); do sops -d -i "$f"; done
oc apply -f tesla-http-proxy/
oc apply -f teslamate/
oc apply -f evcc/
oc apply -f oauth2-proxy/
oc apply -f tesla-public-key/
oc apply -f monitoring/
```

Note: `oc apply` does not update `spec.host` on existing Routes/Ingress. To change hostnames, delete and recreate.

## Docker Images

Most images use `dockerhub.vshn.net` proxy prefix to bypass Docker Hub pull limits. Exception: oauth2-proxy uses `quay.io/oauth2-proxy/oauth2-proxy` (not on Docker Hub).

## Renovate

Configured for auto-merging minor and patch updates, plus GitHub Actions updates. Uses `registryAliases` mapping `dockerhub.vshn.net` to `docker.io`.

## Telegram

- **evcc notifications**: bot token and chat ID in `evcc/secret.sops.yaml` (inside the evcc.yaml config)
- **monitoring alerts**: bot token in `monitoring/secret.sops.yaml`, chat ID in `monitoring/alertmanagerconfig.yaml`

## Database

teslamate uses VSHNPostgreSQL (AppCat managed). The CRD auto-creates a `teslamate-db-credentials` Secret with connection details. No manual DB credential management. Deletion protection is enabled by default — must be disabled before deleting the instance.

## Tesla Fleet API

Public key served at `https://fleet.aukia.com/.well-known/appspecific/com.tesla.3p.public-key.pem`. Tesla developer registration requires a domain that:
- Does not contain the word "tesla"
- Is not behind a reverse proxy (use a direct endpoint, not oauth2-proxy)
- Is registered with a certificate authority (shared platform wildcard domains may be rejected)
