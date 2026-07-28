# Administrative role

Publisher admins work from **repo configs** and a few API-key / client-auth endpoints — there is no separate Mongo admin UI.

## What admins do

1. Declare issuers and `credentials[]` in `configs/issuers.yaml`, with assets under `configs/credentials/{type}/{version}/`.
   Treat `template.yaml` as trusted app config (Jinja). Do not accept untrusted
   template uploads — only untrusted input is publish `data` values.
2. Deploy so startup provisioning creates local `IssuerInstanceRecord`, status lists, and `CredentialTemplateRecord` rows.
3. Issue a client secret with `POST /auth/secret` (`X-API-Key`).
4. Confirm configuration via the public ops APIs (also `X-API-Key` where noted):

| URL | Auth | Purpose |
|-----|------|---------|
| `GET /issuers` | `X-API-Key` | Issuer instances from yaml (+ whether provisioned locally) |
| `GET /templates/{type}/{version}` | None | Provisioned VC template |
| `GET /templates/{type}/{version}/oca.json` | None | OCA bundle |
| `GET /status-lists/{id}` | None | Status list credential |
| `POST /auth/secret` | `X-API-Key` | Generate issuer client secret |
| `POST /auth/token` | Client secret | Exchange for publish JWT |
| `POST /credentials/publish` | Client JWT **or** `X-API-Key` | Issue and store a credential |

Mongo inspection, when needed, is done with normal DB tools (Compass, `mongosh`), not the publisher API.

## Issuer and credential type provisioning (configs)

```yaml
instances:
  - id: mines-act:chief-permitting-officer
    name: Chief Permitting Officer
    description: …
    credentials:
      - type: BCMinesActPermitCredential
        version: v1.1
```

On startup the publisher runs MongoDB schema migrations (indexes), then creates/updates:

- local `IssuerInstanceRecord` rows
- three status lists per issuer
- `CredentialTemplateRecord` for each `credentials[]` entry (template/OCA from `configs/credentials/{type}/{version}/`)

Full DID = `did:web:{WEBVH_SERVER_URL hostname}:{id}`.

Issuers then authenticate with a client secret (`POST /auth/secret` / `POST /auth/token`) and publish via the credentials APIs.
