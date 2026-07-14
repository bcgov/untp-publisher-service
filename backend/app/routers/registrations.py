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
from app.presets.loader import (
    build_template_from_preset,
    get_preset,
    load_oca_bundle as load_preset_oca_bundle,
)
from app.repo_configs.loader import (
    load_oca_bundle as load_config_oca_bundle,
    load_publication_config_optional,
)
from app.services.dcc_builder import publisher_origin
import uuid
import random
import json
import re
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

    issuer = mongo.find_one("IssuerRecord", {"id": credential_registration["issuer"]})
    if not issuer:
        raise HTTPException(status_code=404, detail="Issuer not registered.")

    template_ref = credential_registration.get("templateRef")
    preset = get_preset(template_ref) if template_ref else None
    if preset:
        credential_registration["type"] = credential_type or preset["domain_type"]
        credential_registration["corePaths"] = preset["core_paths"]
        credential_registration["additionalPaths"] = preset["additional_paths"]
        credential_registration["subjectType"] = "ConformityAttestation"

    if not credential_registration.get("corePaths"):
        raise HTTPException(
            status_code=400,
            detail="corePaths is required unless templateRef is provided",
        )

    related = credential_registration.setdefault("relatedResources", {})
    # legalAct / governance come from the request or credential template assets — not BC Laws lookup.

    origin = publisher_origin()

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
            endpoint=f"{origin}/credentials/status/{status_list_id}",
            credential=status_list_credential,
        ).model_dump(),
    )

    json_schema = {}
    pub_config = load_publication_config_optional(credential_type)
    if template_ref:
        credential_template = build_template_from_preset(
            template_ref=template_ref,
            issuer=issuer,
            domain_type=credential_registration["type"],
        )
        oca_bundle = (
            load_config_oca_bundle(credential_type)
            if pub_config
            else load_preset_oca_bundle(template_ref)
        )
        context = {}
    else:
        if not related.get("context"):
            context_name = credential_type.replace("Credential", "") or credential_type
            major = "1"
            version_match = re.search(r"\d+", str(credential_version or ""))
            if version_match:
                major = version_match.group(0)
            related["context"] = (
                f"https://bcgov.github.io/digital-trust-toolkit/contexts/"
                f"{context_name}/v{major}.jsonld"
            )

        credential_template = await PublisherRegistrar().template_credential(
            credential_registration
        )
        context = httpx.get(credential_registration["relatedResources"]["context"]).json()
        context["@context"]["SimpleRefreshQuery"] = "https://schema.org/WebAPI"
        context["@context"]["OCABundle"] = "https://oca.colossi.network/specification/#bundle"
        settings.LOGGER.info(context)
        oca_bundle = OCAProcessor().create_bundle(credential_registration, credential_template)

    credential_template["renderMethod"] = [
        {
            "type": "OCABundle",
            "id": f"{origin}/bundles/{credential_type}/{credential_version}",
            "name": "Overlay Capture Architecture Bundle",
            "digestMultibase": generate_digest_multibase(oca_bundle),
        }
    ]

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
                additional_type=credential_registration.get("additionalType")
                or ("DigitalConformityCredential" if template_ref else None),
                additional_paths=credential_registration.get("additionalPaths"),
                template_ref=template_ref,
                publication_rules=preset.get("publication_rules") if preset else None,
                cardinality_field=preset.get("cardinality_field") if preset else None,
                status_lists=[status_list_id],
            ).model_dump(),
        )
    except MongoClientError:
        raise HTTPException(status_code=409, detail="Duplicate entry")

    return JSONResponse(status_code=201, content=credential_template)
