"""Branded HTML: landing (`/`), discovery (`/discovery`), and OCA view (`/view`)."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.plugins.mongodb import MongoClient
from app.plugins.status_list import BitstringStatusList, BitstringStatusListError
from app.plugins.traction import TractionController
from app.models.credential import Credential as Vcdm20Credential
from app.repo_configs.loader import (
    credential_version_for_type,
    load_oca_bundle,
)
from app.services.composer import publisher_origin, credential_download_filename
from app.utils import generate_digest_multibase
from app.validators.untp import (
    UntpArtefactKind,
    UntpValidationError,
    detect_untp_artefact_kind,
    first_failed_validation_check,
    validate_untp_document_with_checks,
    validate_untp_json_ld,
)
from config import settings
from pydantic import ValidationError as PydanticValidationError
from untp.releases import CONTEXT_BUNDLE, bundled_context_digests_for_document
from untp.jsonld_loader import UntpJsonLdRemoteContextError

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter()

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_PERMIT_DESC_SUFFIX_RE = re.compile(r"\s*\(permit\s+[^)]+\)\.?\s*$", re.IGNORECASE)
_PERMIT_NAME_EMDASH_RE = re.compile(r"\s+[—–]\s+.+$")
_DEFAULT_PRIMARY = "#013366"
_DEFAULT_SECONDARY = "#FCBA19"
_DEFAULT_LOGO = (
    "https://mines.nrs.gov.bc.ca/assets/images/bcgov-mineinfo-horiz-LG.png"
)
_VC_JWT_DATA_PREFIX = "data:application/vc+jwt,"
_CREDENTIALS_V2_CONTEXT = "https://www.w3.org/ns/credentials/v2"
_ENVELOPED_VC_TYPE = "EnvelopedVerifiableCredential"
_VIEW_FETCH_TIMEOUT_S = 30


class EnvelopeValidationError(ValueError):
    """Raised when an EnvelopedVerifiableCredential document is invalid."""


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


def _parse_path_id_url(
    url: str,
    *,
    path_prefix: str,
    require_same_origin: bool,
) -> str | None:
    """Extract a single path segment after ``path_prefix`` from an http(s) URL."""
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if require_same_origin:
        origin = urlparse(publisher_origin())
        if (
            parsed.scheme != origin.scheme
            or parsed.netloc.lower() != (origin.netloc or "").lower()
        ):
            return None
    path = (parsed.path or "").rstrip("/")
    if not path.startswith(path_prefix):
        return None
    value = path[len(path_prefix) :]
    if not value or "/" in value:
        return None
    return value


def parse_same_origin_credential_url(url: str) -> str | None:
    """Return credential id if ``url`` is this publisher's ``/credentials/{id}``."""
    return _parse_path_id_url(
        url,
        path_prefix="/credentials/",
        require_same_origin=True,
    )


def parse_credential_url(url: str, *, allow_remote: bool | None = None) -> str | None:
    """Return credential id from a ``/credentials/{id}`` URL.

    Same-origin only unless ``allow_remote`` / ``VIEW_UNSAFE_MODE`` is enabled.
    """
    remote = settings.VIEW_UNSAFE_MODE if allow_remote is None else allow_remote
    return _parse_path_id_url(
        url,
        path_prefix="/credentials/",
        require_same_origin=not remote,
    )


def view_allows_remote() -> bool:
    """True when ``/view`` may fetch off-origin credential / status / OCA URLs."""
    return bool(settings.VIEW_UNSAFE_MODE)


