from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from app.utils import generate_digest_multibase
from app.models.registrations import IssuerRegistration, CredentialRegistration
from app.models.mongodb import (
    IssuerRecord,
    CredentialTypeRecord,
    StatusListRecord,
)
from config import settings
from app.plugins import (
    MongoClient,
    MongoClientError,
    BitstringStatusList,
    PublisherRegistrar,
    OCAProcessor,
)
from app.services.issuer_registration import register_issuer as register_issuer_service
import uuid
import random
import json
import httpx
from app.security import check_api_key_header


router = APIRouter(prefix="/registrations")


@router.get("/issuers", tags=["Admin"], dependencies=[Depends(check_api_key_header)])
async def list_issuer_registrations():
    mongo = MongoClient()
    issuer_records = mongo.find(
        "IssuerRecord",
        {}
    )
    issuer_records = [json.loads(json.dumps(issuer_record, default=str)) for issuer_record in issuer_records]
    return JSONResponse(status_code=200, content=issuer_records)


@router.post("/issuers", tags=["Admin"], dependencies=[Depends(check_api_key_header)])
async def register_issuer(request_body: IssuerRegistration):
    result = await register_issuer_service(request_body.model_dump())
    return JSONResponse(status_code=201, content=result)


@router.post(
    "/credentials", tags=["Admin"], dependencies=[Depends(check_api_key_header)]
)
async def register_credential_type(request_body: CredentialRegistration):
    credential_registration = request_body.model_dump()
    credential_type = credential_registration.get("type")
    credential_version = credential_registration.get("version")

    mongo = MongoClient()

    # Create a new status status list for this type of credential
    indexes = list(range(500000))
    random.shuffle(indexes)

    status_list_id = str(uuid.uuid4())
    status_list_credential = await BitstringStatusList().create(
        issuer=credential_registration["issuer"],
        purpose=["revocation", "suspension", "refresh"],
        length=len(indexes),
    )
    mongo.insert(
        "StatusListRecord",
        StatusListRecord(
            id=status_list_id,
            indexes=indexes,
            endpoint=f"https://{settings.DOMAIN}/credentials/status/{status_list_id}",
            credential=status_list_credential,
        ).model_dump(),
    )

    # Create a new template for this credential type
    credential_template = await PublisherRegistrar().template_credential(
        credential_registration
    )

    # Fetch remote context
    context = httpx.get(credential_registration["relatedResources"]["context"]).json()
    
    # Inject well known context components
    context['@context']['SimpleRefreshQuery'] = 'https://schema.org/WebAPI'
    context['@context']['OCABundle'] = 'https://oca.colossi.network/specification/#bundle'
    settings.LOGGER.info(context)
    
    # TODO, test context
    # credential_template['relatedResources'] = [
    #     {
    #         'id': f'https://{settings.DOMAIN}/contexts/{credential_type}/{credential_version}',
    #         'digestMultibase': generate_digest_multibase(context),
    #     }
    # ]

    # Create JSON Schema
    json_schema = {}
    # credential_template['credentialSchema'] = [
    #     {
    #         'type': 'JsonSchema',
    #         'id': f'https://{settings.DOMAIN}/schemas/{credential_type}/{version}',
    #         'digestMultibase': generate_digest_multibase(json_schema),
    #     }
    # ]

    # Create OCA Bundle
    oca_bundle = OCAProcessor().create_bundle(credential_registration, credential_template)
    credential_template['renderMethod'] = [
        {
            'type': 'OCABundle',
            'id': f'https://{settings.DOMAIN}/bundles/{credential_type}/{credential_version}',
            'name': 'Overlay Capture Architecture Bundle',
            'digestMultibase': generate_digest_multibase(oca_bundle),
        }
    ]

    # Store credential type record
    try:
        mongo.insert(
            "CredentialTypeRecord",
            CredentialTypeRecord(
                type=credential_registration.get("type"),
                version=credential_registration.get("version"),
                issuer=credential_registration.get("issuer"),
                context=context,
                template=credential_template,
                oca_bundle=oca_bundle,
                json_schema=json_schema,
                core_paths=credential_registration.get("corePaths"),
                subject_type=credential_registration.get("subjectType"),
                subject_paths=credential_registration.get("subjectPaths"),
                additional_type=credential_registration.get("additionalType"),
                additional_paths=credential_registration.get("additionalPaths"),
                status_lists=[status_list_id],
            ).model_dump(),
        )
    except MongoClientError:
        raise HTTPException(status_code=409, detail='Duplicate entry')

    return JSONResponse(status_code=201, content=credential_template)
