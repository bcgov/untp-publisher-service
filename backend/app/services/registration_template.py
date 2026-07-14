"""Materialize registration VC templates from ``configs/credentials/{type}/``."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import HTTPException

from app.repo_configs.loader import load_sample_issued_credential_optional


def load_instance_skeleton(credential_type: str) -> dict[str, Any]:
    """VC skeleton from ``configs/credentials/{type}/{version}/sample.json``."""
    skeleton = load_sample_issued_credential_optional(credential_type)
    if not skeleton:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Sample credential missing for {credential_type!r} "
                "(expected configs/credentials/.../sample.json)"
            ),
        )
    skeleton = copy.deepcopy(skeleton)
    skeleton.pop("proof", None)
    skeleton.pop("id", None)
    skeleton.pop("validFrom", None)
    skeleton.pop("validUntil", None)
    return skeleton


def build_registration_template(
    *,
    credential_type: str,
    issuer: dict[str, Any],
) -> dict[str, Any]:
    """Materialize a registration-time VC template from configs for ``credential_type``."""
    skeleton = load_instance_skeleton(credential_type)

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
        raise HTTPException(
            status_code=500,
            detail=(
                f"Sample for {credential_type!r} is missing "
                "credentialSubject.referenceScheme; set it in configs/credentials/"
            ),
        )
    return skeleton
