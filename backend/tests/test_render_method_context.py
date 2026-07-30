"""Tests for render-method context attachment on issued credentials."""

from __future__ import annotations

from app.services.composer import (
    RENDER_METHOD_CONTEXT_URL,
    ensure_render_method_context,
)


def test_ensure_render_method_context_appends_once():
    credential = {
        "@context": [
            "https://www.w3.org/ns/credentials/v2",
            "https://vocabulary.uncefact.org/untp/0.7.0/context/",
        ]
    }
    ensure_render_method_context(credential)
    ensure_render_method_context(credential)
    assert credential["@context"][-1] == RENDER_METHOD_CONTEXT_URL
    assert credential["@context"].count(RENDER_METHOD_CONTEXT_URL) == 1
