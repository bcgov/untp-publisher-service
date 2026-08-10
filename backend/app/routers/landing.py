"""Branded HTML: landing (`/`) and credential discovery (`/discovery`)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.plugins.mongodb import MongoClient
from app.services.composer import publisher_origin, credential_download_filename
from config import settings

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter()

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_DEFAULT_PRIMARY = "#013366"
_DEFAULT_SECONDARY = "#FCBA19"
_DEFAULT_LOGO = (
    "https://mines.nrs.gov.bc.ca/assets/images/bcgov-mineinfo-horiz-LG.png"
)


def safe_css_color(value: str | None, *, default: str) -> str:
    """Allow only ``#RGB`` / ``#RRGGBB`` / ``#RRGGBBAA`` for CSS interpolation."""
    raw = (value or "").strip()
    if _HEX_COLOR_RE.fullmatch(raw):
        return raw
    return default


def safe_http_url(value: str | None) -> str:
    """Allow ``http`` / ``https`` absolute URLs only; otherwise empty."""
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return raw
    return ""


def safe_asset_url(value: str | None, *, default: str) -> str:
    """Allow http(s) URLs or same-origin paths (``/…``, not ``//…``)."""
    raw = (value or "").strip()
    if not raw:
        return default
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    if safe_http_url(raw):
        return raw
    return default


def _branding() -> dict[str, Any]:
    return {
        "project_title": settings.PROJECT_TITLE,
        "project_version": settings.PROJECT_VERSION,
        "tagline": settings.LANDING_TAGLINE,
        "description": (settings.LANDING_DESCRIPTION or "").strip(),
        "logo_url": safe_asset_url(
            settings.LANDING_LOGO_URL, default=_DEFAULT_LOGO
        ),
        "primary_color": safe_css_color(
            settings.LANDING_PRIMARY_COLOR, default=_DEFAULT_PRIMARY
        ),
        "secondary_color": safe_css_color(
            settings.LANDING_SECONDARY_COLOR, default=_DEFAULT_SECONDARY
        ),
        "partner_url": safe_http_url(settings.LANDING_PARTNER_URL),
        "partner_label": (settings.LANDING_PARTNER_LABEL or "Partner").strip()
        or "Partner",
    }


def credential_public_url(credential_id: str) -> str:
    """Absolute URL that returns ``application/vc`` for this record id."""
    cid = (credential_id or "").strip()
    if cid.startswith("http://") or cid.startswith("https://"):
        return cid
    return f"{publisher_origin()}/credentials/{cid}"


def credential_download_url(credential_id: str) -> str:
    """Same as :func:`credential_public_url` with ``download=true``."""
    base = credential_public_url(credential_id)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}download=true"


def _status_label(record: dict[str, Any]) -> str:
    if record.get("revocation"):
        return "revoked"
    if record.get("suspension"):
        return "suspended"
    if record.get("refresh"):
        return "superseded"
    return "active"


def proof_created_raw(record: dict[str, Any]) -> str:
    """Return the Data Integrity proof ``created`` timestamp when present."""
    vc = record.get("vc")
    if not isinstance(vc, dict):
        return ""
    proof = vc.get("proof")
    if isinstance(proof, list):
        proof = proof[0] if proof else None
    if not isinstance(proof, dict):
        return ""
    return str(proof.get("created") or "").strip()


def format_proof_created(raw: str) -> str:
    """Pretty-print an ISO proof timestamp (e.g. ``30 Jul 2026, 17:59 UTC``)."""
    value = (raw or "").strip()
    if not value:
        return "—"
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except ValueError:
        return value


def entity_name_from_record(record: dict[str, Any]) -> str:
    """Org / party display name from the issued VC when present."""
    vc = record.get("vc")
    if not isinstance(vc, dict):
        return ""
    subject = vc.get("credentialSubject")
    if isinstance(subject, list):
        subject = subject[0] if subject else None
    if not isinstance(subject, dict):
        return ""
    party = subject.get("issuedToParty")
    if isinstance(party, dict):
        name = str(party.get("name") or "").strip()
        if name:
            return name
    return ""


def issuer_from_record(record: dict[str, Any]) -> tuple[str, str]:
    """Return ``(issuer_name, issuer_did)`` from the issued VC when present."""
    vc = record.get("vc")
    if not isinstance(vc, dict):
        return "", ""
    issuer = vc.get("issuer")
    if isinstance(issuer, str):
        did = issuer.strip()
        return "", did
    if isinstance(issuer, dict):
        did = str(issuer.get("id") or "").strip()
        name = str(issuer.get("name") or "").strip()
        return name, did
    return "", ""


def issuer_resolve_url(did: str) -> str:
    """Universal Resolver deep link: ``https://uniresolver.io/#{did}``."""
    value = (did or "").strip()
    if not value:
        return ""
    return f"https://uniresolver.io/#{value}"


