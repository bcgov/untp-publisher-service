"""Tests for the publisher JSON-LD extension context."""

from __future__ import annotations

from app.repo_configs.loader import load_publisher_extension_context
from app.services.credential_builder import (
    ensure_publisher_extension_context,
    publisher_extension_context_url,
)


def test_load_publisher_extension_context_defines_terms():
    document = load_publisher_extension_context()
    terms = document["@context"]
    assert "SimpleRefreshQuery" in terms
    assert "OCABundle" in terms


def test_ensure_publisher_extension_context_appends_once(monkeypatch):
    monkeypatch.setattr(
        "app.services.credential_builder.settings.PUBLISHER_DOMAIN",
        "https://publisher.example",
    )
    url = publisher_extension_context_url()
    assert url == "https://publisher.example/contexts/publisher/v1"

    credential = {
        "@context": [
            "https://www.w3.org/ns/credentials/v2",
            "https://vocabulary.uncefact.org/untp/0.7.0/context/",
        ]
    }
    ensure_publisher_extension_context(credential)
    ensure_publisher_extension_context(credential)
    assert credential["@context"][-1] == url
    assert credential["@context"].count(url) == 1
