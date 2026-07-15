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
        description="Template input data (entity/cardinality resolved via issuers.yaml pointers).",
    )