def group_credential_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse records by ``(entity_id, cardinality_id)``.

    ``records`` must already be newest-inserted-first. The group face prefers the
    current (non-refresh) iteration; otherwise the first/newest. Missing
    entity/cardinality → singleton by credential id.
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    for record in records:
        cred_id = str(record.get("id") or "").strip()
        entity = str(record.get("entity_id") or "").strip()
        cardinality = str(record.get("cardinality_id") or "").strip()
        if entity and cardinality:
            key = (entity, cardinality)
        else:
            key = (cred_id or f"anon-{len(order)}", cred_id or f"anon-{len(order)}")

        url = credential_public_url(cred_id)
        download_url = credential_download_url(cred_id)
        created_raw = proof_created_raw(record)
        download_name = credential_download_filename(record)
        entity_name = entity_name_from_record(record)
        issuer_name, issuer_did = issuer_from_record(record)
        iteration = {
            "id": cred_id,
            "type": record.get("type") or "",
            "entity_id": entity,
            "entity_name": entity_name,
            "cardinality_id": cardinality,
            "issuer_name": issuer_name,
            "issuer_did": issuer_did,
            "issuer_resolve_url": issuer_resolve_url(issuer_did),
            "revocation": bool(record.get("revocation")),
            "suspension": bool(record.get("suspension")),
            "refresh": bool(record.get("refresh")),
            "status": _status_label(record),
            "url": url,
            "download_url": download_url,
            "download_name": download_name,
            "created": created_raw,
            "created_display": format_proof_created(created_raw),
        }

        if key not in groups:
            groups[key] = {
                "entity_id": entity or iteration["entity_id"],
                "entity_name": entity_name,
                "cardinality_id": cardinality or iteration["cardinality_id"],
                "issuer_name": issuer_name,
                "issuer_did": issuer_did,
                "issuer_resolve_url": iteration["issuer_resolve_url"],
                "type": iteration["type"],
                "status": iteration["status"],
                "url": url,
                "download_url": download_url,
                "download_name": download_name,
                "id": cred_id,
                "iterations": [iteration],
            }
            order.append(key)
        else:
            groups[key]["iterations"].append(iteration)

    result = []
    for key in order:
        group = groups[key]
        iterations = group["iterations"]
        # Prefer the live (non-refresh) row as the group face when present.
        face = next((i for i in iterations if not i["refresh"]), iterations[0])
        group["id"] = face["id"]
        group["type"] = face["type"]
        group["status"] = face["status"]
        group["url"] = face["url"]
        group["download_url"] = face["download_url"]
        group["download_name"] = face["download_name"]
        group["entity_name"] = face.get("entity_name") or group.get("entity_name") or ""
        group["issuer_name"] = face.get("issuer_name") or group.get("issuer_name") or ""
        group["issuer_did"] = face.get("issuer_did") or group.get("issuer_did") or ""
        group["issuer_resolve_url"] = (
            face.get("issuer_resolve_url") or group.get("issuer_resolve_url") or ""
        )
        group["iteration_count"] = len(iterations)
        result.append(group)
    return result


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request):
    return templates.TemplateResponse(
        request,
        "landing.html",
        _branding(),
    )


@router.get("/discovery", response_class=HTMLResponse, include_in_schema=False)
async def discovery(request: Request):
    records: list[dict[str, Any]] = []
    load_error = ""
    truncated = False
    try:
        mongo = MongoClient()
        # Newest inserted first. Include refresh=true rows so iteration history
        # can collapse under the same (entity_id, cardinality_id) group.
        # Cap rows to bound memory / response size on this public endpoint.
        # Fetch limit+1 so we can detect truncation without an extra count query.
        limit = int(settings.DISCOVERY_MAX_RECORDS)
        page = mongo.find_page("CredentialRecord", {}, skip=0, limit=limit + 1)
        for record in page:
            if isinstance(record, dict):
                records.append(record)
        truncated = len(records) > limit
        if truncated:
            records = records[:limit]
    except Exception:
        settings.LOGGER.exception("Discovery: failed to load published credentials")
        load_error = "Could not load credentials. Check that the database is reachable and retry."

    groups = group_credential_records(records)
    credential_types = sorted(
        {str(g.get("type") or "") for g in groups if g.get("type")}
    )

    return templates.TemplateResponse(
        request,
        "discovery.html",
        {
            **_branding(),
            "groups": groups,
            "credential_types": credential_types,
            "total_credentials": len(records),
            "total_groups": len(groups),
            "load_error": load_error,
            "truncated": truncated,
            "discovery_max_records": int(settings.DISCOVERY_MAX_RECORDS),
        },
    )
