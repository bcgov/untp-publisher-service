# Vendored CloudPirates MongoDB chart

Based on [CloudPirates mongodb 0.10.3](https://github.com/CloudPirates-io/helm-charts/tree/main/charts/mongodb).

## Patch: stable custom-user credentials

**File:** `templates/custom-user-secret.yaml`

- On first install, generate `CUSTOM_PASSWORD` (or use `customUser.password` if set).
- On `helm upgrade`, reuse existing `CUSTOM_PASSWORD` / `CUSTOM_USER` / `CUSTOM_DB` from the Secret via `lookup` so credentials stay aligned with the initialized PVC.
- Annotation `helm.sh/resource-policy: keep` so the Secret is not removed on accidental release deletion during upgrades.

Upstream issue: empty `customUser.existingSecret` + `randAlphaNum` in the template rotates the password every render.
