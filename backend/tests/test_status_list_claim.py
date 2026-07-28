"""Unit tests for atomic status-list index claims."""

from __future__ import annotations

from unittest.mock import MagicMock

from pymongo import ReturnDocument

from app.plugins.mongodb import MongoClient


def test_claim_status_list_index_uses_atomic_pop():
    client = MongoClient.__new__(MongoClient)
    collection = MagicMock()
    client.db = {"StatusListRecord": collection}
    collection.find_one_and_update.return_value = {
        "id": "list-1",
        "endpoint": "https://publisher.example/status-lists/list-1",
        "indexes": [10, 20, 30],
    }

    claimed = client.claim_status_list_index(
        issuer_id="did:web:example:issuer",
        purpose="revocation",
    )

    assert claimed == {
        "index": 30,
        "endpoint": "https://publisher.example/status-lists/list-1",
        "id": "list-1",
    }
    collection.find_one_and_update.assert_called_once()
    args, kwargs = collection.find_one_and_update.call_args
    assert args[0]["issuer"] == "did:web:example:issuer"
    assert args[0]["purpose"] == "revocation"
    assert args[0]["active"] is True
    assert args[0]["indexes.0"] == {"$exists": True}
    assert args[1] == {"$pop": {"indexes": 1}}
    assert kwargs["return_document"] == ReturnDocument.BEFORE


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
