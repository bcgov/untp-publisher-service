from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field, BaseModel
from config import settings
from app.plugins import MongoClient, MongoClientError
import time
import jwt
import secrets
import hashlib
from app.security import check_api_key_header

router = APIRouter(prefix="/auth", tags=["Auth"])


class RequestSecret(BaseModel):
    client_id: str = Field()


class RequestToken(BaseModel):
    client_id: str = Field()
    client_secret: str = Field()


class RequestDelegatedCredential(BaseModel):
    issuer: str = Field()
    email: str = Field()


@router.post("/secret", dependencies=[Depends(check_api_key_header)])
async def update_client_secret(request_body: RequestSecret):
    client_id = vars(request_body)["client_id"]
    client_secret = secrets.token_urlsafe(64)
    client_hash = hashlib.sha256(client_secret.encode()).hexdigest()

    mongo = MongoClient()
    issuer_record = mongo.find_one("IssuerInstanceRecord", {"id": client_id})
    issuer_record["secret_hash"] = client_hash
    mongo.replace("IssuerInstanceRecord", {"id": client_id}, issuer_record)

    return JSONResponse(status_code=200, content={"client_secret": client_secret})


@router.post("/token")
async def request_client_token(request_body: RequestToken):
    client_id = vars(request_body)["client_id"]
    client_secret = vars(request_body)["client_secret"]
    client_hash = hashlib.sha256(client_secret.encode()).hexdigest()

    mongo = MongoClient()
    issuer_record = mongo.find_one("IssuerInstanceRecord", {"id": client_id})

    if client_hash != issuer_record["secret_hash"]:
        raise HTTPException(
            status_code=403,
            detail="Invalid credentials",
        )

    payload = {"client_id": client_id, "expires": int(time.time()) + 3600}
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    return JSONResponse(status_code=200, content={"access_token": token})
