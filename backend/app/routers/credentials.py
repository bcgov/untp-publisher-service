from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from app.models.publications import PublicationRequest
from app.models.mongodb import CredentialRecord
from app.plugins.mongodb import MongoClient, MongoClientError
from config import settings
from app.plugins import TractionController
from app.services.coordinator import PublisherCoordinator
from app.services.composer import normalize_publication, credential_download_filename
from app.security import AuthPrincipal, jwt_or_api_key
import uuid

router = APIRouter(prefix="/credentials", tags=["Credentials"])


def _authorize_publish(
    auth: AuthPrincipal,
    *,
    issuer_id: str,
) -> None:
    """Admin API key may publish any type; JWT must match the type's issuer."""
    if auth.via == "api_key":
        return
    if auth.via == "jwt" and auth.client_id == issuer_id:
        return
    raise HTTPException(
        status_code=403,
        detail="client_id is not authorized for this credential type",
    )


def _allocate_credential_id(
    mongo: MongoClient,
    *,
    requested_id: str | None,
    credential_type: str,
    entity_id: str,
    cardinality_id: str,
) -> str:
    """Pick a credential id for a new issue; reclaim superseded ids when safe."""
    credential_id = (requested_id or "").strip() or str(uuid.uuid4())
    existing = mongo.find_one("CredentialRecord", {"id": credential_id})
    if not existing:
        return credential_id

    # Allow reuse when the prior row was just marked refresh for this identity.
    if (
        existing.get("refresh")
        and existing.get("type") == credential_type
        and existing.get("entity_id") == entity_id
        and existing.get("cardinality_id") == cardinality_id
    ):
        mongo.delete("CredentialRecord", {"id": credential_id})
        return credential_id

    raise HTTPException(
        status_code=409,
        detail="credentialId already exists",
    )


@router.post("/publish")
async def publish_credential(
    request_body: PublicationRequest,
    auth: Annotated[AuthPrincipal, Depends(jwt_or_api_key)],
):
    settings.LOGGER.info("Publication request")
    raw = request_body.model_dump()
    options = normalize_publication(raw)
    requested_credential_id = options.get("credentialId")

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

    issuer_id = (credential_registration.get("issuer") or "").strip()
    if not issuer_id:
        raise HTTPException(
            status_code=500,
            detail=f"Credential type {credential_type!r} has no issuer",
        )
    _authorize_publish(auth, issuer_id=issuer_id)

    entity_id = options.get("entityId")
    cardinality_id = options.get("cardinalityId")

    # TODO: retire/revoke (or suspend) active credentials when a permit disappears
    # from the daily source feed — replayed publishes only cover present rows.
    cardinality_hash = await PublisherCoordinator().check_cardinality(options=options)

    if cardinality_hash:
        options["credentialId"] = _allocate_credential_id(
            mongo,
            requested_id=requested_credential_id,
            credential_type=credential_type,
            entity_id=entity_id,
            cardinality_id=cardinality_id,
        )
        settings.LOGGER.info("Credential Id: " + options["credentialId"])

        credential = await PublisherCoordinator().format_credential(options=options)

        traction = TractionController()
        traction.authorize()
        vc = traction.issue_vc(credential)
        if not vc:
            raise HTTPException(
                status_code=500,
                detail="Unexpected error occurred while trying to issue the credential.",
            )
        vc_jwt = traction.sign_vc_jwt(vc)
        if not vc_jwt:
            raise HTTPException(
                status_code=500,
                detail="Unexpected error occurred while trying to issue the credential.",
            )

        try:
            mongo.insert(
                "CredentialRecord",
                CredentialRecord(
                    id=options.get("credentialId"),
                    type=credential_type,
                    entity_id=entity_id,
                    cardinality_id=cardinality_id,
                    cardinality_hash=cardinality_hash,
                    refresh=False,
                    revocation=False,
                    suspension=False,
                    vc=vc,
                    vc_jwt=vc_jwt,
                ).model_dump(),
            )
        except MongoClientError as exc:
            raise HTTPException(
                status_code=409,
                detail="credentialId already exists",
            ) from exc
        return JSONResponse(status_code=201, content={"credentialId": vc["id"]})

    credential_record = mongo.find_one(
        "CredentialRecord",
        {
            "type": credential_type,
            "entity_id": entity_id,
            "cardinality_id": cardinality_id,
            "refresh": False,
        },
    )
    if not credential_record:
        raise HTTPException(
            status_code=409,
            detail="Credential changed concurrently; retry publish",
        )
    vc = credential_record["vc"]
    return JSONResponse(status_code=200, content={"credentialId": vc["id"]})


def _enveloped_credential_response(
    credential_record: dict, *, download: bool = False
) -> JSONResponse:
    vc_jwt = credential_record.get("vc_jwt")
    if not vc_jwt:
        raise HTTPException(status_code=500, detail="Credential record missing vc_jwt")
    enveloped = TractionController.as_enveloped_vc(vc_jwt)
    headers = {"Content-Type": "application/vc"}
    if download:
        filename = credential_download_filename(credential_record)
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return JSONResponse(headers=headers, content=enveloped)


@router.get("/refresh")
async def refresh_credential(
    type: str,
    entity: str,
    cardinality: str,
    download: bool = False,
):
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
    return _enveloped_credential_response(credential_record, download=download)


@router.get("/{credential_id}")
async def get_credential(credential_id: str, download: bool = False):
    """Return a published credential as an EnvelopedVerifiableCredential.

    Response ``Content-Type`` is ``application/vc`` (VCDM 2.0 envelope wrapping
    ``data:application/vc+jwt,…``). Pass ``download=true`` to set
    ``Content-Disposition: attachment`` with a typed filename.
    """
    mongo = MongoClient()
    credential_record = mongo.find_one("CredentialRecord", {"id": credential_id})
    if not credential_record:
        raise HTTPException(
            status_code=404,
            detail="No record found.",
        )
    return _enveloped_credential_response(credential_record, download=download)
