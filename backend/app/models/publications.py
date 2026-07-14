from typing import Dict, Any
from pydantic import BaseModel, Field
import uuid

class BaseModel(BaseModel):
    def model_dump(self, **kwargs) -> Dict[str, Any]:
        return super().model_dump(by_alias=True, exclude_none=True, **kwargs)



class PublicationCredential(BaseModel):
    type: str = Field(example="BCPetroleumAndNaturalGasTitleCredential")
    validFrom: str = Field(None, example="2024-11-11T00:00:00Z")
    validUntil: str = Field(None, example="2025-11-11T00:00:00Z")
    credentialSubject: dict = Field(
        example={
            "titleType": "NaturalGasLease",
            "titleNumber": "65338",
            "originType": "DrillingLicense",
            "originNumber": "42566",
        }
    )


class PublicationOptions(BaseModel):
    entityId: str = Field(example="A0131571")
    entityName: str = Field(example="EXAMPLE MINING CO")
    credentialId: str = Field(None, example=str(uuid.uuid4()))
    cardinalityId: str = Field(example="65338")
    additionalData: dict = Field(
        None,
        example={
            "wells": [
                {"type": ["Facility", "Well"], "id": "urn:code:uwi:", "name": ""}
            ],
            "tracts": [
                {
                    "type": ["Product", "Tract"],
                    "id": "urn:code:hs:",
                    "name": "",
                    "zones": [],
                    "notes": [],
                    "rights": [],
                }
            ],
        },
    )


class Publication(BaseModel):
    credential: PublicationCredential = Field()
    options: PublicationOptions = Field()