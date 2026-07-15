from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import HTTPException
from multiformats import multibase
import base64
import hashlib
import validators
from canonicaljson import encode_canonical_json
import re


def valid_datetime_string(datetime_string):
    try:
        datetime.fromisoformat(datetime_string.replace("Z", "+00:00"))
        return True
    except:
        return False


def format_utc_datetime(dt: datetime | None = None) -> str:
    """ISO 8601 UTC instant with ``Z`` suffix (seconds precision)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_uri(value):
    DID_REGEX = re.compile("did:([a-z0-9]+):((?:[a-zA-Z0-9._%-]*:)*[a-zA-Z0-9._%-]+)")
    if DID_REGEX.match(value) or validators.url(value):
        return True
    return False

def timestamp(minutes_forward=0):
    now = datetime.now(timezone.utc)
    delta = timedelta(minutes=minutes_forward)
    return format_utc_datetime(now + delta)

def generate_digest_multibase(content):
    return multibase.encode(hashlib.sha256(encode_canonical_json(content)).digest(), "base58btc")

def verkey_to_multikey(verkey):
    return multibase.encode(bytes.fromhex(f"ed01{multibase.decode(f'z{verkey}').hex()}"), "base58btc")
    
def multikey_to_jwk(multikey):
    return {
        "kty": "OKP", 
        "crv": "Ed25519",
        "x": base64.urlsafe_b64encode(multibase.decode(multikey)[2:]).decode().rstrip("=")
    }


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """Resolve ``pointer`` against ``document`` (RFC 6901).

    Raises HTTP 400 if the pointer is invalid or does not resolve.
    """
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON Pointer: {pointer!r}",
        )
    if pointer == "/":
        return document

    current = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise HTTPException(
                    status_code=400,
                    detail=f"JSON Pointer {pointer!r} does not resolve",
                )
            current = current[token]
        elif isinstance(current, list):
            try:
                index = int(token)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"JSON Pointer {pointer!r} does not resolve",
                ) from exc
            if index < 0 or index >= len(current):
                raise HTTPException(
                    status_code=400,
                    detail=f"JSON Pointer {pointer!r} does not resolve",
                )
            current = current[index]
        else:
            raise HTTPException(
                status_code=400,
                detail=f"JSON Pointer {pointer!r} does not resolve",
            )
    return current


def require_nonempty_string(value: Any, *, label: str) -> str:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise HTTPException(status_code=400, detail=f"{label} is required")
    return str(value).strip()
