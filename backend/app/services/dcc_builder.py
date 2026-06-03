"""Build UNTP 0.7.0 DCC credentials from publication payloads."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.presets.loader import get_preset
from app.repo_configs.loader import load_credential_template_source_optional
from app.services.legal_act import legal_act_for_issuer
from app.services.publication_templates import (
    apply_configured_template_fields,
    publication_template_context,
)
from app.utils import format_utc_datetime
from config import settings


def publisher_origin() -> str:
    domain = (settings.DOMAIN or "").strip()
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain.rstrip("/")
    return f"https://{domain}"


def _assessment_date(valid_from: str | None) -> str:
    if not valid_from:
        return datetime.now(timezone.utc).date().isoformat()
    cleaned = valid_from.strip()
    if "T" in cleaned:
        return cleaned.split("T", 1)[0]
    return cleaned[:10]


def validate_publication(
    *,
    credential_input: dict[str, Any],
    options: dict[str, Any],
    type_record: dict[str, Any],
) -> None:
    preset = get_preset(type_record["template_ref"])
    cardinality_field = preset["cardinality_field"]
    subject = credential_input.get("credentialSubject") or {}
    permit_number = subject.get(cardinality_field)
    cardinality_id = options.get("cardinalityId")

    if permit_number is None or str(permit_number).strip() == "":
        raise HTTPException(
            status_code=400,
            detail=f"credential.credentialSubject.{cardinality_field} is required",
        )
    if str(permit_number) != str(cardinality_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"credential.credentialSubject.{cardinality_field} must match "
                f"options.cardinalityId ({permit_number!r} != {cardinality_id!r})"
            ),
        )

    additional = options.get("additionalData") or {}
    allowed = set(preset.get("allowed_additional_data_keys") or [])
    unknown = set(additional.keys()) - allowed
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown additionalData keys: {sorted(unknown)}",
        )

    rules = preset.get("publication_rules") or {}
    for key, rule in rules.items():
        items = additional.get(key)
        if items is None:
            items = []
        if not isinstance(items, list):
            raise HTTPException(
                status_code=400,
                detail=f"additionalData.{key} must be an array",
            )
        min_count = rule.get("min")
        max_count = rule.get("max")
        if min_count is not None and len(items) < min_count:
            raise HTTPException(
                status_code=400,
                detail=f"additionalData.{key} requires at least {min_count} item(s)",
            )
        if max_count is not None and len(items) > max_count:
            raise HTTPException(
                status_code=400,
                detail=f"additionalData.{key} allows at most {max_count} item(s)",
            )


def build_dcc_from_publication(
    *,
    template: dict[str, Any],
    credential_input: dict[str, Any],
    options: dict[str, Any],
    type_record: dict[str, Any],
    issuer: dict[str, Any],
    entity: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a UNTP 0.7.0 DCC from a stored template and publication payload."""
    validate_publication(
        credential_input=credential_input,
        options=options,
        type_record=type_record,
    )

    preset = get_preset(type_record["template_ref"])
    cardinality_id = str(options["cardinalityId"])
    entity_id = str(options["entityId"])
    entity_name = entity.get("name") or entity_id
    permit_uri = f"{preset['registry_permit_base']}/{cardinality_id}"
    permit_issue_at = credential_input.get("validFrom")
    assessment_date = _assessment_date(permit_issue_at)
    published_at = format_utc_datetime(datetime.now(timezone.utc))

    credential = copy.deepcopy(template)
    credential_id = options.get("credentialId")
    credential["id"] = f"{publisher_origin()}/credentials/{credential_id}"
    credential["validFrom"] = published_at
    if credential_input.get("validUntil"):
        credential["validUntil"] = credential_input["validUntil"]

    subject = credential["credentialSubject"]
    facility_names: list[str] = []
    additional = options.get("additionalData") or {}
    for facility in additional.get("assessedFacility") or []:
        if isinstance(facility, dict) and facility.get("name"):
            facility_names.append(str(facility["name"]))
    facility_hint = facility_names[0] if facility_names else "registered site"
    text_context = publication_template_context(
        credential=credential_input,
        options=options,
        organization=entity,
    )
    text_context["permit_uri"] = permit_uri
    text_context["assessment_date"] = assessment_date
    template_source = load_credential_template_source_optional(type_record.get("type"))
    if template_source:
        apply_configured_template_fields(
            template_source=template_source,
            credential=credential,
            subject=subject,
            assessment=subject["conformityAssessment"][0],
            context=text_context,
        )
    else:
        legal_act = legal_act_for_issuer(issuer)
        subject["id"] = permit_uri
        subject["name"] = f"Mines Act Permit {cardinality_id} — {entity_name}"
        subject["description"] = (
            f"Mines Act permit issued to {entity_name} for {facility_hint} "
            f"(permit {cardinality_id}). One conformity assessment represents this permit."
        )
        subject["issuedToParty"] = {
            "type": ["Party"],
            "id": entity["id"],
            "name": entity_name,
            "registeredId": entity_id,
            "idScheme": {
                "type": ["IdentifierScheme"],
                "id": "https://www.bcregistry.gov.bc.ca/",
                "name": "BC Registry",
            },
        }

    assessment = subject["conformityAssessment"][0]
    if not template_source:
        assessment["id"] = permit_uri
        assessment["registeredId"] = cardinality_id
        assessment["name"] = f"Mines Act Permit {cardinality_id} — {facility_hint}"
        assessment["description"] = (
            f"This conformity assessment is the Mines Act permit. Permit {cardinality_id} "
            f"authorizes operations at {facility_hint} for the stated product scope under "
            f"{legal_act['name']}."
        )
        assessment["assessmentDate"] = assessment_date
        assessment["assessedOrganisation"] = {
            "type": ["Party"],
            "id": entity["id"],
            "name": entity_name,
            "registeredId": entity_id,
            "idScheme": {
                "type": ["IdentifierScheme"],
                "id": "https://www.bcregistry.gov.bc.ca/",
                "name": "BC Registry",
            },
        }
        assessment["assessedFacility"] = []
        assessment["assessedProduct"] = []

    return credential
