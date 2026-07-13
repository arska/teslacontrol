# GitOps Design: evcc + teslamate on APPUiO Cloud

## Overview

GitOps repository for deploying and managing evcc (EV charging control) and teslamate (Tesla data logging) on APPUiO Cloud (shared OpenShift). Public repo with SOPS-encrypted secrets. Service name: **WattLens**.

## Components

| Component | Purpose | Storage | Exposed? |
|---|---|---|---|
| tesla-http-proxy | Fleet API auth + command signing | None (stateless) | No (cluster-internal) |
| evcc | Charging control, web UI | PVC for SQLite (`/home/evcc/.evcc/`) | Yes, via Ingress behind oauth2-proxy |
| teslamate | Vehicle data logging | VSHNPostgreSQL (managed via AppCat CRD) | Yes, via Ingress behind oauth2-proxy |
| oauth2-proxy | Google OAuth gateway (two instances) | None (stateless) | Yes (Ingress terminates here) |
| tesla-public-key | Nginx serving Fleet API public key | None (stateless) | Yes, unauthenticated |
| monitoring | PrometheusRule + AlertmanagerConfig | None | No (cluster-internal) |

## Architecture

```
Internet -> Ingress (TLS/Let's Encrypt) -> oauth2-proxy -> evcc / teslamate
                                            |           |
                                      tesla-http-proxy (internal Service)
                                            |
                                      Tesla Fleet API
```

- tesla-http-proxy is a single shared instance handling Fleet API authentication and signed commands for both evcc and teslamate.
- oauth2-proxy runs as two instances (one per app), sharing the same Google OAuth credentials and email allowlist. Each has its own Deployment, Service, and upstream target.
- Custom domains with Let's Encrypt TLS via cert-manager Ingress annotations.
- tesla-public-key serves the Fleet API public key on a separate unauthenticated domain (`fleet.aukia.com`).

## Repo Structure

```
teslacontrol/
├── tesla-http-proxy/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── secret.sops.yaml          # TLS cert/key + Fleet API private key
├── evcc/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── pvc.yaml
│   ├── ingress.yaml
│   └── secret.sops.yaml          # evcc.yaml config (contains Telegram bot token)
├── teslamate/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── ingress.yaml
│   ├── postgresql.yaml            # VSHNPostgreSQL CRD
│   └── secret.sops.yaml
├── oauth2-proxy/
│   ├── deployment-evcc.yaml
│   ├── deployment-teslamate.yaml
│   ├── service-evcc.yaml
│   ├── service-teslamate.yaml
│   ├── secret.sops.yaml           # Google OAuth client ID/secret, cookie secret
│   └── emails-secret.sops.yaml    # Authorized email allowlist
├── tesla-public-key/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── ingress.yaml
│   ├── configmap.yaml             # Fleet API public key PEM
│   └── nginx-configmap.yaml       # nginx config
├── monitoring/
│   ├── prometheusrule.yaml
│   ├── alertmanagerconfig.yaml
│   └── secret.sops.yaml           # Telegram bot token
├── .sops.yaml
├── .gitignore
├── .github/workflows/deploy.yaml
├── renovate.json
└── CLAUDE.md
```

## Custom Domains

| Domain | Service | Auth |
|---|---|---|
| `evcc.aukia.com` | oauth2-proxy-evcc -> evcc | Google OAuth |
| `teslamate.aukia.com` | oauth2-proxy-teslamate -> teslamate | Google OAuth |
| `fleet.aukia.com` | tesla-public-key (nginx) | None |

DNS CNAMEs point to `cname.exoscale-ch-gva-2-0.appuio.cloud`.

## Database

Teslamate uses a managed PostgreSQL instance provisioned via the VSHNPostgreSQL CRD (`vshn.appcat.vshn.io/v1`). The CRD automatically creates a Kubernetes Secret with connection credentials. Teslamate references this secret directly — no manual credential management needed.

evcc uses SQLite stored on a PVC mounted at `/home/evcc/.evcc/` (HOME set to `/home/evcc` for OpenShift non-root compatibility).

