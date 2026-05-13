# Upgrade Guide

## Chart 0.1.0 — Rename to `untp-publisher`

Chart **name** and directory changed from `orgbook-publisher` to **`untp-publisher`**.

| | Before (chart &lt; 0.1.0) | After (chart ≥ 0.1.0) |
| --- | --- | --- |
| Chart path | `./charts/orgbook-publisher` | `./charts/untp-publisher` |
| Helm chart `name` | `orgbook-publisher` | `untp-publisher` |
| Default `fullnameOverride` / `nameOverride` | `orgbook-publisher` | `untp-publisher` |
| Default MongoDB app user & database | `orgbook-publisher` | `untp-publisher` |
| Default backend image | `ghcr.io/bcgov/orgbook-publisher-service` | `ghcr.io/bcgov/untp-publisher-service` |

**Upgrading an existing release**

- Point `helm upgrade` at `./charts/untp-publisher` (or the packaged chart version from releases).
- If you must keep **Kubernetes resource names** stable (e.g. `myrelease-orgbook-publisher`), set `fullnameOverride` and `nameOverride` in your values to the previous strings.
- If you must keep the **MongoDB** application user/database or **container image** repository, override `mongodb.customUser.*` and `backend.image.repository` in values.
- The pod still receives **`ORGBOOK_URL`** for optional read-only entity lookup; unset or leave empty only if your deployment does not use that integration.

---

## Migrating from Bitnami MongoDB to CloudPirates MongoDB

