"""Tests for MongoDB schema migration runner."""

from __future__ import annotations

from unittest.mock import MagicMock

import pymongo
import pytest

from migrations import runner


class _FakeSchemaCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.create_index = MagicMock()

    def find_one(self, query):
        mid = query.get("id")
        return self.docs.get(mid)

    def insert_one(self, doc):
        mid = doc["id"]
        if mid in self.docs:
            raise pymongo.errors.DuplicateKeyError("dup")
        self.docs[mid] = dict(doc)
        return MagicMock(inserted_id=mid)

    def update_one(self, query, update):
        mid = query["id"]
        doc = self.docs.get(mid)
        if not doc:
            return MagicMock(matched_count=0)
        doc.update(update.get("$set", {}))
        return MagicMock(matched_count=1)

    def delete_one(self, query):
        mid = query["id"]
        doc = self.docs.get(mid)
        if not doc:
            return MagicMock(deleted_count=0)
        if "status" in query and doc.get("status") != query["status"]:
            return MagicMock(deleted_count=0)
        del self.docs[mid]
        return MagicMock(deleted_count=1)


class _FakeDb(dict):
    def __init__(self, schema: _FakeSchemaCollection):
        super().__init__()
        self._schema = schema

    def __getitem__(self, key):
        if key == runner._SCHEMA_COLLECTION:
            return self._schema
        coll = MagicMock()
        super().__setitem__(key, coll)
        return coll


def test_run_migrations_applies_pending(monkeypatch):
    schema = _FakeSchemaCollection()
    db = _FakeDb(schema)
    calls: list[str] = []

    def fake_load(module_name: str):
        mid = module_name

        def up(_db):
            calls.append(mid)

        return mid, up

    monkeypatch.setattr(runner, "_load_migration", fake_load)
    monkeypatch.setattr(
        runner,
        "_MIGRATION_MODULES",
        ("001_initial_indexes", "002_credential_template_type_version_index"),
    )

    applied = runner.run_migrations(db)

    assert applied == [
        "001_initial_indexes",
        "002_credential_template_type_version_index",
    ]
    assert calls == applied
    assert schema.docs["001_initial_indexes"]["status"] == "applied"
    assert schema.docs["002_credential_template_type_version_index"]["status"] == "applied"


def test_run_migrations_skips_already_applied(monkeypatch):
    schema = _FakeSchemaCollection()
    schema.docs["001_initial_indexes"] = {
        "id": "001_initial_indexes",
        "status": "applied",
    }
    db = _FakeDb(schema)
    calls: list[str] = []

    def fake_load(module_name: str):
        def up(_db):
            calls.append(module_name)

        return module_name, up

    monkeypatch.setattr(runner, "_load_migration", fake_load)
    monkeypatch.setattr(runner, "_MIGRATION_MODULES", ("001_initial_indexes",))

    applied = runner.run_migrations(db)

    assert applied == []
    assert calls == []


def test_run_migrations_skips_when_claim_lost(monkeypatch):
    schema = _FakeSchemaCollection()
    db = _FakeDb(schema)

    def fake_load(module_name: str):
        return module_name, MagicMock()

    monkeypatch.setattr(runner, "_load_migration", fake_load)
    monkeypatch.setattr(runner, "_MIGRATION_MODULES", ("001_initial_indexes",))
    monkeypatch.setattr(runner, "_claim_migration", lambda _db, _mid: False)

    applied = runner.run_migrations(db)

    assert applied == []


def test_run_migrations_releases_claim_on_failure(monkeypatch):
    schema = _FakeSchemaCollection()
    db = _FakeDb(schema)

    def fake_load(module_name: str):
        def up(_db):
            raise RuntimeError("boom")

        return module_name, up

    monkeypatch.setattr(runner, "_load_migration", fake_load)
    monkeypatch.setattr(runner, "_MIGRATION_MODULES", ("001_initial_indexes",))

    with pytest.raises(RuntimeError, match="boom"):
        runner.run_migrations(db)

    assert "001_initial_indexes" not in schema.docs


def test_initial_indexes_migration_creates_expected_indexes():
    from importlib import import_module

    mod = import_module("migrations.001_initial_indexes")
    db = MagicMock()
    collections: dict[str, MagicMock] = {}

    def getitem(name):
        if name not in collections:
            collections[name] = MagicMock()
        return collections[name]

    db.__getitem__.side_effect = getitem
    mod.up(db)

    collections["IssuerInstanceRecord"].create_index.assert_called_once()
    collections["CredentialRecord"].create_index.assert_called_once()
    collections["StatusListRecord"].create_index.assert_any_call([("id")], unique=True)
    collections["CredentialTemplateRecord"].create_index.assert_called_once_with(
        [("version")], unique=True
    )
    collections["CredentialPickupRecord"].create_index.assert_called_once()


def test_type_version_migration_drops_legacy_and_creates_compound():
    from importlib import import_module

    mod = import_module("migrations.002_credential_template_type_version_index")
    coll = MagicMock()
    db = {"CredentialTemplateRecord": coll}
    mod.up(db)

    coll.drop_index.assert_called_once_with("version_1")
    coll.create_index.assert_called_once_with(
        [("type", pymongo.ASCENDING), ("version", pymongo.ASCENDING)],
        unique=True,
        name="type_version",
    )
