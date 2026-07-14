"""Branded HTML pages for the full publisher app (not test-suite mode)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.plugins.mongodb import MongoClient
from app.repo_configs import (
    list_issuer_instances,
    load_sample_issued_credential_optional,
)
from config import settings

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter()


def _branding() -> dict:
    return {
        "project_title": settings.PROJECT_TITLE,
        "project_version": settings.PROJECT_VERSION,
        "tagline": settings.LANDING_TAGLINE,
        "description": (settings.LANDING_DESCRIPTION or "").strip(),
        "logo_url": settings.LANDING_LOGO_URL,
        "primary_color": settings.LANDING_PRIMARY_COLOR,
        "secondary_color": settings.LANDING_SECONDARY_COLOR,
        "partner_url": (settings.LANDING_PARTNER_URL or "").strip(),
        "partner_label": (settings.LANDING_PARTNER_LABEL or "BC Mine Information").strip(),
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request):
    return templates.TemplateResponse(
        request,
        "landing.html",
        _branding(),
    )


@router.get("/discovery", response_class=HTMLResponse, include_in_schema=False)
async def discovery(
    request: Request,
    type: str | None = Query(default=None, description="Filter published credentials"),
):
    issuers = list_issuer_instances()
    credential_types: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for issuer in issuers:
        annotated_creds = []
        for cred in issuer.get("credentials") or []:
            cred_type = (cred.get("type") or "").strip()
            version = (cred.get("version") or "").strip()
            has_sample = load_sample_issued_credential_optional(cred_type) is not None
            entry = {
                "type": cred_type,
                "version": version,
                "has_sample": has_sample,
            }
            annotated_creds.append(entry)
            key = (cred_type, version)
            if not cred_type or key in seen:
                continue
            seen.add(key)
            credential_types.append(entry)
        issuer["credentials"] = annotated_creds

    selected_type = (type or "").strip() or None
    published: list[dict] = []
    try:
        mongo = MongoClient()
        query: dict = {"refresh": False}
        if selected_type:
            query["type"] = selected_type
        for record in mongo.find_page("CredentialRecord", query, limit=50):
            published.append(
                {
                    "id": record.get("id"),
                    "type": record.get("type"),
                    "entity_id": record.get("entity_id"),
                    "cardinality_id": record.get("cardinality_id"),
                    "revocation": bool(record.get("revocation")),
                    "suspension": bool(record.get("suspension")),
                }
            )
    except Exception:
        settings.LOGGER.exception("Discovery: failed to load published credentials")

    return templates.TemplateResponse(
        request,
        "discovery.html",
        {
            **_branding(),
            "issuers": issuers,
            "credential_types": credential_types,
            "selected_type": selected_type,
            "published": published,
        },
    )
