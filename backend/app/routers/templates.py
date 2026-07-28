from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.plugins.mongodb import MongoClient
from app.repo_configs.loader import load_oca_bundle

router = APIRouter(prefix="/templates", tags=["Templates"])


def _template_record(credential_type: str, version: str) -> dict:
    mongo = MongoClient()
    record = mongo.find_one(
        "CredentialTemplateRecord",
        {"type": credential_type, "version": version},
    )
    if not record:
        raise HTTPException(
            status_code=404,
            detail="Resource not found",
        )
    return record


@router.get("/{credential_type}/{version}/oca.json")
async def get_oca_bundle(credential_type: str, version: str):
    """Return the OCA bundle from disk (registration must exist)."""
    _template_record(credential_type, version)
    return JSONResponse(status_code=200, content=load_oca_bundle(credential_type))


@router.get("/{credential_type}/{version}")
async def get_credential_template(credential_type: str, version: str):
    """Return the provisioned VC template for a credential type and version."""
    record = _template_record(credential_type, version)
    return JSONResponse(status_code=200, content=record["template"])