## Secrets Management

- **SOPS with age** for encrypting secrets in the public repo.
- `.sops.yaml` at repo root defines encryption rules, referencing the age public key.
- Age private key stored locally as `age-key.txt` (gitignored) and as GitHub Actions secret `SOPS_AGE_KEY`.
- Encrypted files use the naming convention `secret.sops.yaml` (or `emails-secret.sops.yaml`).
- Edit secrets with `sops secret.sops.yaml` — decrypts in `$EDITOR`, re-encrypts on save.
- CI decrypt excludes `.sops.yaml` config: `find . -name '*.sops.yaml' -not -name '.sops.yaml'`

### Secrets per component

| Component | Secrets |
|---|---|
| tesla-http-proxy | TLS cert/key (self-signed), Fleet API private key |
| evcc | evcc.yaml config (contains Telegram bot token + chat ID) |
| teslamate | Encryption key for stored tokens |
| oauth2-proxy | Google OAuth client ID + secret, cookie secret |
| oauth2-proxy-emails | Authorized email allowlist |
| monitoring/telegram-bot | Telegram bot token for alerts |

## Authentication and Routing

- oauth2-proxy with Google OAuth2 provider.
- Email allowlist restricting access to authorized users.
- Kubernetes Ingress (not OpenShift Routes) with cert-manager Let's Encrypt annotations.
- Two separate Ingress resources on custom domains, each pointing to its own oauth2-proxy Service.

## Telegram Notifications

- **evcc**: messaging config in evcc.yaml (inside SOPS-encrypted secret) with Telegram bot token and chat ID
- **APPUiO monitoring**: AlertmanagerConfig routes alerts to Telegram. PrometheusRule covers pod readiness, excessive restarts, and PVC capacity.
- Both share the same Telegram bot.

## CI/CD

GitHub Actions workflow (`.github/workflows/deploy.yaml`), triggered on push to `main`:

1. Checkout repo
2. Install `sops`, `age`, `oc` CLI
3. `oc login` to APPUiO Cloud, switch to `arska-teslacontrol` project
4. Decrypt all `*.sops.yaml` files in-place (excluding `.sops.yaml` config)
5. `oc apply -f <dir>/` for each app directory
6. Decrypted files exist only in the ephemeral runner workspace

### GitHub Actions secrets

| Secret | Purpose |
|---|---|
| `SOPS_AGE_KEY` | Age private key for SOPS decryption |
| `APPUIO_TOKEN` | Token for `oc login` to APPUiO Cloud |

## Tesla Fleet API

- Public key hosted at `https://fleet.aukia.com/.well-known/appspecific/com.tesla.3p.public-key.pem`
- Registration domain must not contain "tesla", must not be behind a reverse proxy, must have a valid CA certificate
- TLS cert for tesla-http-proxy is self-signed (cluster-internal only)

## Decisions Log

| Decision | Rationale |
|---|---|
| Plain manifests over Kustomize/Helm | Two apps, single environment — no templating benefit |
| Shared tesla-http-proxy | Fleet API requires signed commands; one proxy avoids duplicate keypairs |
| Two oauth2-proxy instances (not one shared) | OpenShift Routes/Ingress can't do host-based upstream routing in a single oauth2-proxy; two instances with different upstreams is simpler |
| Custom domains over platform subdomains | Tesla API rejects domains containing "tesla" and shared platform wildcard domains |
| Ingress over Routes | APPUiO Cloud requires Ingress for cert-manager/Let's Encrypt integration |
| VSHNPostgreSQL over self-managed Postgres | Managed backups, no PVC/operator management |
| SOPS + age over KMS | No cloud provider dependency, simple keypair |
| CI-driven apply over cluster-side GitOps | APPUiO Cloud doesn't allow installing ArgoCD/Flux |
| evcc config in Secret (not ConfigMap) | Contains Telegram bot token — must be encrypted |
| nginx-unprivileged for static serving | OpenShift runs containers as non-root |
| HOME=/home/evcc for evcc | OpenShift non-root; evcc looks for `$HOME/.evcc` for SQLite |
