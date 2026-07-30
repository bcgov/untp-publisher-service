from typing import Union, List, Dict, Any
from pydantic import Field, BaseModel, field_validator
from pydantic.json_schema import SkipJsonSchema
from config import settings
from app.utils import valid_datetime_string, valid_uri


class NameField(BaseModel, extra="forbid"):
    value: str = Field(None, alias="@value")
    language: str = Field(None, alias="@language")
    direction: str = Field(None, alias="@direction")


class DescriptionField(BaseModel, extra="forbid"):
    value: str = Field(None, alias="@value")
    language: str = Field(None, alias="@language")
    direction: str = Field(None, alias="@direction")


class BaseModel(BaseModel, extra="allow"):
    id: SkipJsonSchema[str] = Field(None)
    type: Union[str, List[str]] = Field(None)
    name: SkipJsonSchema[Union[str, NameField, List[NameField]]] = Field(None)
    description: SkipJsonSchema[
        Union[str, DescriptionField, List[DescriptionField]]
    ] = Field(None)
    
    class Config:
        populate_by_name = True

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        return super().model_dump(by_alias=True, exclude_none=True, **kwargs)


class Issuer(BaseModel):
    pass


class RelatedResource(BaseModel):
    id: SkipJsonSchema[str] = Field()
    digestSRI: str = Field(None)
    digestMultibase: str = Field(None)


class CredentialSubject(BaseModel):
    pass


class CredentialSchema(BaseModel):
    id: Union[str, List[str]] = Field()
    type: Union[str, List[str]] = Field()

    @field_validator("id")
    @classmethod
    def validate_credential_schema_id(cls, value):
        assert valid_uri(value)


class CredentialStatus(BaseModel):
    id: str = Field(None)
    type: Union[str, List[str]] = Field()
    statusPurpose: str = Field(None)
    statusListIndex: str = Field(None)
    statusListCredential: str = Field(None)
    statusReference: SkipJsonSchema[Union[str, List[str]]] = Field(None)

    @field_validator("id")
    @classmethod
    def validate_credential_status_id(cls, value):
        if value:
            assert valid_uri(value)

        return value


class TermsOfUse(BaseModel):
    type: Union[str, List[str]] = Field()


class Evidence(BaseModel):
    type: Union[str, List[str]] = Field()


class RenderMethod(BaseModel):
    pass


class Credential(BaseModel):
    context: List[str] = Field(
        ["https://www.w3.org/ns/credentials/v2"], alias="@context"
    )
    type: Union[str, List[str]] = Field(["VerifiableCredential"])
    validFrom: SkipJsonSchema[str] = Field(None)
    validUntil: SkipJsonSchema[str | None] = Field(None)
    credentialSubject: Union[List[CredentialSubject], CredentialSubject] = Field(None)
    credentialStatus: SkipJsonSchema[
        Union[List[CredentialStatus], CredentialStatus]
    ] = Field(None)
    credentialSchema: SkipJsonSchema[
        Union[List[CredentialSchema], CredentialSchema]
    ] = Field(None)
    termsOfUse: SkipJsonSchema[Union[List[TermsOfUse], TermsOfUse]] = Field(None)
    evidence: SkipJsonSchema[Union[List[Evidence], Evidence]] = Field(None)
    renderMethod: SkipJsonSchema[Union[List[RenderMethod], RenderMethod]] = Field(None)
    relatedResource: SkipJsonSchema[Union[List[RelatedResource], RelatedResource]] = (
        Field(None)
    )

    @field_validator("context")
    @classmethod
    def validate_context(cls, value):
        assert value[0] == "https://www.w3.org/ns/credentials/v2"
        return value

    @field_validator("id")
    @classmethod
    def validate_credential_id(cls, value):
        assert valid_uri(value)
        return value

    @field_validator("type")
    @classmethod
    def validate_credential_type(cls, value):
        asserted_value = value if isinstance(value, list) else [value]
        assert "VerifiableCredential" in asserted_value
        return value

    @field_validator("validFrom")
    @classmethod
    def validate_valid_from_date(cls, value):
        if value:
            assert valid_datetime_string(value)
        return value

    @field_validator("validUntil")
    @classmethod
    def validate_valid_until_date(cls, value):
        if value:
            assert valid_datetime_string(value)
        return value

    @field_validator("relatedResource")
    @classmethod
    def validate_related_ressource(cls, value):
        asserted_value = value if isinstance(value, list) else [value]
        for ressource in asserted_value:
            assert valid_uri(ressource.id)
            assert ressource.digestSRI or ressource.digestMultibase
        return value
