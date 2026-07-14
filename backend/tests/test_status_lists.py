"""Tests for idempotent issuer status-list provisioning."""

from __future__ import annotations

import asyncio

from app.services.status_lists import STATUS_PURPOSES, ensure_issuer_status_lists


class _FakeMongo:
    def __init__(self):
        self.records: list[dict] = []

    def find_one(self, collection, query):
        assert collection == "StatusListRecord"
        for record in self.records:
            if all(record.get(key) == value for key, value in query.items()):
                return record
        return None

    def insert(self, collection, item):
        assert collection == "StatusListRecord"
        self.records.append(item)


def test_ensure_issuer_status_lists_creates_three_active_lists(monkeypatch):
    monkeypatch.setattr(
        "app.services.status_lists.publisher_origin",
        lambda: "https://publisher.example",
    )
    mongo = _FakeMongo()
    issuer_id = "did:web:example:issuer"

    first = asyncio.run(ensure_issuer_status_lists(issuer_id, mongo=mongo))
    assert len(first) == 3
    assert {item["purpose"] for item in first} == set(STATUS_PURPOSES)
    assert all(item["active"] is True for item in first)
    assert all(item["issuer"] == issuer_id for item in first)
    assert all(
        item["credential"]["credentialSubject"]["statusPurpose"] == item["purpose"]
        for item in first
    )
    assert len(mongo.records) == 3

    second = asyncio.run(ensure_issuer_status_lists(issuer_id, mongo=mongo))
    assert len(second) == 3
    assert len(mongo.records) == 3
    assert {item["id"] for item in second} == {item["id"] for item in first}
