"""Request models for ``/test-suite/*`` routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MINES_ACT_BUILD_EXAMPLE: dict[str, Any] = {
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


class TestSuiteBuildRequest(BaseModel):
    """Publication payload accepted by ``POST /test-suite/build-credential``."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [MINES_ACT_BUILD_EXAMPLE]},
    )

    template: str = Field(description="Credential type / template id.")
    version: str = Field(description="Credential template version.")
    credentialId: str | None = Field(
        default=None,
        description="Optional credential id.",
    )
    validFrom: str | None = Field(
        default=None,
        description="Optional VC envelope validFrom (usually omitted; server sets publish time).",
    )
    validUntil: str | None = Field(default=None)
    data: dict[str, Any] = Field(description="Template input data.")
