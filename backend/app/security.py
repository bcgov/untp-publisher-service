from typing import Annotated, Literal, Optional

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from config import settings
import jwt
import time


X_API_KEY = APIKeyHeader(name="X-API-Key")
OPTIONAL_X_API_KEY = APIKeyHeader(name="X-API-Key", auto_error=False)
OPTIONAL_BEARER = HTTPBearer(auto_error=False)


def check_api_key_header(x_api_key: str = Depends(X_API_KEY)):
    """Check api key"""

    if x_api_key == settings.TRACTION_API_KEY:
        return True
    raise HTTPException(
        status_code=401,
        detail="Invalid API Key",
    )


def decodeJWT(token: str) -> dict:
    try:
        decoded_token = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        return decoded_token if decoded_token["expires"] >= int(time.time()) else None
    except Exception:
        return {}


def verify_jwt(jwtoken: str) -> bool:
    try:
        payload = decodeJWT(jwtoken)
    except Exception:
        payload = None
    return bool(payload)


class JWTBearer(HTTPBearer):
    def __init__(self, auto_error: bool = True):
        super(JWTBearer, self).__init__(auto_error=auto_error)

    async def __call__(self, request: Request):
        credentials: HTTPAuthorizationCredentials = await super(
            JWTBearer, self
        ).__call__(request)
        if credentials:
            if not credentials.scheme == "Bearer":
                raise HTTPException(status_code=403, detail="Invalid or expired token")
            if not verify_jwt(credentials.credentials):
                raise HTTPException(status_code=403, detail="Invalid or expired token")
            return credentials.credentials
        else:
            raise HTTPException(status_code=403, detail="Invalid or expired token")


async def jwt_or_api_key(
    api_key: Annotated[Optional[str], Security(OPTIONAL_X_API_KEY)] = None,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Security(OPTIONAL_BEARER)
    ] = None,
) -> Literal["api_key", "jwt"]:
    """Accept either admin ``X-API-Key`` or client ``Authorization: Bearer`` JWT."""

    if api_key is not None:
        if api_key == settings.TRACTION_API_KEY:
            return "api_key"
        raise HTTPException(status_code=401, detail="Invalid API Key")

    if credentials is not None:
        if credentials.scheme.lower() != "bearer" or not verify_jwt(
            credentials.credentials
        ):
            raise HTTPException(status_code=403, detail="Invalid or expired token")
        return "jwt"

    raise HTTPException(
        status_code=401,
        detail="Missing authentication (Authorization Bearer or X-API-Key)",
    )
