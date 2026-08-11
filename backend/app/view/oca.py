"""OCA presentation build and HTML render helpers for /view."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi.templating import Jinja2Templates

from app.discovery.groups import issuer_resolve_url
from app.plugins.mongodb import MongoClient
from app.repo_configs.loader import (
    credential_version_for_type,
    load_oca_bundle,
)
from app.utils import generate_digest_multibase
from app.view.checks import (
    EnvelopeValidationError,
    _as_string_list,
    credential_type_from_vc,
    decode_jwt_payload,
    extract_vc_jwt,
)
from app.view.fetch import (
    fetch_application_vc,
    fetch_oca_json,
    parse_credential_url,
    parse_oca_url,
    resolve_internal_oca_bundle,
    view_allows_remote,
)
from app.view.refs import _view_parse_error
from config import settings

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_PERMIT_DESC_SUFFIX_RE = re.compile(r"\s*\(permit\s+[^)]+\)\.?\s*$", re.IGNORECASE)


_PERMIT_NAME_EMDASH_RE = re.compile(r"\s+[—–]\s+.+$")


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


_OCA_HERO_FIELD_PICKS: dict[str, tuple[str, ...]] = {
    "scheme": ("/credentialSubject/referenceScheme/name",),
    "title": ("/credentialSubject/name", "/name"),
    "subtitle": ("/credentialSubject/description", "/description"),
    "assessment_title": ("/credentialSubject/conformityAssessment/0/name",),
    "assessment_lead": ("/credentialSubject/conformityAssessment/0/description",),
    "valid_from": ("/validFrom",),
    "assessment_date": ("/credentialSubject/conformityAssessment/0/assessmentDate",),
    "issuer_name": ("/issuer/name", "/issuer/id"),
    "issuer_id": ("/issuer/id",),
}

_OCA_FLOW_FIELD_PICKS: dict[str, tuple[str, ...]] = {
    "assessment": ("/credentialSubject/conformityAssessment/0/registeredId",),
    "organisation": (
        "/credentialSubject/conformityAssessment/0/assessedOrganisation/registeredId",
        "/credentialSubject/issuedToParty/registeredId",
        "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
        "/credentialSubject/issuedToParty/name",
    ),
    "facility": (
        "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
        "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
    ),
    "product": (
        "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/registeredId",
        "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name",
    ),
}

_OCA_ENTITY_CHIP_PICKS: tuple[dict[str, Any], ...] = (
    {
        "label_from": (
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
        ),
        "default_label": "Mining Site",
        "id": (
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
        ),
        "name": (
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
        ),
    },
    {
        "label_from": ("/credentialSubject/issuedToParty/name",),
        "default_label": "Permittee",
        "id": (
            "/credentialSubject/issuedToParty/registeredId",
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/registeredId",
        ),
        "name": (
            "/credentialSubject/issuedToParty/name",
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
        ),
    },
    {
        "label_from": ("/credentialSubject/conformityAssessment/0/registeredId",),
        "default_label": "Permit number",
        "id": ("/credentialSubject/conformityAssessment/0/registeredId",),
        "name": (),
        "name_override": "Mines Act Permit",
        "information_from_id": True,
    },
)


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

    hero = {key: pick(*pointers) for key, pointers in _OCA_HERO_FIELD_PICKS.items()}
    scheme = hero["scheme"]
    title = hero["title"]
    subtitle = hero["subtitle"]
    assessment_title = hero["assessment_title"]
    assessment_lead = hero["assessment_lead"]
    valid_from = hero["valid_from"]
    assessment_date = hero["assessment_date"]
    conforms_entry = _oca_attr(
        attrs, "/credentialSubject/conformityAssessment/0/conformance"
    )
    conforms_raw = conforms_entry.get("raw")
    conforms: bool | None
    if conforms_entry.get("missing") or conforms_raw is None:
        conforms = None
    else:
        conforms = bool(conforms_raw)

    issuer_name = hero["issuer_name"]
    issuer_id_entry = hero["issuer_id"]
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
        **{
            key: _oca_display(pick(*pointers))
            for key, pointers in _OCA_FLOW_FIELD_PICKS.items()
        },
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

    for chip_spec in _OCA_ENTITY_CHIP_PICKS:
        label_entry = pick(*chip_spec["label_from"]) if chip_spec["label_from"] else {}
        id_entry = pick(*chip_spec["id"]) if chip_spec["id"] else {}
        name_pointers = chip_spec.get("name") or ()
        name_entry = pick(*name_pointers) if name_pointers else None
        information = ""
        if chip_spec.get("information_from_id"):
            information = str(id_entry.get("information") or "")
        add_entity_chip(
            label=str(
                label_entry.get("label") or chip_spec["default_label"]
            ),
            id_entry=id_entry,
            name_entry=name_entry,
            name_override=str(chip_spec.get("name_override") or ""),
            information=information,
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


def resolve_render_methods(
    vc: dict[str, Any],
    *,
    fallback_type: str = "",
) -> dict[str, Any]:
    """Look up ``renderMethod``, fetch OCA when present, else fall back by type.

    Mines Act / UNTP Conformity credentials currently omit ``renderMethod`` so
    the UNTP playground schema check stays clean (see ``composer.compose_credential``).
    Without it, OCA is **not** self-describing on the VC:

    * Prefer ``fallback_type`` — the publisher config name from Mongo
      ``CredentialRecord.type`` (e.g. ``BCMinesActPermitCredential``), set by
      ``/view`` after looking up the credential id.
    * Else try ``credential_type_from_vc(vc)`` (e.g. ``DigitalConformityCredential``),
      which has **no** OCA bundle in this repo and will fail.

    Discovery and same-origin ``/view?url=…/credentials/{id}`` therefore depend on
    a CredentialRecord (or an explicit ``fallback_type``). Remote / VC-only paths
    need ``renderMethod`` restored, or another type mapping, before they can render.
    """
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
        if parsed is None:
            row["error"] = (
                "renderMethod id must be a same-origin /templates/{type}/{version}/oca.json URL"
                if not view_allows_remote()
                else "renderMethod id must be an http(s) /templates/{type}/{version}/oca.json URL"
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


def oca_bundle_for_vc(vc: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve OCA from ``renderMethod`` when present, else VC type name."""
    result = resolve_render_methods(vc)
    if result.get("bundle") is not None:
        return result["bundle"]
    return oca_bundle_for_credential_type(credential_type_from_vc(vc))
