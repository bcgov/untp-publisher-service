"""Smoke tests for public landing and discovery HTML pages."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import landing
from app.routers.landing import safe_asset_url, safe_css_color, safe_http_url


class _FakeMongo:
    def __init__(self, records=None):
        self.records = list(records or [])

    def find(self, collection, query):
        assert collection == "CredentialRecord"
        return iter(self.records)


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


def test_discovery_returns_200_when_mongo_empty(monkeypatch):
    monkeypatch.setattr(landing, "MongoClient", lambda: _FakeMongo([]))
    client = TestClient(_app())
    response = client.get("/discovery")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert b"/static/js/discovery.js" in response.content
    assert b"Discover" in response.content or b"discovery" in response.content.lower()


def test_discovery_returns_200_when_mongo_raises(monkeypatch):
    class _Boom:
        def find(self, collection, query):
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
