"""HTTP surface for ``TEST_SUITE`` mode (``/test-suite/validate``, ``/test-suite/build-credential``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import build_app
from app.repo_configs.loader import load_sample_publication_payload
from config import Settings

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "untp_samples" / "v0.7.0" / "dcc"
CREDENTIAL_TYPE = "BCMinesActPermitCredential"


def test_default_app_openapi_excludes_test_suite() -> None:
    from app import app as full_app

    paths = TestClient(full_app).get("/openapi.json").json().get("paths", {})
    assert "/test-suite/validate" not in paths
    assert "/test-suite/build-credential" not in paths


def test_test_suite_app_exposes_only_test_routes() -> None:
    application = build_app(Settings(TEST_SUITE=True))
    paths = TestClient(application).get("/openapi.json").json().get("paths", {})
    assert "/test-suite/validate" in paths
    assert "/test-suite/build-credential" in paths
    assert "/credentials/publish" not in paths
    assert "/auth/token" not in paths


def test_test_suite_validate_dcc_fixture() -> None:
    samples = sorted(FIXTURES.glob("*.json"))
    assert samples, f"missing fixtures under {FIXTURES}"
    doc = json.loads(samples[0].read_text(encoding="utf-8"))
    assert isinstance(doc, dict)

    application = build_app(Settings(TEST_SUITE=True))
    r = TestClient(application).post("/test-suite/validate", json=doc)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["success"] is True
    assert "validation_checks" in data
    assert data.get("artefact_kind") == "dcc_credential"
    assert "json_schema" in data["validation_checks"]


def test_test_suite_build_credential_openapi_has_example() -> None:
    application = build_app(Settings(TEST_SUITE=True))
    content = (
        TestClient(application)
        .get("/openapi.json")
        .json()["paths"]["/test-suite/build-credential"]["post"]["requestBody"]["content"][
            "application/json"
        ]
    )
    named = content.get("examples") or {}
    if named:
        example = next(iter(named.values())).get("value")
    else:
        example = content.get("example")
        if not example:
            schema = content.get("schema", {})
            examples = schema.get("examples") or []
            example = examples[0] if examples else schema.get("example")
    assert example
    assert example["template"] == "BCMinesActPermitCredential"
    assert example["data"]["permit"]["identifier"] == "Q-20"


def test_test_suite_build_credential_from_sample_payload() -> None:
    payload = load_sample_publication_payload(CREDENTIAL_TYPE)

    application = build_app(Settings(TEST_SUITE=True, PUBLISHER_DOMAIN="http://localhost:8000"))
    r = TestClient(application).post("/test-suite/build-credential", json=payload)
    assert r.status_code == 200, r.text
    credential = r.json()["credential"]
    assert "proof" not in credential
    assert credential["credentialSubject"]["issuedToParty"]["registeredId"] == "A0034771"
    assert credential["credentialSubject"]["issuedToParty"]["name"] == "EXAMPLE MINING CO"
    assessment = credential["credentialSubject"]["conformityAssessment"][0]
    assert assessment["registeredId"] == "Q-20"
    assert assessment["assessmentDate"] == "1999-04-19"
    assert len(assessment["assessedFacility"]) == 1


def test_test_suite_build_credential_rejects_invalid_output(monkeypatch) -> None:
    from app.services import composer
    from app.validators.untp import UntpValidationError

    def fail_validation(_credential):
        raise UntpValidationError("invalid test credential")

    monkeypatch.setattr(composer, "validate_untp_document", fail_validation)

    payload = load_sample_publication_payload(CREDENTIAL_TYPE)

    application = build_app(Settings(TEST_SUITE=True, PUBLISHER_DOMAIN="http://localhost:8000"))
    r = TestClient(application).post("/test-suite/build-credential", json=payload)
    assert r.status_code == 400
    assert "UNTP validation failed" in r.json()["detail"]
