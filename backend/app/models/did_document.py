from typing import Union, List, Dict, Any
from pydantic import BaseModel, Field, AliasChoices, field_validator

BASE_CONTEXT = [
    "https://www.w3.org/ns/did/v1",
    "https://w3id.org/security/multikey/v1",
    "https://w3id.org/security/jwk/v1",
]


class BaseModel(BaseModel):
    def model_dump(self, **kwargs) -> Dict[str, Any]:
        return super().model_dump(by_alias=True, exclude_none=True, **kwargs)


class VerificationMethod(BaseModel):
    id: str = Field()
    type: str = Field("Multikey")
    controller: str = Field()
    publicKeyMultibase: str = Field(None)
    publicKeyJwk: dict = Field(None)


class Service(BaseModel):
    id: str = Field()
    type: str = Field()
    serviceEndpoint: str = Field()


class DidDocument(BaseModel):
    context: List[str] = Field(BASE_CONTEXT, alias="@context")
    id: str = Field()
    controller: str = Field(None)
    authentication: List[Union[str, VerificationMethod]] = Field(None)
    assertionMethod: List[Union[str, VerificationMethod]] = Field(None)
    verificationMethod: List[VerificationMethod] = Field(None)
    service: List[Service] = Field(None)
