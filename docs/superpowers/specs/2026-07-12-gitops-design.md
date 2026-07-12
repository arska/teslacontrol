# GitOps Design: evcc + teslamate on APPUiO Cloud

## Overview

GitOps repository for deploying and managing evcc (EV charging control) and teslamate (Tesla data logging) on APPUiO Cloud (shared OpenShift). Public repo with SOPS-encrypted secrets.

## Components

| Component | Purpose | Storage | Exposed? |
|---|---|---|---|
| tesla-http-proxy | Fleet API auth + command signing | None (stateless) | No (cluster-internal) |
| evcc | Charging control, web UI | PVC for SQLite (`/root/.evcc/`) + ConfigMap for config | Yes, via Route behind oauth2-proxy |
| teslamate | Vehicle data logging | VSHNPostgreSQL (managed via AppCat CRD) | Yes, via Route behind oauth2-proxy |
| oauth2-proxy | Google OAuth gateway | None (stateless) | Yes (Routes terminate here) |

## Architecture

```
Internet -> Route (TLS) -> oauth2-proxy -> evcc / teslamate
                                            |           |
                                      tesla-http-proxy (internal Service)
                                            |
                                      Tesla Fleet API
```

- tesla-http-proxy is a single shared instance handling Fleet API authentication and signed commands for both evcc and teslamate.
- oauth2-proxy is a single shared instance fronting both apps, using Google OAuth with an email allowlist.
- Each app gets its own subdomain on `apps.exoscale-ch-gva-2-0.appuio.cloud`. oauth2-proxy uses `X-Forwarded-Host` to route to the correct upstream.

## Repo Structure

```
teslacontrol/
├── tesla-http-proxy/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── secret.sops.yaml
├── evcc/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── pvc.yaml
│   ├── configmap.yaml        # evcc.yaml config
│   ├── route.yaml
│   └── secret.sops.yaml
├── teslamate/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── route.yaml
│   ├── postgresql.yaml       # VSHNPostgreSQL CRD
│   └── secret.sops.yaml
├── oauth2-proxy/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── secret.sops.yaml
├── .sops.yaml
├── .gitignore
├── .github/workflows/deploy.yaml
└── CLAUDE.md
```

## Database

Teslamate uses a managed PostgreSQL instance provisioned via the VSHNPostgreSQL CRD (`vshn.appcat.vshn.io/v1`). The CRD automatically creates a Kubernetes Secret with connection credentials. Teslamate references this secret directly — no manual credential management needed.

evcc uses SQLite stored on a PVC mounted at `/root/.evcc/`.

## Secrets Management

- **SOPS with age** for encrypting secrets in the public repo.
- `.sops.yaml` at repo root defines encryption rules, referencing the age public key.
- Age private key stored locally as `age-key.txt` (gitignored) and as GitHub Actions secret `SOPS_AGE_KEY`.
- Encrypted files use the naming convention `secret.sops.yaml`.
- Edit secrets with `sops secret.sops.yaml` — decrypts in `$EDITOR`, re-encrypts on save.

### Secrets per component

| Component | Secrets |
|---|---|
| tesla-http-proxy | Fleet API private key, Tesla OAuth refresh token |
| evcc | Tesla API config (proxy URL + credentials) |
| teslamate | Tesla API config, encryption key for stored tokens |
| oauth2-proxy | Google OAuth client ID + secret, cookie secret, email allowlist |

## Authentication and Routing

- oauth2-proxy with Google OAuth2 provider.
- Email allowlist restricting access to authorized users.
- Two separate OpenShift Routes (separate subdomains on `apps.exoscale-ch-gva-2-0.appuio.cloud`), both pointing to the oauth2-proxy Service.
- oauth2-proxy routes to the correct upstream based on the incoming hostname.

## CI/CD

GitHub Actions workflow (`.github/workflows/deploy.yaml`), triggered on push to `main`:

1. Checkout repo
2. Install `sops`, `age`, `oc` CLI
3. `oc login` to APPUiO Cloud using service account token
4. Decrypt all `*.sops.yaml` files in-place (`sops -d -i`)
5. `oc apply -f <dir>/` for each app directory in order: tesla-http-proxy, evcc, teslamate, oauth2-proxy
6. Decrypted files exist only in the ephemeral runner workspace

### GitHub Actions secrets

| Secret | Purpose |
|---|---|
| `SOPS_AGE_KEY` | Age private key for SOPS decryption |
| `APPUIO_TOKEN` | Service account token for `oc login` to APPUiO Cloud |

## Local Development

Prerequisites: `age`, `sops`, `oc` CLI.

- Age private key at repo root (`age-key.txt`), gitignored.
- Edit secrets: `sops <app>/secret.sops.yaml`
- Manual deploy: `oc login`, decrypt secrets, `oc apply -f <dir>/` per app.

## Decisions Log

| Decision | Rationale |
|---|---|
| Plain manifests over Kustomize/Helm | Two apps, single environment — no templating benefit |
| Shared tesla-http-proxy | Fleet API requires signed commands; one proxy avoids duplicate keypairs |
| Shared oauth2-proxy | Same access policy for both apps, less infrastructure |
| Separate subdomains over path-based routing | Avoids path-rewriting issues |
| VSHNPostgreSQL over self-managed Postgres | Managed backups, no PVC/operator management |
| SOPS + age over KMS | No cloud provider dependency, simple keypair |
| CI-driven apply over cluster-side GitOps | APPUiO Cloud doesn't allow installing ArgoCD/Flux |
