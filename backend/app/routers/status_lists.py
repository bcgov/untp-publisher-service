from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.plugins import TractionController
from app.plugins.mongodb import MongoClient
from app.utils import timestamp

router = APIRouter(prefix="/status-lists", tags=["Status-Lists"])


@router.get("/{status_credential_id}")
async def get_status_list_credential(status_credential_id: str, request: Request):
    """Return a freshly signed status list as an EnvelopedVerifiableCredential.

    Response ``Content-Type`` is ``application/vc`` (VCDM 2.0 envelope wrapping
    ``data:application/vc+jwt,…``).
    """
    mongo = MongoClient()
    status_list_record = mongo.find_one(
        "StatusListRecord", {"id": status_credential_id}
    )
    if not status_list_record:
        raise HTTPException(
            status_code=404,
            detail="No record found.",
        )
    status_list_credential = dict(status_list_record["credential"])
    status_list_credential["validFrom"] = timestamp()
    status_list_credential["validUntil"] = timestamp(5)

    traction = TractionController()
    traction.authorize()
    vc_jwt = traction.sign_vc_jwt(status_list_credential)
    enveloped = TractionController.as_enveloped_vc(vc_jwt)
    return JSONResponse(headers={"Content-Type": "application/vc"}, content=enveloped)
