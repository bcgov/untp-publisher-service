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
    - a client access token from `POST /auth/token` (`Authorization: Bearer …`), or
    - an admin `X-API-Key` (same key used for `POST /auth/secret` / issuer ops)
2. Send a publication request to `POST /credentials/publish`
    *Publication requests will depend on the provisioned credential type.*
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