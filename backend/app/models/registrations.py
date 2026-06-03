from typing import Dict, Any, List
from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema
from config import settings


class BaseModel(BaseModel):
    def model_dump(self, **kwargs) -> Dict[str, Any]:
        return super().model_dump(by_alias=True, exclude_none=True, **kwargs)


class IssuerRegistration(BaseModel):
    name: str = Field(example="Director of Petroleum Lands")
    scope: str = Field(example="Petroleum and Natural Gas Act")
    description: str = Field(
        example="An officer or employee of the ministry who is designated as the Director of Petroleum Lands by the minister."
    )
    multikey: SkipJsonSchema[str] = Field(
        None, example="z6MkkuJkRuYpHkycUYUnBmUzN5cerBjdhDFC3tEBXfSD6Zr8"
    )


class RelatedResources(BaseModel):
    context: str = Field(
        None,
        example="https://bcgov.github.io/digital-trust-toolkit/contexts/BCPetroleumAndNaturalGasTitle/v1.jsonld",
    )
    legalAct: str = Field(
        None,
        example="https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/00_96361_01",
    )
    governance: str = Field(
        None,
        example="https://bcgov.github.io/digital-trust-toolkit/docs/governance/pilots/bc-petroleum-and-natural-gas-title",
    )


class CorePaths(BaseModel):
    entityId: str = Field(example="/credentialSubject/issuedToParty/registeredId")
    cardinalityId: str = Field(example="/credentialSubject/conformityAssessment/0/registeredId")


class PublicationRule(BaseModel):
    min: int | None = Field(None)
    max: int | None = Field(None)


class CredentialRegistration(BaseModel):
    type: str = Field("BCMinesActPermitCredential")
    version: str = Field(example="v1.0")
    issuer: str = Field(example="did:web:")
    templateRef: str | None = Field(
        None,
        example="untp_v0_7_0_dcc_mines_act_permit",
    )
    corePaths: CorePaths | None = Field(None)
    subjectType: str = Field(None, example="ConformityAttestation")
    subjectPaths: Dict[str, str] | None = Field(None)
    additionalType: str | None = Field(None, example="DigitalConformityCredential")
    additionalPaths: Dict[str, str] | None = Field(None)
    relatedResources: RelatedResources = Field(default_factory=RelatedResources)

    @field_validator("additionalType")
    @classmethod
    def validate_untp_type(cls, value):
        if value is None:
            return value
        if value not in ["DigitalConformityCredential"]:
            raise ValueError(f"Unsupported UNTP type {value}.")
        return value
