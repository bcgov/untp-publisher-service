"""Envelope, JWT, VCDM, UNTP, JSON-LD, proof, issuer, validity, and status checks."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError as PydanticValidationError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from app.discovery.groups import (
    did_method_prefix,
    format_proof_created,
    issuer_from_record,
    issuer_resolve_url,
)
from app.models.credential import Credential as Vcdm20Credential
from app.plugins.status_list import BitstringStatusList, BitstringStatusListError
from app.plugins.traction import TractionController
from app.validators.untp import (
    UntpArtefactKind,
    UntpValidationError,
    detect_untp_artefact_kind,
    first_failed_validation_check,
    validate_untp_document_with_checks,
    validate_untp_json_ld,
)
from app.view.fetch import (
    load_status_list_credential,
    parse_status_list_url,
    view_allows_remote,
)
from config import settings
from untp.jsonld_loader import UntpJsonLdRemoteContextError
from untp.releases import CONTEXT_BUNDLE, bundled_context_digests_for_document

_VC_JWT_DATA_PREFIX = "data:application/vc+jwt,"


_CREDENTIALS_V2_CONTEXT = "https://www.w3.org/ns/credentials/v2"


_ENVELOPED_VC_TYPE = "EnvelopedVerifiableCredential"


class EnvelopeValidationError(ValueError):
    """Raised when an EnvelopedVerifiableCredential document is invalid."""


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


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode a compact JWT payload without verifying the signature.

    Always raises ``ValueError`` (or ``json.JSONDecodeError``, a ValueError
    subclass) on malformed tokens so callers can map failures to
    ``EnvelopeValidationError`` without leaking generic exceptions.
    """
    parts = (token or "").split(".")
    if len(parts) != 3 or not parts[1]:
        raise ValueError("Not a compact JWT")
    try:
        padded = parts[1] + ("=" * (-len(parts[1]) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("JWT payload is not valid base64url JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("JWT payload is not an object")
    return payload


def decode_jwt_header(token: str) -> dict[str, Any]:
    """Decode a compact JWT header without verifying the signature.

    Same error contract as :func:`decode_jwt_payload`.
    """
    parts = (token or "").split(".")
    if len(parts) != 3 or not parts[0]:
        raise ValueError("Not a compact JWT")
    try:
        padded = parts[0] + ("=" * (-len(parts[0]) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        header = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("JWT header is not valid base64url JSON") from exc
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
            # Prefer the human-readable UntpValidationError message. Do not append
            # raw jsonschema ValidationError dumps (they repeat the full schema).
            error = str(run.raising)
            cause = run.raising.__cause__
            if cause is not None and not isinstance(
                cause, (JsonSchemaValidationError, PydanticValidationError)
            ):
                cause_text = str(cause).strip()
                if cause_text and cause_text not in error:
                    error = f"{error}: {cause_text}"
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
        if not status_ok:
            row["error"] = (
                "statusListCredential must be a same-origin /status-lists/{id} URL"
                if not view_allows_remote()
                else "statusListCredential must be an http(s) /status-lists/{id} URL"
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
