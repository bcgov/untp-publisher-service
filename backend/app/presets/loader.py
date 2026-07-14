"""Load DCC presets and materialize registration templates from ``configs/credentials``."""

from __future__ import annotations

import copy
import re
from typing import Any

from fastapi import HTTPException

from app.repo_configs.loader import (
    load_oca_bundle as load_config_oca_bundle,
    load_sample_issued_credential_optional,
    load_sample_publication_payload,
)
from app.services.legal_act import legal_act_for_issuer

PRESET_REGISTRY: dict[str, dict[str, Any]] = {
    "untp_v0_7_0_dcc_mines_act_permit": {
        "domain_type": "BCMinesActPermitCredential",
        "display_name": "Mines Act Permit",
        "cardinality_field": "permitNumber",
        "registry_permit_base": "https://registry.digitaltrust.gov.bc.ca/mines-act/permits",
        "core_paths": {
            "entityId": "/credentialSubject/issuedToParty/registeredId",
            "cardinalityId": "/credentialSubject/conformityAssessment/0/registeredId",
        },
        "additional_paths": {
            "assessedFacility": "/credentialSubject/conformityAssessment/0/assessedFacility",
            "assessedProduct": "/credentialSubject/conformityAssessment/0/assessedProduct",
        },
        "publication_rules": {
            "assessedFacility": {"min": 1, "max": None},
            "assessedProduct": {"min": 0, "max": None},
        },
        "allowed_additional_data_keys": ["assessedFacility", "assessedProduct"],
    },
}


def list_preset_refs() -> list[str]:
    return sorted(PRESET_REGISTRY.keys())


def template_ref_for_domain_type(domain_type: str) -> str | None:
    """Return bundled preset ref for a domain credential type (e.g. ``BCMinesActPermitCredential``)."""
    for ref, meta in PRESET_REGISTRY.items():
        if meta.get("domain_type") == domain_type:
            return ref
    return None


def get_preset(template_ref: str) -> dict[str, Any]:
    if template_ref not in PRESET_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown templateRef {template_ref!r}. Known: {list_preset_refs()}",
        )
    meta = copy.deepcopy(PRESET_REGISTRY[template_ref])
    meta["template_ref"] = template_ref
    return meta


def _domain_type_for_ref(template_ref: str) -> str:
    return get_preset(template_ref)["domain_type"]


def load_instance_skeleton(template_ref: str) -> dict[str, Any]:
    """VC skeleton from ``configs/credentials/{type}/{version}/sample.json``."""
    credential_type = _domain_type_for_ref(template_ref)
    skeleton = load_sample_issued_credential_optional(credential_type)
    if not skeleton:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Preset sample missing for {credential_type!r} "
                "(expected configs/credentials/.../sample.json)"
            ),
        )
    skeleton = copy.deepcopy(skeleton)
    skeleton.pop("proof", None)
    skeleton.pop("id", None)
    skeleton.pop("validFrom", None)
    skeleton.pop("validUntil", None)
    return skeleton


def load_oca_bundle(template_ref: str) -> dict[str, Any]:
    """OCA bundle from ``configs/credentials/{type}/{version}/oca.json``."""
    return load_config_oca_bundle(_domain_type_for_ref(template_ref))


def load_publication_example(template_ref: str) -> dict[str, Any]:
    """Publication payload from ``configs/credentials/{type}/{version}/payload.json``."""
    return load_sample_publication_payload(_domain_type_for_ref(template_ref))


def _apply_legal_act_to_skeleton(skeleton: dict[str, Any], legal_act: dict[str, Any]) -> None:
    subject = skeleton.setdefault("credentialSubject", {})
    subject["referenceScheme"] = {
        "type": ["ConformityScheme"],
        "id": legal_act["id"],
        "name": legal_act["name"],
    }
    assessments = subject.get("conformityAssessment") or []
    if not assessments:
        return
    assessment = assessments[0]
    assessment.setdefault("assessmentCriteria", [{}])
    if assessment["assessmentCriteria"]:
        criterion = assessment["assessmentCriteria"][0]
        criterion["id"] = legal_act["id"]
        criterion["name"] = legal_act["name"]
    assessment["referenceRegulation"] = [
        {"id": legal_act["id"], "name": legal_act["name"]}
    ]


def build_template_from_preset(
    *,
    template_ref: str,
    issuer: dict[str, Any],
    domain_type: str | None = None,
) -> dict[str, Any]:
    """Materialize a registration-time VC template from a bundled preset."""
    preset = get_preset(template_ref)
    if domain_type and domain_type != preset["domain_type"]:
        raise HTTPException(
            status_code=400,
            detail=f"type {domain_type!r} does not match preset domain type {preset['domain_type']!r}",
        )

    skeleton = load_instance_skeleton(template_ref)

    skeleton["name"] = preset["display_name"]
    skeleton["type"] = [
        "VerifiableCredential",
        "DigitalConformityCredential",
    ]
    skeleton["issuer"] = {
        "type": ["CredentialIssuer"],
        "id": issuer["id"],
        "name": issuer["name"],
    }

    subject = skeleton.setdefault("credentialSubject", {})
    subject["type"] = ["ConformityAttestation"]
    subject.pop("id", None)
    subject.pop("name", None)
    subject.pop("description", None)
    subject["issuedToParty"] = {
        "type": ["Party"],
        "idScheme": {
            "type": ["IdentifierScheme"],
            "id": "https://www.bcregistry.gov.bc.ca/",
            "name": "BC Registry",
        },
    }

    assessments = subject.get("conformityAssessment") or []
    if assessments:
        assessment = assessments[0]
        for key in (
            "id",
            "name",
            "description",
            "registeredId",
            "assessmentDate",
            "assessedProduct",
            "assessedFacility",
            "assessedOrganisation",
        ):
            assessment.pop(key, None)

    if not (subject.get("referenceScheme") or {}).get("id"):
        legal_act = legal_act_for_issuer(issuer)
        _apply_legal_act_to_skeleton(skeleton, legal_act)
    return skeleton


def product_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "product"
