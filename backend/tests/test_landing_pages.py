"""Smoke tests for public landing, discovery, and OCA view HTML pages."""

import base64
import json
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import landing
from app.routers.landing import (
    EnvelopeValidationError,
    data_uri_media_type,
    compact_data_uri_media_type,
    decode_jwt_payload,
    extract_vc_jwt,
    fetch_application_vc,
    format_oca_value,
    oca_fields_for_vc,
    parse_credential_url,
    parse_same_origin_credential_url,
    safe_asset_url,
    safe_css_color,
    safe_http_url,
    soft_resolve_json_pointer,
    unwrap_enveloped_vc,
    validate_enveloped_credential,
)
from app.view import checks as view_checks
from app.view import fetch as view_fetch
from app.view import oca as view_oca
from app.view import pipeline as view_pipeline
from app.view import refs as view_refs
from config import settings

_PARTNER_HREF_RE = re.compile(
    rb'class="pub-topbar-partner"[^>]*\bhref="([^"]+)"',
    re.DOTALL,
)
_SAMPLE_VC = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "configs/credentials/BCMinesActPermitCredential/v1.1/sample.json"
    ).read_text(encoding="utf-8")
)
_SAMPLE_OCA = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "configs/credentials/BCMinesActPermitCredential/v1.1/oca.json"
    ).read_text(encoding="utf-8")
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _fake_vc_jwt(payload: dict) -> str:
    header = _b64url(json.dumps({"alg": "none", "typ": "vc+jwt"}).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return f"{header}.{body}.sig"


def _enveloped(payload: dict | None = None) -> dict:
    vc = payload if payload is not None else _SAMPLE_VC
    return {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "id": f"data:application/vc+jwt,{_fake_vc_jwt(vc)}",
        "type": "EnvelopedVerifiableCredential",
    }


def _mock_fetch(monkeypatch, envelope: dict | None = None, *, status_code: int = 200):
    body = envelope if envelope is not None else _enveloped()

    class _Resp:
        def __init__(self):
            self.status_code = status_code
            self.ok = 200 <= status_code < 300

        def json(self):
            return body

    def _get(url, headers=None, timeout=None, allow_redirects=True, **_kwargs):
        assert headers and headers.get("Accept") == "application/vc"
        assert allow_redirects is False
        _get.last = {
            "url": url,
            "headers": headers,
            "timeout": timeout,
            "allow_redirects": allow_redirects,
        }
        return _Resp()

    _get.last = None
    monkeypatch.setattr(view_fetch.requests, "get", _get)
    monkeypatch.setattr(
        view_fetch,
        "assert_view_fetch_host_allowed",
        lambda _url: None,
    )
    return _get


def _mock_jwt_verify(monkeypatch, *, ok: bool = True, kid: str = "did:web:ex#key-01-jwk"):
    class _Traction:
        def authorize(self):
            return None

        def verify_jwt(self, token):
            assert token and token.count(".") == 2
            return {
                "valid": ok,
                "headers": {"alg": "EdDSA", "typ": "vc+jwt"},
                "kid": kid,
                "payload": {},
                "error": "" if ok else "signature mismatch",
            }

    monkeypatch.setattr(view_checks, "TractionController", _Traction)


def _partner_href(html: bytes) -> bytes | None:
    match = _PARTNER_HREF_RE.search(html)
    return match.group(1) if match else None


class _FakeMongo:
    def __init__(self, records=None):
        self.records = list(records or [])
        self.last_find_page = None

    def find(self, collection, query):
        assert collection == "CredentialRecord"
        return iter(self.records)

    def find_page(self, collection, query, *, skip: int = 0, limit: int = 50):
        assert collection == "CredentialRecord"
        self.last_find_page = {"skip": skip, "limit": limit, "query": query}
        return self.records[skip : skip + limit]

    def find_one(self, collection, query):
        assert collection == "CredentialRecord"
        for record in self.records:
            if all(record.get(k) == v for k, v in query.items()):
                return record
        return None




def _patch_mongo(monkeypatch, factory):
    """Patch MongoClient wherever /view and discovery resolve records."""
    monkeypatch.setattr(landing, "MongoClient", factory)
    monkeypatch.setattr(view_refs, "MongoClient", factory)
    monkeypatch.setattr(view_pipeline, "MongoClient", factory)
    monkeypatch.setattr(view_fetch, "MongoClient", factory)

def _app():
    app = FastAPI()
    app.include_router(landing.router)
    return app


def test_landing_returns_200():
    client = TestClient(_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert b"Discover credentials" in response.content
    assert b'href="/discovery"' in response.content


def test_landing_renders_partner_link(monkeypatch):
    monkeypatch.setattr(settings, "LANDING_PARTNER_URL", "https://mines.nrs.gov.bc.ca/")
    monkeypatch.setattr(settings, "LANDING_PARTNER_LABEL", "BC Mine Information")
    client = TestClient(_app())
    response = client.get("/")
    assert response.status_code == 200
    assert _partner_href(response.content) == b"https://mines.nrs.gov.bc.ca/"
    assert b"BC Mine Information" in response.content


def test_discovery_returns_200_when_mongo_empty(monkeypatch):
    _patch_mongo(monkeypatch, lambda: _FakeMongo([]))
    client = TestClient(_app())
    response = client.get("/discovery")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert b"/static/js/discovery.js" in response.content
    assert b"Discover" in response.content or b"discovery" in response.content.lower()


def test_discovery_renders_partner_link(monkeypatch):
    _patch_mongo(monkeypatch, lambda: _FakeMongo([]))
    monkeypatch.setattr(settings, "LANDING_PARTNER_URL", "https://example.com/partner")
    monkeypatch.setattr(settings, "LANDING_PARTNER_LABEL", "Partner site")
    client = TestClient(_app())
    response = client.get("/discovery")
    assert response.status_code == 200
    assert _partner_href(response.content) == b"https://example.com/partner"
    assert b"Partner site" in response.content


def test_discovery_caps_records_via_find_page(monkeypatch):
    records = [
        {
            "id": f"c-{i}",
            "type": "T",
            "entity_id": "e",
            "cardinality_id": f"n-{i}",
        }
        for i in range(5)
    ]
    fake = _FakeMongo(records)
    _patch_mongo(monkeypatch, lambda: fake)
    monkeypatch.setattr(settings, "DISCOVERY_MAX_RECORDS", 3)
    client = TestClient(_app())
    response = client.get("/discovery")
    assert response.status_code == 200
    assert fake.last_find_page == {"skip": 0, "limit": 4, "query": {}}
    assert b"most recent credential records" in response.content


def test_discovery_returns_200_when_mongo_raises(monkeypatch):
    class _Boom:
        def find_page(self, collection, query, *, skip: int = 0, limit: int = 50):
            raise RuntimeError("db down")

    _patch_mongo(monkeypatch, lambda: _Boom())
    client = TestClient(_app())
    response = client.get("/discovery")
    assert response.status_code == 200
    assert b"Could not load credentials" in response.content


def test_discovery_open_links_to_view(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    record = {
        "id": "cred-1",
        "type": "BCMinesActPermitCredential",
        "entity_id": "urn:entity",
        "cardinality_id": "C-217",
        "refresh": False,
        "revocation": False,
        "suspension": False,
        "vc": _SAMPLE_VC,
        "vc_jwt": _fake_vc_jwt(_SAMPLE_VC),
    }
    _patch_mongo(monkeypatch, lambda: _FakeMongo([record]))
    client = TestClient(_app())
    response = client.get("/discovery")
    assert response.status_code == 200
    # Group face opens the latest-active shortcut (same as /credentials/refresh).
    expected_ref = quote(
        "BCMinesActPermitCredential:C-217:urn:entity", safe=""
    )
    assert f"/view?credential={expected_ref}".encode() in response.content
    assert b'aria-label="View credential"' in response.content
    # Machine VC URL remains available for copy/download.
    assert b"https://publisher.test/credentials/cred-1" in response.content
    assert b"Basin Coal Mine" in response.content
    assert b"1500601" in response.content
    assert b"Issuer" not in response.content or b"data-sort=\"issuer\"" not in response.content
    assert b'data-sort="facility"' in response.content
    assert b'data-label="Facility"' in response.content


def test_parse_credential_ref_and_resolve_latest(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    assert landing.parse_credential_ref(
        "BCMinesActPermitCredential:M-1:BC1333706"
    ) == ("BCMinesActPermitCredential", "M-1", "BC1333706")
    assert landing.parse_credential_ref(
        "BCMinesActPermitCredential:C-217:urn:ca:bcgov:x"
    ) == ("BCMinesActPermitCredential", "C-217", "urn:ca:bcgov:x")
    assert landing.parse_credential_ref("missing-parts") is None

    active = {
        "id": "live-id",
        "type": "BCMinesActPermitCredential",
        "entity_id": "BC1333706",
        "cardinality_id": "M-1",
        "refresh": False,
    }
    stale = {
        "id": "stale-id",
        "type": "BCMinesActPermitCredential",
        "entity_id": "BC1333706",
        "cardinality_id": "M-1",
        "refresh": True,
    }
    _patch_mongo(monkeypatch, lambda: _FakeMongo([stale, active]))
    url, error = landing.resolve_view_target(
        credential="BCMinesActPermitCredential:M-1:BC1333706"
    )
    assert error == ""
    assert url == "https://publisher.test/credentials/live-id"


def test_view_with_credential_ref_returns_loading_shell(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    record = {
        "id": "cred-ref-1",
        "type": "BCMinesActPermitCredential",
        "entity_id": "BC1333706",
        "cardinality_id": "M-1231411",
        "refresh": False,
    }
    _patch_mongo(monkeypatch, lambda: _FakeMongo([record]))
    mocked = _mock_fetch(monkeypatch)
    client = TestClient(_app())
    response = client.get(
        "/view",
        params={
            "credential": "BCMinesActPermitCredential:M-1231411:BC1333706"
        },
    )
    assert response.status_code == 200
    assert mocked.last is None
    assert b'id="view-app"' in response.content
    expected = quote("https://publisher.test/credentials/cred-ref-1", safe="")
    assert f"/view/stream?url={expected}".encode() in response.content


def test_view_credential_ref_not_found(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    _patch_mongo(monkeypatch, lambda: _FakeMongo([]))
    client = TestClient(_app())
    response = client.get(
        "/view",
        params={"credential": "BCMinesActPermitCredential:M-1:MISSING"},
    )
    assert response.status_code == 200
    assert b"No active credential found" in response.content
    assert b'id="view-app"' not in response.content


def _collect_sse_events(client, **params):
    response = client.get("/view/stream", params=params)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _sse_by_type(events, event_type):
    return [e for e in events if e.get("type") == event_type]


def _sse_check(events, check_id):
    for event in events:
        if event.get("type") == "check" and event.get("id") == check_id:
            return event
    return None


def test_view_without_url_redirects_to_discovery_in_safe_mode(monkeypatch):
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", False)
    _patch_mongo(monkeypatch, lambda: _FakeMongo([]))
    client = TestClient(_app())
    response = client.get("/view", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == "/discovery"


def test_view_without_url_shows_resolver_in_unsafe_mode(monkeypatch):
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    _patch_mongo(monkeypatch, lambda: _FakeMongo([]))
    client = TestClient(_app())
    response = client.get("/view", follow_redirects=False)
    assert response.status_code == 200
    assert b"resolve-stage" in response.content
    assert b'name="url"' in response.content
    assert b"Unsafe" in response.content
    assert response.headers.get("location") is None


def test_view_error_keeps_url_form(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", False)
    _patch_mongo(monkeypatch, lambda: _FakeMongo([]))
    client = TestClient(_app())
    response = client.get(
        "/view",
        params={"url": "https://evil.example/credentials/cred-1"},
    )
    assert response.status_code == 200
    assert b"same-origin" in response.content
    assert b'name="url"' in response.content
    assert b"https://evil.example/credentials/cred-1" in response.content
    assert b"Unsafe mode is on" not in response.content


def test_view_with_url_returns_loading_shell(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    mocked = _mock_fetch(monkeypatch)
    client = TestClient(_app())
    response = client.get(
        "/view",
        params={"url": "https://publisher.test/credentials/cred-1"},
    )
    assert response.status_code == 200
    assert mocked.last is None  # pipeline runs on /view/stream, not /view
    assert b"Credential View" in response.content
    assert b'id="view-app"' in response.content
    assert b"/view/stream?url=" in response.content
    assert b"/static/js/view.js" in response.content
    assert b"Permit number" not in response.content
    assert b'id="view-oca-slot"' in response.content
    assert b'id="view-oca-frame"' not in response.content
    assert b'data-check="envelope"' in response.content
    assert b"/static/js/view.js" in response.content


def test_view_unsafe_mode_shell_allows_remote_url(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    client = TestClient(_app())
    response = client.get(
        "/view",
        params={"url": "https://remote.test/credentials/cred-1"},
    )
    assert response.status_code == 200
    assert b"Unsafe mode is on" in response.content
    assert b"Credential View" in response.content
    assert b"must be a same-origin" not in response.content
    assert b'id="view-app"' in response.content
    assert b"/view/stream?url=" in response.content


def test_view_unsafe_mode_still_rejects_bad_path(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    _patch_mongo(monkeypatch, lambda: _FakeMongo([]))
    client = TestClient(_app())
    response = client.get(
        "/view",
        params={"url": "https://remote.test/not-credentials/cred-1"},
    )
    assert response.status_code == 200
    assert b"/credentials/{id}" in response.content
    assert b"Unsafe mode is on" in response.content


def test_view_stream_runs_pipeline(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    record = {
        "id": "cred-1",
        "type": "BCMinesActPermitCredential",
        "entity_id": "urn:entity",
        "cardinality_id": "C-217",
        "refresh": False,
        "revocation": False,
        "suspension": False,
        "vc": _SAMPLE_VC,
        "vc_jwt": _fake_vc_jwt(_SAMPLE_VC),
    }
    _patch_mongo(monkeypatch, lambda: _FakeMongo([record]))
    mocked = _mock_fetch(monkeypatch)
    _mock_jwt_verify(monkeypatch, ok=True, kid="did:web:publisher.test#key-01-jwk")
    client = TestClient(_app())
    events = _collect_sse_events(
        client, url="https://publisher.test/credentials/cred-1"
    )
    # Publisher-origin credentials load from Mongo (no HTTP self-fetch).
    assert mocked.last is None

    assert _sse_by_type(events, "progress")
    assert _sse_by_type(events, "meta")
    metas = _sse_by_type(events, "meta")
    assert metas[-1]["latest_view_url"] == (
        "/view?credential="
        + quote("BCMinesActPermitCredential:C-217:urn:entity", safe="")
    )
    check_ids = [
        e["id"] for e in events if e.get("type") == "check"
    ]
    assert check_ids == [
        "envelope",
        "vcdm",
        "untp",
        "jsonld",
        "proof",
        "issuer",
        "validity",
        "credentialStatus",
        "renderMethod",
    ]
    assert _sse_check(events, "envelope")["summary"] == "vc+jwt"
    assert _sse_check(events, "envelope")["media_type"] == "application/vc+jwt"
    assert _sse_check(events, "envelope")["verification"] == "JWT verified"
    assert _sse_check(events, "envelope")["kid"] == "did:web:publisher.test#key-01-jwk"
    assert _sse_check(events, "vcdm")["ok"] is True
    assert _sse_check(events, "untp")["ok"] is True
    assert _sse_check(events, "jsonld")["ok"] is True
    assert _sse_check(events, "jsonld")["safe"] is True
    assert _sse_check(events, "jsonld")["summary"] == "SAFE JSON-LD"
    assert _sse_check(events, "jsonld")["rdf_nquads_length"] > 0
    assert _sse_check(events, "proof")["ok"] is True
    assert _sse_check(events, "issuer")["ok"] is True
    assert _sse_check(events, "issuer")["method"] == "did:web"
    assert _sse_check(events, "issuer")["summary"] == "did:web"
    assert _sse_check(events, "validity")["ok"] is True
    validity = _sse_check(events, "validity")
    assert validity["summary"] == "active"
    assert "valid_from_display" in validity
    assert "valid_until_display" in validity
    assert " – " in validity["period_display"]
    assert _sse_check(events, "credentialStatus")["summary"] == "none"
    assert _sse_check(events, "renderMethod")["ok"] is True
    assert _sse_check(events, "renderMethod")["summary"] == "fallback"
    assert _sse_check(events, "renderMethod")["render_suite"] == ""
    context_events = _sse_by_type(events, "context")
    assert context_events
    ctx = context_events[0]
    assert ctx["language"] == "en"
    assert "en" in (ctx.get("overlays_i18n") or {})
    assert "fr" in (ctx.get("overlays_i18n") or {})
    assert "Permit number" in ctx["html"]
    assert "C-217" in ctx["html"]
    assert "BASIN COAL MINE COMPANY" in ctx["html"]
    assert 'class="oca-doc"' in ctx["html"]
    assert "oca-overlay-i18n" in ctx["html"]
    assert _sse_by_type(events, "done")
    assert not _sse_by_type(events, "error")
    assert not _sse_by_type(events, "fields")


def test_view_stream_unsafe_mode_fetches_remote(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    record = {
        "id": "cred-1",
        "type": "BCMinesActPermitCredential",
        "entity_id": "urn:entity",
        "cardinality_id": "C-217",
        "refresh": False,
        "revocation": False,
        "suspension": False,
        "vc": _SAMPLE_VC,
        "vc_jwt": _fake_vc_jwt(_SAMPLE_VC),
    }
    _patch_mongo(monkeypatch, lambda: _FakeMongo([record]))
    mocked = _mock_fetch(monkeypatch)
    _mock_jwt_verify(monkeypatch, ok=True, kid="did:web:remote.test#key-01-jwk")
    client = TestClient(_app())
    events = _collect_sse_events(
        client, url="https://remote.test/credentials/cred-1"
    )
    assert mocked.last["url"] == "https://remote.test/credentials/cred-1"
    assert _sse_by_type(events, "done")
    assert _sse_check(events, "envelope")["ok"] is True


def test_view_stream_shows_invalid_jwt(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    record = {
        "id": "cred-1",
        "type": "BCMinesActPermitCredential",
        "entity_id": "urn:entity",
        "cardinality_id": "C-217",
        "refresh": False,
        "revocation": False,
        "suspension": False,
        "vc": _SAMPLE_VC,
        "vc_jwt": _fake_vc_jwt(_SAMPLE_VC),
    }
    _patch_mongo(monkeypatch, lambda: _FakeMongo([record]))
    _mock_fetch(monkeypatch)
    _mock_jwt_verify(monkeypatch, ok=False)
    client = TestClient(_app())
    events = _collect_sse_events(
        client, url="https://publisher.test/credentials/cred-1"
    )
    jwt = _sse_check(events, "envelope")
    assert jwt["summary"] == "vc+jwt"
    assert jwt["media_type"] == "application/vc+jwt"
    assert jwt["verification"] == "JWT invalid"
    assert "signature mismatch" in jwt["error"]
    assert _sse_by_type(events, "done")


def test_view_stream_fetch_404_shows_error(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    _patch_mongo(monkeypatch, lambda: _FakeMongo([]))
    _mock_fetch(monkeypatch, status_code=404)
    client = TestClient(_app())
    events = _collect_sse_events(
        client, url="https://publisher.test/credentials/missing"
    )
    errors = _sse_by_type(events, "error")
    assert errors
    assert "No credential found" in errors[0]["message"]
    assert not _sse_by_type(events, "done")


def test_view_stream_rejects_invalid_envelope(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    _patch_mongo(monkeypatch, lambda: _FakeMongo([]))
    bad = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "id": "data:application/vc+jwt,not-a-jwt",
        "type": "EnvelopedVerifiableCredential",
    }
    _mock_fetch(monkeypatch, bad)
    client = TestClient(_app())
    events = _collect_sse_events(
        client, url="https://publisher.test/credentials/cred-1"
    )
    errors = _sse_by_type(events, "error")
    assert errors
    assert "Invalid EnvelopedVerifiableCredential" in errors[0]["message"]
    assert "compact JWT" in errors[0]["message"]
    assert not _sse_by_type(events, "done")


def test_fetch_and_unwrap_helpers(monkeypatch):
    envelope = _enveloped()
    mocked = _mock_fetch(monkeypatch, envelope)
    fetched = fetch_application_vc("https://publisher.test/credentials/x")
    assert fetched == envelope
    assert mocked.last["headers"]["Accept"] == "application/vc"
    token = extract_vc_jwt(envelope)
    assert token.count(".") == 2
    vc = unwrap_enveloped_vc(envelope)
    assert vc["name"] == _SAMPLE_VC["name"]
    assert decode_jwt_payload(_fake_vc_jwt({"a": 1})) == {"a": 1}
    try:
        decode_jwt_payload("a.!!!not-base64!!!.c")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "base64url" in str(exc)


def test_data_uri_media_type():
    assert (
        data_uri_media_type("data:application/vc+jwt,abc.def.ghi")
        == "application/vc+jwt"
    )
    assert (
        compact_data_uri_media_type("data:application/vc+jwt,abc.def.ghi")
        == "vc+jwt"
    )
    assert data_uri_media_type("data:text/plain;charset=utf-8,hi") == "text/plain"
    assert compact_data_uri_media_type("data:text/plain;charset=utf-8,hi") == "text/plain"
    assert data_uri_media_type("https://example.com") == ""
    assert data_uri_media_type("") == ""


def test_validate_enveloped_credential_rejects_bad_shape():
    good = _enveloped()
    assert validate_enveloped_credential(good).count(".") == 2

    missing_context = dict(good)
    missing_context["@context"] = ["https://example.com/other"]
    try:
        validate_enveloped_credential(missing_context)
        assert False, "expected EnvelopeValidationError"
    except EnvelopeValidationError as exc:
        assert "@context" in str(exc)

    bad_type = dict(good)
    bad_type["type"] = "VerifiableCredential"
    try:
        validate_enveloped_credential(bad_type)
        assert False, "expected EnvelopeValidationError"
    except EnvelopeValidationError as exc:
        assert "EnvelopedVerifiableCredential" in str(exc)

    bad_id = dict(good)
    bad_id["id"] = "https://example.com/not-a-data-uri"
    try:
        validate_enveloped_credential(bad_id)
        assert False, "expected EnvelopeValidationError"
    except EnvelopeValidationError as exc:
        assert "data:application/vc+jwt" in str(exc)

    not_jwt = dict(good)
    not_jwt["id"] = "data:application/vc+jwt,not-a-jwt"
    try:
        validate_enveloped_credential(not_jwt)
        assert False, "expected EnvelopeValidationError"
    except EnvelopeValidationError as exc:
        assert "compact JWT" in str(exc)


def test_parse_same_origin_credential_url(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    assert (
        parse_same_origin_credential_url(
            "https://publisher.test/credentials/abc-123"
        )
        == "abc-123"
    )
    assert parse_same_origin_credential_url("https://evil.example/credentials/x") is None
    assert parse_same_origin_credential_url("https://publisher.test/status-lists/x") is None
    assert (
        parse_same_origin_credential_url(
            "https://publisher.test/credentials/a/b"
        )
        is None
    )
    assert parse_same_origin_credential_url("") is None


def test_parse_credential_url_respects_unsafe_mode(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", False)
    assert parse_credential_url("https://evil.example/credentials/x") is None
    assert (
        parse_credential_url(
            "https://evil.example/credentials/x", allow_remote=True
        )
        == "x"
    )
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    assert parse_credential_url("https://evil.example/credentials/x") == "x"
    assert parse_credential_url("https://evil.example/status-lists/x") is None


def test_resolve_credential_statuses_rejects_remote_when_safe(monkeypatch):
    from app.routers.landing import resolve_credential_statuses

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", False)
    vc = {
        **_SAMPLE_VC,
        "credentialStatus": {
            "type": "BitstringStatusListEntry",
            "statusPurpose": "revocation",
            "statusListIndex": 0,
            "statusListCredential": "https://evil.example/status-lists/list-1",
        },
    }
    result = resolve_credential_statuses(vc)
    assert result["present"] is True
    assert result["ok"] is False
    assert "same-origin" in result["entries"][0]["error"]


def test_resolve_credential_statuses_allows_remote_in_unsafe_mode(monkeypatch):
    from app.plugins.status_list import BitstringStatusList
    from app.routers.landing import resolve_credential_statuses

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    bst = BitstringStatusList()
    encoded = bst.generate("0" * 16)

    status_vc = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "type": ["VerifiableCredential", "BitstringStatusListCredential"],
        "credentialSubject": {
            "type": "BitstringStatusList",
            "encodedList": encoded,
            "statusPurpose": "revocation",
        },
    }
    envelope = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "id": f"data:application/vc+jwt,{_fake_vc_jwt(status_vc)}",
        "type": "EnvelopedVerifiableCredential",
    }
    mocked = _mock_fetch(monkeypatch, envelope)
    vc = {
        **_SAMPLE_VC,
        "credentialStatus": {
            "type": "BitstringStatusListEntry",
            "statusPurpose": "revocation",
            "statusListIndex": 0,
            "statusListCredential": "https://evil.example/status-lists/list-1",
        },
    }
    result = resolve_credential_statuses(vc)
    assert result["present"] is True
    assert result["ok"] is True
    assert result["entries"][0]["label"] == "not revoked"
    assert mocked.last["url"] == "https://evil.example/status-lists/list-1"


def test_resolve_credential_statuses_rejects_arbitrary_path_in_unsafe_mode(monkeypatch):
    from app.routers.landing import resolve_credential_statuses

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    vc = {
        **_SAMPLE_VC,
        "credentialStatus": {
            "type": "BitstringStatusListEntry",
            "statusPurpose": "revocation",
            "statusListIndex": 0,
            "statusListCredential": "https://evil.example/anything/status.json",
        },
    }
    result = resolve_credential_statuses(vc)
    assert result["present"] is True
    assert result["ok"] is False
    assert "/status-lists/" in result["entries"][0]["error"]


def test_validate_vcdm_and_untp_helpers():
    from app.routers.landing import validate_untp_payload, validate_vcdm20_payload

    vcdm = validate_vcdm20_payload(_SAMPLE_VC)
    assert vcdm["ok"] is True

    untp = validate_untp_payload(_SAMPLE_VC)
    assert untp["ok"] is True
    assert untp["kind"] == "dcc_credential"
    assert untp["kind_label"] == "DigitalConformityCredential"
    assert untp["checks"]

    bad = dict(_SAMPLE_VC)
    bad["type"] = ["VerifiableCredential"]
    untp_bad = validate_untp_payload(bad)
    assert untp_bad["ok"] is False
    assert "unsupported" in untp_bad["error"].lower() or "type" in untp_bad["error"].lower()


def test_resolve_credential_statuses_none():
    from app.routers.landing import resolve_credential_statuses

    result = resolve_credential_statuses(_SAMPLE_VC)
    assert result["present"] is False
    assert result["summary"] == "none"
    assert result["entries"] == []


def test_resolve_credential_statuses_checks_bit(monkeypatch):
    from app.plugins.status_list import BitstringStatusList
    from app.routers.landing import resolve_credential_statuses

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    bst = BitstringStatusList()
    encoded = bst.generate("0" * 16)
    encoded_revoked = bst.set_status_bit(encoded, 3, True)

    status_vc = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "type": ["VerifiableCredential", "BitstringStatusListCredential"],
        "credentialSubject": {
            "type": "BitstringStatusList",
            "encodedList": encoded_revoked,
            "statusPurpose": "revocation",
        },
    }
    envelope = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "id": f"data:application/vc+jwt,{_fake_vc_jwt(status_vc)}",
        "type": "EnvelopedVerifiableCredential",
    }
    _mock_fetch(monkeypatch, envelope)

    vc = {
        **_SAMPLE_VC,
        "credentialStatus": {
            "type": "BitstringStatusListEntry",
            "statusPurpose": "revocation",
            "statusListIndex": 3,
            "statusListCredential": "https://publisher.test/status-lists/list-1",
        },
    }
    result = resolve_credential_statuses(vc)
    assert result["present"] is True
    assert result["ok"] is True
    assert result["summary"] == "revoked"
    assert result["entries"][0]["bit_set"] is True
    assert result["entries"][0]["label"] == "revoked"


def test_resolve_render_methods_none_falls_back_to_type():
    from app.routers.landing import resolve_render_methods

    result = resolve_render_methods(
        _SAMPLE_VC, fallback_type="BCMinesActPermitCredential"
    )
    assert result["present"] is False
    assert result["ok"] is True
    assert result["source"] == "credential_type"
    assert isinstance(result["bundle"], dict)
    assert "attributes" in result["bundle"]


def test_resolve_render_methods_vc_only_without_render_method_fails():
    """Contract: no renderMethod + no Mongo fallback_type → no OCA.

    Sample Mines Act VCs are typed DigitalConformityCredential, which has no
    publisher OCA bundle. View must pass CredentialRecord.type as fallback_type
    (or restore renderMethod on publish).
    """
    from app.routers.landing import resolve_render_methods

    vc = {k: v for k, v in _SAMPLE_VC.items() if k != "renderMethod"}
    assert "renderMethod" not in vc
    result = resolve_render_methods(vc)
    assert result["present"] is False
    assert result["ok"] is False
    assert result["bundle"] is None
    assert result["source"] == ""
    assert "no oca" in result["error"].lower()


def test_resolve_render_methods_uses_local_oca(monkeypatch):
    from app.routers.landing import resolve_render_methods

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")

    def _get(*_args, **_kwargs):
        raise AssertionError("internal /templates/.../oca.json must not HTTP-fetch")

    monkeypatch.setattr(view_fetch.requests, "get", _get)

    vc = {
        **_SAMPLE_VC,
        "renderMethod": [
            {
                "type": ["TemplateRenderMethod"],
                "id": (
                    "https://publisher.test/templates/"
                    "BCMinesActPermitCredential/v1.1/oca.json"
                ),
                "name": "Overlay Capture Architecture Bundle",
                "renderSuite": "oca-bundle",
            }
        ],
    }
    result = resolve_render_methods(vc)
    assert result["present"] is True
    assert result["ok"] is True
    assert result["source"] == "renderMethod"
    assert result["entries"][0]["ok"] is True
    assert result["entries"][0]["resolved"] == "local"
    assert result["entries"][0]["render_suite"] == "oca-bundle"
    assert result["bundle"]["attributes"]


def test_resolve_render_methods_fetches_when_type_unknown(monkeypatch):
    from app.routers.landing import resolve_render_methods

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(view_fetch, "assert_view_fetch_host_allowed", lambda _url: None)

    class _Resp:
        status_code = 200
        ok = True

        def json(self):
            return _SAMPLE_OCA

    def _get(url, headers=None, timeout=None, allow_redirects=True, **_kwargs):
        assert url.endswith("/UnknownOcaType/v9/oca.json")
        assert headers and headers.get("Accept") == "application/json"
        assert allow_redirects is False
        return _Resp()

    monkeypatch.setattr(view_fetch.requests, "get", _get)

    vc = {
        **_SAMPLE_VC,
        "renderMethod": [
            {
                "type": ["TemplateRenderMethod"],
                "id": "https://publisher.test/templates/UnknownOcaType/v9/oca.json",
                "name": "Overlay Capture Architecture Bundle",
                "renderSuite": "oca-bundle",
            }
        ],
    }
    result = resolve_render_methods(vc)
    assert result["present"] is True
    assert result["ok"] is True
    assert result["source"] == "renderMethod"
    assert result["entries"][0]["ok"] is True
    assert result["entries"][0]["resolved"] == "http"
    assert result["bundle"] == _SAMPLE_OCA


def test_safe_view_get_blocks_private_remote_ip(monkeypatch):
    from app.routers.landing import ViewFetchError, safe_view_get

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    monkeypatch.setattr(
        view_fetch,
        "resolve_view_fetch_host_ips",
        lambda _host: ["10.0.0.5"],
    )

    def _get(*_args, **_kwargs):
        raise AssertionError("must not fetch private remote IP")

    monkeypatch.setattr(view_fetch.requests, "get", _get)
    try:
        safe_view_get(
            "https://evil.example/credentials/cred-1",
            kind="credential",
            accept="application/vc",
        )
        assert False, "expected ViewFetchError"
    except ViewFetchError as exc:
        assert "non-public" in str(exc)


def test_safe_view_get_refuses_redirects(monkeypatch):
    from app.routers.landing import ViewFetchError, safe_view_get

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(view_fetch, "assert_view_fetch_host_allowed", lambda _url: None)

    class _Resp:
        status_code = 302
        ok = False

        def json(self):
            raise AssertionError("redirect body unused")

    def _get(url, headers=None, timeout=None, allow_redirects=True, **_kwargs):
        assert allow_redirects is False
        return _Resp()

    monkeypatch.setattr(view_fetch.requests, "get", _get)
    try:
        safe_view_get(
            "https://publisher.test/credentials/cred-1",
            kind="credential",
            accept="application/vc",
        )
        assert False, "expected ViewFetchError"
    except ViewFetchError as exc:
        assert "redirect" in str(exc).lower()


def test_safe_view_get_rejects_userinfo(monkeypatch):
    from app.routers.landing import ViewFetchError, validate_view_fetch_url

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    try:
        validate_view_fetch_url(
            "https://user:pass@evil.example/credentials/cred-1",
            kind="credential",
        )
        assert False, "expected ViewFetchError"
    except ViewFetchError as exc:
        assert "userinfo" in str(exc).lower()


def test_ip_is_blocked_for_view_fetch():
    from app.routers.landing import ip_is_blocked_for_view_fetch

    assert ip_is_blocked_for_view_fetch("127.0.0.1") is True
    assert ip_is_blocked_for_view_fetch("10.1.2.3") is True
    assert ip_is_blocked_for_view_fetch("169.254.169.254") is True
    assert ip_is_blocked_for_view_fetch("::1") is True
    assert ip_is_blocked_for_view_fetch("8.8.8.8") is False
    assert ip_is_blocked_for_view_fetch("not-an-ip") is True


def test_resolve_render_methods_local_ignores_host(monkeypatch):
    """Published VCs may point at another host; still use our configs when type matches."""
    from app.routers.landing import resolve_render_methods

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)

    def _get(*_args, **_kwargs):
        raise AssertionError("should resolve from local configs")

    monkeypatch.setattr(view_fetch.requests, "get", _get)

    vc = {
        **_SAMPLE_VC,
        "renderMethod": [
            {
                "type": ["TemplateRenderMethod"],
                "id": (
                    "https://other.example/templates/"
                    "BCMinesActPermitCredential/v1.1/oca.json"
                ),
                "renderSuite": "oca-bundle",
            }
        ],
    }
    result = resolve_render_methods(vc)
    assert result["ok"] is True
    assert result["entries"][0]["resolved"] == "local"
    assert result["bundle"]["attributes"]


def test_validate_jsonld_contexts_sample():
    from app.routers.landing import validate_jsonld_contexts

    result = validate_jsonld_contexts(_SAMPLE_VC)
    assert result["ok"] is True
    assert result["rdf_nquads_length"] > 0
    urls = [c["value"] for c in result["contexts"] if c["kind"] == "url"]
    assert "https://www.w3.org/ns/credentials/v2" in urls
    assert result["digests"]


def test_validate_jsonld_contexts_rejects_unbundled():
    from app.routers.landing import validate_jsonld_contexts

    bad = {
        "@context": ["https://example.invalid/unbundled-context.jsonld"],
        "id": "urn:test:credential",
        "type": ["VerifiableCredential"],
    }
    result = validate_jsonld_contexts(bad)
    assert result["ok"] is False
    assert "not bundled" in result["error"] or "CONTEXT_BUNDLE" in result["error"]


def test_soft_resolve_and_oca_fields():
    assert soft_resolve_json_pointer({"a": {"b": 1}}, "/a/b") == 1
    assert soft_resolve_json_pointer({"a": {}}, "/a/missing") is None
    assert format_oca_value(True) == "Yes"
    assert format_oca_value(None) == "—"
    assert (
        format_oca_value("2026-08-10T18:37:35Z", attr_type="DateTime")
        == "10 Aug 2026, 18:37 UTC"
    )
    assert (
        format_oca_value("2026-05-01T00:00:00+00:00", attr_type="DateTime")
        == "01 May 2026"
    )

    from app.routers.landing import build_oca_template_context

    context = build_oca_template_context(_SAMPLE_VC, _SAMPLE_OCA, "en")
    assert context["error"] == ""
    assert context["language"] == "en"
    assert "en" in context["languages"]
    assert "fr" in context["languages"]
    pointer = "/credentialSubject/conformityAssessment/0/registeredId"
    assert pointer in context["order"]
    permit = context["attributes"][pointer]
    assert permit["label"] == "Permit number"
    assert permit["value"] == "C-217"
    assert permit["flagged"] is True
    assert pointer in context["flagged"]

    holder = context["attributes"]["/credentialSubject/issuedToParty/name"]
    assert holder["label"] == "Permittee"
    assert holder["value"] == "BASIN COAL MINE COMPANY"
    assert context["presentation"]["subtitle"] == (
        "Mines Act permit issued to BASIN COAL MINE COMPANY for Basin Coal Mine."
    )

    # Older published descriptions appended `` (permit X)`` — strip in the view.
    legacy = dict(_SAMPLE_VC)
    legacy_subject = dict(legacy["credentialSubject"])
    legacy_subject["description"] = (
        "Mines Act permit issued to BASIN COAL MINE COMPANY for Basin Coal Mine "
        "(permit C-217)."
    )
    legacy["credentialSubject"] = legacy_subject
    legacy_ctx = build_oca_template_context(legacy, _SAMPLE_OCA, "en")
    assert legacy_ctx["presentation"]["subtitle"] == (
        "Mines Act permit issued to BASIN COAL MINE COMPANY for Basin Coal Mine."
    )

    legacy_subject["name"] = "Mines Act Permit C-217 — BASIN COAL MINE COMPANY"
    legacy["credentialSubject"] = legacy_subject
    legacy_name_ctx = build_oca_template_context(legacy, _SAMPLE_OCA, "en")
    assert legacy_name_ctx["presentation"]["title"] == "Mines Act Permit"

    fr = build_oca_template_context(_SAMPLE_VC, _SAMPLE_OCA, "fr")
    assert fr["language"] == "fr"
    assert fr["attributes"][pointer]["label"] == "Numéro de permis"

    from app.routers.landing import render_oca_box_html

    html = render_oca_box_html(context, page_url="https://publisher.test/credentials/x")
    assert "Permit number" in html
    assert "C-217" in html
    assert 'class="oca-doc"' in html
    assert "oca-title" in html
    assert "oca-lang" in html
    assert "oca-lang-btn" in html
    assert 'for="oca-overlays-toggle"' in html
    assert 'target="_parent"' not in html
    assert "data-oca-url=" not in html
    fr_html = render_oca_box_html(
        build_oca_template_context(_SAMPLE_VC, _SAMPLE_OCA, "fr"),
        page_url="https://publisher.test/credentials/x",
    )
    assert "Site minier" in fr_html
    assert "Substances" in fr_html
    assert 'data-oca-lang="fr"' in fr_html
    assert "oca-overlay-i18n" in fr_html
    en_ctx = build_oca_template_context(_SAMPLE_VC, _SAMPLE_OCA, "en")
    assert "en" in en_ctx["overlays_i18n"]
    assert "fr" in en_ctx["overlays_i18n"]
    assert (
        en_ctx["overlays_i18n"]["fr"]["labels"][
            "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name"
        ]
        == "Substances"
    )
    assert en_ctx["overlays_i18n"]["fr"]["ui"]["generated_prefix"] == "Généré"
    assert en_ctx["overlays_i18n"]["fr"]["ui"]["verified"] == "Vérifié"
    assert en_ctx["overlays_i18n"]["fr"]["ui"]["unverified"] == "Non vérifié"
    assert "Gouvernance" in fr_html
    assert "Titulaire" in fr_html
    assert "Généré" in fr_html
    assert "Vérifié" in fr_html
    assert 'data-oca-ui="generated_prefix"' in html
    assert 'data-oca-ui="verified"' in html
    attestation_for_pointer = next(
        s for s in context["presentation"]["sections"] if s.get("id") == "attestation"
    )
    assert (
        attestation_for_pointer.get("title_pointer")
        == "/credentialSubject/referenceProfile/name"
    )
    assert 'data-oca-pointer="/credentialSubject/referenceProfile/name"' in html
    assert 'id="oca-overlays-toggle"' in html
    assert 'id="oca-overlays"' in html
    assert "oca-lexicon" in html
    assert 'class="oca-footer"' in html
    assert "UTC" in html
    assert context["presentation"]["flow"].get("issuer_label") == "Issuer"
    assert context["presentation"]["flow"].get("assessment_label") == "Permit number"
    assert context["presentation"]["flow"].get("organisation_label") == "Permittee"
    assert context["presentation"]["flow"].get("facility_label") == "Mining Site"
    # Default view keeps the overlays lexicon collapsed (checkbox unchecked).
    toggle_open = html.split('id="oca-overlays-toggle"', 1)[1].split(">", 1)[0]
    assert "checked" not in toggle_open
    debug_html = render_oca_box_html(
        context, page_url="https://publisher.test/credentials/x", debug=True
    )
    assert 'id="oca-overlays-toggle"' in debug_html
    # debug=True starts with the overlays lexicon visible (checkbox checked).
    debug_toggle_open = debug_html.split('id="oca-overlays-toggle"', 1)[1].split(">", 1)[0]
    assert "checked" in debug_toggle_open
    assert context["presentation"]["title"]
    assert context["presentation"]["sections"]
    assert context["presentation"]["stats"] == []
    kinds = {s["kind"] for s in context["presentation"]["sections"]}
    assert "links" in kinds or "entity" in kinds or "panel" in kinds or "product" in kinds
    attestation = next(
        s for s in context["presentation"]["sections"] if s.get("id") == "attestation"
    )
    assert attestation["kind"] == "links"
    assert attestation["headline"]["value"] == "BC Mines Act Permit Governance"
    assert attestation["title"] == "Governance"
    assert any(b.get("value") == "certification" for b in attestation.get("badges") or [])
    assert any(b.get("value") == "authority-mandate" for b in attestation.get("badges") or [])
    fact_labels = {f.get("label") for f in attestation.get("facts") or []}
    assert "Regulation" in fact_labels
    assert "Criterion" in fact_labels
    assert "Assessment level" not in fact_labels
    assert "Assessor level" not in fact_labels
    assert "oca-stats" not in html
    assert 'class="oca-chip"' not in html
    assert "oca-scheme-lead" in html
    assert "oca-scheme-meta" in html
    assert "Permit title" not in html.split("oca-overlay-i18n", 1)[0]
    assert any(
        s.get("headline") or s.get("chips") or s.get("facts") or s.get("entries")
        for s in context["presentation"]["sections"]
    )
    product_sections = [
        s for s in context["presentation"]["sections"] if s.get("id") == "product"
    ]
    assert len(product_sections) == 1
    assert len(product_sections[0].get("entries") or []) == 1
    assert product_sections[0]["title"] == "Commodities"
    assert product_sections[0]["entries"][0]["headline"]["value"] == "Metallurgic"
    assert "product" not in (context["presentation"].get("flow") or {})
    assert "oca-card" in html
    assert "oca-card-item" in html
    assert "Metallurgic" in html
    assert html.count("oca-flow-small") == 2
    assert "oca-chip" in html or "oca-card-headline" in html

    fields = oca_fields_for_vc(_SAMPLE_VC, _SAMPLE_OCA, "en")
    by_pointer = {f["pointer"]: f for f in fields}
    permit_row = by_pointer[pointer]
    assert permit_row["label"] == "Permit number"
    assert permit_row["value"] == "C-217"
    assert permit_row["flagged"] is True

    holder_row = by_pointer["/credentialSubject/issuedToParty/name"]
    assert holder_row["label"] == "Permittee"
    assert holder_row["value"] == "BASIN COAL MINE COMPANY"
    name_pointer = (
        "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name"
    )
    assert name_pointer in by_pointer
    assert by_pointer[name_pointer]["label"] == "Commodities"
    assert by_pointer[name_pointer]["value"] == "Metallurgic"
    assert (
        "/credentialSubject/conformityAssessment/0/assessedProduct"
        not in by_pointer
    )
    assert (
        "/credentialSubject/conformityAssessment/0/assessedProduct/*/product/name"
        not in by_pointer
    )


def test_oca_array_star_expands_multiple_commodities():
    from copy import deepcopy

    from app.routers.landing import build_oca_template_context, render_oca_box_html
    from app.view.oca import expand_oca_array_attributes

    vc = deepcopy(_SAMPLE_VC)
    products = vc["credentialSubject"]["conformityAssessment"][0]["assessedProduct"]
    products.append(
        {
            "product": {
                "type": ["Product"],
                "id": "urn:ca:bcgov:mines-act:permit:C-217:commodity:copper",
                "name": "Copper",
            },
            "idVerifiedByCAB": True,
        }
    )

    array_root = "/credentialSubject/conformityAssessment/0/assessedProduct"
    star_name = f"{array_root}/*/product/name"
    expanded, labels, _info, _flagged, order = expand_oca_array_attributes(
        {
            array_root: "Array",
            star_name: "Text",
            f"{array_root}/*/product/id": "Text",
        },
        {star_name: "Commodities"},
        {},
        [],
        vc,
    )
    assert array_root not in expanded
    assert star_name not in expanded
    assert f"{array_root}/0/product/name" in expanded
    assert f"{array_root}/1/product/name" in expanded
    assert f"{array_root}/0/product/name" in order
    assert f"{array_root}/1/product/name" in order
    assert labels[f"{array_root}/1/product/name"] == "Commodities"

    context = build_oca_template_context(vc, _SAMPLE_OCA, "en")
    assert context["error"] == ""
    product = next(
        s for s in context["presentation"]["sections"] if s.get("id") == "product"
    )
    assert product["title"] == "Commodities"
    assert len(product["entries"]) == 2
    names = [item["headline"]["value"] for item in product["entries"]]
    assert names == ["Metallurgic", "Copper"]
    assert "product" not in context["presentation"]["flow"]

    html = render_oca_box_html(
        context, page_url="https://publisher.test/credentials/x"
    )
    assert "Metallurgic" in html
    assert "Copper" in html
    assert html.count('class="oca-card-item"') == 2
    assert html.count("oca-flow-small") == 2
    assert "Commodities" in html

    fr = build_oca_template_context(vc, _SAMPLE_OCA, "fr")
    fr_product = next(
        s for s in fr["presentation"]["sections"] if s.get("id") == "product"
    )
    assert fr_product["title"] == "Substances"
    assert len(fr_product["entries"]) == 2


def test_safe_css_color_allows_hex_only():
    assert safe_css_color("#013366", default="#000") == "#013366"
    assert safe_css_color("#fcb", default="#000") == "#fcb"
    assert safe_css_color("#FCBA19AA", default="#000") == "#FCBA19AA"
    assert safe_css_color("red", default="#013366") == "#013366"
    assert safe_css_color("url(evil)", default="#013366") == "#013366"
    assert safe_css_color("", default="#013366") == "#013366"


def test_safe_http_url_rejects_non_http():
    assert safe_http_url("https://example.com/x") == "https://example.com/x"
    assert safe_http_url("http://localhost:8000/") == "http://localhost:8000/"
    assert safe_http_url("javascript:alert(1)") == ""
    assert safe_http_url("data:text/html,x") == ""
    assert safe_http_url("/relative") == ""
    assert safe_http_url("") == ""


def test_safe_asset_url_allows_path_or_http():
    assert safe_asset_url("/static/logo.svg", default="/d.svg") == "/static/logo.svg"
    assert (
        safe_asset_url("https://cdn.example/l.png", default="/d.svg")
        == "https://cdn.example/l.png"
    )
    assert safe_asset_url("//evil.example/x", default="/d.svg") == "/d.svg"
    assert safe_asset_url("javascript:alert(1)", default="/d.svg") == "/d.svg"
    assert safe_asset_url("", default="/d.svg") == "/d.svg"
