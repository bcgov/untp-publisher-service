from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from app.models.publications import (
    Publication,
)
from app.models.mongodb import CredentialRecord
from app.plugins.mongodb import MongoClient
from config import settings
from app.plugins import (
    TractionController,
    PublisherRegistrar,
)
from app.security import jwt_or_api_key
from app.services.publication_request import normalize_publication
import uuid

router = APIRouter(prefix="/credentials", tags=["Credentials"])


@router.post("/publish", dependencies=[Depends(jwt_or_api_key)])
async def publish_credential(request_body: Publication):
    settings.LOGGER.info("Publication request")
    raw = request_body.model_dump()
    options = normalize_publication(raw)

    if not options.get("credentialId"):
        options["credentialId"] = str(uuid.uuid4())
        settings.LOGGER.info("No credential id provided, new id generated.")

    settings.LOGGER.info("Credential Id: " + options["credentialId"])

    mongo = MongoClient()

    credential_type = options["template"]
    credential_registration = mongo.find_one(
        "CredentialTemplateRecord",
        {"type": credential_type},
    )
    if not credential_registration:
        raise HTTPException(
            status_code=404,
            detail="Unregistered credential type",
        )

    entity_id = options.get("entityId")

    cardinality_hash = await PublisherRegistrar().check_cardinality(options=options)

    if cardinality_hash:
        credential = await PublisherRegistrar().format_credential(options=options)

        traction = TractionController()
        traction.authorize()
        vc = traction.issue_vc(credential)
        if not vc:
            raise HTTPException(
                status_code=500,
                detail="Unexpected error occured while trying to issue the credential.",
            )
        vc_jwt = traction.sign_vc_jwt(vc)
        if not vc_jwt:
            raise HTTPException(
                status_code=500,
                detail="Unexpected error occured while trying to issue the credential.",
            )

        mongo.insert(
            "CredentialRecord",
            CredentialRecord(
                id=options.get("credentialId"),
                type=credential_type,
                entity_id=entity_id,
                cardinality_id=options.get("cardinalityId"),
                cardinality_hash=cardinality_hash,
                refresh=False,
                revocation=False,
                suspension=False,
                vc=vc,
                vc_jwt=vc_jwt,
            ).model_dump(),
        )
        return JSONResponse(status_code=201, content={"credentialId": vc["id"]})

    credential_records = mongo.find_one(
        "CredentialRecord",
        {
            "type": credential_type,
            "entity_id": options.get("entityId"),
            "cardinality_id": options.get("cardinalityId"),
            "refresh": False,
        },
    )
    vc = credential_records["vc"]
    return JSONResponse(status_code=200, content={"credentialId": vc["id"]})


def _enveloped_credential_response(credential_record: dict) -> JSONResponse:
    vc_jwt = credential_record.get("vc_jwt")
    if not vc_jwt:
        raise HTTPException(status_code=500, detail="Credential record missing vc_jwt")
    enveloped = TractionController.as_enveloped_vc(vc_jwt)
    return JSONResponse(headers={"Content-Type": "application/vc"}, content=enveloped)


@router.get("/refresh")
async def refresh_credential(type: str, entity: str, cardinality: str):
    """Return the active credential as an EnvelopedVerifiableCredential."""
    mongo = MongoClient()
    credential_record = mongo.find_one(
        "CredentialRecord",
        {
            "type": type,
            "entity_id": entity,
            "cardinality_id": cardinality,
            "refresh": False,
        },
    )
    if not credential_record:
        raise HTTPException(status_code=404, detail="No record found.")
    return _enveloped_credential_response(credential_record)


@router.get("/{credential_id}")
async def get_credential(credential_id: str):
    """Return a published credential as an EnvelopedVerifiableCredential.

    Response ``Content-Type`` is ``application/vc`` (VCDM 2.0 envelope wrapping
    ``data:application/vc+jwt,…``).
    """
    mongo = MongoClient()
    credential_record = mongo.find_one("CredentialRecord", {"id": credential_id})
    if not credential_record:
        raise HTTPException(
            status_code=404,
            detail="No record found.",
        )
    return _enveloped_credential_response(credential_record)
