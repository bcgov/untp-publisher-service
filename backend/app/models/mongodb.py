from typing import Dict, Any
from pydantic import BaseModel, Field


class BaseModel(BaseModel):
    def model_dump(self, **kwargs) -> Dict[str, Any]:
        return super().model_dump(by_alias=True, exclude_none=True, **kwargs)


class IssuerInstanceRecord(BaseModel):
    id: str = Field()
    name: str = Field()
    namespace: str = Field(
        None,
        description="Issuer namespace (from configs or registration).",
    )
    secret_hash: str = Field(None)
    authorized_key: str | None = Field(
        None,
        description="Issuing multikey when known (from configs verificationMethod or registration).",
    )


class CredentialTemplateRecord(BaseModel):
    type: str = Field()
    version: str = Field()
    issuer: str = Field()
    template: dict = Field()
    oca_bundle: dict = Field()


class CredentialRecord(BaseModel):
    id: str = Field()
    type: str = Field()
    entity_id: str = Field()
    cardinality_id: str = Field()
    cardinality_hash: str = Field()
    refresh: bool = Field()
    revocation: bool = Field()
    suspension: bool = Field()
    vc: dict = Field()
    vc_jwt: str = Field()


class StatusListRecord(BaseModel):
    id: str = Field()
    issuer: str = Field(None, description="Issuer DID that owns this status list.")
    purpose: str = Field(
        None,
        description="Bitstring status purpose: revocation, suspension, or refresh.",
    )
    type: str = Field(None)
    version: str = Field(None)
    active: bool = Field(None)
    indexes: list = Field()
    endpoint: str = Field()
    credential: dict = Field()
