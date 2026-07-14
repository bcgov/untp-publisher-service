from typing import Any
from pydantic import BaseModel, Field
import uuid


class BaseModel(BaseModel):
    def model_dump(self, **kwargs) -> dict[str, Any]:
        return super().model_dump(by_alias=True, exclude_none=True, **kwargs)


class Publication(BaseModel):
    """``POST /credentials/publish`` body (template + version + data)."""

    template: str = Field(
        example="BCMinesActPermitCredential",
        description="Credential type / template id (matches issuers.yaml).",
    )
    version: str = Field(
        example="v1.1",
        description="Credential template version (matches issuers.yaml).",
    )
    credentialId: str | None = Field(
        default=None,
        example=str(uuid.uuid4()),
        description="Optional id; generated when omitted.",
    )
    validFrom: str | None = Field(
        default=None,
        example=None,
        description="Optional VC envelope validFrom (usually omitted; server sets publish time).",
    )
    validUntil: str | None = Field(
        default=None,
        example="2027-01-01T00:00:00Z",
        description="Optional VC expiry (envelope).",
    )
    data: dict[str, Any] = Field(
        example={
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
        description="Template input data (entity/cardinality resolved via issuers.yaml pointers).",
    )
