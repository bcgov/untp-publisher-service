# Administrative role

## MongoDB browser (dev / ops)

When the full publisher API is enabled (`TEST_SUITE=false`), an admin UI and JSON API expose read-only access to MongoDB collections:

| URL | Auth | Purpose |
|-----|------|---------|
| `/admin` | None (HTML only) | Collection browser UI — set `X-API-Key` in the page (same as `TRACTION_API_KEY`) |
| `GET /admin/api/collections` | `X-API-Key` | List collections and record counts |
| `GET /admin/api/collections/{name}` | `X-API-Key` | Paginated list (`skip`, `limit`, optional `q` search) |
| `GET /admin/api/collections/{name}/records/{id}` | `X-API-Key` | Full record (sensitive fields redacted/truncated in list view) |
| `POST /admin/api/issuers` | `X-API-Key` | Register issuer (same as `POST /registrations/issuers`; returns issuer + DID document) |

The admin UI includes a **Register issuer** form on the Issuers collection (and on setup step 1).

Collections: `IssuerRecord`, `CredentialTypeRecord`, `CredentialRecord`, `StatusListRecord`, `CredentialPickupRecord`.

The admin UI at `/admin` shows a **recommended setup order** and groups collections by phase (Setup → Configuration → Runtime → Operations).

### Recommended setup order

| Step | Action | API | MongoDB |
|------|--------|-----|---------|
| 1 | Register issuer | `POST /registrations/issuers` | `IssuerRecord` |
| 2 | Issue client secret | `POST /auth/secret` | updates `IssuerRecord.secret_hash` |
| 3 | Register credential type | `POST /registrations/credentials` | `CredentialTypeRecord` + `StatusListRecord` (auto) |
| 4 | Issue credentials | Issuer APIs (JWT from `/auth/token`) | `CredentialRecord` |
| 5 | Pickup (optional) | Pickup flow if enabled | `CredentialPickupRecord` |

Status lists are **not** registered separately — they are created in step 3 with the credential type.

---

A publisher software admin has 3 key functions:
- Register issuers
- Register credential types
- Generate/Provide secrets to issuers


## Issuer Registrations
An issuer registration will begin with a request, in the form of a gh issue on the [digital trust toolkit](https://github.com/bcgov/digital-trust-toolkit/issues).

Once that issue has been approved by the respective governance team, the admin is responsible for conducting the issuer registration through an api call. He will need to gather the name, scope and description of the issuer from the issue and send the following request:
```
POST
https://publisher.example.com/registrations/issuer
{
    "name": $ISSUER_NAME,
    "scope": $ISSUER_SCOPE,
    "description": $ISSUER_DESCRIPTION
}
```

The request must contain an `X-API-KEY` header corresponding to the Publisher's Traction Tenant `api_key` value.

A successful response will return a 201 with a did document. The admin can confirm the did web's availablility be resolving it through the traction did resolver endpoint. Alternatively, the uniresolver may be used.

At this point, a [PR should be opened](https://github.com/bcgov/digital-trust-toolkit/pulls) to address the issue and add this issuer to the [corresponding registry](https://github.com/bcgov/digital-trust-toolkit/tree/main/related_resources/registrations/issuers).

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