def is_http_url(url: str) -> bool:
    """True for absolute ``http`` / ``https`` URLs with a host."""
    parsed = urlparse((url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def soft_resolve_json_pointer(document: Any, pointer: str) -> Any | None:
    """Resolve RFC 6901 ``pointer``; return ``None`` when missing or invalid."""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return None
    if pointer == "/":
        return document
    current: Any = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        elif isinstance(current, list):
            try:
                index = int(token)
            except ValueError:
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def format_oca_value(value: Any, *, attr_type: str = "") -> str:
    """Human-readable string for an OCA attribute value."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)
    text = str(value).strip()
    if not text:
        return "—"
    kind = (attr_type or "").strip()
    if kind == "DateTime" or _looks_like_iso_datetime(text):
        return format_oca_datetime(text)
    return text


def _looks_like_iso_datetime(text: str) -> bool:
    """True when ``text`` is plausibly an ISO-8601 timestamp."""
    if "T" not in text or len(text) < 10:
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def format_oca_datetime(raw: str) -> str:
    """Pretty-print an OCA DateTime (date-only at midnight UTC, else with time)."""
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
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
            return dt.strftime("%d %b %Y")
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except ValueError:
        return value


def oca_languages(oca_bundle: dict[str, Any]) -> list[str]:
    """Languages present on label overlays, ``en`` first when available."""
    found: list[str] = []
    for overlay in oca_bundle.get("overlays") or []:
        if not isinstance(overlay, dict):
            continue
        if str(overlay.get("type") or "") != "spec/overlays/label/1.0":
            continue
        lang = str(overlay.get("language") or "").strip().lower()
        if lang and lang not in found:
            found.append(lang)
    if "en" in found:
        found.remove("en")
        found.insert(0, "en")
    return found


def _overlay_map(
    oca_bundle: dict[str, Any],
    *,
    overlay_type: str,
    language: str,
    field: str,
) -> dict[str, str]:
    chosen: dict[str, str] = {}
    fallback: dict[str, str] = {}
    for overlay in oca_bundle.get("overlays") or []:
        if not isinstance(overlay, dict):
            continue
        if str(overlay.get("type") or "") != overlay_type:
            continue
        raw = overlay.get(field)
        if not isinstance(raw, dict):
            continue
        mapped = {
            str(k): str(v)
            for k, v in raw.items()
            if isinstance(k, str) and v is not None and str(v).strip()
        }
        lang = str(overlay.get("language") or "").strip().lower()
        if lang == language:
            chosen = mapped
            break
        if not fallback:
            fallback = mapped
    return chosen or fallback


def _overlay_capture_base(oca_bundle: dict[str, Any], *, language: str) -> str:
    """Return ``capture_base`` from the label overlay chosen for ``language``."""
    fallback = ""
    for overlay in oca_bundle.get("overlays") or []:
        if not isinstance(overlay, dict):
            continue
        if str(overlay.get("type") or "") != "spec/overlays/label/1.0":
            continue
        base = str(overlay.get("capture_base") or "").strip()
        if not base:
            continue
        lang = str(overlay.get("language") or "").strip().lower()
        if lang == language:
            return base
        if not fallback:
            fallback = base
    return fallback


_OCA_HERO_POINTERS = {
    "/name",
    "/description",
    "/credentialSubject/name",
    "/credentialSubject/description",
    "/credentialSubject/referenceScheme/name",
    "/credentialSubject/conformityAssessment/0/name",
    "/credentialSubject/conformityAssessment/0/description",
}

_OCA_SECTION_RULES: list[tuple[str, str, Any]] = [
    ("facility", "Mining Site", lambda p: "/assessedFacility/" in p),
    ("organisation", "Organisation", lambda p: "/assessedOrganisation/" in p),
    ("product", "Commodity", lambda p: "/assessedProduct/" in p),
    ("criteria", "Permit criteria", lambda p: "/assessmentCriteria/" in p),
    ("evidence", "Evidence", lambda p: "/evidence/" in p),
    ("assessment", "Regulation", lambda p: "/conformityAssessment/" in p),
    ("holder", "Permit holder", lambda p: "/issuedToParty/" in p),
    (
        "attestation",
        "Attestation",
        lambda p: p
        in {
            "/credentialSubject/assessmentLevel",
            "/credentialSubject/assessorLevel",
            "/credentialSubject/attestationType",
        },
    ),
    (
        "governance",
        "Governance",
        lambda p: "/referenceProfile/" in p or "/referenceScheme/" in p,
    ),
    (
        "credential",
        "Credential",
        lambda p: p in {"/id", "/issuer/id", "/issuer/name", "/validFrom"}
        or p.startswith("/issuer/"),
    ),
]

_OCA_SECTION_DISPLAY_ORDER = [
    "criteria",
    "assessment",
    "attestation",
    "facility",
    "organisation",
    "product",
    "evidence",
    "holder",
    "governance",
    "credential",
    "details",
]

_OCA_SECTION_KIND = {
    "attestation": "chips",
    "facility": "entity",
    "organisation": "entity",
    "holder": "entity",
    "product": "product",
    "criteria": "criteria",
    "assessment": "panel",
    "evidence": "evidence",
    "governance": "links",
    "credential": "links",
    "details": "panel",
}

_OCA_SECTION_HEADLINES = {
    "facility": (
        "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
    ),
    "organisation": (
        "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
    ),
    "product": (
        "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name",
    ),
    "holder": ("/credentialSubject/issuedToParty/name",),
    "governance": ("/credentialSubject/referenceProfile/name",),
    "criteria": ("/credentialSubject/conformityAssessment/0/assessmentCriteria/0/name",),
    "assessment": (
        "/credentialSubject/conformityAssessment/0/referenceRegulation/0/name",
    ),
}


def _oca_attr(
    attributes: dict[str, Any], pointer: str
) -> dict[str, Any]:
    entry = attributes.get(pointer)
    return entry if isinstance(entry, dict) else {}


def _oca_display(entry: dict[str, Any]) -> str:
    if not entry or entry.get("missing") or entry.get("value") in (None, ""):
        return "—"
    return str(entry.get("value"))


def _oca_section_id(pointer: str) -> str:
    for section_id, _title, match in _OCA_SECTION_RULES:
        if match(pointer):
            return section_id
    return "details"


def _oca_section_title(section_id: str) -> str:
    for sid, title, _match in _OCA_SECTION_RULES:
        if sid == section_id:
            return title
    return "Details"


def _oca_is_http_url(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _oca_is_identifier(value: str) -> bool:
    stripped = (value or "").strip()
    return stripped.startswith(("urn:", "did:", "data:"))


def _oca_field_payload(pointer: str, entry: dict[str, Any]) -> dict[str, Any]:
    value = _oca_display(entry)
    label = str(entry.get("label") or pointer)
    missing = bool(entry.get("missing") or value == "—")
    href = value if not missing and _oca_is_http_url(value) else ""
    raw = entry.get("raw")
    badge_ok: bool | None = None
    if str(entry.get("type") or "") == "Boolean" or value in {"Yes", "No"}:
        if raw is True or value == "Yes":
            badge_ok = True
        elif raw is False or value == "No":
            badge_ok = False
    return {
        "pointer": pointer,
        "label": label,
        "value": value,
        "information": str(entry.get("information") or ""),
        "missing": missing,
        "href": href,
        "identifier": (not missing and _oca_is_identifier(value)),
        "badge_ok": badge_ok,
    }


def _oca_classify_field(pointer: str, entry: dict[str, Any]) -> str:
    payload_preview = _oca_field_payload(pointer, entry)
    if payload_preview["missing"]:
        return "empty"
    label = str(entry.get("label") or "").lower()
    value = payload_preview["value"]
    if payload_preview["badge_ok"] is not None:
        return "badge"
    if payload_preview["href"]:
        return "link"
    if payload_preview["identifier"] or "uri" in label or label.endswith(" id"):
        return "id"
    if label.endswith("name") and "uri" not in label and "scheme" not in label:
        return "headline"
    if "scheme" in label and "uri" not in label:
        return "scheme"
    if any(token in label for token in ("metric", "unit", "value", "plus code", "location")):
        return "metric"
    return "fact"


def _build_oca_section_card(
    section_id: str,
    pointers: list[str],
    attrs: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a render-ready section component; ``None`` when nothing useful to show."""
    kind = _OCA_SECTION_KIND.get(section_id, "panel")
    headline: dict[str, Any] | None = None
    scheme: dict[str, Any] | None = None
    badges: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    ids: list[dict[str, Any]] = []
    chips: list[dict[str, Any]] = []
    seen: set[str] = set()

    for pointer in _OCA_SECTION_HEADLINES.get(section_id, ()):
        entry = _oca_attr(attrs, pointer)
        if not entry or entry.get("missing"):
            continue
        headline = _oca_field_payload(pointer, entry)
        seen.add(pointer)
        break

    for pointer in pointers:
        if pointer in seen:
            continue
        entry = _oca_attr(attrs, pointer)
        if not entry:
            continue
        role = _oca_classify_field(pointer, entry)
        if role == "empty":
            continue
        payload = _oca_field_payload(pointer, entry)
        seen.add(pointer)
        if kind == "chips":
            chips.append(payload)
            continue
        if role == "headline" and headline is None:
            headline = payload
        elif role == "scheme" and scheme is None:
            scheme = payload
        elif role == "badge":
            badges.append(payload)
        elif role == "metric":
            metrics.append(payload)
        elif role == "link":
            links.append(payload)
        elif role == "id":
            ids.append(payload)
        else:
            facts.append(payload)

    if kind == "chips":
        if not chips:
            return None
        return {
            "id": section_id,
            "title": _oca_section_title(section_id),
            "kind": kind,
            "chips": chips,
            "pointers": pointers,
        }

    if kind == "evidence" and not any([headline, facts, links, ids, badges, metrics]):
        return None

    # idScheme id+name → one labeled link; drop the redundant scheme line / raw URL text.
    scheme, links, facts, ids = _oca_collapse_idscheme_fields(
        scheme, links, facts, ids, attrs
    )

    # Entity cards: show the subject URN under the name (not "ID scheme · …").
    entity_id: dict[str, Any] | None = None
    if section_id in {"facility", "organisation", "holder", "product"}:
        kept_ids: list[dict[str, Any]] = []
        for payload in ids:
            pointer = str(payload.get("pointer") or "")
            if pointer.endswith(
                (
                    "/facility/id",
                    "/assessedOrganisation/id",
                    "/issuedToParty/id",
                    "/product/id",
                )
            ):
                if entity_id is None:
                    entity_id = payload
                continue
            kept_ids.append(payload)
        ids = kept_ids
        if entity_id is not None:
            scheme = None

    if section_id == "assessment":
        # Regulation lives in the stats strip beside Governance (same card styling).
        return None

    # UNTP assessedOrganisation is a Party (no idVerifiedByCAB); still show the same
    # Verified chrome as facility when we have an identified organisation.
    if section_id == "organisation" and not badges and (headline or entity_id):
        badges.append(
            {
                "pointer": "",
                "label": "Verified",
                "value": "Yes",
                "information": "",
                "missing": False,
                "href": "",
                "identifier": False,
                "badge_ok": True,
            }
        )

    if not any(
        [headline, scheme, entity_id, badges, facts, metrics, links, ids, chips]
    ):
        return None

    return {
        "id": section_id,
        "title": _oca_section_title(section_id),
        "kind": kind,
        "headline": headline,
        "scheme": scheme,
        "entity_id": entity_id,
        "badges": badges,
        "facts": facts,
        "metrics": metrics,
        "links": links,
        "ids": ids,
        "chips": chips,
        "pointers": pointers,
    }


def _oca_collapse_idscheme_fields(
    scheme: dict[str, Any] | None,
    links: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    ids: list[dict[str, Any]],
    attrs: dict[str, Any],
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Turn ``idScheme.id`` URLs into name-labeled links; hide duplicate scheme text."""
    consumed_names: set[str] = set()
    out_links: list[dict[str, Any]] = []

    def absorb(payload: dict[str, Any]) -> dict[str, Any]:
        pointer = str(payload.get("pointer") or "")
        href = str(payload.get("href") or "")
        value = str(payload.get("value") or "")
        if not href and _oca_is_http_url(value):
            href = value
        if not pointer.endswith("/idScheme/id") or not href:
            payload = dict(payload)
            payload["href"] = href
            return payload
        name_pointer = pointer[: -len("/id")] + "/name"
        name_entry = _oca_attr(attrs, name_pointer)
        name_text = ""
        if name_entry and not name_entry.get("missing"):
            name_text = _oca_display(name_entry)
        # Prefer short OCA labels (e.g. "BC Mine Information") over the scheme's legal name.
        oca_label = str(payload.get("label") or "").strip()
        if oca_label and (not name_text or oca_label.casefold() != name_text.casefold()):
            label = oca_label
        elif name_text:
            label = name_text
        else:
            label = oca_label or "Link"
        consumed_names.add(name_pointer)
        return {
            **payload,
            "label": label,
            "value": label,
            "href": href,
            "information": str(
                (name_entry or {}).get("information")
                or payload.get("information")
                or href
            ),
        }

    for payload in links:
        out_links.append(absorb(payload))

    # Promote http(s) ids that were misclassified into labeled links.
    kept_ids: list[dict[str, Any]] = []
    for payload in ids:
        value = str(payload.get("value") or "")
        if _oca_is_http_url(value) or payload.get("href"):
            promoted = dict(payload)
            promoted["href"] = str(payload.get("href") or value)
            out_links.append(absorb(promoted))
        else:
            kept_ids.append(payload)

    if scheme and str(scheme.get("pointer") or "") in consumed_names:
        scheme = None

    facts = [
        f for f in facts if str(f.get("pointer") or "") not in consumed_names
    ]
    return scheme, out_links, facts, kept_ids


def build_oca_presentation(attributes: dict[str, Any], order: list[str], flagged: list[str]) -> dict[str, Any]:
    """Derive hero / flow / section layout from pointer-keyed attributes."""
    attrs = attributes if isinstance(attributes, dict) else {}
    flagged_set = set(flagged or [])

    def pick(*pointers: str) -> dict[str, Any]:
        for pointer in pointers:
            entry = _oca_attr(attrs, pointer)
            if entry and not entry.get("missing") and entry.get("value") not in (None, ""):
                return entry
        for pointer in pointers:
            entry = _oca_attr(attrs, pointer)
            if entry:
                return entry
        return {}

    scheme = pick("/credentialSubject/referenceScheme/name")
    title = pick("/credentialSubject/name", "/name")
    subtitle = pick("/credentialSubject/description", "/description")
    assessment_title = pick("/credentialSubject/conformityAssessment/0/name")
    assessment_lead = pick("/credentialSubject/conformityAssessment/0/description")
    valid_from = pick("/validFrom")
    assessment_date = pick("/credentialSubject/conformityAssessment/0/assessmentDate")
    conforms_entry = _oca_attr(
        attrs, "/credentialSubject/conformityAssessment/0/conformance"
    )
    conforms_raw = conforms_entry.get("raw")
    conforms: bool | None
    if conforms_entry.get("missing") or conforms_raw is None:
        conforms = None
    else:
        conforms = bool(conforms_raw)

    issuer_name = pick("/issuer/name", "/issuer/id")
    issuer_id_entry = pick("/issuer/id")
    issuer_did = ""
    if issuer_id_entry and not issuer_id_entry.get("missing"):
        issuer_did = str(issuer_id_entry.get("value") or "").strip()

    subtitle_text = (
        _oca_display(subtitle) if subtitle and not subtitle.get("missing") else ""
    )
    if subtitle_text:
        # Older published permits appended `` (permit X)``; drop it in the view.
        cleaned = _PERMIT_DESC_SUFFIX_RE.sub("", subtitle_text).strip()
        if cleaned and not cleaned.endswith("."):
            cleaned += "."
        subtitle_text = cleaned

    title_text = (
        _oca_display(title)
        if title and not title.get("missing")
        else "Verifiable credential"
    )
    if title_text and title_text != "Verifiable credential":
        # Older published permits used ``Mines Act Permit X — Permittee``.
        stripped = _PERMIT_NAME_EMDASH_RE.sub("", title_text).strip()
        if stripped:
            title_text = stripped

    assessment_title_text = (
        _oca_display(assessment_title)
        if assessment_title and not assessment_title.get("missing")
        else ""
    )
    if assessment_title_text:
        stripped = _PERMIT_NAME_EMDASH_RE.sub("", assessment_title_text).strip()
        if stripped:
            assessment_title_text = stripped

    flow = {
        "issuer": _oca_display(issuer_name),
        "issuer_href": issuer_resolve_url(issuer_did) if issuer_did.startswith("did:") else "",
        "assessment": _oca_display(
            pick("/credentialSubject/conformityAssessment/0/registeredId")
        ),
        "organisation": _oca_display(
            pick(
                "/credentialSubject/conformityAssessment/0/assessedOrganisation/registeredId",
                "/credentialSubject/issuedToParty/registeredId",
                "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
                "/credentialSubject/issuedToParty/name",
            )
        ),
        "facility": _oca_display(
            pick(
                "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
                "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
            )
        ),
        "product": _oca_display(
            pick(
                "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/registeredId",
                "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name",
            )
        ),
    }

    stats_specs = [
        (
            "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/name",
        ),
        ("/credentialSubject/conformityAssessment/0/assessmentCriteria/0/name",),
        ("/credentialSubject/referenceProfile/name",),
        (
            "/credentialSubject/conformityAssessment/0/referenceRegulation/0/name",
        ),
    ]
    # Name fields whose sibling ``…/id`` should become the clickable href.
    stats_link_ids = {
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/name": (
            "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/id"
        ),
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/name": (
            "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/id"
        ),
        "/credentialSubject/conformityAssessment/0/referenceRegulation/0/name": (
            "/credentialSubject/conformityAssessment/0/referenceRegulation/0/id"
        ),
        "/credentialSubject/referenceProfile/name": "/credentialSubject/referenceProfile/id",
    }
    stats_label_overrides = {
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/name": (
            "Criteria"
        ),
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/name": (
            "Conformity topic"
        ),
        "/credentialSubject/conformityAssessment/0/referenceRegulation/0/name": "Regulation",
    }
    stats: list[dict[str, str]] = []
    for pointers in stats_specs:
        entry = pick(*pointers)
        if not entry:
            continue
        href = ""
        id_pointer = stats_link_ids.get(pointers[0], "")
        if id_pointer:
            id_entry = pick(id_pointer)
            id_value = _oca_display(id_entry) if id_entry else ""
            if id_value and _oca_is_http_url(id_value):
                href = id_value
        stats.append(
            {
                "label": stats_label_overrides.get(
                    pointers[0], str(entry.get("label") or pointers[0])
                ),
                "value": _oca_display(entry),
                "information": str(entry.get("information") or ""),
                "href": href,
            }
        )

    id_chips: list[dict[str, str]] = []

    def add_entity_chip(
        *,
        label: str,
        id_entry: dict[str, Any],
        name_entry: dict[str, Any] | None = None,
        name_override: str = "",
        information: str = "",
    ) -> None:
        id_value = ""
        if id_entry and not id_entry.get("missing"):
            id_value = _oca_display(id_entry)
        name_value = (name_override or "").strip()
        if not name_value and name_entry and not name_entry.get("missing"):
            name_value = _oca_display(name_entry)
        if not id_value and not name_value:
            return
        tip = information
        if not tip and id_entry:
            tip = str(id_entry.get("information") or "")
        if not tip and name_entry:
            tip = str(name_entry.get("information") or "")
        id_chips.append(
            {
                "label": label,
                "name": name_value or "—",
                "id": id_value or "—",
                "value": id_value or name_value or "—",
                "information": tip,
            }
        )

    facility_name = pick(
        "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name"
    )
    facility_id = pick(
        "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId"
    )
    add_entity_chip(
        label=str(facility_name.get("label") or facility_id.get("label") or "Mining Site"),
        id_entry=facility_id,
        name_entry=facility_name,
    )

    permittee_name = pick(
        "/credentialSubject/issuedToParty/name",
        "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
    )
    permittee_id = pick(
        "/credentialSubject/issuedToParty/registeredId",
        "/credentialSubject/conformityAssessment/0/assessedOrganisation/registeredId",
    )
    add_entity_chip(
        label=str(permittee_name.get("label") or "Permittee"),
        id_entry=permittee_id,
        name_entry=permittee_name,
    )

    permit_id = pick("/credentialSubject/conformityAssessment/0/registeredId")
    add_entity_chip(
        label=str(permit_id.get("label") or "Permit number"),
        id_entry=permit_id,
        name_override="Mines Act Permit",
        information=str(permit_id.get("information") or ""),
    )

    used = set(_OCA_HERO_POINTERS) | flagged_set
    for pointers in stats_specs:
        used.update(pointers)
    used.update(
        {
            "/issuer/name",
            "/issuer/id",
            "/credentialSubject/conformityAssessment/0/registeredId",
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/registeredId",
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
            "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name",
            "/credentialSubject/conformityAssessment/0/conformance",
            "/credentialSubject/conformityAssessment/0/assessmentDate",
            "/credentialSubject/issuedToParty/name",
            "/credentialSubject/issuedToParty/registeredId",
            "/credentialSubject/referenceProfile/name",
            "/credentialSubject/referenceProfile/id",
            "/credentialSubject/conformityAssessment/0/referenceRegulation/0/name",
            "/credentialSubject/conformityAssessment/0/referenceRegulation/0/id",
            "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/name",
            "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/id",
            "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/name",
            "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/id",
            "/validFrom",
        }
    )

    section_map: dict[str, list[str]] = {}
    for pointer in order or []:
        if pointer in used:
            continue
        # UNTP assessedPerformance (e.g. "Permit issued" / C62 / 1) is scaffolding
        # for permit DCCs — keep it in the technical dump, not the summary cards.
        if "/assessedPerformance/" in pointer:
            continue
        # Holder / governance / credential / residual details are already covered by
        # the hero, stats strip, or are technical identifiers — dump only.
        section_id = _oca_section_id(pointer)
        if section_id in {
            "holder",
            "governance",
            "credential",
            "details",
            "assessment",
            "criteria",
        }:
            continue
        entry = _oca_attr(attrs, pointer)
        if not entry:
            continue
        label = str(entry.get("label") or "").lower()
        if entry.get("missing") and ("uri" in label or label.endswith(" id")):
            continue
        section_map.setdefault(section_id, []).append(pointer)

    # Ensure headline-only sections (names already used in stats) still appear.
    for section_id, headlines in _OCA_SECTION_HEADLINES.items():
        if section_id in {
            "holder",
            "governance",
            "credential",
            "details",
            "assessment",
            "criteria",
        }:
            continue
        if section_id in section_map:
            continue
        if any(_oca_attr(attrs, pointer) for pointer in headlines):
            section_map[section_id] = []

    sections: list[dict[str, Any]] = []
    for section_id in _OCA_SECTION_DISPLAY_ORDER:
        pointers = section_map.pop(section_id, None)
        if pointers is None:
            continue
        card = _build_oca_section_card(section_id, pointers, attrs)
        if card:
            sections.append(card)
    for section_id, pointers in section_map.items():
        card = _build_oca_section_card(section_id, pointers, attrs)
        if card:
            sections.append(card)

    return {
        "scheme": _oca_display(scheme) if scheme else "",
        "title": title_text,
        "subtitle": subtitle_text,
        "assessment_title": assessment_title_text,
        "assessment_lead": _oca_display(assessment_lead)
        if assessment_lead and not assessment_lead.get("missing")
        else "",
        "valid_from": _oca_display(valid_from),
        "valid_from_label": str(valid_from.get("label") or "Valid from"),
        "assessment_date": _oca_display(assessment_date),
        "assessment_date_label": str(assessment_date.get("label") or "Permit Issuance Date"),
        "entity_label": "Permit",
        "conforms": conforms,
        "conforms_label": str(conforms_entry.get("label") or "Conformance"),
        "flow": flow,
        "stats": stats,
        "id_chips": id_chips,
        "sections": sections,
    }


def build_oca_template_context(
    vc: dict[str, Any],
    oca_bundle: dict[str, Any],
    language: str = "en",
) -> dict[str, Any]:
    """Build a template context document from an OCA bundle + unwrapped VC.

    Attribute keys are capture-base JSON Pointers (RFC 6901) from the VC root.
    Soft-resolve cannot distinguish a missing path from JSON ``null``; both are
    treated as missing (``raw`` / ``value`` null, ``missing`` true).
    """
    empty: dict[str, Any] = {
        "language": (language or "en").strip().lower() or "en",
        "capture_base": "",
        "order": [],
        "flagged": [],
        "attributes": {},
        "languages": [],
        "presentation": {},
        "error": "",
    }
    if not isinstance(vc, dict):
        empty["error"] = "credential document must be an object"
        return empty
    if not isinstance(oca_bundle, dict):
        empty["error"] = "OCA bundle must be an object"
        return empty
    attributes = oca_bundle.get("attributes")
    if not isinstance(attributes, dict) or not attributes:
        empty["error"] = "OCA bundle is missing attributes"
        return empty

    languages = oca_languages(oca_bundle) or ["en"]
    lang = (language or "en").strip().lower() or "en"
    if lang not in languages:
        lang = languages[0]

    labels = _overlay_map(
        oca_bundle,
        overlay_type="spec/overlays/label/1.0",
        language=lang,
        field="attribute_labels",
    )
    information = _overlay_map(
        oca_bundle,
        overlay_type="spec/overlays/information/1.0",
        language=lang,
        field="attribute_information",
    )
    flagged_list = [
        str(p)
        for p in (oca_bundle.get("flagged_attributes") or [])
        if isinstance(p, str) and str(p).strip()
    ]
    flagged_set = set(flagged_list)

    order: list[str] = []
    attr_map: dict[str, Any] = {}
    for pointer, attr_type in attributes.items():
        if not isinstance(pointer, str):
            continue
        order.append(pointer)
        resolved = soft_resolve_json_pointer(vc, pointer)
        missing = resolved is None
        raw = None if missing else resolved
        value = None if missing else format_oca_value(raw, attr_type=str(attr_type or ""))
        attr_map[pointer] = {
            "type": str(attr_type or "").strip() or "Text",
            "label": labels.get(pointer) or pointer,
            "information": information.get(pointer) or "",
            "raw": raw,
            "value": value,
            "flagged": pointer in flagged_set,
            "missing": missing,
        }

    flagged = [p for p in flagged_list if p in attr_map]
    presentation = build_oca_presentation(attr_map, order, flagged)
    presentation["entity_label"] = "Permis" if lang.startswith("fr") else "Permit"
    return {
        "language": lang,
        "capture_base": _overlay_capture_base(oca_bundle, language=lang),
        "order": order,
        "flagged": flagged,
        "attributes": attr_map,
        "languages": languages,
        "presentation": presentation,
        "error": "",
    }


def oca_fields_for_vc(
    vc: dict[str, Any],
    oca_bundle: dict[str, Any],
    language: str = "en",
) -> list[dict[str, Any]]:
    """Build labeled OCA attribute rows for ``vc`` (soft pointer misses → —).

    Thin projection of :func:`build_oca_template_context` for legacy callers.
    """
    context = build_oca_template_context(vc, oca_bundle, language)
    rows: list[dict[str, Any]] = []
    for pointer in context.get("order") or []:
        entry = (context.get("attributes") or {}).get(pointer)
        if not isinstance(entry, dict):
            continue
        missing = bool(entry.get("missing"))
        value = entry.get("value")
        rows.append(
            {
                "pointer": pointer,
                "label": str(entry.get("label") or pointer),
                "information": str(entry.get("information") or ""),
                "value": "—" if missing or value is None else str(value),
                "missing": missing,
                "flagged": bool(entry.get("flagged")),
            }
        )
    return rows


def view_debug_enabled(raw: str | bool | None) -> bool:
    """True for ``?debug=1`` / ``true`` / ``yes`` (case-insensitive)."""
    if isinstance(raw, bool):
        return raw
    value = str(raw or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def render_oca_box_html(
    context: dict[str, Any],
    *,
    page_url: str = "",
    debug: bool = False,
) -> str:
    """Render the OCA credential box from a template context document."""
    rendered_dt = datetime.now(timezone.utc)
    return templates.get_template("oca_box.html").render(
        language=str(context.get("language") or "en"),
        languages=context.get("languages") or [],
        capture_base=str(context.get("capture_base") or ""),
        order=context.get("order") or [],
        flagged=context.get("flagged") or [],
        attributes=context.get("attributes") or {},
        presentation=context.get("presentation") or {},
        page_url=(page_url or "").strip(),
        debug=bool(debug),
        rendered_at=rendered_dt.strftime("%d %b %Y, %H:%M:%S UTC"),
        rendered_at_iso=rendered_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def compose_view_oca_payload(
    raw_url: str, language: str = "en", *, debug: bool = False
) -> dict[str, Any]:
    """Fetch a credential and return an OCA ``context`` event payload (or error)."""
    credential_id = parse_credential_url(raw_url)
    if not credential_id:
        return {"type": "error", "message": _view_parse_error(raw_url)}

    parsed_cred = urlparse(raw_url)
    credential_url = (
        f"{parsed_cred.scheme}://{parsed_cred.netloc}/credentials/{credential_id}"
    )
    try:
        envelope = fetch_application_vc(credential_url)
        vc_jwt = extract_vc_jwt(envelope)
        try:
            vc = decode_jwt_payload(vc_jwt)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EnvelopeValidationError(
                "Extracted JWT payload is not valid base64url JSON"
            ) from exc
    except LookupError:
        return {"type": "error", "message": "No credential found for that URL."}
    except EnvelopeValidationError as exc:
        return {
            "type": "error",
            "message": f"Invalid EnvelopedVerifiableCredential: {exc}",
        }
    except Exception:
        settings.LOGGER.exception("View OCA: failed to fetch/unwrap %s", credential_url)
        return {
            "type": "error",
            "message": (
                "Could not load this credential as application/vc. "
                "Check the URL and try again."
            ),
        }

    record: dict[str, Any] | None = None
    try:
        found = MongoClient().find_one("CredentialRecord", {"id": credential_id})
        if isinstance(found, dict):
            record = found
    except Exception:
        settings.LOGGER.exception(
            "View OCA: optional Mongo lookup failed for %s", credential_id
        )

    fallback_type = (
        str((record or {}).get("type") or "").strip() or credential_type_from_vc(vc)
    )
    render_check = resolve_render_methods(vc, fallback_type=fallback_type)
    oca_bundle = render_check.get("bundle")
    if not isinstance(oca_bundle, dict):
        return {
            "type": "error",
            "message": (
                str(render_check.get("error") or "").strip()
                or "No OCA bundle is available for this credential, so it cannot be rendered yet."
            ),
        }

    languages = oca_languages(oca_bundle) or ["en"]
    lang = (language or "en").strip().lower() or "en"
    if lang not in languages:
        lang = languages[0]
    context = build_oca_template_context(vc, oca_bundle, lang)
    html = render_oca_box_html(context, page_url=credential_url, debug=debug)
    return {
        "type": "context",
        "url": credential_url,
        "language": context.get("language") or lang,
        "languages": context.get("languages") or languages,
        "capture_base": context.get("capture_base") or "",
        "html": html,
        # Decoded JWT payload (not the opaque application/vc envelope).
        "credential": vc,
    }


def oca_bundle_for_credential_type(credential_type: str) -> dict[str, Any] | None:
    """Load OCA for a configured credential type name."""
    cred_type = (credential_type or "").strip()
    if not cred_type:
        return None
    try:
        credential_version_for_type(cred_type)
        return load_oca_bundle(cred_type)
    except Exception:
        settings.LOGGER.exception("View: failed to load OCA for type %s", cred_type)
        return None


def render_method_entries(vc: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize ``renderMethod`` to a list of entry objects."""
    raw = (vc or {}).get("renderMethod")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def parse_same_origin_oca_url(url: str) -> tuple[str, str] | None:
    """Return ``(credential_type, version)`` for same-origin ``/templates/.../oca.json``."""
    return parse_oca_url(url, allow_remote=False)


def parse_oca_url(
    url: str, *, allow_remote: bool | None = None
) -> tuple[str, str] | None:
    """Return ``(credential_type, version)`` for ``/templates/{type}/{version}/oca.json``.

    Same-origin only unless ``allow_remote`` / ``VIEW_UNSAFE_MODE`` is enabled.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    remote = settings.VIEW_UNSAFE_MODE if allow_remote is None else allow_remote
    if not remote:
        origin = urlparse(publisher_origin())
        if (
            parsed.scheme != origin.scheme
            or parsed.netloc.lower() != (origin.netloc or "").lower()
        ):
            return None
    return parse_oca_templates_path(raw)


def parse_oca_templates_path(url: str) -> tuple[str, str] | None:
    """Return ``(credential_type, version)`` from a ``/templates/.../oca.json`` URL path."""
    raw = (url or "").strip()
    if not raw:
        return None
    path = (urlparse(raw).path or "").rstrip("/")
    prefix = "/templates/"
    if not path.startswith(prefix) or not path.endswith("/oca.json"):
        return None
    rest = path[len(prefix) : -len("/oca.json")]
    parts = [p for p in rest.split("/") if p]
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def fetch_oca_json(url: str) -> dict[str, Any]:
    """GET an OCA bundle JSON document from ``url``."""
    response = requests.get(
        url,
        headers={"Accept": "application/json"},
        timeout=_VIEW_FETCH_TIMEOUT_S,
    )
    if response.status_code == 404:
        raise LookupError("OCA bundle not found")
    if not response.ok:
        raise RuntimeError(f"OCA fetch failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("OCA response was not JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("OCA response was not a JSON object")
    return payload


def resolve_internal_oca_bundle(url: str) -> dict[str, Any] | None:
    """Load OCA from repo configs when ``url`` is a ``/templates/{type}/{ver}/oca.json`` path.

    Ignores host so published credentials that point at this publisher's template
    URL can be rendered from the local bundle (no HTTP self-fetch).
    """
    parsed = parse_oca_templates_path(url)
    if not parsed:
        return None
    cred_type, _version = parsed
    return oca_bundle_for_credential_type(cred_type)


def resolve_render_methods(
    vc: dict[str, Any],
    *,
    fallback_type: str = "",
) -> dict[str, Any]:
    """Look up ``renderMethod``, fetch OCA when present, else fall back by type."""
    methods = render_method_entries(vc)
    if not methods:
        bundle = oca_bundle_for_credential_type(
            (fallback_type or "").strip() or credential_type_from_vc(vc)
        )
        return {
            "present": False,
            "ok": bundle is not None,
            "error": ""
            if bundle is not None
            else "No renderMethod on credential and no OCA for type",
            "source": "credential_type" if bundle is not None else "",
            "bundle": bundle,
            "entries": [],
        }

    resolved: list[dict[str, Any]] = []
    bundle: dict[str, Any] | None = None
    errors: list[str] = []
    for method in methods:
        types = _as_string_list(method.get("type"))
        method_id = str(method.get("id") or "").strip()
        name = str(method.get("name") or "").strip()
        suite = str(method.get("renderSuite") or "").strip()
        digest = str(method.get("digestMultibase") or "").strip()
        row: dict[str, Any] = {
            "type": ", ".join(types) if types else str(method.get("type") or ""),
            "id": method_id,
            "name": name,
            "render_suite": suite,
            "digest_multibase": digest,
            "ok": False,
            "label": "",
            "error": "",
        }
        type_ok = (
            not types
            or "TemplateRenderMethod" in types
            or "RenderTemplate2024" in types
        )
        suite_ok = not suite or suite in {"oca-bundle", "oca-bundle-v2"}
        if not type_ok:
            row["error"] = f"Unsupported renderMethod type={types!r}"
            errors.append(row["error"])
            resolved.append(row)
            continue
        if not suite_ok:
            row["error"] = f"Unsupported renderSuite={suite!r}"
            errors.append(row["error"])
            resolved.append(row)
            continue
        if not method_id:
            row["error"] = "renderMethod is missing id"
            errors.append(row["error"])
            resolved.append(row)
            continue
        parsed = parse_oca_url(method_id)
        remote_oca = False
        if parsed is None and view_allows_remote() and is_http_url(method_id):
            # Unsafe mode: fetch any http(s) OCA JSON (path need not match templates/).
            remote_oca = True
            parsed = ("external", "oca")
        if parsed is None:
            row["error"] = (
                "renderMethod id must be a same-origin /templates/{type}/{version}/oca.json URL"
                if not view_allows_remote()
                else "renderMethod id must be an http(s) OCA URL"
            )
            errors.append(row["error"])
            resolved.append(row)
            continue
        try:
            fetched = resolve_internal_oca_bundle(method_id)
            from_internal = fetched is not None
            if fetched is None:
                fetched = fetch_oca_json(method_id)
            if digest:
                actual = generate_digest_multibase(fetched)
                if actual != digest:
                    raise ValueError(
                        f"OCA digestMultibase mismatch (expected {digest}, got {actual})"
                    )
            if bundle is None:
                bundle = fetched
            row["ok"] = True
            row["resolved"] = "local" if from_internal else "http"
            if from_internal:
                base = name or f"{parsed[0]} {parsed[1]} OCA"
                row["label"] = f"{base} (local)"
            elif remote_oca:
                row["label"] = name or "External OCA"
            else:
                row["label"] = name or f"{parsed[0]} {parsed[1]} OCA"
        except Exception as exc:
            settings.LOGGER.exception("View: renderMethod OCA fetch failed for %s", method_id)
            row["error"] = str(exc)
            errors.append(row["error"])
        resolved.append(row)

    if bundle is None and fallback_type:
        bundle = oca_bundle_for_credential_type(fallback_type)

    return {
        "present": True,
        "ok": bundle is not None and not errors,
        "error": "; ".join(errors),
        "source": "renderMethod" if any(r.get("ok") for r in resolved) else (
            "credential_type" if bundle is not None else ""
        ),
        "bundle": bundle,
        "entries": resolved,
    }


def validate_jsonld_contexts(vc: dict[str, Any]) -> dict[str, Any]:
    """Validate ``@context`` URLs against the offline bundle and expand JSON-LD."""
    raw_ctx = (vc or {}).get("@context")
    if raw_ctx is None:
        return {
            "ok": False,
            "error": 'document is missing required "@context"',
            "contexts": [],
            "digests": {},
            "rdf_nquads_length": 0,
        }

    items = raw_ctx if isinstance(raw_ctx, list) else [raw_ctx]
    contexts: list[dict[str, Any]] = []
    url_contexts: list[str] = []
    errors: list[str] = []

    for item in items:
        if isinstance(item, str):
            url = item.strip()
            url_contexts.append(url)
            scheme = urlparse(url).scheme
            bundled = url in CONTEXT_BUNDLE
            row = {
                "value": url,
                "kind": "url",
                "bundled": bundled,
                "ok": True,
                "error": "",
            }
            if scheme in ("http", "https") and not bundled:
                row["ok"] = False
                row["error"] = "context URL is not in the offline CONTEXT_BUNDLE"
                errors.append(f"{url}: not bundled")
            contexts.append(row)
        elif isinstance(item, dict):
            contexts.append(
                {
                    "value": "(inline)",
                    "kind": "inline",
                    "bundled": False,
                    "ok": True,
                    "error": "",
                }
            )
        else:
            msg = f"unsupported @context entry type={type(item).__name__}"
            errors.append(msg)
            contexts.append(
                {
                    "value": str(item),
                    "kind": "unknown",
                    "bundled": False,
                    "ok": False,
                    "error": msg,
                }
            )

    if _CREDENTIALS_V2_CONTEXT not in url_contexts:
        errors.append(
            f"@context must include {_CREDENTIALS_V2_CONTEXT}"
        )

    digests = bundled_context_digests_for_document(vc)
    rdf_len = 0
    expand_error = ""
    try:
        nquads = validate_untp_json_ld(vc)
        rdf_len = len(nquads)
    except UntpValidationError as exc:
        expand_error = str(exc)
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, UntpJsonLdRemoteContextError):
            expand_error = str(cause)
        elif cause is not None:
            expand_error = f"{expand_error}: {cause}"
        errors.append(expand_error)
    except Exception as exc:
        expand_error = f"JSON-LD expansion failed: {exc}"
        errors.append(expand_error)

    # Safe = every http(s) @context URL is in the offline bundle (no remote fetch).
    url_rows = [c for c in contexts if c.get("kind") == "url"]
    safe = bool(url_rows) and all(bool(c.get("bundled")) for c in url_rows)

    return {
        "ok": not errors,
        "safe": safe,
        "summary": "SAFE JSON-LD" if safe else "UNSAFE JSON-LD",
        "error": "; ".join(errors),
        "contexts": contexts,
        "digests": digests,
        "rdf_nquads_length": rdf_len,
    }


def oca_bundle_for_vc(vc: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve OCA from ``renderMethod`` when present, else VC type name."""
    result = resolve_render_methods(vc)
    if result.get("bundle") is not None:
        return result["bundle"]
    return oca_bundle_for_credential_type(credential_type_from_vc(vc))


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode a compact JWT payload without verifying the signature."""
    parts = (token or "").split(".")
    if len(parts) != 3 or not parts[1]:
        raise ValueError("Not a compact JWT")
    padded = parts[1] + ("=" * (-len(parts[1]) % 4))
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JWT payload is not an object")
    return payload


def decode_jwt_header(token: str) -> dict[str, Any]:
    """Decode a compact JWT header without verifying the signature."""
    parts = (token or "").split(".")
    if len(parts) != 3 or not parts[0]:
        raise ValueError("Not a compact JWT")
    padded = parts[0] + ("=" * (-len(parts[0]) % 4))
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    header = json.loads(raw.decode("utf-8"))
    if not isinstance(header, dict):
        raise ValueError("JWT header is not an object")
    return header


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


def data_uri_media_type(value: Any) -> str:
    """Return the media type from a ``data:[mediatype][;params],…`` URI, or ``\"\"``."""
    raw = str(value or "").strip()
    if not raw.lower().startswith("data:"):
        return ""
    rest = raw[5:]
    comma = rest.find(",")
    if comma < 0:
        return ""
    header = rest[:comma].strip()
    if not header:
        return ""
    return header.split(";", 1)[0].strip()


def compact_data_uri_media_type(value: Any) -> str:
    """Short label for chips: ``application/vc+jwt`` → ``vc+jwt``."""
    media_type = data_uri_media_type(value)
    if not media_type:
        return ""
    if "/" in media_type:
        type_name, subtype = media_type.split("/", 1)
        if type_name.lower() == "application" and subtype:
            return subtype
    return media_type


def validate_enveloped_credential(envelope: Any) -> str:
    """Validate a VCDM 2.0 ``EnvelopedVerifiableCredential`` and return its JWT.

    Checks ``@context``, ``type``, and ``id`` (``data:application/vc+jwt,…``),
    then confirms the embedded token is compact JWT-shaped. Does not verify
    the cryptographic signature.
    """
    if not isinstance(envelope, dict):
        raise EnvelopeValidationError("Envelope must be a JSON object")

    contexts = _as_string_list(envelope.get("@context"))
    if _CREDENTIALS_V2_CONTEXT not in contexts:
        raise EnvelopeValidationError(
            "Envelope @context must include "
            f"{_CREDENTIALS_V2_CONTEXT}"
        )

    types = _as_string_list(envelope.get("type"))
    if _ENVELOPED_VC_TYPE not in types:
        raise EnvelopeValidationError(
            "Envelope type must be EnvelopedVerifiableCredential"
        )

    env_id = envelope.get("id")
    if not isinstance(env_id, str) or not env_id.startswith(_VC_JWT_DATA_PREFIX):
        raise EnvelopeValidationError(
            "Envelope id must be a data:application/vc+jwt,... URI"
        )

    token = env_id[len(_VC_JWT_DATA_PREFIX) :].strip()
    if not token:
        raise EnvelopeValidationError("Envelope id is missing the vc+jwt token")

    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise EnvelopeValidationError(
            "Extracted token is not a compact JWT (header.payload.signature)"
        )

    try:
        header = decode_jwt_header(token)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EnvelopeValidationError(
            "Extracted JWT header is not valid base64url JSON"
        ) from exc

    typ = str(header.get("typ") or "").strip()
    if typ and typ.lower() not in ("vc+jwt", "application/vc+jwt"):
        raise EnvelopeValidationError(
            f"JWT typ must be vc+jwt when present (got {typ!r})"
        )

    return token


def extract_vc_jwt(envelope: Any) -> str:
    """Validate the envelope and return the compact ``vc+jwt`` string."""
    return validate_enveloped_credential(envelope)


def verify_vc_jwt(jwt_token: str) -> dict[str, Any]:
    """Verify ``jwt_token`` with Traction and normalize the result.

    Returns ``{ok, kid, error, details}`` where ``ok`` is True only when Traction
    reports ``valid: true``.
    """
    traction = TractionController()
    traction.authorize()
    result = traction.verify_jwt(jwt_token)
    valid = bool(result.get("valid"))
    kid = str(result.get("kid") or "").strip()
    error = str(result.get("error") or "").strip()
    return {
        "ok": valid,
        "kid": kid,
        "error": error if not valid else "",
        "details": result,
    }


def validate_vcdm20_payload(vc: dict[str, Any]) -> dict[str, Any]:
    """Validate JWT payload as a VCDM 2.0 credential via the publisher Credential model."""
    try:
        Vcdm20Credential.model_validate(vc)
        return {"ok": True, "error": ""}
    except PydanticValidationError as exc:
        first = exc.errors()[0] if exc.errors() else None
        if first:
            loc = ".".join(str(part) for part in first.get("loc") or ())
            msg = str(first.get("msg") or "validation failed")
            detail = f"{loc}: {msg}" if loc else msg
        else:
            detail = str(exc)
        return {"ok": False, "error": detail}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def validate_untp_payload(vc: dict[str, Any]) -> dict[str, Any]:
    """Detect UNTP artefact kind and run the full UNTP validation pipeline."""
    try:
        kind = detect_untp_artefact_kind(vc)
    except UntpValidationError as exc:
        return {
            "ok": False,
            "kind": "",
            "kind_label": "",
            "error": str(exc),
            "checks": {},
            "failed_check": "",
        }

    run = validate_untp_document_with_checks(vc, kind=kind)
    failed = first_failed_validation_check(run.checks)
    error = ""
    if not run.success:
        if run.raising is not None:
            error = str(run.raising)
            cause = run.raising.__cause__
            if cause is not None:
                error = f"{error}: {cause}"
        elif failed:
            error = str(failed[1].get("error") or failed[0])
        else:
            error = "UNTP validation failed"
    return {
        "ok": bool(run.success),
        "kind": kind.value,
        "kind_label": {
            UntpArtefactKind.DCC_CREDENTIAL: "DigitalConformityCredential",
            UntpArtefactKind.DCC_ATTESTATION: "ConformityAttestation",
        }.get(kind, kind.value),
        "error": error,
        "checks": run.checks,
        "failed_check": failed[0] if failed else "",
    }


def credential_status_entries(vc: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize ``credentialStatus`` to a list of entry objects."""
    raw = (vc or {}).get("credentialStatus")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def parse_same_origin_status_list_url(url: str) -> str | None:
    """Return status-list id if ``url`` is this publisher's ``/status-lists/{id}``."""
    return parse_status_list_url(url, allow_remote=False)


def parse_status_list_url(url: str, *, allow_remote: bool | None = None) -> str | None:
    """Return status-list id from a ``/status-lists/{id}`` URL.

    Same-origin only unless ``allow_remote`` / ``VIEW_UNSAFE_MODE`` is enabled.
    """
    return _parse_path_id_url(
        url,
        path_prefix="/status-lists/",
        require_same_origin=not (
            settings.VIEW_UNSAFE_MODE if allow_remote is None else allow_remote
        ),
    )


def load_status_list_credential(url: str) -> dict[str, Any]:
    """Fetch a status-list credential URL and return the unwrapped VC document."""
    document = fetch_application_vc(url)
    types = _as_string_list(document.get("type"))
    if _ENVELOPED_VC_TYPE in types or str(document.get("id") or "").startswith(
        _VC_JWT_DATA_PREFIX
    ):
        token = extract_vc_jwt(document)
        return decode_jwt_payload(token)
    return document


def _status_bit_label(*, purpose: str, bit_set: bool) -> str:
    purpose_key = (purpose or "").strip().lower()
    if purpose_key == "revocation":
        return "revoked" if bit_set else "not revoked"
    if purpose_key == "suspension":
        return "suspended" if bit_set else "not suspended"
    if purpose_key == "refresh":
        return "refresh available" if bit_set else "current"
    return "set" if bit_set else "unset"


def resolve_credential_statuses(vc: dict[str, Any]) -> dict[str, Any]:
    """Look up ``credentialStatus`` entries and evaluate bitstring status lists."""
    entries = credential_status_entries(vc)
    if not entries:
        return {
            "present": False,
            "ok": True,
            "error": "",
            "summary": "none",
            "entries": [],
        }

    resolved: list[dict[str, Any]] = []
    errors: list[str] = []
    for entry in entries:
        purpose = str(entry.get("statusPurpose") or "").strip()
        index_raw = entry.get("statusListIndex")
        status_url = str(entry.get("statusListCredential") or "").strip()
        entry_type = entry.get("type")
        types = _as_string_list(entry_type)
        row: dict[str, Any] = {
            "purpose": purpose,
            "index": index_raw,
            "status_list": status_url,
            "type": types[0] if types else str(entry_type or ""),
            "bit_set": None,
            "label": "",
            "error": "",
            "ok": False,
        }
        if "BitstringStatusListEntry" not in types and types:
            row["error"] = f"Unsupported credentialStatus type={types!r}"
            errors.append(row["error"])
            resolved.append(row)
            continue
        if index_raw is None or status_url == "":
            row["error"] = "credentialStatus entry is missing index or statusListCredential"
            errors.append(row["error"])
            resolved.append(row)
            continue
        status_ok = parse_status_list_url(status_url) is not None
        if not status_ok and view_allows_remote() and is_http_url(status_url):
            # Unsafe mode: any http(s) status-list credential URL is fetchable.
            status_ok = True
        if not status_ok:
            row["error"] = (
                "statusListCredential must be a same-origin /status-lists/{id} URL"
                if not view_allows_remote()
                else "statusListCredential must be an http(s) URL"
            )
            errors.append(row["error"])
            resolved.append(row)
            continue
        try:
            index = int(index_raw)
        except (TypeError, ValueError):
            row["error"] = f"Invalid statusListIndex: {index_raw!r}"
            errors.append(row["error"])
            resolved.append(row)
            continue
        try:
            status_vc = load_status_list_credential(status_url)
            subject = status_vc.get("credentialSubject")
            if not isinstance(subject, dict):
                raise ValueError("status list credentialSubject missing")
            encoded = subject.get("encodedList")
            if not isinstance(encoded, str) or not encoded.strip():
                raise ValueError("status list encodedList missing")
            bits = BitstringStatusList().expand(encoded)
            if index < 0 or index >= len(bits):
                raise ValueError(
                    f"statusListIndex {index} out of range for list length {len(bits)}"
                )
            bit_set = bits[index] == "1"
            row["bit_set"] = bit_set
            row["label"] = _status_bit_label(purpose=purpose, bit_set=bit_set)
            row["ok"] = True
        except (LookupError, EnvelopeValidationError, BitstringStatusListError, ValueError) as exc:
            row["error"] = str(exc)
            errors.append(row["error"])
        except Exception as exc:
            settings.LOGGER.exception(
                "View: status list resolve failed for %s", status_url
            )
            row["error"] = f"Could not resolve status list: {exc}"
            errors.append(row["error"])
        resolved.append(row)

    # Prefer live bitstring outcomes for a compact summary.
    summary = "active"
    found_adverse = False
    any_failed = False
    for row in resolved:
        if not row.get("ok"):
            any_failed = True
            continue
        purpose = str(row.get("purpose") or "").lower()
        if purpose == "revocation" and row.get("bit_set"):
            summary = "revoked"
            found_adverse = True
            break
        if purpose == "suspension" and row.get("bit_set"):
            summary = "suspended"
            found_adverse = True
    if not found_adverse and any_failed:
        summary = "unknown"

    return {
        "present": True,
        "ok": not errors,
        "error": "; ".join(errors),
        "summary": summary,
        "entries": resolved,
    }


def unwrap_enveloped_vc(envelope: Any) -> dict[str, Any]:
    """Validate the envelope, extract the JWT, and return its payload object."""
    token = extract_vc_jwt(envelope)
    try:
        return decode_jwt_payload(token)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EnvelopeValidationError(
            "Extracted JWT payload is not valid base64url JSON"
        ) from exc


def fetch_application_vc(url: str) -> dict[str, Any]:
    """GET ``url`` with ``Accept: application/vc`` and return the JSON body."""
    response = requests.get(
        url,
        headers={"Accept": "application/vc"},
        timeout=_VIEW_FETCH_TIMEOUT_S,
    )
    if response.status_code == 404:
        raise LookupError("Credential not found")
    if not response.ok:
        raise RuntimeError(
            f"Credential fetch failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Credential response was not JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Credential response was not a JSON object")
    return payload


def credential_type_from_vc(vc: dict[str, Any]) -> str:
    """Pick the primary credential type (skip generic VC envelope types)."""
    raw = vc.get("type")
    if isinstance(raw, str):
        types = [raw]
    elif isinstance(raw, list):
        types = [str(t) for t in raw if t]
    else:
        types = []
    skip = {"VerifiableCredential", "EnvelopedVerifiableCredential"}
    for entry in types:
        if entry not in skip:
            return entry
    return ""


def oca_bundle_for_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Load OCA for a credential record (renderMethod id when present, else type)."""
    vc = record.get("vc")
    if isinstance(vc, dict):
        bundle = oca_bundle_for_vc(vc)
        if bundle is not None:
            return bundle
    return oca_bundle_for_credential_type(str(record.get("type") or ""))


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


def facility_from_record(record: dict[str, Any]) -> tuple[str, str]:
    """Return ``(facility_name, facility_registered_id)`` from the issued VC."""
    vc = record.get("vc")
    if not isinstance(vc, dict):
        return "", ""
    subject = vc.get("credentialSubject")
    if isinstance(subject, list):
        subject = subject[0] if subject else None
    if not isinstance(subject, dict):
        return "", ""
    assessments = subject.get("conformityAssessment")
    if isinstance(assessments, dict):
        assessments = [assessments]
    if not isinstance(assessments, list) or not assessments:
        return "", ""
    assessment = assessments[0]
    if not isinstance(assessment, dict):
        return "", ""
    facilities = assessment.get("assessedFacility")
    if isinstance(facilities, dict):
        facilities = [facilities]
    if not isinstance(facilities, list) or not facilities:
        return "", ""
    entry = facilities[0]
    if not isinstance(entry, dict):
        return "", ""
    facility = entry.get("facility")
    if not isinstance(facility, dict):
        return "", ""
    name = str(facility.get("name") or "").strip()
    registered_id = str(facility.get("registeredId") or "").strip()
    return name, registered_id


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


def did_method_prefix(did: str) -> str:
    """Return ``did:method`` from a DID string (e.g. ``did:key``), or ``\"\"``."""
    value = (did or "").strip()
    if not value.lower().startswith("did:"):
        return ""
    parts = value.split(":", 2)
    method = (parts[1] if len(parts) > 1 else "").strip()
    if not method:
        return ""
    return f"did:{method}"


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
        cred_type = str(record.get("type") or "").strip()
        # Specific iteration always deep-links by credential id URL.
        view_url = credential_view_url(url)
        # Latest-active shortcut when the triple is known (same as /credentials/refresh).
        latest_view_url = (
            credential_ref_view_url(cred_type, cardinality, entity)
            if cred_type and cardinality and entity
            else view_url
        )
        created_raw = proof_created_raw(record)
        download_name = credential_download_filename(record)
        entity_name = entity_name_from_record(record)
        facility_name, facility_id = facility_from_record(record)
        issuer_name, issuer_did = issuer_from_record(record)
        iteration = {
            "id": cred_id,
            "type": cred_type,
            "entity_id": entity,
            "entity_name": entity_name,
            "facility_id": facility_id,
            "facility_name": facility_name,
            "cardinality_id": cardinality,
            "issuer_name": issuer_name,
            "issuer_did": issuer_did,
            "issuer_resolve_url": issuer_resolve_url(issuer_did),
            "revocation": bool(record.get("revocation")),
            "suspension": bool(record.get("suspension")),
            "refresh": bool(record.get("refresh")),
            "status": _status_label(record),
            "url": url,
            "view_url": view_url,
            "latest_view_url": latest_view_url,
            "download_url": download_url,
            "download_name": download_name,
            "created": created_raw,
            "created_display": format_proof_created(created_raw),
        }

        if key not in groups:
            groups[key] = {
                "entity_id": entity or iteration["entity_id"],
                "entity_name": entity_name,
                "facility_id": facility_id,
                "facility_name": facility_name,
                "cardinality_id": cardinality or iteration["cardinality_id"],
                "issuer_name": issuer_name,
                "issuer_did": issuer_did,
                "issuer_resolve_url": iteration["issuer_resolve_url"],
                "type": iteration["type"],
                "status": iteration["status"],
                "url": url,
                "view_url": latest_view_url,
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
        group["view_url"] = face.get("latest_view_url") or face["view_url"]
        group["download_url"] = face["download_url"]
        group["download_name"] = face["download_name"]
        group["entity_name"] = face.get("entity_name") or group.get("entity_name") or ""
        group["facility_id"] = face.get("facility_id") or group.get("facility_id") or ""
        group["facility_name"] = (
            face.get("facility_name") or group.get("facility_name") or ""
        )
        group["issuer_name"] = face.get("issuer_name") or group.get("issuer_name") or ""
        group["issuer_did"] = face.get("issuer_did") or group.get("issuer_did") or ""
        group["issuer_resolve_url"] = (
            face.get("issuer_resolve_url") or group.get("issuer_resolve_url") or ""
        )
        group["iteration_count"] = len(iterations)
        result.append(group)
    return result


def _proof_entries(vc: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (vc or {}).get("proof")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def validate_vc_proof(vc: dict[str, Any]) -> dict[str, Any]:
    """Soft-check Data Integrity ``proof`` object(s) on the unwrapped VC."""
    proofs = _proof_entries(vc)
    if not proofs:
        return {
            "ok": False,
            "summary": "missing",
            "error": "Credential has no proof object",
            "proofs": [],
        }
    summarized: list[dict[str, Any]] = []
    errors: list[str] = []
    for proof in proofs:
        types = _as_string_list(proof.get("type"))
        cryptosuite = str(proof.get("cryptosuite") or "").strip()
        vm = str(proof.get("verificationMethod") or "").strip()
        created = str(proof.get("created") or "").strip()
        has_value = bool(
            str(proof.get("proofValue") or proof.get("jws") or "").strip()
        )
        row = {
            "type": ", ".join(types) if types else str(proof.get("type") or ""),
            "cryptosuite": cryptosuite,
            "verification_method": vm,
            "created": created,
            "ok": bool(types and vm and has_value),
        }
        if not row["ok"]:
            errors.append(
                "proof is missing type, verificationMethod, or proofValue/jws"
            )
        summarized.append(row)
    ok = all(row["ok"] for row in summarized)
    first = summarized[0]
    summary = first.get("cryptosuite") or first.get("type") or ("ok" if ok else "invalid")
    return {
        "ok": ok,
        "summary": summary if ok else "invalid",
        "error": "; ".join(errors),
        "proofs": summarized,
    }


def validate_vc_issuer(vc: dict[str, Any]) -> dict[str, Any]:
    """Soft-check that ``issuer`` has a DID (and optional name)."""
    name, did = issuer_from_record({"vc": vc})
    method = did_method_prefix(did)
    if not did:
        return {
            "ok": False,
            "summary": "missing",
            "error": "issuer id is missing",
            "name": name,
            "did": "",
            "method": "",
            "resolve_url": "",
        }
    ok = bool(method)
    return {
        "ok": ok,
        "summary": method or "not a DID",
        "error": "" if ok else "issuer id is not a DID",
        "name": name,
        "did": did,
        "method": method,
        "resolve_url": issuer_resolve_url(did),
    }


def _parse_vc_datetime(raw: str) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def validate_vc_validity(vc: dict[str, Any]) -> dict[str, Any]:
    """Soft-check ``validFrom`` / ``validUntil`` against the current UTC time."""
    valid_from_raw = str(vc.get("validFrom") or "").strip()
    valid_until_raw = str(vc.get("validUntil") or "").strip()
    valid_from = _parse_vc_datetime(valid_from_raw)
    valid_until = _parse_vc_datetime(valid_until_raw)
    now = datetime.now(timezone.utc)
    errors: list[str] = []
    if not valid_from_raw:
        errors.append("validFrom is missing")
    elif valid_from is None:
        errors.append(f"validFrom is not a valid timestamp: {valid_from_raw!r}")
    elif valid_from > now:
        errors.append("credential is not yet valid (validFrom is in the future)")
    if valid_until_raw:
        if valid_until is None:
            errors.append(f"validUntil is not a valid timestamp: {valid_until_raw!r}")
        elif valid_until < now:
            errors.append("credential has expired (validUntil is in the past)")
    ok = not errors
    if ok:
        summary = "active"
    elif valid_until is not None and valid_until < now:
        summary = "expired"
    elif valid_from is not None and valid_from > now:
        summary = "not yet valid"
    else:
        summary = "invalid"
    valid_from_display = (
        format_proof_created(valid_from_raw) if valid_from_raw else "—"
    )
    valid_until_display = (
        format_proof_created(valid_until_raw) if valid_until_raw else "open"
    )
    period_display = f"{valid_from_display} – {valid_until_display}"
    return {
        "ok": ok,
        "summary": summary,
        "period_display": period_display,
        "error": "; ".join(errors),
        "valid_from": valid_from_raw,
        "valid_until": valid_until_raw,
        "valid_from_display": valid_from_display,
        "valid_until_display": valid_until_display,
    }


VIEW_PIPELINE_STEPS: tuple[tuple[str, str], ...] = (
    ("envelope", "Envelope + JWT"),
    ("vcdm", "Validating VCDM 2.0"),
    ("untp", "Validating UNTP 0.7.0"),
    ("jsonld", "Validating JSON-LD"),
    ("proof", "Checking proof"),
    ("issuer", "Checking issuer"),
    ("validity", "Checking validity"),
    ("status", "Resolving credentialStatus"),
    ("render", "Loading renderMethod / OCA"),
)


def _view_progress(index: int) -> dict[str, Any]:
    step, label = VIEW_PIPELINE_STEPS[index]
    return {
        "type": "progress",
        "step": step,
        "label": label,
        "index": index + 1,
        "total": len(VIEW_PIPELINE_STEPS),
    }


def _view_parse_error(raw_url: str) -> str:
    if view_allows_remote():
        return "Enter an http(s) /credentials/{id} URL."
    return (
        "This viewer only opens credentials published by this service "
        "(same-origin /credentials/{id} URLs)."
    )


def iter_view_pipeline(
    url: str, lang: str = "en", *, debug: bool = False
) -> Iterator[dict[str, Any]]:
    """Run the credential view pipeline, yielding SSE-ready event dicts."""
    raw_url = (url or "").strip()
    language = (lang or "en").strip().lower() or "en"
    if not raw_url:
        yield {"type": "error", "message": "Provide a credential URL."}
        return

    credential_id = parse_credential_url(raw_url)
    if not credential_id:
        yield {"type": "error", "message": _view_parse_error(raw_url)}
        return

    parsed_cred = urlparse(raw_url)
    credential_url = (
        f"{parsed_cred.scheme}://{parsed_cred.netloc}/credentials/{credential_id}"
    )

    yield _view_progress(0)
    vc_jwt = ""
    try:
        envelope = fetch_application_vc(credential_url)
        vc_jwt = extract_vc_jwt(envelope)
        try:
            vc = decode_jwt_payload(vc_jwt)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EnvelopeValidationError(
                "Extracted JWT payload is not valid base64url JSON"
            ) from exc
    except LookupError:
        yield {"type": "error", "message": "No credential found for that URL."}
        return
    except EnvelopeValidationError as exc:
        yield {
            "type": "error",
            "message": f"Invalid EnvelopedVerifiableCredential: {exc}",
        }
        return
    except Exception:
        settings.LOGGER.exception("View: failed to fetch/unwrap %s", credential_url)
        yield {
            "type": "error",
            "message": (
                "Could not load this credential as application/vc. "
                "Check the URL and try again."
            ),
        }
        return

    view_record: dict[str, Any] = {"vc": vc}
    issuer_name, issuer_did = issuer_from_record(view_record)
    valid_from = str(vc.get("validFrom") or "").strip()
    credential_type = credential_type_from_vc(vc)
    yield {
        "type": "meta",
        "credential_url": credential_url,
        "download_url": credential_download_url(credential_url),
        "download_name": credential_download_filename(
            {
                "type": credential_type,
                "cardinality_id": "",
                "entity_id": "",
                "vc": vc,
            }
        ),
        "view_url": credential_view_url(credential_url),
        "issuer_name": issuer_name,
        "issuer_did": issuer_did,
        "issuer_resolve_url": issuer_resolve_url(issuer_did),
        "entity_name": entity_name_from_record(view_record),
        "credential_type": credential_type,
        "credential_name": str(vc.get("name") or "").strip(),
        "valid_from": valid_from,
        "valid_from_display": format_proof_created(valid_from) if valid_from else "—",
        "status": "",
    }

    jwt_verified: bool | None = None
    jwt_kid = ""
    jwt_verify_error = ""
    try:
        verification = verify_vc_jwt(vc_jwt)
        jwt_verified = bool(verification.get("ok"))
        jwt_kid = str(verification.get("kid") or "").strip()
        if not jwt_verified:
            jwt_verify_error = (
                str(verification.get("error") or "").strip()
                or "JWT signature verification failed"
            )
    except Exception:
        settings.LOGGER.exception("View: JWT verification failed for %s", credential_url)
        jwt_verified = None
        jwt_verify_error = "Could not verify the JWT with Traction."

    if jwt_verified is True:
        jwt_summary = "JWT verified"
    elif jwt_verified is False:
        jwt_summary = "JWT invalid"
    else:
        jwt_summary = "JWT unverified"
    envelope_media_type = data_uri_media_type(
        envelope.get("id") if isinstance(envelope, dict) else ""
    )
    envelope_media_label = compact_data_uri_media_type(
        envelope.get("id") if isinstance(envelope, dict) else ""
    )
    yield {
        "type": "check",
        "id": "envelope",
        "ok": jwt_verified,
        "summary": envelope_media_label or envelope_media_type or jwt_summary,
        "media_type": envelope_media_type,
        "verification": jwt_summary,
        "kid": jwt_kid,
        "error": jwt_verify_error,
    }

    yield _view_progress(1)
    vcdm = validate_vcdm20_payload(vc)
    vcdm_ok = bool(vcdm.get("ok"))
    yield {
        "type": "check",
        "id": "vcdm",
        "ok": vcdm_ok,
        "summary": "valid" if vcdm_ok else "invalid",
        "error": str(vcdm.get("error") or "").strip(),
    }

    yield _view_progress(2)
    untp = validate_untp_payload(vc)
    untp_ok = bool(untp.get("ok"))
    untp_checks = untp.get("checks") if isinstance(untp.get("checks"), dict) else {}
    yield {
        "type": "check",
        "id": "untp",
        "ok": untp_ok,
        "summary": "valid" if untp_ok else "invalid",
        "kind": str(untp.get("kind") or "").strip(),
        "kind_label": str(untp.get("kind_label") or "").strip(),
        "error": str(untp.get("error") or "").strip(),
        "failed_check": str(untp.get("failed_check") or "").strip(),
        "checks": untp_checks,
    }

    yield _view_progress(3)
    jsonld_check = validate_jsonld_contexts(vc)
    yield {
        "type": "check",
        "id": "jsonld",
        "ok": bool(jsonld_check.get("ok")),
        "safe": bool(jsonld_check.get("safe")),
        "summary": str(jsonld_check.get("summary") or "UNSAFE JSON-LD"),
        "error": str(jsonld_check.get("error") or "").strip(),
        "contexts": jsonld_check.get("contexts") or [],
        "digests": jsonld_check.get("digests") or {},
        "rdf_nquads_length": int(jsonld_check.get("rdf_nquads_length") or 0),
    }

    yield _view_progress(4)
    proof_check = validate_vc_proof(vc)
    yield {
        "type": "check",
        "id": "proof",
        "ok": bool(proof_check.get("ok")),
        "summary": str(proof_check.get("summary") or ""),
        "error": str(proof_check.get("error") or "").strip(),
        "proofs": proof_check.get("proofs") or [],
    }

    yield _view_progress(5)
    issuer_check = validate_vc_issuer(vc)
    yield {
        "type": "check",
        "id": "issuer",
        "ok": bool(issuer_check.get("ok")),
        "summary": str(issuer_check.get("summary") or ""),
        "error": str(issuer_check.get("error") or "").strip(),
        "name": str(issuer_check.get("name") or ""),
        "did": str(issuer_check.get("did") or ""),
        "method": str(issuer_check.get("method") or ""),
        "resolve_url": str(issuer_check.get("resolve_url") or ""),
    }

    yield _view_progress(6)
    validity_check = validate_vc_validity(vc)
    yield {
        "type": "check",
        "id": "validity",
        "ok": bool(validity_check.get("ok")),
        "summary": str(validity_check.get("summary") or ""),
        "period_display": str(validity_check.get("period_display") or ""),
        "error": str(validity_check.get("error") or "").strip(),
        "valid_from": str(validity_check.get("valid_from") or ""),
        "valid_until": str(validity_check.get("valid_until") or ""),
        "valid_from_display": str(validity_check.get("valid_from_display") or ""),
        "valid_until_display": str(validity_check.get("valid_until_display") or ""),
    }

    yield _view_progress(7)
    status_check = resolve_credential_statuses(vc)
    yield {
        "type": "check",
        "id": "credentialStatus",
        "ok": bool(status_check.get("ok")),
        "summary": str(status_check.get("summary") or ""),
        "present": bool(status_check.get("present")),
        "error": str(status_check.get("error") or "").strip(),
        "entries": status_check.get("entries") or [],
    }

    yield _view_progress(8)
    record: dict[str, Any] | None = None
    try:
        found = MongoClient().find_one("CredentialRecord", {"id": credential_id})
        if isinstance(found, dict):
            record = found
    except Exception:
        settings.LOGGER.exception(
            "View: optional Mongo lookup failed for %s", credential_id
        )

    fallback_type = (
        str((record or {}).get("type") or "").strip() or credential_type_from_vc(vc)
    )
    render_check = resolve_render_methods(vc, fallback_type=fallback_type)
    oca_bundle = render_check.get("bundle")
    render_entries = render_check.get("entries") or []
    render_suite = ""
    for entry in render_entries:
        if not isinstance(entry, dict):
            continue
        suite = str(entry.get("render_suite") or "").strip()
        if suite and entry.get("ok"):
            render_suite = suite
            break
    if not render_suite:
        for entry in render_entries:
            if not isinstance(entry, dict):
                continue
            suite = str(entry.get("render_suite") or "").strip()
            if suite:
                render_suite = suite
                break
    if render_check.get("present") and render_check.get("ok"):
        render_summary = render_suite or "loaded"
    elif not render_check.get("present"):
        render_summary = "fallback" if render_check.get("source") else "none"
    else:
        render_summary = render_suite or "error"
    yield {
        "type": "check",
        "id": "renderMethod",
        "ok": bool(render_check.get("ok")),
        "summary": render_summary,
        "render_suite": render_suite,
        "present": bool(render_check.get("present")),
        "source": str(render_check.get("source") or ""),
        "error": str(render_check.get("error") or "").strip(),
        "entries": render_entries,
    }

    view_record = {"vc": vc, **(record or {})}
    issuer_name, issuer_did = issuer_from_record(view_record)
    status_label = (
        str(status_check.get("summary") or "")
        if status_check.get("present")
        else (_status_label(record) if record else "")
    )
    yield {
        "type": "meta",
        "credential_url": credential_url,
        "download_url": credential_download_url(credential_url),
        "download_name": credential_download_filename(
            record
            or {
                "type": fallback_type,
                "cardinality_id": "",
                "entity_id": "",
                "vc": vc,
            }
        ),
        "view_url": credential_view_url(credential_url),
        "issuer_name": issuer_name,
        "issuer_did": issuer_did,
        "issuer_resolve_url": issuer_resolve_url(issuer_did),
        "entity_name": entity_name_from_record(view_record),
        "credential_type": fallback_type,
        "credential_name": str(vc.get("name") or "").strip(),
        "valid_from": valid_from,
        "valid_from_display": format_proof_created(valid_from) if valid_from else "—",
        "status": status_label,
    }

    if not isinstance(oca_bundle, dict):
        yield {
            "type": "error",
            "message": (
                str(render_check.get("error") or "").strip()
                or "No OCA bundle is available for this credential, so it cannot be rendered yet."
            ),
        }
        return

    languages = oca_languages(oca_bundle) or ["en"]
    if language not in languages:
        language = languages[0]
    context = build_oca_template_context(vc, oca_bundle, language)
    html = render_oca_box_html(context, page_url=credential_url, debug=debug)
    yield {
        "type": "context",
        "url": credential_url,
        "language": context.get("language") or language,
        "languages": context.get("languages") or languages,
        "capture_base": context.get("capture_base") or "",
        "html": html,
        # Decoded JWT payload (not the opaque application/vc envelope).
        "credential": vc,
    }
    yield {"type": "done"}


def _view_shell_context(
    *,
    url: str = "",
    credential: str = "",
    lang: str = "en",
    welcome: bool = False,
    loading: bool = False,
    error: str = "",
    debug: bool = False,
) -> dict[str, Any]:
    stream = ""
    if loading and url:
        stream = (
            f"/view/stream?url={quote(url, safe='')}&lang={quote(lang, safe='')}"
        )
        if debug:
            stream += "&debug=1"
    return {
        **_branding(),
        "url": url,
        "credential": credential,
        "lang": lang,
        "welcome": welcome,
        "loading": loading,
        "unsafe_mode": view_allows_remote(),
        "error": error,
        "debug": debug,
        "stream_url": stream,
        "pipeline_steps": [
            {"id": step, "label": label} for step, label in VIEW_PIPELINE_STEPS
        ],
    }


_VIEW_STREAM_SENTINEL = object()


def _next_view_event(iterator: Iterator[dict[str, Any]]) -> Any:
    try:
        return next(iterator)
    except StopIteration:
        return _VIEW_STREAM_SENTINEL


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request):
    return templates.TemplateResponse(
        request,
        "landing.html",
        _branding(),
    )


@router.get("/view", response_class=HTMLResponse, include_in_schema=False)
async def view_credential(
    request: Request,
    url: str = "",
    credential: str = "",
    lang: str = "en",
    debug: str = "",
):
    """OCA-labeled human view of a published credential.

    In safe mode (default), bare ``/view`` redirects to Discovery — open
    credentials from a Discovery link or other deep link. With
    ``VIEW_UNSAFE_MODE=true``, bare ``/view`` shows a resolver form that
    accepts credential URLs (including remote hosts).

    With a valid ``url`` or ``credential`` (``type:cardinality:entity``),
    returns a loading shell immediately; the browser streams pipeline results
    from ``GET /view/stream``. Parse errors are still server-rendered.

    ``credential`` resolves the latest active publication for that triple
    (same semantics as ``GET /credentials/refresh``).

    Pass ``?debug=1`` to include the technical OCA attribute dump in the document.
    """
    language = (lang or "en").strip().lower() or "en"
    debug_on = view_debug_enabled(debug)
    target_url, error = resolve_view_target(url=url, credential=credential)

    if error:
        return templates.TemplateResponse(
            request,
            "view.html",
            _view_shell_context(
                url=(url or "").strip(),
                credential=(credential or "").strip(),
                lang=language,
                error=error,
                debug=debug_on,
            ),
        )

    if not target_url:
        if view_allows_remote():
            return templates.TemplateResponse(
                request,
                "view.html",
                _view_shell_context(welcome=True, lang=language, debug=debug_on),
            )
        # Safe mode: no free-form URL entry — browse Discovery instead.
        return RedirectResponse(url="/discovery", status_code=302)

    return templates.TemplateResponse(
        request,
        "view.html",
        _view_shell_context(
            url=target_url, lang=language, loading=True, debug=debug_on
        ),
    )


@router.get("/view/stream", include_in_schema=False)
async def view_credential_stream(
    url: str = "",
    credential: str = "",
    lang: str = "en",
    debug: str = "",
):
    """Server-Sent Events stream of view-pipeline progress and check results."""
    language = (lang or "en").strip().lower() or "en"
    target_url, error = resolve_view_target(url=url, credential=credential)
    if error:
        iterator = iter([{"type": "error", "message": error}])
    elif not target_url:
        iterator = iter([{"type": "error", "message": "Provide a credential URL or credential=type:cardinality:entity."}])
    else:
        iterator = iter_view_pipeline(
            target_url, language, debug=view_debug_enabled(debug)
        )

    async def event_publisher():
        loop = asyncio.get_running_loop()
        while True:
            event = await loop.run_in_executor(None, _next_view_event, iterator)
            if event is _VIEW_STREAM_SENTINEL:
                break
            payload = json.dumps(event, ensure_ascii=False, default=str)
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        event_publisher(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/view/oca", include_in_schema=False)
async def view_oca_fragment(
    url: str = "",
    credential: str = "",
    lang: str = "en",
    debug: str = "",
):
    """JSON OCA document fragment for language switching without a full page reload."""
    target_url, error = resolve_view_target(url=url, credential=credential)
    if error or not target_url:
        return JSONResponse(
            {
                "type": "error",
                "message": error
                or "Provide a credential URL or credential=type:cardinality:entity.",
            },
            status_code=400,
        )
    payload = compose_view_oca_payload(
        target_url,
        (lang or "en").strip().lower() or "en",
        debug=view_debug_enabled(debug),
    )
    status = 200 if payload.get("type") == "context" else 400
    return JSONResponse(payload, status_code=status)


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
