# untp-publisher

![Version: 0.1.0](https://img.shields.io/badge/Version-0.1.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 0.0.5](https://img.shields.io/badge/AppVersion-0.0.5-informational?style=flat-square)

Helm chart for the [UNTP Publisher](https://github.com/bcgov/untp-publisher-service) — credential type registration, issuance via Traction, optional read-only entity lookup, and optional bundled MongoDB.

## Requirements

| Repository | Name | Version |
|------------|------|---------|
| https://charts.bitnami.com/bitnami | common | 2.x.x |
| oci://registry-1.docker.io/cloudpirates | mongodb | 0.10.3 |

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| backend.containerSecurityContext | object | `{}` | Security context for backend containers |
| backend.testSuite | bool | `false` | When true, sets `TEST_SUITE` and exposes only `/server/status` and `/test-suite/validate` |
| backend.environment.webvhServerUrl | string | `""` | WebVH / DID web server base URL. Hostname is used for issuer `did:web` IDs from `issuers.yaml`. |
| backend.environment.publisherWitnessId | string | `""` | Witness `did:key` (sets `PUBLISHER_WITNESS_ID`; multikey derived at runtime) |
| backend.environment.projectTitle | string | `"UNTP Publisher"` | Brand / OpenAPI title (`PROJECT_TITLE`) |
| backend.environment.projectVersion | string | `"v0"` | Version string (`PROJECT_VERSION`) |
| backend.environment.ocaDigest | bool | `false` | When true, include `digestMultibase` on OCA `renderMethod` (`OCA_DIGEST`) |
| backend.traction.apiUrl | string | `""` | Traction tenant proxy base URL (sets `TRACTION_API_URL`) |
| backend.traction.existingSecret | string | `""` | Pre-created Traction Secret. When empty, Helm manages `{fullname}-traction`. When set, Helm does not create the Secret. |
| backend.traction.secretKeys.apiKey | string | `"traction_api_key"` | Secret key for the Traction API key |
| backend.traction.secretKeys.tenantId | string | `"traction_tenant_id"` | Secret key for the Traction tenant ID |
| backend.host | string | `""` | Backend hostname used for the Ingress rule and `PUBLISHER_DOMAIN` env var |
| backend.image.pullPolicy | string | `"IfNotPresent"` | Backend image pull policy |
| backend.image.pullSecrets | list | `[]` | Backend image pull secrets |
| backend.image.repository | string | `"ghcr.io/bcgov/untp-publisher-service"` | Backend image repository |
| backend.image.tag | string | `"v0.0.2"` | Backend image tag |
| backend.networkPolicy.ingress.podSelector | object | `{}` | Pod selector labels for the backend ingress network policy |
| backend.podAnnotations | object | `{}` | Annotations for backend pods |
| backend.podSecurityContext | object | `{}` | Security context for backend pods |
| backend.replicaCount | int | `1` | Number of backend replicas |
| backend.resources.limits.cpu | string | `"100m"` | CPU limit for backend |
| backend.resources.limits.memory | string | `"512Mi"` | Memory limit for backend |
| backend.resources.requests.cpu | string | `"10m"` | CPU request for backend |
| backend.resources.requests.memory | string | `"128Mi"` | Memory request for backend |
| backend.service.apiPort | int | `8000` | Backend API container port |
| backend.service.servicePort | int | `8000` | Backend service port |
| backend.service.type | string | `"ClusterIP"` | Backend service type |
| fullnameOverride | string | `"untp-publisher"` | String to fully override the chart name |
| ingress.annotations | object | `{}` | Annotations for the ingress resource |
| ingress.enabled | bool | `true` | Enable the Ingress resource |
| ingress.labels | object | `{}` | Additional labels for the ingress resource |
| ingress.tls | list | `[]` | TLS configuration for the ingress |
| mongodb.auth.enabled | bool | `true` | Enable MongoDB authentication |
| mongodb.auth.rootUsername | string | `"admin"` | MongoDB root username |
| mongodb.commonLabels | object | `{ app.kubernetes.io/role: database }` | Labels added to all MongoDB resources |
| mongodb.containerSecurityContext | object | `{}` | Set MongoDB container security context |
| mongodb.customUser.database | string | `"untp-publisher"` | Name of the MongoDB database |
| mongodb.customUser.existingSecret | string | `""` | Name of an existing secret for custom-user credentials. Leave empty to let the subchart generate its own credentials secret. When set, the subchart uses this secret instead of generating one, and the backend deployment reads credentials from this secret. |
| mongodb.customUser.name | string | `"untp-publisher"` | Name of the custom MongoDB user |
| mongodb.customUser.secretKeys | object | `{"database":"CUSTOM_DB","name":"CUSTOM_USER","password":"CUSTOM_PASSWORD"}` | Secret key names in the custom-user secret. Defaults match the CloudPirates subchart's generated secret keys. |
| mongodb.enabled | bool | `true` | Enable bundled MongoDB subchart |
| mongodb.image.pullPolicy | string | `"IfNotPresent"` | MongoDB image pull policy |
| mongodb.image.registry | string | `"docker.io"` | MongoDB image registry |
| mongodb.image.repository | string | `"mongo"` | MongoDB image repository |
| mongodb.image.tag | string | `"8.0"` | MongoDB image tag |
| mongodb.networkPolicy.enabled | bool | `true` | Enable NetworkPolicy for MongoDB pods |
| mongodb.networkPolicy.extraEgress | list | `[]` | Egress rules. When non-empty, policyType: Egress is added and only the listed rules are allowed. Leave empty to keep egress unrestricted (Kubernetes default). |
| mongodb.networkPolicy.extraIngress | list | `[]` | Additional ingress rules appended to the default backend→db and inter-pod rules |
| mongodb.persistence.enabled | bool | `true` | Enable MongoDB data persistence using PVC |
| mongodb.persistence.size | string | `"8Gi"` | PVC Storage size for MongoDB data volume |
| mongodb.persistence.storageClass | string | `""` | PVC Storage Class for MongoDB data volume |
| mongodb.podSecurityContext | object | `{}` | Set MongoDB pod security context |
| mongodb.replicaSet.enabled | bool | `false` | Enable MongoDB replica set mode (standalone if false) |
| mongodb.service.port | int | `27017` | MongoDB service port |
| mongodb.service.type | string | `"ClusterIP"` | MongoDB service type |
| mongodb.targetPlatform | string | `""` | Target platform for MongoDB deployment. Set to "openshift" for OpenShift compatibility. |
| nameOverride | string | `"untp-publisher"` | String to partially override the chart name |
| networkPolicy.ingress.namespaceSelector | object | `{}` | Namespace selector labels for the backend ingress network policy |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
