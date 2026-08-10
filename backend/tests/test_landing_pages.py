"""Smoke tests for public landing and discovery HTML pages."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import landing
from app.routers.landing import safe_asset_url, safe_css_color, safe_http_url
from config import settings


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


def test_landing_renders_partner_link(monkeypatch):
    monkeypatch.setattr(settings, "LANDING_PARTNER_URL", "https://mines.nrs.gov.bc.ca/")
    monkeypatch.setattr(settings, "LANDING_PARTNER_LABEL", "BC Mine Information")
    client = TestClient(_app())
    response = client.get("/")
    assert response.status_code == 200
    assert b'class="pub-topbar-partner"' in response.content
    assert b"https://mines.nrs.gov.bc.ca/" in response.content
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
    assert b'class="pub-topbar-partner"' in response.content
    assert b"https://example.com/partner" in response.content
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
