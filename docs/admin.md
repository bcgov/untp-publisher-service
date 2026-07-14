# Administrative role

## MongoDB browser (ops)

When the full publisher API is enabled (`TEST_SUITE=false`), a read-only admin UI and JSON API expose MongoDB collections:

| URL | Auth | Purpose |
|-----|------|---------|
| `/admin` | None (HTML only) | Collection browser — set `X-API-Key` in the page (same as `TRACTION_API_KEY`) |
| `GET /admin/api/collections` | `X-API-Key` | List collections and record counts |
| `GET /admin/api/collections/{name}` | `X-API-Key` | Paginated list (`skip`, `limit`, optional `q` search) |
| `GET /admin/api/collections/{name}/records/{id}` | `X-API-Key` | Full record (sensitive fields redacted/truncated in list view) |

**Issuers** and **status lists** are provisioned at startup from `configs/issuers.yaml`. Use `/admin` to verify those records after boot.

Collections: `IssuerRecord`, `CredentialTypeRecord`, `CredentialRecord`, `StatusListRecord`, `CredentialPickupRecord`.

---

A publisher software admin has 3 key functions:
- Ensure issuers are declared in `configs/issuers.yaml` (and DIDs exist where required)
- Register credential types (API)
- Generate/provide secrets to issuers

## Issuer provisioning (configs)

Issuers are declared in repo config:

```yaml
instances:
  - id: mines-act:chief-permitting-officer
    name: Chief Permitting Officer
    description: …
    credentials:
      - type: BCMinesActPermitCredential
        version: v1.1
```

On startup the publisher creates/updates local `IssuerRecord` rows and three status lists per issuer. Full DID = `did:web:{PUBLISHER_DOMAIN}:{id}`.

Optional interactive registration via `POST /registrations/issuers` remains for non-config flows; it is not driven from the admin UI.

## Credential Type Registration
The credential type registration is the most complex and critical component: it defines how issuers issue Verifiable Credentials through this publisher. OrgBook is used only for **entity lookup** during publication; credential type definitions and issued VCs are **not** pushed to OrgBook for indexing.

This will also begin with a gh issue, describing the credential to be issued, the data points contained in the credential and other associated metadata. 
```
POST
https://publisher.example.com/registrations/credentials
{
    "type": "BCExampleDocumentCredential",
    "version": "1.0",
    "issuer": "did:web:example.gov.bc.ca",
    "mappings": {
        "entityId": "documentOwner",
        "cardinalityId": "documentNumber"
    },
    "subjectType": "ExampleDocument",
    "subjectPaths": {
        "documentOwner": "$.credentialSubject.documentOwner",
        "documentNumber": "$.credentialSubject.documentNumber",
    },
    "relatedResources": {
        "context": "https://bcgov.github.io/digital-trust-toolkit/contexts/ExampleDocument/v1.jsonld"
    }
}
```
