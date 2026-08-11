"""Discovery page grouping and record field helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.composer import credential_download_filename
from app.view.refs import (
    credential_download_url,
    credential_public_url,
    credential_ref_view_url,
    credential_view_url,
)

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


def oca_bundle_for_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Load OCA for a credential record (renderMethod id when present, else type)."""
    from app.view.oca import oca_bundle_for_credential_type, oca_bundle_for_vc

    vc = record.get("vc")
    if isinstance(vc, dict):
        bundle = oca_bundle_for_vc(vc)
        if bundle is not None:
            return bundle
    return oca_bundle_for_credential_type(str(record.get("type") or ""))
