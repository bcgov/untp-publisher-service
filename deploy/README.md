# Deploy overlays

Environment-specific Helm values for the [`untp-publisher`](../charts/untp-publisher) chart. Merge an overlay with chart defaults:

```bash
cd charts/untp-publisher
helm dependency update
helm upgrade --install untp-publisher-service . \
  -f ../../deploy/<env>/values.yaml \
  -n f890b1-<env>
```

| Overlay | Namespace | Notes |
|---------|-----------|--------|
| `deploy/dev/values.yaml` | `f890b1-dev` | Shared with `tests-untp-ri`. Quota: [tests-untp `f890b1-dev-quota.md`](https://github.com/bcgov/tests-untp/blob/charts/deploy/f890b1-dev-quota.md). Image **`dev-0.0.4`** + Mines landing branding. |
| `deploy/test/values.yaml` | `f890b1-test` | Traction/WebVH **test**. Image **`dev-0.0.4`** + Mines landing branding. |
| `deploy/prod/values.yaml` | `f890b1-prod` | Traction/WebVH **prod**. Image **`dev-0.0.4`** + Mines landing branding. |

Use release name **`untp-publisher-service`** (matches `fullnameOverride`, Traction Secret naming, and MongoDB `customUser.existingSecret`). MongoDB Service: `untp-publisher-service-mongodb`.

MongoDB app-user credentials are **not** regenerated on every `helm upgrade`: set `mongodb.customUser.existingSecret` to `{release}-mongodb-custom-user-secret` so the parent chart owns the Secret (same `lookup` pattern as the Traction secret). The bundled CloudPirates subchart only uses `lookup` for the root admin password; its `custom-user-secret` template does not.

## Layout

| Path | Environment |
|------|-------------|
| `deploy/dev/values.yaml` | Development (BC Gov OpenShift Gold) |
| `deploy/test/values.yaml` | Test (BC Gov OpenShift Gold) |
| `deploy/prod/values.yaml` | Production (BC Gov OpenShift Gold) |

## Prerequisites

### Test suite mode (optional)

Set **`backend.testSuite: true`** to expose only **`GET /server/status`** and **`/test-suite/*`** (publisher API routes are not registered). Startup is unchanged: the pod still runs **`main.py`** and **`provision()`** (Traction + Mongo). MongoDB and the Traction Secret must be healthy.

### Traction

Required when **`backend.testSuite`** is false (and still needed at startup for provision even when true). Set **`backend.traction.apiUrl`** in values (non-secret). Create a Secret for the tenant credentials and reference it with **`backend.traction.existingSecret`**.

```yaml
backend:
  traction:
    existingSecret: untp-publisher-service-traction-tenant-info
    apiUrl: "https://traction-tenant-proxy-<env>.apps.silver.devops.gov.bc.ca"
```

```bash
kubectl create secret generic untp-publisher-service-traction-tenant-info \
  --from-literal=traction_tenant_id='<tenant-id>' \
  --from-literal=traction_api_key='<api-key>' \
  -n <namespace>
```

Default keys are `traction_tenant_id` and `traction_api_key` (`backend.traction.secretKeys`). When `existingSecret` is empty, Helm manages **`{fullname}-traction`** (e.g. `untp-publisher-service-traction`).

| Env | Traction tenant proxy |
|-----|------------------------|
| dev | `https://traction-tenant-proxy-dev.apps.silver.devops.gov.bc.ca` |
| test | `https://traction-tenant-proxy-test.apps.silver.devops.gov.bc.ca` |
| prod | `https://traction-tenant-proxy-prod.apps.silver.devops.gov.bc.ca` |

### WebVH / witness

Set under **`backend.environment`** (mapped to pod env by the Deployment):

```yaml
backend:
  environment:
    webvhServerUrl: "https://registry-<env>.digitaltrust.gov.bc.ca"  # prod: registry.digitaltrust.gov.bc.ca
    publisherWitnessId: "did:key:z6Mk…"
```

| Env | WebVH registry |
|-----|----------------|
| dev | `https://registry-dev.digitaltrust.gov.bc.ca` |
| test | `https://registry-test.digitaltrust.gov.bc.ca` |
| prod | `https://registry.digitaltrust.gov.bc.ca` |

Witness `did:key` values in the overlays match the former orgbook-publisher chart multikeys for each environment.

### Ingress / TLS

All overlays follow [BC-Wallet-Demo `deploy/showcase/values-dev.yaml`](https://github.com/bcgov/BC-Wallet-Demo/blob/main/deploy/showcase/values-dev.yaml):

| Env | Host |
|-----|------|
| dev | `untp-publisher-api-dev.apps.gold.devops.gov.bc.ca` |
| test | `untp-publisher-api-test.apps.gold.devops.gov.bc.ca` |
| prod | `untp-publisher-api.apps.gold.devops.gov.bc.ca` |

- Annotation: `route.openshift.io/termination: edge` (router terminates HTTPS)
- **No** `ingress.tls` block and **no** custom cert Secret

Traction tenant proxies remain on **Silver** even though the publisher Route is on Gold.

## Notes

- **`backend.host`** sets the Ingress host and the pod **`PUBLISHER_DOMAIN`** env var (hostname only, no `https://`; do not set `PUBLISHER_DOMAIN` under `backend.environment`).
- Overlays omit **HPA / autoscaling** fields from the legacy gitops chart; the current chart uses fixed `backend.replicaCount`.
- **MongoDB 8 memory:** overlays use a **768Mi** limit / **256Mi** request, WiredTiger `cacheSizeGB: 0.25`, and 30s probe periods. Namespace quotas count **requests** only — this stays well under the 2Gi long-running request quota without a quota increase. Do not drop the limit below ~768Mi without re-validating (384–512Mi has OOMKilled under `mongosh` probes).
