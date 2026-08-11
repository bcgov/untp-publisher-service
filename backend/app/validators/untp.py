"""
Validate UNTP JSON artefacts: JSON Schema (draft 2020-12), JSON-LD → RDF, and Pydantic models.

Use this module for API payloads. Bundled schemas and contexts live
under the ``untp`` package; JSON-LD resolution is offline via :mod:`untp.jsonld_loader`.

Typical use (e.g. FastAPI body already parsed to a ``dict``)::

    from app.validators.untp import validate_untp_document

    result = validate_untp_document(body)
    credential = result.model  # v0.7.0 Pydantic instance
    rdf = result.rdf_nquads  # JSON-LD interpreted as RDF (N-Quads)

Use :func:`validate_untp_document_with_checks` when you need per-check payloads (same shape as
HTTP ``validation_checks``).

JSON-LD uses rdflib with a bundled-context-only hook: ``@context`` URLs must be in
:data:`untp.releases.CONTEXT_BUNDLE` (inlined from ``untp/bundled/``); other http(s)
context URLs raise :exc:`untp.jsonld_loader.UntpJsonLdRemoteContextError` (wrapped as
:exc:`UntpValidationError`).
"""

from __future__ import annotations

import json
import untp as _untp_package
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ValidationError as PydanticValidationError
from rdflib.plugins.shared.jsonld.errors import JSONLDException

from untp.jsonld_loader import UntpJsonLdRemoteContextError, jsonld_to_rdf_nquads
from untp.releases import SCHEMA_BUNDLE, bundled_context_digests_for_document

_BUNDLED_ROOT = Path(_untp_package.__file__).resolve().parent / "bundled"


class UntpArtefactKind(StrEnum):
    DCC_CREDENTIAL = "dcc_credential"
    DCC_ATTESTATION = "dcc_attestation"


_SCHEMA_URL: dict[UntpArtefactKind, str] = {
    UntpArtefactKind.DCC_CREDENTIAL: (
        "https://untp.unece.org/artefacts/schema/v0.7.0/dcc/ConformityCredential.json"
    ),
    UntpArtefactKind.DCC_ATTESTATION: (
        "https://untp.unece.org/artefacts/schema/v0.7.0/dcc/ConformityAttestation.json"
    ),
}

_validators: dict[UntpArtefactKind, Draft202012Validator] = {}

# Order for reporting / finding the first failed check in :attr:`UntpValidationRun.checks`
# (serialized as HTTP ``validation_checks``).
VALIDATION_CHECK_KEYS: tuple[str, ...] = (
    "document_root",
    "json_schema",
    "json_ld",
    "data_model",
)


def _subject_schema_kind_for_credential(
    envelope_kind: UntpArtefactKind,
) -> UntpArtefactKind | None:
    """When validating a VC envelope, return the kind for ``credentialSubject``'s schema."""
    if envelope_kind is UntpArtefactKind.DCC_CREDENTIAL:
        return UntpArtefactKind.DCC_ATTESTATION
    return None


class UntpValidationError(ValueError):
    """Raised when UNTP validation fails (detection, JSON Schema, JSON-LD, or Pydantic)."""


@dataclass(frozen=True)
class UntpValidatedDocument:
    """Outcome of a full UNTP validation pipeline."""

    raw: dict[str, Any]
    rdf_nquads: str
    model: BaseModel
    kind: UntpArtefactKind


@dataclass(frozen=True)
class UntpValidationRun:
    """Result of :func:`validate_untp_document_with_checks`."""

    success: bool
    #: Check id → payload (same keys as HTTP ``validation_checks``). ``json_schema`` is a list of
    #: ``{schema_id, pass, error?}``; other entries are dicts.
    checks: dict[str, Any]
    document: UntpValidatedDocument | None
    #: First failing :exc:`UntpValidationError` (preserves ``__cause__`` for :func:`validate_untp_document`).
    raising: UntpValidationError | None = None


