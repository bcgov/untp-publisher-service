"""Unit tests for atomic status-list index claims."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.plugins.mongodb import MongoClient


def test_claim_status_list_index_uses_atomic_pop(monkeypatch):
    monkeypatch.setattr(
        "app.services.composer.status_list_endpoint",
        lambda list_id: f"http://localhost:8000/status-lists/{list_id}",
    )
    client = MongoClient.__new__(MongoClient)
    collection = MagicMock()
    client.db = {"StatusListRecord": collection}
    collection.find_one_and_update.return_value = {
        "id": "list-1",
        "endpoint": "https://example.com/status-lists/list-1",
        "indexes": [10, 20, 30],
    }

    claimed = client.claim_status_list_index(
        issuer_id="did:web:example:issuer",
        purpose="revocation",
    )

    assert claimed == {
        "index": 30,
        "endpoint": "http://localhost:8000/status-lists/list-1",
        "id": "list-1",
    }


def test_claim_status_list_index_returns_none_when_exhausted():
    client = MongoClient.__new__(MongoClient)
    collection = MagicMock()
    client.db = {"StatusListRecord": collection}
    collection.find_one_and_update.return_value = None

    assert (
        client.claim_status_list_index(
            issuer_id="did:web:example:issuer",
            purpose="suspension",
        )
        is None
    )
