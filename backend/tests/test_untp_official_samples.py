"""Unit tests using official UNTP v0.7.0 sample credentials (see ``tests/fixtures/untp_samples``)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError

from untp.jsonld_loader import UntpJsonLdRemoteContextError
from untp.releases import CONTEXT_BUNDLE
from app.validators.untp import (
    UntpArtefactKind,
    UntpValidationError,
    format_json_schema_validation_error,
    validate_untp_document,
    validate_untp_json_ld,
    validate_untp_json_schema,
    validate_untp_pydantic,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "untp_samples" / "v0.7.0"

VCDM_V2_CONTEXT_URL = "https://www.w3.org/ns/credentials/v2"

DCC_SAMPLES = sorted((FIXTURES / "dcc").glob("*.json"))
ALL_VC_SAMPLES = DCC_SAMPLES


def _load_json_object(path: Path) -> dict[str, Any]:
    """Read a UTF-8 JSON file whose root must be an object (same expectation as API bodies)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"expected JSON object at root in {path}, got {type(data).__name__}")
    return data


def test_vcdm_2_context_is_bundled() -> None:
    """W3C VCDM 2.0 context is vendored for offline JSON-LD (see ``untp/releases.py``)."""
    assert VCDM_V2_CONTEXT_URL in CONTEXT_BUNDLE
    rel = CONTEXT_BUNDLE[VCDM_V2_CONTEXT_URL]["path"]
    path = BACKEND_ROOT / "untp" / "bundled" / rel
    assert path.is_file(), f"missing bundled file: {path}"
    data = _load_json_object(path)
    assert "@context" in data


def test_json_ld_rejects_unbundled_context_url() -> None:
    """http(s) ``@context`` URLs not in CONTEXT_BUNDLE must not be fetched (offline)."""
    doc = {
        "@context": ["https://example.invalid/unbundled-context.jsonld"],
        "id": "urn:test:credential",
        "type": "VerifiableCredential",
    }
    with pytest.raises(UntpValidationError) as excinfo:
        validate_untp_json_ld(doc)
    assert isinstance(excinfo.value.__cause__, UntpJsonLdRemoteContextError)


def test_json_ld_inline_context_cannot_redefine_protected_name() -> None:
    """
    VCDM 2.0 marks ``name`` as protected; a later inline context must not remap it
    (JSON-LD 1.1 / rdflib keeps ``https://schema.org/name`` for the VC ``name`` key).
    """
    doc = {
        "@context": [
            "https://www.w3.org/ns/credentials/v2",
            {"name": "http://example.invalid/evil-name"},
        ],
        "id": "urn:test:credential",
        "type": "VerifiableCredential",
        "name": "Credential title",
    }
    nq = validate_untp_json_ld(doc)
    assert "http://example.invalid/evil-name" not in nq
    assert "<https://schema.org/name>" in nq


def test_json_ld_inline_context_cannot_redefine_protected_untp_prefix() -> None:
    """UNTP context protects the ``untp`` prefix; a trailing inline map must not replace it."""
    untp_ctx = "https://vocabulary.uncefact.org/untp/0.7.0/context/"
    doc = {
        "@context": [
            "https://www.w3.org/ns/credentials/v2",
            untp_ctx,
            {"untp": "http://example.invalid/wrong-untp/"},
        ],
        "id": "urn:test:credential",
        "type": ["VerifiableCredential", "DigitalConformityCredential"],
        "name": "n",
        "issuer": {"id": "urn:test:issuer", "type": "CredentialIssuer", "name": "i"},
        "credentialSubject": {"type": "ConformityAttestation", "id": "urn:test:subject"},
    }
    nq = validate_untp_json_ld(doc)
    assert "http://example.invalid/wrong-untp" not in nq
    assert "https://vocabulary.uncefact.org/untp/CredentialIssuer" in nq


def _assert_samples_present() -> None:
    assert DCC_SAMPLES, "missing DCC fixtures under tests/fixtures/untp_samples/v0.7.0/dcc/"