Starting with chart version `0.0.4`, the bundled MongoDB subchart has changed from
[Bitnami MongoDB](https://github.com/bitnami/charts/tree/main/bitnami/mongodb) to
[CloudPirates MongoDB](https://github.com/CloudPirates-io/helm-charts/tree/main/charts/mongodb).

> **This is a breaking change.** Data does **not** migrate automatically.
> Follow the steps below before running `helm upgrade`.

---

### What Changed

| | Bitnami (old) | CloudPirates (new) |
| --- | --- | --- |
| Default architecture | `replicaset` | standalone |
| MongoDB image | Bitnami custom image | `mongo:8.0` (official) |
| Custom user creation | `auth.usernames[]` / `auth.databases[]` | init script via `customUser.*` |
| App connects to | `{release}-mongodb-headless` (headless) | `{release}-mongodb` |
| Credential secret name | `{release}-mongodb` | `{release}-mongodb-custom-user-secret` |
| Secret key — user password | `mongodb-passwords` | `CUSTOM_PASSWORD` |
| Default storage class | `netapp-block-standard` | cluster default (`""`) |

---

### Pre-Migration Checklist

```bash
RELEASE=<your-helm-release-name>
NAMESPACE=<your-namespace>
# Application database name on the *old* Bitnami deployment (often orgbook-publisher for older chart defaults)
SOURCE_DB=orgbook-publisher
# Application database name on the *new* CloudPirates deployment — must match mongodb.customUser.* in your values (chart default untp-publisher for chart ≥ 0.1.0)
TARGET_DB=untp-publisher
```

- [ ] Confirm the release name and namespace above
- [ ] You have `kubectl` access with exec permissions
- [ ] You have enough local disk space for the dump
- [ ] You have tested this procedure in a non-production environment first
- [ ] If `SOURCE_DB` and `TARGET_DB` differ, plan namespace mapping or temporarily set `mongodb.customUser` to `SOURCE_DB` until data is migrated

---

### Step 1 — Back Up the Existing Data

Retrieve the custom-user password from the old secret:

```bash
OLD_PASSWORD=$(kubectl get secret "${RELEASE}-mongodb" \
  -n "${NAMESPACE}" \
  -o jsonpath="{.data.mongodb-passwords}" | base64 --decode)
```

> If you used `database.existingSecret` to supply your own secret, substitute that
> secret name and key here instead.

Run `mongodump` from inside the primary replica pod (use your real **`SOURCE_DB`**):

```bash
kubectl exec -it "${RELEASE}-mongodb-0" -n "${NAMESPACE}" -- \
  mongodump \
    --host "localhost:27017" \
    --username "${SOURCE_DB}" \
    --password "${OLD_PASSWORD}" \
    --authenticationDatabase "${SOURCE_DB}" \
    --db "${SOURCE_DB}" \
    --out /tmp/backup
```

Copy the dump to your local machine:

```bash
kubectl cp "${NAMESPACE}/${RELEASE}-mongodb-0:/tmp/backup" ./mongodb-backup
```

Verify the dump is non-empty before proceeding:

```bash
ls -lh "./mongodb-backup/${SOURCE_DB}/"
```

---

### Step 2 — Scale Down the Backend

Prevent the application from writing to MongoDB while the migration is in progress:

```bash
kubectl scale deployment "${RELEASE}" \
  -n "${NAMESPACE}" \
  --replicas=0
```

---

### Step 3 — Delete the Old MongoDB StatefulSet and PVCs

> **Warning:** This permanently deletes the old Bitnami MongoDB data volumes.
> Only proceed if Step 1 completed successfully.

Uninstall the old StatefulSet by upgrading to the new chart version (Helm will
replace the Bitnami StatefulSet with the CloudPirates one):

```bash
helm upgrade "${RELEASE}" ./charts/untp-publisher \
  -n "${NAMESPACE}" \
  --set mongodb.persistence.storageClass=<your-storage-class>
```

The old Bitnami PVCs are not deleted by `helm upgrade` automatically. Once the
new MongoDB pod is `Running`, delete the orphaned Bitnami PVCs:

```bash
# Identify old PVCs (Bitnami labels them with app.kubernetes.io/name=mongodb
# and app.kubernetes.io/instance=<release>)
kubectl get pvc -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE}"

# Delete only after confirming the new pod is healthy (see Step 4)
```

---

### Step 4 — Wait for the New MongoDB Pod

```bash
kubectl rollout status statefulset "${RELEASE}-mongodb" -n "${NAMESPACE}"
```

Confirm the custom user was created by the init script:

```bash
NEW_PASSWORD=$(kubectl get secret "${RELEASE}-mongodb-custom-user-secret" \
  -n "${NAMESPACE}" \
  -o jsonpath="{.data['CUSTOM_PASSWORD']}" | base64 --decode)

kubectl exec -it "${RELEASE}-mongodb-0" -n "${NAMESPACE}" -- \
  mongosh \
    --username "${TARGET_DB}" \
    --password "${NEW_PASSWORD}" \
    --authenticationDatabase "${TARGET_DB}" \
    --eval "db.runCommand({ connectionStatus: 1 })"
```

You should see `"ok" : 1` in the output. If you see an authentication error,
the init script may not have run yet — wait a moment and retry.

---

### Step 5 — Restore the Data

Copy the dump into the new pod and restore:

```bash
kubectl cp ./mongodb-backup "${NAMESPACE}/${RELEASE}-mongodb-0:/tmp/backup"

kubectl exec -it "${RELEASE}-mongodb-0" -n "${NAMESPACE}" -- \
  mongorestore \
    --host "localhost:27017" \
    --username "${TARGET_DB}" \
    --password "${NEW_PASSWORD}" \
    --authenticationDatabase "${TARGET_DB}" \
    --db "${TARGET_DB}" \
    --drop \
    "/tmp/backup/${SOURCE_DB}"
```

> If **`SOURCE_DB`** and **`TARGET_DB`** differ, `mongorestore` may require
> [`--nsFrom` / `--nsTo`](https://www.mongodb.com/docs/database-tools/mongorestore/)
> instead of the simple `--db` + directory layout above. Alternatively, set
> `mongodb.customUser.name` and `mongodb.customUser.database` to **`SOURCE_DB`**
> for the upgrade so credentials match the dump layout, then rename or migrate later.

The `--drop` flag drops and recreates each collection before restoring, which
is safe here since the new database is empty.

---

### Step 6 — Scale the Backend Back Up

```bash
kubectl scale deployment "${RELEASE}" \
  -n "${NAMESPACE}" \
  --replicas=1

kubectl rollout status deployment "${RELEASE}" -n "${NAMESPACE}"
```

Hit the health endpoint to confirm the application is healthy:

```bash
kubectl exec -it deploy/"${RELEASE}" -n "${NAMESPACE}" -- \
  curl -s http://localhost:8000/server/status
```

---

### Step 7 — Clean Up

Once you have confirmed the application is fully healthy, remove the old Bitnami PVCs:

```bash
kubectl delete pvc \
  -n "${NAMESPACE}" \
  -l "app.kubernetes.io/name=mongodb,app.kubernetes.io/instance=${RELEASE}"
```

You may also delete the local backup once you are satisfied:

```bash
rm -rf ./mongodb-backup
```

---

### Rollback

If something goes wrong before Step 5, you can roll back to the previous Helm release:

```bash
helm rollback "${RELEASE}" -n "${NAMESPACE}"
```

Then scale the backend back up and verify:

```bash
kubectl scale deployment "${RELEASE}" -n "${NAMESPACE}" --replicas=1
```

> Note: `helm rollback` restores the Bitnami StatefulSet but will not restore
> deleted PVCs. If you deleted PVCs before rolling back, you will need to restore
> from the Step 1 dump into the Bitnami MongoDB instead.

---

### Using an Existing Secret (GitOps / External Credentials)

If you want to supply your own credentials secret instead of using the
auto-generated one, create a secret with the following keys before upgrading:

```bash
kubectl create secret generic my-mongodb-secret \
  -n "${NAMESPACE}" \
  --from-literal=CUSTOM_USER="${TARGET_DB}" \
  --from-literal=CUSTOM_PASSWORD=<your-password> \
  --from-literal=CUSTOM_DB="${TARGET_DB}"
```

Then set in your values:

```yaml
mongodb:
  customUser:
    existingSecret: "my-mongodb-secret"
```

The password from Step 1 can then be used directly as `CUSTOM_PASSWORD` when you
intend to keep the same application password across the migration.
