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

## Deployment Environment

- **Cluster**: APPUiO Cloud (shared OpenShift)
- **Namespace**: `arska-teslacontrol`

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

All images use `dockerhub.vshn.net` proxy prefix to bypass Docker Hub pull limits. Renovate is configured with `registryAliases` to resolve versions through this proxy.

## Renovate

Renovate is configured for auto-merging minor and patch updates. The configuration uses `dockerhub.vshn.net` as the Docker Hub proxy to handle image version resolution.

## Database

teslamate uses VSHNPostgreSQL (AppCat managed). The CRD auto-creates a `teslamate-db-credentials` Secret with connection details. No manual DB credential management.