@pytest.mark.parametrize(
    "path",
    ALL_VC_SAMPLES,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_official_samples_pass_json_schema(path: Path) -> None:
    _assert_samples_present()
    data = _load_json_object(path)
    t = data.get("type")
    types = [t] if isinstance(t, str) else list(t or [])
    if "DigitalConformityCredential" in types:
        kind = UntpArtefactKind.DCC_CREDENTIAL
    else:
        kind = UntpArtefactKind.DIA_CREDENTIAL
    validate_untp_json_schema(data, kind)


@pytest.mark.parametrize(
    "path",
    ALL_VC_SAMPLES,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_official_samples_pass_json_ld(path: Path) -> None:
    _assert_samples_present()
    data = _load_json_object(path)
    nq = validate_untp_json_ld(data)
    assert isinstance(nq, str)
    assert len(nq.strip()) > 0
    # Context terms must resolve to full IRIs (rdflib does this while parsing; we do not
    # expose separate JSON-LD expand/compact APIs).
    assert "https://vocabulary.uncefact.org/untp/" in nq


@pytest.mark.parametrize(
    "path",
    ALL_VC_SAMPLES,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_official_samples_full_validation_pipeline(path: Path) -> None:
    _assert_samples_present()
    result = validate_untp_document(_load_json_object(path))
    assert result.raw.get("@context")
    assert len(result.rdf_nquads.strip()) > 0
    assert result.model is not None
    assert result.kind == UntpArtefactKind.DCC_CREDENTIAL


@pytest.mark.parametrize(
    "path",
    DCC_SAMPLES,
    ids=lambda p: p.name,
)
def test_dcc_credential_subject_validates_as_conformity_attestation(path: Path) -> None:
    _assert_samples_present()
    data = _load_json_object(path)
    subject = data["credentialSubject"]
    validate_untp_pydantic(subject, UntpArtefactKind.DCC_ATTESTATION)




def test_validate_untp_document_explicit_kind_matches_inference() -> None:
    """Explicit ``kind`` should agree with :func:`detect_untp_artefact_kind` for DCC fixtures."""
    _assert_samples_present()
    path = DCC_SAMPLES[0]
    data = _load_json_object(path)
    inferred = validate_untp_document(data)
    explicit = validate_untp_document(data, kind=UntpArtefactKind.DCC_CREDENTIAL)
    assert inferred.kind == explicit.kind
    assert type(inferred.model) is type(explicit.model)


def test_vc_rejects_unknown_property_on_issuer() -> None:
    kind = UntpArtefactKind.DCC_CREDENTIAL
    """
    ``CredentialIssuer`` in the UNTP JSON Schemas sets ``additionalProperties: false``;
    Pydantic uses ``extra="forbid"`` for the same shape. A stray key under ``issuer``
    must fail validation.

    Note: the VC envelope itself uses ``additionalProperties: true``, so an unknown
    *top-level* property is not rejected by JSON Schema or the root Pydantic model.
    """
    _assert_samples_present()
    path = DCC_SAMPLES[0]
    data = copy.deepcopy(_load_json_object(path))
    issuer = data["issuer"]
    assert isinstance(issuer, dict)
    issuer["completelyUnknownUntpTestProperty"] = "must-not-be-here"

    with pytest.raises(UntpValidationError) as excinfo:
        validate_untp_document(data, kind=kind)
    cause = excinfo.value.__cause__
    assert cause is not None
    assert isinstance(cause, JsonSchemaValidationError | PydanticValidationError)


def test_vc_rejects_missing_required_name() -> None:
    kind = UntpArtefactKind.DCC_CREDENTIAL
    """``name`` is required on the VC envelope; omitting it must fail JSON Schema / Pydantic."""
    _assert_samples_present()
    path = DCC_SAMPLES[0]
    data = copy.deepcopy(_load_json_object(path))
    del data["name"]

    with pytest.raises(UntpValidationError) as excinfo:
        validate_untp_document(data, kind=kind)
    cause = excinfo.value.__cause__
    assert cause is not None
    assert isinstance(cause, JsonSchemaValidationError | PydanticValidationError)


def test_json_schema_error_message_is_readable_not_schema_dump() -> None:
    """Failed schema checks should name the path/issue without dumping the schema body."""
    _assert_samples_present()
    data = copy.deepcopy(_load_json_object(DCC_SAMPLES[0]))
    data["renderMethod"] = [
        {
            "id": "https://example.com/oca.json",
            "type": "TemplateRenderMethod",
            "name": "OCA",
            "renderSuite": "oca-bundle",
        }
    ]
    with pytest.raises(UntpValidationError) as excinfo:
        validate_untp_json_schema(data, UntpArtefactKind.DCC_CREDENTIAL)
    message = str(excinfo.value)
    assert "renderMethod[0]" in message
    assert "Additional properties" in message or "unexpected" in message.lower()
    assert "id" in message and "renderSuite" in message
    assert "Failed validating" not in message
    assert "'type': {'type': 'array'" not in message
    cause = excinfo.value.__cause__
    assert isinstance(cause, JsonSchemaValidationError)
    formatted = format_json_schema_validation_error(cause)
    assert formatted == message
    assert "Hint:" in formatted