def first_failed_validation_check(
    checks: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Return ``(check_id, failing_fragment)`` for the first failing check."""
    for key in VALIDATION_CHECK_KEYS:
        if key not in checks:
            continue
        payload = checks[key]
        if key == "json_schema" and isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and not item.get("pass", False):
                    return key, item
            continue
        if isinstance(payload, dict) and not payload.get("pass", False):
            return key, payload
    return None


def detect_untp_artefact_kind(data: Mapping[str, Any]) -> UntpArtefactKind:
    """Infer :class:`UntpArtefactKind` from the ``type`` property."""
    t = data.get("type")
    if t is None:
        raise UntpValidationError('document is missing required "type" property')
    types = [t] if isinstance(t, str) else list(t)
    if "DigitalConformityCredential" in types:
        return UntpArtefactKind.DCC_CREDENTIAL
    if "VerifiableCredential" in types:
        raise UntpValidationError(
            f"unsupported VerifiableCredential subtype in type={types!r}"
        )
    if "ConformityAttestation" in types:
        return UntpArtefactKind.DCC_ATTESTATION
    raise UntpValidationError(f"unknown UNTP document type={types!r}")


def _schema_path_for_kind(kind: UntpArtefactKind) -> Path:
    url = _SCHEMA_URL[kind]
    try:
        rel = SCHEMA_BUNDLE[url]["path"]
    except KeyError as e:
        raise UntpValidationError(f"no bundled schema for {kind}") from e
    return _BUNDLED_ROOT / rel


def _validator_for_kind(kind: UntpArtefactKind) -> Draft202012Validator:
    if kind not in _validators:
        path = _schema_path_for_kind(kind)
        schema = json.loads(path.read_text(encoding="utf-8"))
        _validators[kind] = Draft202012Validator(schema)
    return _validators[kind]


def format_json_schema_validation_error(exc: JsonSchemaValidationError) -> str:
    """Human-readable JSON Schema failure (no full schema dump)."""
    path_parts: list[str] = []
    for part in exc.absolute_path:
        if isinstance(part, int):
            if path_parts:
                path_parts[-1] = f"{path_parts[-1]}[{part}]"
            else:
                path_parts.append(f"[{part}]")
        else:
            path_parts.append(str(part))
    path = ".".join(path_parts) if path_parts else "(root)"

    instance = exc.instance
    if isinstance(instance, (dict, list)):
        instance_text = json.dumps(instance, ensure_ascii=False, separators=(", ", ": "))
    else:
        instance_text = repr(instance)
    if len(instance_text) > 240:
        instance_text = instance_text[:237] + "..."

    lines = [
        f"JSON Schema validation failed at {path}",
        f"Issue: {exc.message}",
        f"Got: {instance_text}",
    ]
    # Hint when UNTP renderMethod schema rejects modern TemplateRenderMethod fields.
    if path.startswith("renderMethod") and exc.validator == "additionalProperties":
        lines.append(
            "Hint: UNTP 0.7.0 ConformityCredential expects RenderTemplate2024 "
            "(url, name, …) — not TemplateRenderMethod id/renderSuite."
        )
    return "\n".join(lines)


def validate_untp_json_schema(data: Mapping[str, Any], kind: UntpArtefactKind) -> None:
    """Validate ``data`` against the bundled UNTP JSON Schema for ``kind``."""
    try:
        _validator_for_kind(kind).validate(data)
    except JsonSchemaValidationError as e:
        raise UntpValidationError(format_json_schema_validation_error(e)) from e


def validate_untp_json_ld(data: Mapping[str, Any]) -> str:
    """
    Interpret JSON-LD as RDF and return **N-Quads**.

    Remote ``@context`` resolution uses only :data:`untp.releases.CONTEXT_BUNDLE`;
    unbundled http(s) context URLs are not fetched.
    """
    if not isinstance(data, dict):
        raise UntpValidationError("JSON-LD document must be a JSON object (dict)")
    try:
        return jsonld_to_rdf_nquads(data)
    except (JSONLDException, ValueError, OSError, UntpJsonLdRemoteContextError) as e:
        raise UntpValidationError("JSON-LD processing failed") from e


def _pydantic_model_for_kind(kind: UntpArtefactKind) -> type[BaseModel]:
    if kind is UntpArtefactKind.DCC_CREDENTIAL:
        from app.models.untp.v0_7_0.dcc import DigitalConformityCredential

        return DigitalConformityCredential
    if kind is UntpArtefactKind.DCC_ATTESTATION:
        from app.models.untp.v0_7_0.dcc import ConformityAttestation

        return ConformityAttestation
    raise UntpValidationError(f"no Pydantic model mapped for {kind!r}")


def validate_untp_pydantic(data: Mapping[str, Any], kind: UntpArtefactKind) -> BaseModel:
    """Parse ``data`` into the v0.7.0 Pydantic model for ``kind``."""
    model_cls = _pydantic_model_for_kind(kind)
    try:
        return model_cls.model_validate(data)
    except PydanticValidationError as e:
        raise UntpValidationError("Pydantic model validation failed") from e


def validate_untp_document_with_checks(
    data: Mapping[str, Any],
    *,
    kind: UntpArtefactKind | None = None,
) -> UntpValidationRun:
    """
    Run the same checks as :func:`validate_untp_document`, recording each check.

    For ``DigitalConformityCredential`` envelopes, also validates ``credentialSubject`` against ``ConformityAttestation`` JSON Schema.

    Stops at the first failing check; later checks are not run (not listed).
    """
    out: dict[str, Any] = {}

    if not isinstance(data, dict):
        out["document_root"] = {
            "pass": False,
            "error": "document must be a JSON object (dict)",
        }
        return UntpValidationRun(
            False,
            out,
            None,
            UntpValidationError("document must be a JSON object (dict)"),
        )

    raw = dict(data)

    try:
        k = kind or detect_untp_artefact_kind(raw)
    except UntpValidationError as e:
        return UntpValidationRun(False, out, None, e)

    js: list[dict[str, Any]] = []
    envelope_schema_id = _SCHEMA_URL[k]
    try:
        validate_untp_json_schema(raw, k)
        js.append({"schema_id": envelope_schema_id, "pass": True})
    except UntpValidationError as e:
        js.append(
            {
                "schema_id": envelope_schema_id,
                "pass": False,
                "error": str(e),
            }
        )
        out["json_schema"] = js
        return UntpValidationRun(False, out, None, e)

    sub_kind = _subject_schema_kind_for_credential(k)
    if sub_kind is not None:
        subject = raw.get("credentialSubject")
        subject_schema_id = _SCHEMA_URL[sub_kind]
        if not isinstance(subject, Mapping):
            err = UntpValidationError(
                'credential must contain a JSON object "credentialSubject"'
            )
            js.append(
                {
                    "schema_id": subject_schema_id,
                    "pass": False,
                    "error": str(err),
                }
            )
            out["json_schema"] = js
            return UntpValidationRun(False, out, None, err)
        try:
            validate_untp_json_schema(subject, sub_kind)
            js.append({"schema_id": subject_schema_id, "pass": True})
        except UntpValidationError as e:
            js.append(
                {
                    "schema_id": subject_schema_id,
                    "pass": False,
                    "error": str(e),
                }
            )
            out["json_schema"] = js
            return UntpValidationRun(False, out, None, e)

    out["json_schema"] = js

    try:
        rdf_nquads = validate_untp_json_ld(raw)
        ctx_digests = bundled_context_digests_for_document(raw)
        out["json_ld"] = {
            "pass": True,
            "rdf_nquads_length": len(rdf_nquads),
            "context_digests": ctx_digests,
        }
    except UntpValidationError as e:
        out["json_ld"] = {"pass": False, "error": str(e)}
        return UntpValidationRun(False, out, None, e)

    try:
        model = validate_untp_pydantic(raw, k)
        out["data_model"] = {"pass": True, "type": model.__class__.__name__}
    except UntpValidationError as e:
        out["data_model"] = {"pass": False, "error": str(e)}
        return UntpValidationRun(False, out, None, e)

    doc = UntpValidatedDocument(raw=raw, rdf_nquads=rdf_nquads, model=model, kind=k)
    return UntpValidationRun(True, out, doc, None)


def validate_untp_document(
    data: Mapping[str, Any],
    *,
    kind: UntpArtefactKind | None = None,
) -> UntpValidatedDocument:
    """
    Run JSON Schema validation (envelope and, for DCC credentials, ``credentialSubject``),
    JSON-LD → RDF, and Pydantic parsing in order.

    If ``kind`` is omitted, it is inferred from ``type`` via
    :func:`detect_untp_artefact_kind`.
    """
    run = validate_untp_document_with_checks(data, kind=kind)
    if run.success and run.document is not None:
        return run.document
    if run.raising is not None:
        raise run.raising
    raise UntpValidationError("UNTP validation failed")
