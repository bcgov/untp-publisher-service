"""Request models for ``/test-suite/*`` routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MINES_ACT_BUILD_EXAMPLE: dict[str, Any] = {
    "credential": {
        "type": "BCMinesActPermitCredential",
        "validFrom": "1999-04-19T00:00:00+00:00",
        "credentialSubject": {"permitNumber": "Q-20"},
    },
    "options": {
        "entityId": "A0034771",
        "cardinalityId": "Q-20",
        "additionalData": {
            "assessedFacility": [
                {
                    "type": "Facility",
                    "name": "Kootenay West",
                    "registeredId": "0500956",
                    "locationInformation": "https://plus.codes/9526679P+4V",
                    "IDverifiedByCAB": True,
                }
            ],
            "assessedProduct": [
                {
                    "type": "Product",
                    "name": "Construction Aggregate",
                    "IDverifiedByCAB": False,
                }
            ],
        },
        "credentialId": "ab2bac74-4bff-4686-a54f-e850d8408de8",
    },
    "organization": {
        "id": "https://dev.orgbook.gov.bc.ca/entity/A0034771/type/registration.registries.ca",
        "name": "EXAMPLE MINING CO",
    },
}


class TestSuiteOrganization(BaseModel):
    id: str | None = Field(
        None,
        description="OrgBook entity URI; auto-generated from entityId when omitted.",
    )
    name: str | None = Field(None, description="Legal name of the permit holder.")


class TestSuiteBuildRequest(BaseModel):
    """Publication payload accepted by ``POST /test-suite/build-credential``."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [MINES_ACT_BUILD_EXAMPLE]},
    )

    credential: dict[str, Any] = Field(
        description="Publication credential input (`type`, `validFrom`, `credentialSubject`, …).",
    )
    options: dict[str, Any] = Field(
        description="Publication options (`entityId`, `cardinalityId`, `additionalData`, …).",
    )
    organization: TestSuiteOrganization | None = Field(
        None,
        description="Optional OrgBook override; skips live OrgBook lookup when provided.",
    )
