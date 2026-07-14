"""RFC 6901 JSON Pointer resolution."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


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
    # Skip leading empty segment from split on "/a/b"
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
