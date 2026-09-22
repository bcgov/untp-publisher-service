# Publishing credentials
Instructions for lines of business to obtain Verifiable Credentials from the publisher.

Send ``template``, ``version``, and ``data``. ``data`` is validated against
``configs/credentials/{type}/{version}/data.schema.json``. Entity and cardinality
ids are taken from ``data`` using ``x-publisher-pointers`` in that schema.

## Integration
### Issuer and credential type setup
1. Open an issue on the [digital trust toolkit](https://github.com/bcgov/digital-trust-toolkit)
    Include the name, namespace and description of the issuing entity (and credential types to publish).
2. An admin adds the issuer (and `credentials[]`) to `configs/issuers.yaml` and merges related credential assets under `configs/credentials/` (including `data.schema.json` with `x-publisher-pointers`). Startup provisioning creates local issuer, status list, and credential type records.
3. Once deployed, a secret key will be provided to you (`POST /auth/secret`).

### Credential publication
#### By api
1. Authenticate with either:
    - a client access token from `POST /auth/token` (`Authorization: Bearer …`).
      The token `client_id` must be the issuer id (`IssuerInstanceRecord.id` / DID)
      for the credential type you publish.
    - an admin `X-API-Key` (same key used for `POST /auth/secret` / issuer ops);
      admin may publish any registered type.
2. Send a publication request to `POST /credentials/publish`
    *Publication requests will depend on the provisioned credential type.*
    *Omit `credentialId` (or send a new one) on first issue; on re-issue you may
    reuse the previous id after the prior record is marked refresh, or omit it
    to allocate a new id.*
    ```json
    {
        "template": "BCMinesActPermitCredential",
        "version": "v1.1",
        "credentialId": "",
        "data": {
            "permit": {
                "issuanceDate": "",
                "identifier": ""
            },
            "permittee": {
                "name": "Example Mining Co.",
                "identifier": ""
            },
            "mine": {
                "name": "",
                "identifier": "",
                "infoPageId": ""
            },
            "commodities": []
        }
    }
    ```

#### By File upload
*TBD*

### Credential revocation
1. Authenticate the same way as for `POST /credentials/publish` (client JWT
   matching the credential's registered issuer, or admin `X-API-Key`).
2. Send an [Update Status](https://www.w3.org/TR/vcalm-1.0/#update-status)
   (VC-API) request to `POST /credentials/status`. `credentialStatus` must
   match the entry already stored on the credential (`statusPurpose`,
   `statusListIndex`, `statusListCredential`, and optionally `id`/`type` when
   supplied); a mismatch is rejected with `400`. Only `statusPurpose` values
   of `revocation` or `suspension` are supported; any other purpose is
   rejected with `400`.
   Set `status: true` to revoke (or suspend), `false` to reverse it.
   **Revocation is one-way** — once `statusPurpose: "revocation"` is set to
   `true`, a request with `status: false` for that same entry is rejected
   with `400`. Only `suspension` may be reversed.

   > **Note:** The endpoint accepts `statusPurpose: "suspension"`, but
   > `POST /credentials/publish` does not currently emit a `suspension`
   > entry on issued credentials (only `revocation`; see the `BUG` note in
   > `app/services/coordinator.py`), so no credential can actually be
   > suspended until that is added.
    ```json
    {
        "credentialId": "ab2bac74-4bff-4686-a54f-e850d8408de8",
        "credentialStatus": {
            "statusPurpose": "revocation",
            "statusListIndex": "42",
            "statusListCredential": "https://publisher.example/status-lists/xyz"
        },
        "status": true
    }
    ```
    Responds `200` with `{"credentialId": "...", "status": true}` on success,
    `404` if `credentialId` is unknown, `409` if the status-list bit was
    updated but the credential record could not be found to persist the
    cached flag (e.g. deleted concurrently), `400` if `credentialStatus`
    doesn't match the stored entry, uses an unsupported `statusPurpose`, or
    the request attempts to un-revoke.

### Credential deletion
1. Authenticate the same way as for `POST /credentials/publish` (client JWT
   matching the credential's registered issuer, or admin `X-API-Key`).
2. Send a [Delete a Specific Credential](https://www.w3.org/TR/vcalm-1.0/#delete-a-specific-credential)
   (VC-API) request: `DELETE /credentials/{credentialId}`.
   Removes the stored record; subsequent `GET`/`/refresh`/`/status` calls for
   that id return `404`.
    ```
    DELETE /credentials/ab2bac74-4bff-4686-a54f-e850d8408de8
    ```
    Responds `202` (accepted, no body) on success, `404` if `credentialId`
    is unknown, `403` if the caller isn't authorized for the credential's
    issuer.

## Mines Act DCC (BCMinesActPermitCredential)

Facility (`mine`), products (`commodities`), and optional evidence are supplied in
``data``. The Jinja credential template maps them into UNTP
``assessedFacility`` / ``assessedProduct`` / ``evidence`` — callers do **not**
send those UNTP objects in the publish body.

Optional ``mine.infoPageId`` adds evidence linking to the mine’s NRS authorizations page.

## Examples
### Lines of Business
#### Intergrated Petroleum System

##### Issuer
* Dev:
* Test:
* Prod: N/A

##### Credential Type
```json
{
  "type": "BCPetroleumAndNaturalGasTitleCredential",
  "version": "v1.0",
  "relatedResources": {
    "context": "https://bcgov.github.io/digital-trust-toolkit/contexts/BCPetroleumAndNaturalGasTitle/v1.jsonld",
    "legalAct": "https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/00_96361_01",
    "governance": "https://bcgov.github.io/digital-trust-toolkit/docs/governance/pilots/bc-petroleum-and-natural-gas-title"
  }
}
```
##### Publication Payload
```json
{
    "credential": {
        "type": "BCPetroleumAndNaturalGasTitleCredential",
        "validFrom": "2024-06-01T00:00:00Z",
        "validUntil": "2025-06-01T00:00:00Z",
        "credentialSubject": {
            "type": "PetroleumAndNaturalGasTitle",
            "term": "10",
            "area": "2046",
            "caveats": [],
            "titleType": "NaturalGasLease",
            "titleNumber": "62715",
            "originType": "DrillingLicence",
            "originNumber": "60646"
        }
    },
    "options": {
        "entityId": "",
        "entityName": "Example Mining Co.",
        "cardinalityId": "62715",
        "additionalData": {
            "wells": [
                {
                    "type": [
                        "Facility",
                        "Well"
                    ],
                    "id": "urn:uwi:100010408718W603",
                    "name": "Pacific Canbriam",
                    "description": "ORPHAN PREDATOR  MONTNEY  01-04-087-18",
                    "registeredId": "100010408718W603",
                    "idScheme": {
                        "id": "https://dl.ppdm.org/dl/551",
                        "name": "Unique Well Identifier Format (UWI)"
                    }
                }
            ],
            "tracts": [
                {
                    "type": [
                        "Product",
                        "Tract"
                    ],
                    "id": "urn:hs-code:2711.21.00.00",
                    "name": "Natural Gas",
                    "description": "Petroleum gases and other gaseous hydrocarbons",
                    "registeredId": "2711.21.00.00",
                    "idScheme": {
                        "id": "https://www.wcoomd.org/en/topics/nomenclature/overview/what-is-the-harmonized-system.aspx",
                        "name": "Harmonized System Codes (HS)"
                    }
                }
            ]
        }
    }
}
```
#### Mojor Mines

##### Issuer
* Dev:
* Test:
* Prod: N/A

##### Credential Type
```json
{
  "type": "BCMinesActPermitCredential",
  "version": "v1.1"
}
```
##### Publication Payload
```json
{
    "credential": {
        "type": "BCMinesActPermitCredential",
        "validFrom": "2024-06-01T00:00:00Z",
        "validUntil": "2025-06-01T00:00:00Z",
        "credentialSubject": {}
    },
    "options": {
        "entityId": "",
        "entityName": "Example Mining Co.",
        "cardinalityId": "62715",
        "additionalData": {
            "assessedProduct": [
                {
                    "type": [
                        "Product",
                        "RawMaterial"
                    ],
                    "id": "",
                    "name": "",
                    "description": "",
                    "registeredId": "",
                    "idScheme": {
                        "id": "",
                        "name": ""
                    }
                }
            ],
            "assessedFacility": [
                {
                    "type": [
                        "Facility",
                        "Mine"
                    ],
                    "id": "",
                    "name": "",
                    "description": "",
                    "registeredId": "",
                    "idScheme": {
                        "id": "",
                        "name": ""
                    }
                }
            ]
        }
    }
}
```