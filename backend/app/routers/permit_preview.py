"""Serve the Mines Act permit visual preview UI (public, no API key).

Entry is under Discovery: ``/discovery/samples/{credential_type}``.
``/permit`` redirects there for old bookmarks.

OCA labels come from the public Templates API:
``GET /templates/{credential_type}/{version}/oca.json``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.repo_configs import (
    credential_version_for_type,
    load_credential_template,
    load_sample_issued_credential_optional,
    load_sample_publication_payload,
)
from app.services.test_suite_build import build_unsigned_credential_from_publication

# Mines Act is the only type with a rich visual sample for now.
_RICH_SAMPLE_TYPES = frozenset({"BCMinesActPermitCredential"})

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "permit"

router = APIRouter(include_in_schema=False)


def _require_rich_sample(credential_type: str) -> None:
    if credential_type not in _RICH_SAMPLE_TYPES:
        raise HTTPException(
            status_code=404,
            detail=f"No rich sample preview for type {credential_type!r}",
        )
    if not load_sample_issued_credential_optional(credential_type):
        raise HTTPException(
            status_code=404,
            detail=f"No sample credential for type {credential_type!r}",
        )


@router.get("/discovery/samples/{credential_type}")
async def discovery_sample_preview(credential_type: str):
    """Rich visual sample (permit document UI) for a credential type."""
    _require_rich_sample(credential_type)
    version = credential_version_for_type(credential_type)
    oca_url = f"/templates/{credential_type}/{version}/oca.json"
    html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('data-oca-url=""', f'data-oca-url="{oca_url}"', 1)
    return HTMLResponse(content=html)


@router.get("/discovery/samples/{credential_type}/api/sample")
async def discovery_sample_credential(credential_type: str):
    _require_rich_sample(credential_type)
    sample = load_sample_issued_credential_optional(credential_type)
    return JSONResponse(content=sample)


@router.get("/discovery/samples/{credential_type}/api/publication-payload")
async def discovery_sample_publication_payload(credential_type: str):
    _require_rich_sample(credential_type)
    return JSONResponse(content=load_sample_publication_payload(credential_type))


@router.get("/discovery/samples/{credential_type}/api/template-preview")
async def discovery_sample_template_preview(credential_type: str):
    _require_rich_sample(credential_type)
    return JSONResponse(content=load_credential_template(credential_type))


@router.get("/discovery/samples/{credential_type}/api/build")
async def discovery_sample_build(credential_type: str):
    """Build an unsigned UNTP DCC from the bundled publication payload."""
    _require_rich_sample(credential_type)
    payload = load_sample_publication_payload(credential_type)
    credential = build_unsigned_credential_from_publication(publication=payload)
    return JSONResponse(
        content={
            "credential": credential,
            "publicationPayload": payload,
        }
    )


@router.get("/permit")
@router.get("/permit/")
async def permit_redirect():
    return RedirectResponse(
        url="/discovery/samples/BCMinesActPermitCredential",
        status_code=307,
    )
