# Deploy overlays

Environment-specific Helm values for the [`untp-publisher`](../charts/untp-publisher) chart. Merge an overlay with chart defaults:

```bash
cd charts/untp-publisher
helm dependency update
helm upgrade --install untp-publisher-service . \
  -f ../../deploy/dev/values.yaml \
  -n <namespace>
```

Use release name **`untp-publisher-service`** (matches `fullnameOverride`, dev Traction Secret naming, and MongoDB `customUser.existingSecret`). MongoDB Service: `untp-publisher-service-mongodb`.

MongoDB app-user credentials are **not** regenerated on every `helm upgrade`: set `mongodb.customUser.existingSecret` to `{release}-mongodb-custom-user-secret` so the parent chart owns the Secret (same `lookup` pattern as the Traction secret). The bundled CloudPirates subchart only uses `lookup` for the root admin password; its `custom-user-secret` template does not.

## Layout

| Path | Environment |
|------|-------------|
| `deploy/dev/values.yaml` | Development (BC Gov OpenShift Gold) |
| `deploy/test/values.yaml` | Test (placeholder — align before use) |

## Prerequisites

### Test suite mode (optional)

Set **`backend.testSuite: true`** to expose only **`GET /server/status`** and **`POST /test-suite/validate`** (publisher API routes are not registered). Startup is unchanged: the pod still runs **`main.py`** and **`provision()`** (Traction + Mongo). MongoDB must be healthy.

### Traction

Required when **`backend.testSuite`** is false. Set **`backend.traction.apiUrl`** in values (non-secret). Create a Secret for the tenant credentials and reference it with **`backend.traction.existingSecret`**.

```yaml
backend:
  traction:
    existingSecret: untp-publisher-service-traction-tenant-info
    apiUrl: "https://traction-tenant-proxy-dev.apps.silver.devops.gov.bc.ca"
```

```bash
kubectl create secret generic untp-publisher-service-traction-tenant-info \
  --from-literal=traction_tenant_id='<tenant-id>' \
  --from-literal=traction_api_key='<api-key>' \
  -n <namespace>
```

Default keys are `traction_tenant_id` and `traction_api_key` (`backend.traction.secretKeys`). When `existingSecret` is empty, Helm manages **`{fullname}-traction`** (e.g. `untp-publisher-service-traction`).

### Ingress / TLS (dev)

Dev follows [BC-Wallet-Demo `deploy/showcase/values-dev.yaml`](https://github.com/bcgov/BC-Wallet-Demo/blob/main/deploy/showcase/values-dev.yaml):

- Host: `untp-publisher-dev.apps.gold.devops.gov.bc.ca` (standard Gold `*.apps.gold.devops.gov.bc.ca` Route)
- Annotation: `route.openshift.io/termination: edge` (router terminates HTTPS)
- **No** `ingress.tls` block and **no** custom cert Secret

Traction dev tenant proxy remains on **Silver** (`traction-tenant-proxy-dev.apps.silver.devops.gov.bc.ca`) even though the publisher Route is on Gold.

The test overlay may use ministry DNS (`*.orgbook.gov.bc.ca`) and TLS secrets when configured.

## Notes

- **`backend.host`** sets the Ingress host and the pod `DOMAIN` env var (hostname only, no `https://`; do not set `DOMAIN` under `backend.environment`).
- Overlays omit **HPA / autoscaling** fields from the legacy gitops chart; the current chart uses fixed `backend.replicaCount`.
