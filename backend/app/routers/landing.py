"""Branded HTML: landing (`/`), discovery (`/discovery`), and OCA view (`/view`)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.discovery.groups import (
    did_method_prefix,
    entity_name_from_record,
    facility_from_record,
    format_proof_created,
    group_credential_records,
    issuer_from_record,
    issuer_resolve_url,
    oca_bundle_for_record,
    proof_created_raw,
)
from app.plugins.mongodb import MongoClient
from app.view.branding import (
    _branding,
    safe_asset_url,
    safe_css_color,
    safe_http_url,
)
from app.view.checks import (
    EnvelopeValidationError,
    compact_data_uri_media_type,
    credential_type_from_vc,
    data_uri_media_type,
    decode_jwt_header,
    decode_jwt_payload,
    extract_vc_jwt,
    resolve_credential_statuses,
    unwrap_enveloped_vc,
    validate_enveloped_credential,
    validate_jsonld_contexts,
    validate_untp_payload,
    validate_vcdm20_payload,
    validate_vc_issuer,
    validate_vc_proof,
    validate_vc_validity,
    verify_vc_jwt,
)
from app.view.fetch import (
    ViewFetchError,
    ViewFetchKind,
    assert_view_fetch_host_allowed,
    fetch_application_vc,
    fetch_oca_json,
    ip_is_blocked_for_view_fetch,
    is_http_url,
    load_status_list_credential,
    parse_credential_url,
    parse_oca_templates_path,
    parse_oca_url,
    parse_same_origin_credential_url,
    parse_same_origin_oca_url,
    parse_same_origin_status_list_url,
    parse_status_list_url,
    resolve_internal_oca_bundle,
    resolve_view_fetch_host_ips,
    safe_view_get,
    validate_view_fetch_url,
    view_allows_remote,
    view_fetch_url_is_publisher,
)
from app.view.oca import (
    build_oca_presentation,
    build_oca_template_context,
    format_oca_datetime,
    format_oca_value,
    oca_bundle_for_credential_type,
    oca_bundle_for_vc,
    oca_fields_for_vc,
    oca_languages,
    render_method_entries,
    render_oca_box_html,
    resolve_render_methods,
    soft_resolve_json_pointer,
    view_debug_enabled,
)
from app.view.pipeline import (
    VIEW_PIPELINE_STEPS,
    _VIEW_STREAM_SENTINEL,
    _next_view_event,
    iter_view_pipeline,
)
from app.view.refs import (
    credential_download_url,
    credential_public_url,
    credential_ref_view_url,
    credential_view_url,
    find_latest_credential_record,
    parse_credential_ref,
    resolve_view_target,
)
from config import settings

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter()

# Re-exports for ``from app.routers.landing import …`` and legacy callers.
__all__ = [
    "EnvelopeValidationError",
    "MongoClient",
    "VIEW_PIPELINE_STEPS",
    "ViewFetchError",
    "ViewFetchKind",
    "assert_view_fetch_host_allowed",
    "build_oca_presentation",
    "build_oca_template_context",
    "compact_data_uri_media_type",
    "credential_download_url",
    "credential_public_url",
    "credential_ref_view_url",
    "credential_type_from_vc",
    "credential_view_url",
    "data_uri_media_type",
    "decode_jwt_header",
    "decode_jwt_payload",
    "did_method_prefix",
    "entity_name_from_record",
    "extract_vc_jwt",
    "facility_from_record",
    "fetch_application_vc",
    "fetch_oca_json",
    "find_latest_credential_record",
    "format_oca_datetime",
    "format_oca_value",
    "format_proof_created",
    "group_credential_records",
    "ip_is_blocked_for_view_fetch",
    "is_http_url",
    "issuer_from_record",
    "issuer_resolve_url",
    "iter_view_pipeline",
    "load_status_list_credential",
    "oca_bundle_for_credential_type",
    "oca_bundle_for_record",
    "oca_bundle_for_vc",
    "oca_fields_for_vc",
    "oca_languages",
    "parse_credential_ref",
    "parse_credential_url",
    "parse_oca_templates_path",
    "parse_oca_url",
    "parse_same_origin_credential_url",
    "parse_same_origin_oca_url",
    "parse_same_origin_status_list_url",
    "parse_status_list_url",
    "proof_created_raw",
    "render_method_entries",
    "render_oca_box_html",
    "resolve_credential_statuses",
    "resolve_internal_oca_bundle",
    "resolve_render_methods",
    "resolve_view_fetch_host_ips",
    "resolve_view_target",
    "router",
    "safe_asset_url",
    "safe_css_color",
    "safe_http_url",
    "safe_view_get",
    "soft_resolve_json_pointer",
    "templates",
    "unwrap_enveloped_vc",
    "validate_enveloped_credential",
    "validate_jsonld_contexts",
    "validate_untp_payload",
    "validate_vcdm20_payload",
    "validate_vc_issuer",
    "validate_vc_proof",
    "validate_vc_validity",
    "validate_view_fetch_url",
    "verify_vc_jwt",
    "view_allows_remote",
    "view_debug_enabled",
    "view_fetch_url_is_publisher",
]


def _view_shell_context(
    *,
    url: str = "",
    credential: str = "",
    welcome: bool = False,
    loading: bool = False,
    error: str = "",
    debug: bool = False,
) -> dict[str, Any]:
    stream = ""
    if loading and url:
        stream = f"/view/stream?url={quote(url, safe='')}"
        if debug:
            stream += "&debug=1"
    return {
        **_branding(),
        "url": url,
        "credential": credential,
        "welcome": welcome,
        "loading": loading,
        "unsafe_mode": view_allows_remote(),
        "error": error,
        "debug": debug,
        "stream_url": stream,
        "pipeline_steps": [
            {"id": step, "label": label} for step, label in VIEW_PIPELINE_STEPS
        ],
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request):
    return templates.TemplateResponse(
        request,
        "landing.html",
        _branding(),
    )


@router.get("/view", response_class=HTMLResponse, include_in_schema=False)
async def view_credential(
    request: Request,
    url: str = "",
    credential: str = "",
    debug: str = "",
):
    """OCA-labeled human view of a published credential.

    In safe mode (default), bare ``/view`` redirects to Discovery — open
    credentials from a Discovery link or other deep link. With
    ``VIEW_UNSAFE_MODE=true``, bare ``/view`` shows a resolver form that
    accepts credential URLs (including remote hosts).

    With a valid ``url`` or ``credential`` (``type:cardinality:entity``),
    returns a loading shell immediately; the browser streams pipeline results
    from ``GET /view/stream``. Parse errors are still server-rendered.

    ``credential`` resolves the latest active publication for that triple
    (same semantics as ``GET /credentials/refresh``).

    Language for the OCA card starts in English; EN/FR (and other OCA
    overlay languages) are switched in the page UI, not via a ``lang`` query.

    Pass ``?debug=1`` to include the technical OCA attribute dump in the document.
    """
    debug_on = view_debug_enabled(debug)
    target_url, error = resolve_view_target(url=url, credential=credential)

    if error:
        return templates.TemplateResponse(
            request,
            "view.html",
            _view_shell_context(
                url=(url or "").strip(),
                credential=(credential or "").strip(),
                error=error,
                debug=debug_on,
            ),
        )

    if not target_url:
        if view_allows_remote():
            return templates.TemplateResponse(
                request,
                "view.html",
                _view_shell_context(welcome=True, debug=debug_on),
            )
        # Safe mode: no free-form URL entry — browse Discovery instead.
        return RedirectResponse(url="/discovery", status_code=302)

    return templates.TemplateResponse(
        request,
        "view.html",
        _view_shell_context(url=target_url, loading=True, debug=debug_on),
    )


@router.get("/view/stream", include_in_schema=False)
async def view_credential_stream(
    url: str = "",
    credential: str = "",
    debug: str = "",
):
    """Server-Sent Events stream of view-pipeline progress and check results."""
    target_url, error = resolve_view_target(url=url, credential=credential)
    if error:
        iterator = iter([{"type": "error", "message": error}])
    elif not target_url:
        iterator = iter([{"type": "error", "message": "Provide a credential URL or credential=type:cardinality:entity."}])
    else:
        iterator = iter_view_pipeline(
            target_url, debug=view_debug_enabled(debug)
        )

    async def event_publisher():
        loop = asyncio.get_running_loop()
        while True:
            event = await loop.run_in_executor(None, _next_view_event, iterator)
            if event is _VIEW_STREAM_SENTINEL:
                break
            payload = json.dumps(event, ensure_ascii=False, default=str)
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        event_publisher(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/discovery", response_class=HTMLResponse, include_in_schema=False)
async def discovery(request: Request):
    records: list[dict[str, Any]] = []
    load_error = ""
    truncated = False
    try:
        mongo = MongoClient()
        # Newest inserted first. Include refresh=true rows so iteration history
        # can collapse under the same (entity_id, cardinality_id) group.
        # Cap rows to bound memory / response size on this public endpoint.
        # Fetch limit+1 so we can detect truncation without an extra count query.
        limit = int(settings.DISCOVERY_MAX_RECORDS)
        page = mongo.find_page("CredentialRecord", {}, skip=0, limit=limit + 1)
        for record in page:
            if isinstance(record, dict):
                records.append(record)
        truncated = len(records) > limit
        if truncated:
            records = records[:limit]
    except Exception:
        settings.LOGGER.exception("Discovery: failed to load published credentials")
        load_error = "Could not load credentials. Check that the database is reachable and retry."

    groups = group_credential_records(records)
    credential_types = sorted(
        {str(g.get("type") or "") for g in groups if g.get("type")}
    )

    return templates.TemplateResponse(
        request,
        "discovery.html",
        {
            **_branding(),
            "groups": groups,
            "credential_types": credential_types,
            "total_credentials": len(records),
            "total_groups": len(groups),
            "load_error": load_error,
            "truncated": truncated,
            "discovery_max_records": int(settings.DISCOVERY_MAX_RECORDS),
        },
    )
