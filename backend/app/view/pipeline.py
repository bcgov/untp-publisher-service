"""Credential /view SSE pipeline."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

from app.discovery.groups import (
    _status_label,
    entity_name_from_record,
    format_proof_created,
    issuer_from_record,
    issuer_resolve_url,
)
from app.plugins.mongodb import MongoClient
from app.services.composer import credential_download_filename
from app.view.checks import (
    EnvelopeValidationError,
    compact_data_uri_media_type,
    credential_type_from_vc,
    data_uri_media_type,
    decode_jwt_payload,
    extract_vc_jwt,
    resolve_credential_statuses,
    validate_jsonld_contexts,
    validate_untp_payload,
    validate_vcdm20_payload,
    validate_vc_issuer,
    validate_vc_proof,
    validate_vc_validity,
    verify_vc_jwt,
)
from app.view.fetch import parse_credential_url, resolve_application_vc
from app.view.oca import (
    build_oca_template_context,
    oca_languages,
    render_oca_box_html,
    resolve_render_methods,
)
from app.view.refs import (
    _view_parse_error,
    credential_download_url,
    credential_view_url,
)
from config import settings

VIEW_PIPELINE_STEPS: tuple[tuple[str, str], ...] = (
    ("envelope", "Envelope + JWT"),
    ("vcdm", "Validating VCDM 2.0"),
    ("untp", "Validating UNTP 0.7.0"),
    ("jsonld", "Validating JSON-LD"),
    ("proof", "Checking proof"),
    ("issuer", "Checking issuer"),
    ("validity", "Checking validity"),
    ("status", "Resolving credentialStatus"),
    ("render", "Loading renderMethod / OCA"),
)


def _view_progress(index: int) -> dict[str, Any]:
    step, label = VIEW_PIPELINE_STEPS[index]
    return {
        "type": "progress",
        "step": step,
        "label": label,
        "index": index + 1,
        "total": len(VIEW_PIPELINE_STEPS),
    }


def iter_view_pipeline(
    url: str, lang: str = "en", *, debug: bool = False
) -> Iterator[dict[str, Any]]:
    """Run the credential view pipeline, yielding SSE-ready event dicts."""
    raw_url = (url or "").strip()
    language = (lang or "en").strip().lower() or "en"
    if not raw_url:
        yield {"type": "error", "message": "Provide a credential URL."}
        return

    credential_id = parse_credential_url(raw_url)
    if not credential_id:
        yield {"type": "error", "message": _view_parse_error(raw_url)}
        return

    parsed_cred = urlparse(raw_url)
    credential_url = (
        f"{parsed_cred.scheme}://{parsed_cred.netloc}/credentials/{credential_id}"
    )

    yield _view_progress(0)
    vc_jwt = ""
    try:
        envelope = resolve_application_vc(credential_url)
        vc_jwt = extract_vc_jwt(envelope)
        try:
            vc = decode_jwt_payload(vc_jwt)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EnvelopeValidationError(
                "Extracted JWT payload is not valid base64url JSON"
            ) from exc
    except LookupError:
        yield {"type": "error", "message": "No credential found for that URL."}
        return
    except EnvelopeValidationError as exc:
        yield {
            "type": "error",
            "message": f"Invalid EnvelopedVerifiableCredential: {exc}",
        }
        return
    except Exception:
        settings.LOGGER.exception("View: failed to fetch/unwrap %s", credential_url)
        yield {
            "type": "error",
            "message": (
                "Could not load this credential as application/vc. "
                "Check the URL and try again."
            ),
        }
        return

    view_record: dict[str, Any] = {"vc": vc}
    issuer_name, issuer_did = issuer_from_record(view_record)
    valid_from = str(vc.get("validFrom") or "").strip()
    credential_type = credential_type_from_vc(vc)
    yield {
        "type": "meta",
        "credential_url": credential_url,
        "download_url": credential_download_url(credential_url),
        "download_name": credential_download_filename(
            {
                "type": credential_type,
                "cardinality_id": "",
                "entity_id": "",
                "vc": vc,
            }
        ),
        "view_url": credential_view_url(credential_url),
        "issuer_name": issuer_name,
        "issuer_did": issuer_did,
        "issuer_resolve_url": issuer_resolve_url(issuer_did),
        "entity_name": entity_name_from_record(view_record),
        "credential_type": credential_type,
        "credential_name": str(vc.get("name") or "").strip(),
        "valid_from": valid_from,
        "valid_from_display": format_proof_created(valid_from) if valid_from else "—",
        "status": "",
    }

    jwt_verified: bool | None = None
    jwt_kid = ""
    jwt_verify_error = ""
    try:
        verification = verify_vc_jwt(vc_jwt)
        jwt_verified = bool(verification.get("ok"))
        jwt_kid = str(verification.get("kid") or "").strip()
        if not jwt_verified:
            jwt_verify_error = (
                str(verification.get("error") or "").strip()
                or "JWT signature verification failed"
            )
    except Exception:
        settings.LOGGER.exception("View: JWT verification failed for %s", credential_url)
        jwt_verified = None
        jwt_verify_error = "Could not verify the JWT with Traction."

    if jwt_verified is True:
        jwt_summary = "JWT verified"
    elif jwt_verified is False:
        jwt_summary = "JWT invalid"
    else:
        jwt_summary = "JWT unverified"
    envelope_media_type = data_uri_media_type(
        envelope.get("id") if isinstance(envelope, dict) else ""
    )
    envelope_media_label = compact_data_uri_media_type(
        envelope.get("id") if isinstance(envelope, dict) else ""
    )
    yield {
        "type": "check",
        "id": "envelope",
        "ok": jwt_verified,
        "summary": envelope_media_label or envelope_media_type or jwt_summary,
        "media_type": envelope_media_type,
        "verification": jwt_summary,
        "kid": jwt_kid,
        "error": jwt_verify_error,
    }

    yield _view_progress(1)
    vcdm = validate_vcdm20_payload(vc)
    vcdm_ok = bool(vcdm.get("ok"))
    yield {
        "type": "check",
        "id": "vcdm",
        "ok": vcdm_ok,
        "summary": "valid" if vcdm_ok else "invalid",
        "error": str(vcdm.get("error") or "").strip(),
    }

    yield _view_progress(2)
    untp = validate_untp_payload(vc)
    untp_ok = bool(untp.get("ok"))
    untp_checks = untp.get("checks") if isinstance(untp.get("checks"), dict) else {}
    yield {
        "type": "check",
        "id": "untp",
        "ok": untp_ok,
        "summary": "valid" if untp_ok else "invalid",
        "kind": str(untp.get("kind") or "").strip(),
        "kind_label": str(untp.get("kind_label") or "").strip(),
        "error": str(untp.get("error") or "").strip(),
        "failed_check": str(untp.get("failed_check") or "").strip(),
        "checks": untp_checks,
    }

    yield _view_progress(3)
    jsonld_check = validate_jsonld_contexts(vc)
    yield {
        "type": "check",
        "id": "jsonld",
        "ok": bool(jsonld_check.get("ok")),
        "safe": bool(jsonld_check.get("safe")),
        "summary": str(jsonld_check.get("summary") or "UNSAFE JSON-LD"),
        "error": str(jsonld_check.get("error") or "").strip(),
        "contexts": jsonld_check.get("contexts") or [],
        "digests": jsonld_check.get("digests") or {},
        "rdf_nquads_length": int(jsonld_check.get("rdf_nquads_length") or 0),
    }

    yield _view_progress(4)
    proof_check = validate_vc_proof(vc)
    yield {
        "type": "check",
        "id": "proof",
        "ok": bool(proof_check.get("ok")),
        "summary": str(proof_check.get("summary") or ""),
        "error": str(proof_check.get("error") or "").strip(),
        "proofs": proof_check.get("proofs") or [],
    }

    yield _view_progress(5)
    issuer_check = validate_vc_issuer(vc)
    yield {
        "type": "check",
        "id": "issuer",
        "ok": bool(issuer_check.get("ok")),
        "summary": str(issuer_check.get("summary") or ""),
        "error": str(issuer_check.get("error") or "").strip(),
        "name": str(issuer_check.get("name") or ""),
        "did": str(issuer_check.get("did") or ""),
        "method": str(issuer_check.get("method") or ""),
        "resolve_url": str(issuer_check.get("resolve_url") or ""),
    }

    yield _view_progress(6)
    validity_check = validate_vc_validity(vc)
    yield {
        "type": "check",
        "id": "validity",
        "ok": bool(validity_check.get("ok")),
        "summary": str(validity_check.get("summary") or ""),
        "period_display": str(validity_check.get("period_display") or ""),
        "error": str(validity_check.get("error") or "").strip(),
        "valid_from": str(validity_check.get("valid_from") or ""),
        "valid_until": str(validity_check.get("valid_until") or ""),
        "valid_from_display": str(validity_check.get("valid_from_display") or ""),
        "valid_until_display": str(validity_check.get("valid_until_display") or ""),
    }

    yield _view_progress(7)
    status_check = resolve_credential_statuses(vc)
    yield {
        "type": "check",
        "id": "credentialStatus",
        "ok": bool(status_check.get("ok")),
        "summary": str(status_check.get("summary") or ""),
        "present": bool(status_check.get("present")),
        "error": str(status_check.get("error") or "").strip(),
        "entries": status_check.get("entries") or [],
    }

    yield _view_progress(8)
    record: dict[str, Any] | None = None
    try:
        found = MongoClient().find_one("CredentialRecord", {"id": credential_id})
        if isinstance(found, dict):
            record = found
    except Exception:
        settings.LOGGER.exception(
            "View: optional Mongo lookup failed for %s", credential_id
        )

    fallback_type = (
        str((record or {}).get("type") or "").strip() or credential_type_from_vc(vc)
    )
    render_check = resolve_render_methods(vc, fallback_type=fallback_type)
    oca_bundle = render_check.get("bundle")
    render_entries = render_check.get("entries") or []
    render_suite = ""
    for entry in render_entries:
        if not isinstance(entry, dict):
            continue
        suite = str(entry.get("render_suite") or "").strip()
        if suite and entry.get("ok"):
            render_suite = suite
            break
    if not render_suite:
        for entry in render_entries:
            if not isinstance(entry, dict):
                continue
            suite = str(entry.get("render_suite") or "").strip()
            if suite:
                render_suite = suite
                break
    if render_check.get("present") and render_check.get("ok"):
        render_summary = render_suite or "loaded"
    elif not render_check.get("present"):
        render_summary = "fallback" if render_check.get("source") else "none"
    else:
        render_summary = render_suite or "error"
    yield {
        "type": "check",
        "id": "renderMethod",
        "ok": bool(render_check.get("ok")),
        "summary": render_summary,
        "render_suite": render_suite,
        "present": bool(render_check.get("present")),
        "source": str(render_check.get("source") or ""),
        "error": str(render_check.get("error") or "").strip(),
        "entries": render_entries,
    }

    view_record = {"vc": vc, **(record or {})}
    issuer_name, issuer_did = issuer_from_record(view_record)
    status_label = (
        str(status_check.get("summary") or "")
        if status_check.get("present")
        else (_status_label(record) if record else "")
    )
    yield {
        "type": "meta",
        "credential_url": credential_url,
        "download_url": credential_download_url(credential_url),
        "download_name": credential_download_filename(
            record
            or {
                "type": fallback_type,
                "cardinality_id": "",
                "entity_id": "",
                "vc": vc,
            }
        ),
        "view_url": credential_view_url(credential_url),
        "issuer_name": issuer_name,
        "issuer_did": issuer_did,
        "issuer_resolve_url": issuer_resolve_url(issuer_did),
        "entity_name": entity_name_from_record(view_record),
        "credential_type": fallback_type,
        "credential_name": str(vc.get("name") or "").strip(),
        "valid_from": valid_from,
        "valid_from_display": format_proof_created(valid_from) if valid_from else "—",
        "status": status_label,
    }

    if not isinstance(oca_bundle, dict):
        yield {
            "type": "error",
            "message": (
                str(render_check.get("error") or "").strip()
                or "No OCA bundle is available for this credential, so it cannot be rendered yet."
            ),
        }
        return

    languages = oca_languages(oca_bundle) or ["en"]
    if language not in languages:
        language = languages[0]
    context = build_oca_template_context(vc, oca_bundle, language)
    html = render_oca_box_html(context, page_url=credential_url, debug=debug)
    yield {
        "type": "context",
        "url": credential_url,
        "language": context.get("language") or language,
        "languages": context.get("languages") or languages,
        "overlays_i18n": context.get("overlays_i18n") or {},
        "capture_base": context.get("capture_base") or "",
        "html": html,
        # Decoded JWT payload (not the opaque application/vc envelope).
        "credential": vc,
    }
    yield {"type": "done"}


_VIEW_STREAM_SENTINEL = object()


def _next_view_event(iterator: Iterator[dict[str, Any]]) -> Any:
    try:
        return next(iterator)
    except StopIteration:
        return _VIEW_STREAM_SENTINEL
