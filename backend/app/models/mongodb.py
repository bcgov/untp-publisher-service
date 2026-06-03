from typing import Dict, Any
from pydantic import BaseModel, Field


class BaseModel(BaseModel):
    def model_dump(self, **kwargs) -> Dict[str, Any]:
        return super().model_dump(by_alias=True, exclude_none=True, **kwargs)


class IssuerRecord(BaseModel):
    id: str = Field()
    name: str = Field()
    scope: str = Field(
        None,
        description="Statutory scope (BC Laws act name) bound at issuer registration.",
    )
    secret_hash: str = Field(None)
    authorized_key: str = Field()


class CredentialTypeRecord(BaseModel):
    type: str = Field()
    version: str = Field()
    issuer: str = Field()
    context: dict = Field()
    template: dict = Field()
    oca_bundle: dict = Field()
    json_schema: dict = Field()
    core_paths: dict = Field()
    subject_type: str = Field(None)
    subject_paths: dict = Field(None)
    additional_type: str = Field(None)
    additional_paths: dict = Field(None)
    template_ref: str = Field(None)
    publication_rules: dict = Field(None)
    cardinality_field: str = Field(None)
    status_lists: list = Field()


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
    type: str = Field(None)
    version: str = Field(None)
    active: bool = Field(None)
    indexes: list = Field()
    endpoint: str = Field()
    credential: dict = Field()
