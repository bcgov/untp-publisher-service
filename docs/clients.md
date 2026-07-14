# Publishing credentials
Instructions for lines of business to obtain Verifiable Credentials from the publisher. Include the holder’s `entityId` and `entityName` on each publication request.

## Integration
### Issuer and credential type setup
1. Open an issue on the [digital trust toolkit](https://github.com/bcgov/digital-trust-toolkit)
    Include the name, namespace and description of the issuing entity (and credential types to publish).
2. An admin adds the issuer (and `credentials[]`) to `configs/issuers.yaml` and merges related credential assets under `configs/credentials/`. Startup provisioning creates local issuer, status list, and credential type records.
3. Once deployed, a secret key will be provided to you (`POST /auth/secret`).

### Credential publication
#### By api
1. Request an access token from the UNTP publisher
    ```json
    {
        "client_id": "",
        "client_secret": ""
    }
    ```
2. Send a publication request
    *Publication requests will depend on the provisioned credential type.*
    ```json
    {
        "credential": {
            "type": "",
            "validFrom": "",
            "validUntil": "",
            "credentialSubject": {}
        },
        "options": {
            "entityId": "",
            "entityName": "Example Mining Co.",
            "credentialId": "",
            "cardinalityId": ""
        }
    }
    ```

#### By File upload
*TBD*

## Extensions
### Untp
For UNTP Digital Conformity Credentials, `assessedFacility` and `assessedProduct` must be provided in addition to the standard request information.
#### Assessed Facility
A list of assessed facilities
```json
{
    "type": ["Facility"],
    "id": "",
    "name": "",
    "description": "",
    "registeredId": "",
    "idScheme": {
      "id": "",
      "name": ""
    }
}
```
#### Assessed Product
A list of assessed products
```json
{
    "type": ["Product"],
    "id": "",
    "name": "",
    "description": "",
    "registeredId": "",
    "idScheme": {
      "id": "",
      "name": ""
    }
}
```

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
  "additionalType": "DigitalConformityCredential",
  "corePaths": {
    "entityId": "$.credentialSubject.issuedToParty.registeredId",
    "cardinalityId": "$.credentialSubject.titleNumber"
  },
  "subjectPaths": {
    "term": "$.credentialSubject.term",
    "area": "$.credentialSubject.area",
    "caveats": "$.credentialSubject.caveats",
    "titleType": "$.credentialSubject.titleType",
    "titleNumber": "$.credentialSubject.titleNumber",
    "originType": "$.credentialSubject.originType",
    "originNumber": "$.credentialSubject.originNumber"
  },
  "additionalPaths": {
    "wells": "$.credentialSubject.assessment[0].assessedFacility",
    "tracts": "$.credentialSubject.assessment[0].assessedProduct"
  },
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
  "version": "v1.0",
  "additionalType": "DigitalConformityCredential",
  "corePaths": {
    "entityId": "$.credentialSubject.issuedToParty.registeredId",
    "cardinalityId": "$.credentialSubject.permitNumber"
  },
  "subjectPaths": {
    "permitNumber": "$.credentialSubject.titleNumber"
  },
  "additionalPaths": {
    "assessedProduct": "$.credentialSubject.assessment[0].assessedProduct",
    "assessedFacility": "$.credentialSubject.assessment[0].assessedFacility"
  },
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
        "type": "BCMinesActPermitCredential",
        "validFrom": "2024-06-01T00:00:00Z",
        "validUntil": "2025-06-01T00:00:00Z",
        "credentialSubject": {
            "type": "MinesActPermit",
            "permitNumber": "62715"
        }
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