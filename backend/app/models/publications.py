from typing import Any
from pydantic import BaseModel, ConfigDict, Field
import uuid

MINES_ACT_PUBLISH_EXAMPLE: dict[str, Any] = {
    "template": "BCMinesActPermitCredential",
    "version": "v1.1",
    "credentialId": "ab2bac74-4bff-4686-a54f-e850d8408de8",
    "data": {
        "permit": {
            "issuanceDate": "1999-04-19",
            "identifier": "Q-20",
        },
        "permittee": {
            "name": "EXAMPLE MINING CO",
            "identifier": "A0034771",
        },
        "mine": {
            "name": "Kootenay West",
            "identifier": "0500956",
            "infoPageId": "5fa1e3ec4635c865df00c420",
            "locationInformation": "https://plus.codes/9526679P+4V",
            "IDverifiedByCAB": True,
        },
        "commodities": [
            {
                "name": "Construction Aggregate",
                "IDverifiedByCAB": False,
            }
        ],
    },
}


class BaseModel(BaseModel):
    def model_dump(self, **kwargs) -> dict[str, Any]:
        kwargs.setdefault("by_alias", True)
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)


class PublicationRequest(BaseModel):
    """``POST /credentials/publish`` (and test-suite build) request body."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [MINES_ACT_PUBLISH_EXAMPLE]},
    )

    template: str = Field(
        examples=["BCMinesActPermitCredential"],
        description="Credential type / template id (matches issuers.yaml).",
    )
    version: str = Field(
        examples=["v1.1"],
        description="Credential template version (matches issuers.yaml).",
    )
    credentialId: str | None = Field(
        default=None,
        examples=[str(uuid.uuid4())],
        description="Optional id; generated when omitted.",
    )
    validFrom: str | None = Field(
        default=None,
        examples=[None],
        description="Optional VC envelope validFrom (usually omitted; server sets publish time).",
    )
    validUntil: str | None = Field(
        default=None,
        examples=["2027-01-01T00:00:00Z"],
        description="Optional VC expiry (envelope).",
    )
    data: dict[str, Any] = Field(
        examples=[MINES_ACT_PUBLISH_EXAMPLE["data"]],
        description=(
            "Template input data; validated against "
            "configs/credentials/{type}/{version}/data.schema.json. "
            "Entity/cardinality resolved via x-publisher-pointers in that schema."
        ),
    )


CREDENTIAL_STATUS_UPDATE_EXAMPLE: dict[str, Any] = {
    "credentialId": "ab2bac74-4bff-4686-a54f-e850d8408de8",
    "credentialStatus": {
        "statusPurpose": "revocation",
        "statusListIndex": "42",
        "statusListCredential": "https://publisher.example/status-lists/xyz",
    },
    "status": True,
}


class CredentialStatusUpdateEntry(BaseModel):
    """A single ``credentialStatus`` entry identifier (VC-API ``UpdateStatus``)."""

    id: str | None = Field(
        default=None,
        examples=[None],
        description="Optional; when present, must match the id of the credential's existing status entry.",
    )
    type: str | None = Field(
        default=None,
        examples=["BitstringStatusListEntry"],
        description="Optional; when present, must match the type of the credential's existing status entry.",
    )
    statusPurpose: str = Field(
        examples=["revocation"],
        description="Must match the purpose of the credential's existing status entry.",
    )
    statusListIndex: str = Field(
        examples=["42"],
        description="Must match the index of the credential's existing status entry.",
    )
    statusListCredential: str = Field(
        examples=["https://publisher.example/status-lists/xyz"],
        description="Must match the URL of the credential's existing status entry.",
    )


class CredentialStatusUpdateRequest(BaseModel):
    """``POST /credentials/status`` (VC-API / VCALM ``Update Status``) request body."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [CREDENTIAL_STATUS_UPDATE_EXAMPLE]},
    )

    credentialId: str = Field(
        examples=["ab2bac74-4bff-4686-a54f-e850d8408de8"],
        description="Id of the previously published credential to update.",
    )
    credentialStatus: CredentialStatusUpdateEntry = Field(
        description="Identifies the status entry to update; must match the credential's stored entry."
    )
    status: bool = Field(
        examples=[True],
        description="Desired bit value: true sets the status (e.g. revokes), false clears it.",
    )
