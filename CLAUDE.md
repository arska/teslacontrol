# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

GitOps repository deploying evcc (EV charging), teslamate (Tesla data logging), emoncms (energy monitoring), and oauth2-proxy (Google OAuth) to APPUiO Cloud (shared OpenShift). Service name: **WattLens**.

## Architecture

Plain Kubernetes/OpenShift manifests in per-app directories. No Helm, no Kustomize. GitHub Actions decrypts SOPS secrets and runs `oc apply` on push to main.

- **evcc**: charging control with SQLite on PVC, web UI exposed via oauth2-proxy
- **teslamate**: data logging with managed VSHNPostgreSQL, web UI exposed via oauth2-proxy
- **emoncms**: energy monitoring receiving IoTaWatt data, custom rootless Docker image on GHCR, VSHNMariaDB for metadata, PVC for PHPFina feed data
- **oauth2-proxy**: instances per app sharing Google OAuth credentials
- **monitoring**: PrometheusRule + AlertmanagerConfig with Telegram alerts, plus a blackbox exporter probing every app (`monitoring/blackbox-exporter.yaml`, `monitoring/probe.yaml`)

## Deployment Environment

- **Cluster**: APPUiO Cloud (shared OpenShift)
- **Namespace**: `arska-teslacontrol`
- **Custom domains**: `evcc.aukia.com`, `teslamate.aukia.com`, `emoncms.aukia.com`
- DNS CNAMEs point to `cname.exoscale-ch-gva-2-0.appuio.cloud`

## Ingress and TLS

Uses Kubernetes Ingress (not OpenShift Routes) — APPUiO Cloud requires Ingress for cert-manager/Let's Encrypt integration. Routes do not support cert-manager.

Annotate with `cert-manager.io/cluster-issuer: letsencrypt-production` for automatic TLS. Use `letsencrypt-staging` for testing to avoid rate limits.

## OpenShift Constraints

Containers run as **non-root**. Key implications:
- nginx: use `nginxinc/nginx-unprivileged` (not `nginx:alpine`)
- evcc: set `HOME=/home/evcc` env var and mount PVC at `/home/evcc/.evcc` (not `/root/.evcc`)
- emoncms: custom rootless Dockerfile (`emoncms/docker/`), Apache on port 8080, `chgrp -R 0` + `chmod -R g=u` for arbitrary UID
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
oc apply -f evcc/
oc apply -f teslamate/
oc apply -f oauth2-proxy/
oc apply -f emoncms/
oc apply -f monitoring/
# Patch emoncms API routes to allow HTTP (IoTaWatt can't do HTTPS)
for route in $(oc get routes -o name | grep emoncms-api); do
  oc patch "$route" -p '{"spec":{"tls":{"insecureEdgeTerminationPolicy":"Allow"}}}'
done
```

Note: `oc apply` does not update `spec.host` on existing Routes/Ingress. To change hostnames, delete and recreate. OpenShift auto-creates Routes from Ingresses — the `insecureEdgeTerminationPolicy` annotation on Ingresses is ignored, so API routes must be patched after apply.

## Docker Images

Most images use `dockerhub.vshn.net` proxy prefix to bypass Docker Hub pull limits. Exceptions: oauth2-proxy uses `quay.io/oauth2-proxy/oauth2-proxy` (not on Docker Hub), emoncms uses `ghcr.io/arska/emoncms` (custom-built, pushed by `.github/workflows/build-emoncms.yaml`).

## Renovate

Configured for auto-merging minor and patch updates, plus GitHub Actions updates. Uses `registryAliases` mapping `dockerhub.vshn.net` to `docker.io`. Custom regex manager tracks `emoncms/emoncms` GitHub tags for the `EMONCMS_VERSION` ARG in the Dockerfile.

## Telegram

- **evcc notifications**: bot token and chat ID in `evcc/secret.sops.yaml` (inside the evcc.yaml config)
- **monitoring alerts**: bot token in `monitoring/secret.sops.yaml`, chat ID in `monitoring/alertmanagerconfig.yaml`

## Database

- **teslamate**: VSHNPostgreSQL (AppCat managed). CRD auto-creates `teslamate-db-credentials` Secret. Deletion protection enabled by default.
- **emoncms**: VSHNMariaDB (AppCat managed). CRD auto-creates `emoncms-db-credentials` Secret. Note: the AppCat secret has no database name — the deployment maps individual keys (`MARIADB_HOST`, `MARIADB_PORT`, `MARIADB_USERNAME`, `MARIADB_PASSWORD`) and an init container creates the `emoncms` database on startup.

**Disk sizing**: every AppCat plan (`standard-512m` through `standard-8`) ships the same 16Gi disk. The plan only scales CPU and memory. Set `spec.parameters.size.disk` explicitly. AppCat allows increasing disk, never decreasing.

**Alerting**: the database pods and PVCs run in a VSHN-managed instance namespace, not `arska-teslacontrol`, so `monitoring/prometheusrule.yaml` cannot see them. AppCat generates its own `PersistentVolumeFillingUp` / `PersistentVolumeExpectedToFillUp` / `MemoryCritical` rules in that namespace, but they only reach us if `spec.parameters.monitoring.alertmanagerConfigRef` + `alertmanagerConfigSecretRef` name an AlertmanagerConfig and Secret in our namespace for AppCat to copy over.

## Monitoring

Alerting rules live in `monitoring/prometheusrule.yaml` and route to Telegram via `monitoring/alertmanagerconfig.yaml`.

**Probe apps from outside, not from status codes alone.** emoncms answers HTTP 200 with a PHP fatal error in the body when its database is unreachable, so a status-code check reports a dead app as healthy. The `http_php` blackbox module adds `fail_if_body_matches_regexp`. Internal probes (`http://<svc>.arska-teslacontrol.svc.cluster.local:<port>/`) test the app itself and bypass oauth2-proxy; public probes test DNS, TLS and ingress, where an unauthenticated 403 from oauth2-proxy is the healthy answer.

**Two Prometheus instances evaluate our alerts.** Rules in `monitoring/prometheusrule.yaml` run in `openshift-user-workload-monitoring/user-workload`; AppCat's instance-namespace rules run in `openshift-monitoring/k8s`. Both route to our Telegram. `kube_pod_status_ready` and `kubelet_volume_stats_*` were confirmed present in user-workload monitoring on 2026-08-24, so the pod and PVC rules do work for resources in `arska-teslacontrol`. `PlatformMetricsUnavailable` is the canary that catches it if that ever stops being true, because the failure mode is silence rather than an error.

## Tesla Fleet API

Pre-2021 Model S/X vehicles do not require a command proxy — commands go directly through Tesla's Fleet API using access/refresh tokens. No signed commands, no keypair, no domain registration needed.

For newer vehicles (2021+ Model S/X, all Model 3/Y), a command proxy with registered keypair would be needed. This is not currently deployed.

App registered via myteslamate.com (client-credentials grant). Tokens obtained via myteslamate's OAuth flow.
