"""OCA presentation build and HTML render helpers for /view."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates

from app.discovery.groups import issuer_resolve_url
from app.repo_configs.loader import (
    credential_version_for_type,
    load_oca_bundle,
)
from app.utils import generate_digest_multibase
from app.view.checks import (
    _as_string_list,
    credential_type_from_vc,
)
from app.view.fetch import (
    fetch_oca_json,
    parse_oca_url,
    resolve_internal_oca_bundle,
    view_allows_remote,
)
from config import settings

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_PERMIT_DESC_SUFFIX_RE = re.compile(r"\s*\(permit\s+[^)]+\)\.?\s*$", re.IGNORECASE)

# ``Mines Act Permit C-217 — Permittee`` → drop em dash clause, then trailing permit id.
_PERMIT_NAME_EMDASH_RE = re.compile(r"\s+[—–]\s+.+$")
_PERMIT_NAME_ID_RE = re.compile(r"\s+[A-Za-z]+-\d+\s*$")


def _oca_clean_permit_title(value: str) -> str:
    """Normalize published permit titles to a short product name (e.g. Mines Act Permit)."""
    text = (value or "").strip()
    if not text:
        return ""
    text = _PERMIT_NAME_EMDASH_RE.sub("", text).strip()
    text = _PERMIT_NAME_ID_RE.sub("", text).strip()
    return text or value.strip()


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


_ASSESSED_PRODUCT_INDEX_RE = re.compile(r"/assessedProduct/(\d+)/")


def expand_oca_array_attributes(
    attributes: dict[str, Any],
    labels: dict[str, str],
    information: dict[str, str],
    flagged: list[str],
    vc: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str], dict[str, str], list[str], list[str]]:
    """Expand capture-base ``Array`` roots and ``*`` child templates.

    For each attribute typed ``Array`` at pointer ``P``, child keys ``P/*/…`` are
    treated as item templates. When ``P`` resolves to a list of length ``N``, each
    template becomes concrete pointers ``P/0/…`` … ``P/{N-1}/…`` with the same
    overlay label/information/flagged metadata. Array roots and ``*`` templates are
    omitted from the returned attribute map and order.
    """
    if not isinstance(attributes, dict) or not attributes:
        empty_order: list[str] = [
            p for p in (attributes or {}) if isinstance(p, str)
        ]
        return (
            dict(attributes or {}),
            dict(labels or {}),
            dict(information or {}),
            list(flagged or []),
            empty_order,
        )

    array_roots = [
        pointer
        for pointer, attr_type in attributes.items()
        if isinstance(pointer, str)
        and str(attr_type or "").strip().lower() == "array"
    ]
    if not array_roots:
        order = [p for p in attributes if isinstance(p, str)]
        return (
            dict(attributes),
            dict(labels or {}),
            dict(information or {}),
            list(flagged or []),
            order,
        )

    templates_by_root: dict[str, list[tuple[str, Any]]] = {
        root: [] for root in array_roots
    }
    skip: set[str] = set(array_roots)
    for pointer, attr_type in attributes.items():
        if not isinstance(pointer, str):
            continue
        for root in array_roots:
            star_prefix = root + "/*"
            if pointer == star_prefix or pointer.startswith(star_prefix + "/"):
                skip.add(pointer)
                templates_by_root[root].append((pointer, attr_type))
                break

    flagged_set = {p for p in (flagged or []) if isinstance(p, str)}
    new_attributes: dict[str, Any] = {}
    new_labels = dict(labels or {})
    new_information = dict(information or {})
    new_flagged: list[str] = []
    order: list[str] = []
    emitted_roots: set[str] = set()

    def _emit_concrete(pointer: str, attr_type: Any, *, template: str = "") -> None:
        if pointer in new_attributes:
            return
        new_attributes[pointer] = attr_type
        order.append(pointer)
        src = template or pointer
        if src in (labels or {}):
            new_labels[pointer] = labels[src]
        if src in (information or {}):
            new_information[pointer] = information[src]
        if src in flagged_set and pointer not in new_flagged:
            new_flagged.append(pointer)

    for pointer, attr_type in attributes.items():
        if not isinstance(pointer, str):
            continue
        if pointer in array_roots:
            if pointer in emitted_roots:
                continue
            emitted_roots.add(pointer)
            resolved = soft_resolve_json_pointer(vc, pointer)
            if not isinstance(resolved, list) or not resolved:
                continue
            for index in range(len(resolved)):
                for tmpl_pointer, tmpl_type in templates_by_root.get(pointer) or []:
                    concrete = tmpl_pointer.replace(
                        pointer + "/*", pointer + f"/{index}", 1
                    )
                    _emit_concrete(concrete, tmpl_type, template=tmpl_pointer)
            continue
        if pointer in skip:
            continue
        _emit_concrete(pointer, attr_type)

    for pointer in flagged or []:
        if (
            isinstance(pointer, str)
            and pointer not in skip
            and pointer in new_attributes
            and pointer not in new_flagged
        ):
            new_flagged.append(pointer)

    return new_attributes, new_labels, new_information, new_flagged, order


def format_oca_value(value: Any, *, attr_type: str = "", language: str = "en") -> str:
    """Human-readable string for an OCA attribute value."""
    if value is None:
        return "—"
    fr = (language or "en").startswith("fr")
    if isinstance(value, bool):
        if fr:
            return "Oui" if value else "Non"
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
    if kind == "Binary":
        return f"[binary · {len(text)} chars]" if not fr else f"[binaire · {len(text)} car.]"
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


def build_oca_overlays_i18n(
    oca_bundle: dict[str, Any],
    vc: dict[str, Any],
    languages: list[str],
) -> dict[str, dict[str, dict[str, str]]]:
    """Per-language label/information maps (Array ``*`` templates expanded).

    Used by the viewer to swap overlay text in place without re-rendering.
    """
    attributes = oca_bundle.get("attributes")
    if not isinstance(attributes, dict):
        return {}
    out: dict[str, dict[str, dict[str, str]]] = {}
    for lang_code in languages:
        code = str(lang_code or "").strip().lower()
        if not code:
            continue
        raw_labels = _overlay_map(
            oca_bundle,
            overlay_type="spec/overlays/label/1.0",
            language=code,
            field="attribute_labels",
        )
        raw_info = _overlay_map(
            oca_bundle,
            overlay_type="spec/overlays/information/1.0",
            language=code,
            field="attribute_information",
        )
        _attrs, labels, information, _flagged, _order = expand_oca_array_attributes(
            attributes,
            raw_labels,
            raw_info,
            [],
            vc,
        )
        out[code] = {
            "labels": {
                str(k): str(v)
                for k, v in labels.items()
                if isinstance(k, str) and str(v).strip()
            },
            "information": {
                str(k): str(v)
                for k, v in information.items()
                if isinstance(k, str) and str(v).strip()
            },
            "ui": _oca_ui_strings(code),
        }
    return out


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


_OCA_SECTION_RULES: list[tuple[str, Any]] = [
    ("facility", lambda p: "/assessedFacility/" in p),
    ("organisation", lambda p: "/assessedOrganisation/" in p),
    ("product", lambda p: "/assessedProduct/" in p),
    ("criteria", lambda p: "/assessmentCriteria/" in p),
    ("evidence", lambda p: "/evidence/" in p),
    ("assessment", lambda p: "/conformityAssessment/" in p),
    ("holder", lambda p: "/issuedToParty/" in p),
    (
        "attestation",
        lambda p: p
        in {
            "/credentialSubject/assessmentLevel",
            "/credentialSubject/assessorLevel",
            "/credentialSubject/attestationType",
        },
    ),
    (
        "governance",
        lambda p: "/referenceProfile/" in p or "/referenceScheme/" in p,
    ),
    (
        "credential",
        lambda p: p in {"/id", "/issuer/id", "/issuer/name", "/validFrom"}
        or p.startswith("/issuer/"),
    ),
]

_OCA_SECTION_DISPLAY_ORDER = [
    "criteria",
    "assessment",
    "attestation",
    "organisation",
    "facility",
    "product",
    "evidence",
    "holder",
    "governance",
    "credential",
    "details",
]


_OCA_SECTION_KIND = {
    "attestation": "links",
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


def _oca_label(attributes: dict[str, Any], *pointers: str) -> str:
    """Return the first non-empty OCA overlay label for ``pointers``."""
    for pointer in pointers:
        entry = _oca_attr(attributes, pointer)
        label = str(entry.get("label") or "").strip()
        if label:
            return label
    return ""


def _oca_section_id(pointer: str) -> str:
    for section_id, match in _OCA_SECTION_RULES:
        if match(pointer):
            return section_id
    return "details"


# Section kickers: prefer these attribute labels (OCA overlays), never invented chrome.
_OCA_SECTION_TITLE_POINTERS: dict[str, tuple[str, ...]] = {
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
    "criteria": (
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/name",
    ),
    "assessment": (
        "/credentialSubject/conformityAssessment/0/referenceRegulation/0/name",
    ),
    "attestation": (
        "/credentialSubject/referenceProfile/name",
        "/credentialSubject/attestationType",
        "/credentialSubject/assessmentLevel",
    ),
    "evidence": (
        "/credentialSubject/conformityAssessment/0/evidence/0/linkName",
    ),
}


def _oca_section_title_from_attrs(
    section_id: str,
    attrs: dict[str, Any],
    *,
    headline: dict[str, Any] | None = None,
) -> str:
    if headline and str(headline.get("label") or "").strip():
        return str(headline.get("label") or "").strip()
    for pointer in _OCA_SECTION_TITLE_POINTERS.get(section_id, ()):
        label = _oca_label(attrs, pointer)
        if label:
            return label
    if section_id == "product":
        for pointer, entry in attrs.items():
            if not isinstance(pointer, str) or not isinstance(entry, dict):
                continue
            if pointer.endswith("/product/name"):
                label = str(entry.get("label") or "").strip()
                if label:
                    return label
    return ""


def _oca_section_title_pointer(
    section_id: str,
    attrs: dict[str, Any],
    *,
    headline: dict[str, Any] | None = None,
) -> str:
    """Pointer whose overlay label drives the section kicker (for client i18n)."""
    if headline and str(headline.get("label") or "").strip():
        pointer = str(headline.get("pointer") or "").strip()
        if pointer:
            return pointer
    for pointer in _OCA_SECTION_TITLE_POINTERS.get(section_id, ()):
        if _oca_label(attrs, pointer):
            return pointer
    if section_id == "product":
        for pointer, entry in attrs.items():
            if not isinstance(pointer, str) or not isinstance(entry, dict):
                continue
            if pointer.endswith("/product/name") and str(entry.get("label") or "").strip():
                return pointer
    return ""


def _oca_ui_strings(language: str = "en") -> dict[str, str]:
    """Non-content chrome (footer, lexicon, verify badges, aria)."""
    if (language or "en").startswith("fr"):
        return {
            "flow_aria": "Chaîne de relation du justificatif",
            "key_facts_aria": "Faits clés",
            "empty_summary": "Aucun autre attribut OCA à afficher dans la vue résumé.",
            "empty_none": "Aucun attribut OCA n'était disponible pour l'affichage.",
            "resolve_issuer": "Résoudre le DID de l'émetteur sur uniresolver.io",
            "summary_suffix": "résumé",
            "generated_prefix": "Généré",
            "printed_prefix": "Imprimé",
            "overlays_aria": "Sémantique",
            "overlays_kicker": "Superpositions",
            "overlays_title": "Sémantique",
            "overlays_toggle": "Sémantique",
            "verified": "Vérifié",
            "unverified": "Non vérifié",
        }
    return {
        "flow_aria": "Credential relationship chain",
        "key_facts_aria": "Key facts",
        "empty_summary": "No additional OCA attributes to display in the summary view.",
        "empty_none": "No OCA attributes were available to display.",
        "resolve_issuer": "Resolve issuer DID on uniresolver.io",
        "summary_suffix": "summary",
        "generated_prefix": "Generated",
        "printed_prefix": "Printed",
        "overlays_aria": "Semantics",
        "overlays_kicker": "Overlays",
        "overlays_title": "Semantics",
        "overlays_toggle": "Semantics",
        "verified": "Verified",
        "unverified": "Unverified",
    }


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
    if str(entry.get("type") or "") == "Boolean" or value in {"Yes", "No", "Oui", "Non"}:
        if raw is True or value in {"Yes", "Oui"}:
            badge_ok = True
        elif raw is False or value in {"No", "Non"}:
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


def _group_pointers_by_assessed_product_index(
    pointers: list[str],
) -> dict[int, list[str]]:
    groups: dict[int, list[str]] = {}
    for pointer in pointers:
        match = _ASSESSED_PRODUCT_INDEX_RE.search(pointer)
        if not match:
            continue
        groups.setdefault(int(match.group(1)), []).append(pointer)
    return groups


def _build_oca_product_item(
    pointers: list[str],
    attrs: dict[str, Any],
) -> dict[str, Any] | None:
    """Build one commodity block from pointers for a single assessedProduct index."""
    headline: dict[str, Any] | None = None
    entity_id: dict[str, Any] | None = None
    badges: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    ids: list[dict[str, Any]] = []
    seen: set[str] = set()

    for pointer in pointers:
        if not pointer.endswith("/product/name"):
            continue
        entry = _oca_attr(attrs, pointer)
        if entry and not entry.get("missing") and entry.get("value") not in (None, ""):
            headline = _oca_field_payload(pointer, entry)
            seen.add(pointer)
        break

    for pointer in pointers:
        if not pointer.endswith("/product/id"):
            continue
        entry = _oca_attr(attrs, pointer)
        if entry and not entry.get("missing") and entry.get("value") not in (None, ""):
            entity_id = _oca_field_payload(pointer, entry)
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
        if role == "badge":
            badges.append(payload)
        elif role == "metric":
            metrics.append(payload)
        elif role == "link":
            links.append(payload)
        elif role == "id":
            ids.append(payload)
        elif role == "headline" and headline is None:
            headline = payload
        else:
            facts.append(payload)

    if not any([headline, entity_id, badges, facts, metrics, links, ids]):
        return None
    return {
        "headline": headline,
        "entity_id": entity_id,
        "badges": badges,
        "facts": facts,
        "metrics": metrics,
        "links": links,
        "ids": ids,
        "pointers": pointers,
    }


def _build_oca_section_card(
    section_id: str,
    pointers: list[str],
    attrs: dict[str, Any],
    *,
    language: str = "en",
) -> dict[str, Any] | None:
    """Build a render-ready section component; ``None`` when nothing useful to show."""
    kind = _OCA_SECTION_KIND.get(section_id, "panel")

    if section_id == "product":
        groups = _group_pointers_by_assessed_product_index(pointers)
        if not groups:
            for pointer in attrs:
                if not isinstance(pointer, str):
                    continue
                match = _ASSESSED_PRODUCT_INDEX_RE.search(pointer)
                if match:
                    groups.setdefault(int(match.group(1)), []).append(pointer)
        items: list[dict[str, Any]] = []
        for index in sorted(groups):
            item = _build_oca_product_item(groups[index], attrs)
            if item:
                items.append(item)
        if not items:
            return None
        product_headline = items[0].get("headline") if items else None
        title = _oca_section_title_from_attrs(
            section_id,
            attrs,
            headline=product_headline if isinstance(product_headline, dict) else None,
        )
        return {
            "id": section_id,
            "title": title,
            "title_pointer": _oca_section_title_pointer(
                section_id,
                attrs,
                headline=product_headline if isinstance(product_headline, dict) else None,
            ),
            "kind": kind,
            "entries": items,
            "headline": None,
            "scheme": None,
            "entity_id": None,
            "badges": [],
            "facts": [],
            "metrics": [],
            "links": [],
            "ids": [],
            "chips": [],
            "pointers": pointers,
        }

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
            "title": _oca_section_title_from_attrs(section_id, attrs),
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
        # Regulation / criteria live in the scheme-story attestation card.
        return None

    if section_id == "attestation":
        # Built once in compose_oca_presentation as the scheme/regulation story.
        return None

    # UNTP assessedOrganisation is a Party (no idVerifiedByCAB); still show Verified
    # when the organisation is identified.
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
        "title": _oca_section_title_from_attrs(
            section_id, attrs, headline=headline
        ),
        "title_pointer": _oca_section_title_pointer(
            section_id, attrs, headline=headline
        ),
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
}

_OCA_ENTITY_CHIP_PICKS: tuple[dict[str, Any], ...] = (
    # Match flow strip order: Permit → Organisation → Site. Labels from OCA only.
    {
        "label_from": ("/credentialSubject/conformityAssessment/0/registeredId",),
        "id": ("/credentialSubject/conformityAssessment/0/registeredId",),
        "information_from_id": True,
    },
    {
        "label_from": (
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
            "/credentialSubject/issuedToParty/name",
        ),
        "id": (
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/registeredId",
            "/credentialSubject/issuedToParty/registeredId",
        ),
        "name": (
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
            "/credentialSubject/issuedToParty/name",
        ),
    },
    {
        "label_from": (
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
        ),
        "id": (
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
        ),
        "name": (
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
        ),
    },
)


_OCA_SCHEME_STORY_ROWS: list[tuple[str, str]] = [
    # (name_pointer, id_pointer) — id supplies HTTP href when present
    (
        "/credentialSubject/conformityAssessment/0/referenceRegulation/0/name",
        "/credentialSubject/conformityAssessment/0/referenceRegulation/0/id",
    ),
    (
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/name",
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/id",
    ),
    (
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/name",
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/id",
    ),
]

_OCA_SCHEME_STORY_USED: frozenset[str] = frozenset(
    {
        "/credentialSubject/referenceProfile/name",
        "/credentialSubject/referenceProfile/id",
        "/credentialSubject/conformityAssessment/0/referenceRegulation/0/name",
        "/credentialSubject/conformityAssessment/0/referenceRegulation/0/id",
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/name",
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/id",
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/name",
        "/credentialSubject/conformityAssessment/0/assessmentCriteria/0/conformityTopic/id",
        "/credentialSubject/assessmentLevel",
        "/credentialSubject/assessorLevel",
        "/credentialSubject/attestationType",
    }
)


def _build_oca_scheme_story_card(attrs: dict[str, Any]) -> dict[str, Any] | None:
    """One scheme/regulation card: governance headline, labeled rows, type badge."""
    gov_pointer = "/credentialSubject/referenceProfile/name"
    gov_id_pointer = "/credentialSubject/referenceProfile/id"
    reg_pointer = (
        "/credentialSubject/conformityAssessment/0/referenceRegulation/0/name"
    )

    headline_entry = _oca_attr(attrs, gov_pointer)
    headline: dict[str, Any] | None = None
    if headline_entry and not headline_entry.get("missing"):
        headline = _oca_field_payload(gov_pointer, headline_entry)
        id_entry = _oca_attr(attrs, gov_id_pointer)
        if id_entry and not id_entry.get("missing"):
            id_value = _oca_display(id_entry)
            if id_value and _oca_is_http_url(id_value):
                headline["href"] = id_value

    if headline is None:
        reg_entry = _oca_attr(attrs, reg_pointer)
        if reg_entry and not reg_entry.get("missing"):
            headline = _oca_field_payload(reg_pointer, reg_entry)

    facts: list[dict[str, Any]] = []
    for name_pointer, id_pointer in _OCA_SCHEME_STORY_ROWS:
        entry = _oca_attr(attrs, name_pointer)
        if not entry or entry.get("missing"):
            continue
        if headline and headline.get("pointer") == name_pointer:
            continue
        payload = _oca_field_payload(name_pointer, entry)
        id_entry = _oca_attr(attrs, id_pointer)
        if id_entry and not id_entry.get("missing"):
            id_value = _oca_display(id_entry)
            if id_value and _oca_is_http_url(id_value):
                payload["href"] = id_value
        # Regulation is the primary story; criterion/topic sit in a meta strip.
        if name_pointer.endswith("/referenceRegulation/0/name"):
            payload["emphasis"] = "lead"
        else:
            payload["emphasis"] = "meta"
        facts.append(payload)

    # Assessor level is omitted intentionally (often "unspecified").
    badges: list[dict[str, Any]] = []
    level_pointer = "/credentialSubject/assessmentLevel"
    level_entry = _oca_attr(attrs, level_pointer)
    if level_entry and not level_entry.get("missing"):
        level_payload = _oca_field_payload(level_pointer, level_entry)
        level_payload["compact"] = True
        badges.append(level_payload)

    type_pointer = "/credentialSubject/attestationType"
    type_entry = _oca_attr(attrs, type_pointer)
    if type_entry and not type_entry.get("missing"):
        type_payload = _oca_field_payload(type_pointer, type_entry)
        type_payload["compact"] = True
        badges.append(type_payload)

    if not any([headline, facts, badges]):
        return None

    return {
        "id": "attestation",
        "title": _oca_section_title_from_attrs(
            "attestation", attrs, headline=headline
        ),
        "title_pointer": _oca_section_title_pointer(
            "attestation", attrs, headline=headline
        ),
        "kind": "links",
        "headline": headline,
        "scheme": None,
        "entity_id": None,
        "badges": badges,
        "facts": facts,
        "metrics": [],
        "links": [],
        "ids": [],
        "chips": [],
        "pointers": sorted(_OCA_SCHEME_STORY_USED),
    }


def build_oca_presentation(
    attributes: dict[str, Any],
    order: list[str],
    flagged: list[str],
    *,
    language: str = "en",
) -> dict[str, Any]:
    """Derive hero / flow / section layout from pointer-keyed attributes."""
    attrs = attributes if isinstance(attributes, dict) else {}
    flagged_set = set(flagged or [])
    ui = _oca_ui_strings(language)

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
        _oca_display(title) if title and not title.get("missing") else ""
    )
    if title_text:
        # Older published permits used ``Mines Act Permit X — Permittee``.
        cleaned = _oca_clean_permit_title(title_text)
        if cleaned:
            title_text = cleaned

    assessment_title_text = (
        _oca_display(assessment_title)
        if assessment_title and not assessment_title.get("missing")
        else ""
    )
    if assessment_title_text:
        cleaned = _oca_clean_permit_title(assessment_title_text)
        if cleaned:
            assessment_title_text = cleaned

    assessment_flow = pick(*_OCA_FLOW_FIELD_PICKS["assessment"])
    organisation_flow = pick(*_OCA_FLOW_FIELD_PICKS["organisation"])
    facility_flow = pick(*_OCA_FLOW_FIELD_PICKS["facility"])
    flow = {
        "issuer": _oca_display(issuer_name),
        "issuer_label": _oca_label(attrs, "/issuer/name", "/issuer/id"),
        "issuer_pointer": "/issuer/name",
        "issuer_href": issuer_resolve_url(issuer_did) if issuer_did.startswith("did:") else "",
        "assessment": _oca_display(assessment_flow),
        "assessment_label": _oca_label(
            attrs,
            "/credentialSubject/conformityAssessment/0/registeredId",
            "/credentialSubject/conformityAssessment/0/name",
        ),
        "assessment_pointer": "/credentialSubject/conformityAssessment/0/registeredId",
        "organisation": _oca_display(organisation_flow),
        "organisation_label": _oca_label(
            attrs,
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
            "/credentialSubject/issuedToParty/name",
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/registeredId",
            "/credentialSubject/issuedToParty/registeredId",
        ),
        "organisation_pointer": (
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/name"
        ),
        "facility": _oca_display(facility_flow),
        "facility_label": _oca_label(
            attrs,
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
        ),
        "facility_pointer": (
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name"
        ),
    }

    # Scheme / regulation / criterion / attestation type live in one card (not a stats grid).
    stats: list[dict[str, str]] = []
    scheme_story = _build_oca_scheme_story_card(attrs)

    id_chips: list[dict[str, str]] = []

    def add_entity_chip(
        *,
        label: str,
        label_pointer: str = "",
        id_entry: dict[str, Any],
        name_entry: dict[str, Any] | None = None,
        information: str = "",
    ) -> None:
        id_value = ""
        if id_entry and not id_entry.get("missing"):
            id_value = _oca_display(id_entry)
        name_value = ""
        if name_entry and not name_entry.get("missing"):
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
                "pointer": label_pointer,
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
        label = str(label_entry.get("label") or "").strip()
        if not label:
            continue
        information = ""
        if chip_spec.get("information_from_id"):
            information = str(id_entry.get("information") or "")
        label_pointer = ""
        for pointer in chip_spec.get("label_from") or ():
            if _oca_attr(attrs, pointer):
                label_pointer = pointer
                break
        add_entity_chip(
            label=label,
            label_pointer=label_pointer,
            id_entry=id_entry,
            name_entry=name_entry,
            information=information,
        )

    used = set(_OCA_HERO_POINTERS) | flagged_set | set(_OCA_SCHEME_STORY_USED)
    used.update(
        {
            "/issuer/name",
            "/issuer/id",
            "/credentialSubject/conformityAssessment/0/registeredId",
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
            "/credentialSubject/conformityAssessment/0/assessedOrganisation/registeredId",
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
            "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
            "/credentialSubject/conformityAssessment/0/conformance",
            "/credentialSubject/conformityAssessment/0/assessmentDate",
            "/credentialSubject/issuedToParty/name",
            "/credentialSubject/issuedToParty/registeredId",
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
        # the hero, scheme-story card, or are technical identifiers — dump only.
        section_id = _oca_section_id(pointer)
        if section_id in {
            "holder",
            "governance",
            "credential",
            "details",
            "assessment",
            "criteria",
            "attestation",
        }:
            continue
        entry = _oca_attr(attrs, pointer)
        if not entry:
            continue
        label = str(entry.get("label") or "").lower()
        if entry.get("missing") and ("uri" in label or label.endswith(" id")):
            continue
        section_map.setdefault(section_id, []).append(pointer)

    # Ensure headline-only sections (names already used in scheme story) still appear.
    for section_id, headlines in _OCA_SECTION_HEADLINES.items():
        if section_id in {
            "holder",
            "governance",
            "credential",
            "details",
            "assessment",
            "criteria",
            "attestation",
        }:
            continue
        if section_id in section_map:
            continue
        if any(_oca_attr(attrs, pointer) for pointer in headlines):
            section_map[section_id] = []

    sections: list[dict[str, Any]] = []
    for section_id in _OCA_SECTION_DISPLAY_ORDER:
        if section_id == "attestation":
            section_map.pop("attestation", None)
            if scheme_story:
                sections.append(scheme_story)
            continue
        pointers = section_map.pop(section_id, None)
        if pointers is None:
            continue
        card = _build_oca_section_card(
            section_id, pointers, attrs, language=language
        )
        if card:
            sections.append(card)
    for section_id, pointers in section_map.items():
        card = _build_oca_section_card(
            section_id, pointers, attrs, language=language
        )
        if card:
            sections.append(card)

    trustmark: dict[str, str] = {}
    tm_name = pick("/credentialSubject/trustmark/name")
    tm_desc = pick("/credentialSubject/trustmark/description")
    tm_data = _oca_attr(attrs, "/credentialSubject/trustmark/imageData")
    tm_media = _oca_attr(attrs, "/credentialSubject/trustmark/mediaType")
    image_data = ""
    if tm_data and not tm_data.get("missing"):
        image_data = str(tm_data.get("raw") or "").strip()
    media_type = "image/png"
    if tm_media and not tm_media.get("missing"):
        media_type = str(tm_media.get("raw") or media_type).strip() or media_type
    if image_data:
        trustmark = {
            "name": _oca_display(tm_name) if tm_name else "",
            "description": _oca_display(tm_desc) if tm_desc else "",
            "src": f"data:{media_type};base64,{image_data}",
        }
        if trustmark["name"] == "—":
            trustmark["name"] = ""
        if trustmark["description"] == "—":
            trustmark["description"] = ""

    return {
        "scheme": _oca_display(scheme) if scheme else "",
        "title": title_text,
        "subtitle": subtitle_text,
        "assessment_title": assessment_title_text,
        "assessment_lead": _oca_display(assessment_lead)
        if assessment_lead and not assessment_lead.get("missing")
        else "",
        "valid_from": _oca_display(valid_from),
        "valid_from_label": str(valid_from.get("label") or ""),
        "valid_from_pointer": "/validFrom",
        "assessment_date": _oca_display(assessment_date),
        "assessment_date_label": str(assessment_date.get("label") or ""),
        "assessment_date_pointer": (
            "/credentialSubject/conformityAssessment/0/assessmentDate"
        ),
        "entity_label": _oca_label(
            attrs,
            "/credentialSubject/conformityAssessment/0/name",
            "/credentialSubject/conformityAssessment/0/registeredId",
            "/credentialSubject/name",
        ),
        "entity_label_pointer": "/credentialSubject/conformityAssessment/0/name",
        "conforms": conforms,
        "conforms_label": str(conforms_entry.get("label") or ""),
        "conforms_value": _oca_display(conforms_entry) if conforms_entry else "—",
        "conforms_pointer": (
            "/credentialSubject/conformityAssessment/0/conformance"
        ),
        "trustmark": trustmark,
        "flow": flow,
        "ui": ui,
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
        "overlays_i18n": {},
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
    attributes, labels, information, flagged_list, order = expand_oca_array_attributes(
        attributes,
        labels,
        information,
        flagged_list,
        vc,
    )
    flagged_set = set(flagged_list)

    attr_map: dict[str, Any] = {}
    for pointer in order:
        attr_type = attributes.get(pointer)
        resolved = soft_resolve_json_pointer(vc, pointer)
        missing = resolved is None
        raw = None if missing else resolved
        value = None if missing else format_oca_value(
            raw, attr_type=str(attr_type or ""), language=lang
        )
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
    presentation = build_oca_presentation(
        attr_map, order, flagged, language=lang
    )
    overlays_i18n = build_oca_overlays_i18n(oca_bundle, vc, languages)
    return {
        "language": lang,
        "capture_base": _overlay_capture_base(oca_bundle, language=lang),
        "order": order,
        "flagged": flagged,
        "attributes": attr_map,
        "languages": languages,
        "overlays_i18n": overlays_i18n,
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
        overlays_i18n=context.get("overlays_i18n") or {},
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
