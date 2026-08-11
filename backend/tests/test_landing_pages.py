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

    def _get(url, headers=None, timeout=None):
        assert headers and headers.get("Accept") == "application/vc"
        _get.last = {"url": url, "headers": headers, "timeout": timeout}
        return _Resp()

    _get.last = None
    monkeypatch.setattr(landing.requests, "get", _get)
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

    monkeypatch.setattr(landing, "TractionController", _Traction)


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
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
    client = TestClient(_app())
    response = client.get("/discovery")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert b"/static/js/discovery.js" in response.content
    assert b"Discover" in response.content or b"discovery" in response.content.lower()


def test_discovery_renders_partner_link(monkeypatch):
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
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
    monkeypatch.setattr(landing, "MongoClient", lambda: fake)
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

    monkeypatch.setattr(landing, "MongoClient", lambda: _Boom())
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
        "vc_jwt": "eyJ.e30.sig",
    }
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([record]))
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
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([stale, active]))
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
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([record]))
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
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
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
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
    client = TestClient(_app())
    response = client.get("/view", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == "/discovery"


def test_view_without_url_shows_resolver_in_unsafe_mode(monkeypatch):
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
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
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
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
    assert b'data-check="envelope"' in response.content


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
    assert b"same-origin" not in response.content
    assert b"/view/stream?url=" in response.content


def test_view_unsafe_mode_still_rejects_bad_path(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
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
        "vc_jwt": "eyJ.e30.sig",
    }
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([record]))
    mocked = _mock_fetch(monkeypatch)
    _mock_jwt_verify(monkeypatch, ok=True, kid="did:web:publisher.test#key-01-jwk")
    client = TestClient(_app())
    events = _collect_sse_events(
        client, url="https://publisher.test/credentials/cred-1"
    )
    assert mocked.last is not None
    assert mocked.last["url"] == "https://publisher.test/credentials/cred-1"
    assert mocked.last["headers"]["Accept"] == "application/vc"

    assert _sse_by_type(events, "progress")
    assert _sse_by_type(events, "meta")
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
    assert "Permit number" in ctx["html"]
    assert "C-217" in ctx["html"]
    assert "BASIN COAL MINE COMPANY" in ctx["html"]
    assert 'class="oca-doc"' in ctx["html"]
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
        "vc_jwt": "eyJ.e30.sig",
    }
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([record]))
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
        "vc_jwt": "eyJ.e30.sig",
    }
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([record]))
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
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
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
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
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
            "statusListCredential": "https://evil.example/anything/status.json",
        },
    }
    result = resolve_credential_statuses(vc)
    assert result["present"] is True
    assert result["ok"] is True
    assert result["entries"][0]["label"] == "not revoked"
    assert mocked.last["url"] == "https://evil.example/anything/status.json"


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


def test_resolve_render_methods_uses_local_oca(monkeypatch):
    from app.routers.landing import resolve_render_methods

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")

    def _get(*_args, **_kwargs):
        raise AssertionError("internal /templates/.../oca.json must not HTTP-fetch")

    monkeypatch.setattr(landing.requests, "get", _get)

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

    class _Resp:
        status_code = 200
        ok = True

        def json(self):
            return _SAMPLE_OCA

    def _get(url, headers=None, timeout=None):
        assert url.endswith("/UnknownOcaType/v9/oca.json")
        assert headers and headers.get("Accept") == "application/json"
        return _Resp()

    monkeypatch.setattr(landing.requests, "get", _get)

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


def test_resolve_render_methods_local_ignores_host(monkeypatch):
    """Published VCs may point at another host; still use our configs when type matches."""
    from app.routers.landing import resolve_render_methods

    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")
    monkeypatch.setattr(settings, "VIEW_UNSAFE_MODE", True)

    def _get(*_args, **_kwargs):
        raise AssertionError("should resolve from local configs")

    monkeypatch.setattr(landing.requests, "get", _get)

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
    assert legacy_name_ctx["presentation"]["title"] == "Mines Act Permit C-217"

    fr = build_oca_template_context(_SAMPLE_VC, _SAMPLE_OCA, "fr")
    assert fr["language"] == "fr"
    assert fr["attributes"][pointer]["label"] == "Numéro de permis"

    from app.routers.landing import render_oca_box_html

    html = render_oca_box_html(context, page_url="https://publisher.test/credentials/x")
    assert "Permit number" in html
    assert "C-217" in html
    assert 'class="oca-doc"' in html
    assert "hreflang=\"fr\"" in html
    assert "data-oca-lang=\"fr\"" in html
    assert "oca-title" in html
    assert 'data-oca-info-toggle' in html
    assert 'id="oca-overlays"' in html
    assert "Semantic overlays" in html
    assert "oca-lexicon" in html
    assert 'class="oca-footer"' in html
    assert "Rendered" in html
    assert "UTC" in html
    # Default view keeps the overlays lexicon collapsed.
    assert 'id="oca-overlays"' in html and "hidden" in html.split('id="oca-overlays"', 1)[1][:100]
    debug_html = render_oca_box_html(
        context, page_url="https://publisher.test/credentials/x", debug=True
    )
    assert 'data-oca-info-toggle' in debug_html
    assert "is-active" in debug_html
    # debug=True starts with the overlays lexicon visible.
    debug_chunk = debug_html.split('id="oca-overlays"', 1)[1][:120]
    assert "hidden" not in debug_chunk.split(">", 1)[0]
    assert context["presentation"]["title"]
    assert context["presentation"]["sections"]
    kinds = {s["kind"] for s in context["presentation"]["sections"]}
    assert "chips" in kinds or "entity" in kinds or "panel" in kinds
    assert any(s.get("headline") or s.get("chips") or s.get("facts") for s in context["presentation"]["sections"])
    assert "oca-card" in html
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
