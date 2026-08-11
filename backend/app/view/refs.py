"""Credential URL / ref resolution for /view."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.plugins.mongodb import MongoClient
from app.services.composer import publisher_origin
from app.view.fetch import parse_credential_url, view_allows_remote
from config import settings

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


def credential_view_url(credential_url: str) -> str:
    """Relative HTML view link for a machine credential URL."""
    return f"/view?url={quote((credential_url or '').strip(), safe='')}"


def credential_ref_view_url(
    cred_type: str, cardinality_id: str, entity_id: str
) -> str:
    """Relative HTML view link for the latest active credential of a triple.

    Uses ``/view?credential={type}:{cardinality}:{entity}``, matching
    ``GET /credentials/refresh`` semantics (``refresh: false``).
    """
    key = ":".join(
        (
            (cred_type or "").strip(),
            (cardinality_id or "").strip(),
            (entity_id or "").strip(),
        )
    )
    return f"/view?credential={quote(key, safe='')}"


def parse_credential_ref(raw: str) -> tuple[str, str, str] | None:
    """Parse ``type:cardinality:entity`` (entity may contain additional ``:``)."""
    value = (raw or "").strip()
    if not value:
        return None
    parts = value.split(":", 2)
    if len(parts) != 3:
        return None
    cred_type, cardinality, entity = (part.strip() for part in parts)
    if not cred_type or not cardinality or not entity:
        return None
    return cred_type, cardinality, entity


def find_latest_credential_record(
    cred_type: str, cardinality_id: str, entity_id: str
) -> dict[str, Any] | None:
    """Return the active CredentialRecord for type/cardinality/entity.

    Same filter as ``GET /credentials/refresh``: ``refresh: False``.
    """
    try:
        found = MongoClient().find_one(
            "CredentialRecord",
            {
                "type": cred_type,
                "cardinality_id": cardinality_id,
                "entity_id": entity_id,
                "refresh": False,
            },
        )
    except Exception:
        settings.LOGGER.exception(
            "View: latest credential lookup failed for %s / %s / %s",
            cred_type,
            cardinality_id,
            entity_id,
        )
        return None
    return found if isinstance(found, dict) else None


def resolve_view_target(*, url: str = "", credential: str = "") -> tuple[str, str]:
    """Resolve ``url`` or ``credential`` to a credential URL.

    Returns ``(credential_url, error)``. Empty url+credential yields
    ``("", "")`` (welcome). ``credential`` selects the latest active
    publication for ``type:cardinality:entity``.
    """
    raw_url = (url or "").strip()
    raw_ref = (credential or "").strip()
    if raw_url and raw_ref:
        return "", "Provide either a credential URL or credential=type:cardinality:entity, not both."
    if raw_ref:
        parsed = parse_credential_ref(raw_ref)
        if not parsed:
            return (
                "",
                "Enter credential as type:cardinality:entity "
                "(for example BCMinesActPermitCredential:M-1231411:BC1333706).",
            )
        cred_type, cardinality_id, entity_id = parsed
        record = find_latest_credential_record(cred_type, cardinality_id, entity_id)
        cred_id = str((record or {}).get("id") or "").strip()
        if not cred_id:
            return (
                "",
                "No active credential found for that type, cardinality, and entity.",
            )
        return credential_public_url(cred_id), ""
    if not raw_url:
        return "", ""
    if not parse_credential_url(raw_url):
        return "", _view_parse_error(raw_url)
    return raw_url, ""


def _view_parse_error(raw_url: str) -> str:
    if view_allows_remote():
        return "Enter an http(s) /credentials/{id} URL."
    return (
        "This viewer only opens credentials published by this service "
        "(same-origin /credentials/{id} URLs)."
    )
