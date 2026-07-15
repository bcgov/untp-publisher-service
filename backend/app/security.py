from dataclasses import dataclass
from typing import Annotated, Literal, Optional

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from config import settings
import jwt
import time


X_API_KEY = APIKeyHeader(name="X-API-Key")
OPTIONAL_X_API_KEY = APIKeyHeader(name="X-API-Key", auto_error=False)
OPTIONAL_BEARER = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthPrincipal:
    """Authenticated caller for publish / protected routes."""

    via: Literal["api_key", "jwt"]
    client_id: str | None = None


def check_api_key_header(x_api_key: str = Depends(X_API_KEY)):
    """Check api key"""

    if x_api_key == settings.TRACTION_API_KEY:
        return True
    raise HTTPException(
        status_code=401,
        detail="Invalid API Key",
    )


def decodeJWT(token: str) -> dict | None:
    try:
        decoded_token = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        if int(decoded_token.get("expires") or 0) < int(time.time()):
            return None
        return decoded_token
    except Exception:
        return None


def verify_jwt(jwtoken: str) -> bool:
    return decodeJWT(jwtoken) is not None


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
) -> AuthPrincipal:
    """Accept either admin ``X-API-Key`` or client ``Authorization: Bearer`` JWT.

    Admin API key may publish any registered type. JWT ``client_id`` must equal
    the credential type's issuer id (``IssuerInstanceRecord.id`` / DID).
    """

    if api_key is not None:
        if api_key == settings.TRACTION_API_KEY:
            return AuthPrincipal(via="api_key")
        raise HTTPException(status_code=401, detail="Invalid API Key")

    if credentials is not None:
        if credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=403, detail="Invalid or expired token")
        payload = decodeJWT(credentials.credentials)
        if not payload:
            raise HTTPException(status_code=403, detail="Invalid or expired token")
        client_id = str(payload.get("client_id") or "").strip()
        if not client_id:
            raise HTTPException(
                status_code=403,
                detail="Token missing client_id",
            )
        return AuthPrincipal(via="jwt", client_id=client_id)

    raise HTTPException(
        status_code=401,
        detail="Missing authentication (Authorization Bearer or X-API-Key)",
    )
