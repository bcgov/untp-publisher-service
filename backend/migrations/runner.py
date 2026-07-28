"""Run ordered MongoDB migrations once per deployment.

Uses a ``SchemaMigration`` collection so horizontally scaled pods do not re-apply
the same migration. Claiming a migration id is atomic via a unique insert.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from typing import Callable

import pymongo

from config import settings

# Ordered migration module names (without package prefix).
_MIGRATION_MODULES = (
    "001_initial_indexes",
    "002_credential_template_type_version_index",
)

_SCHEMA_COLLECTION = "SchemaMigration"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_schema_collection(db: pymongo.database.Database) -> None:
    db[_SCHEMA_COLLECTION].create_index([("id")], unique=True)


def _claim_migration(db: pymongo.database.Database, migration_id: str) -> bool:
    """Return True if this process claimed ``migration_id`` and should run it."""
    try:
        db[_SCHEMA_COLLECTION].insert_one(
            {
                "id": migration_id,
                "status": "running",
                "started_at": _utc_now(),
            }
        )
        return True
    except pymongo.errors.DuplicateKeyError:
        return False


def _mark_applied(db: pymongo.database.Database, migration_id: str) -> None:
    db[_SCHEMA_COLLECTION].update_one(
        {"id": migration_id},
        {
            "$set": {
                "status": "applied",
                "applied_at": _utc_now(),
            }
        },
    )


def _mark_failed(db: pymongo.database.Database, migration_id: str, error: str) -> None:
    db[_SCHEMA_COLLECTION].update_one(
        {"id": migration_id},
        {
            "$set": {
                "status": "failed",
                "failed_at": _utc_now(),
                "error": error,
            }
        },
    )
    # Allow a later pod/start to retry by releasing the unique claim.
    db[_SCHEMA_COLLECTION].delete_one({"id": migration_id, "status": "failed"})


def _load_migration(module_name: str) -> tuple[str, Callable[[pymongo.database.Database], None]]:
    module = import_module(f"migrations.{module_name}")
    migration_id = getattr(module, "MIGRATION_ID", module_name)
    up = getattr(module, "up")
    return migration_id, up


def run_migrations(db: pymongo.database.Database | None = None) -> list[str]:
    """Apply pending migrations. Returns ids applied by this process."""
    if db is None:
        from app.plugins.mongodb import MongoClient

        db = MongoClient().db

    _ensure_schema_collection(db)
    applied: list[str] = []

    for module_name in _MIGRATION_MODULES:
        migration_id, up = _load_migration(module_name)
        existing = db[_SCHEMA_COLLECTION].find_one({"id": migration_id})
        if existing and existing.get("status") == "applied":
            continue
        if existing and existing.get("status") == "running":
            settings.LOGGER.info(
                "Migration %s already claimed by another process; skipping.",
                migration_id,
            )
            continue
        if not _claim_migration(db, migration_id):
            settings.LOGGER.info(
                "Migration %s claimed concurrently; skipping.",
                migration_id,
            )
            continue

        settings.LOGGER.info("Applying migration %s", migration_id)
        try:
            up(db)
        except Exception as exc:
            settings.LOGGER.exception("Migration %s failed: %s", migration_id, exc)
            _mark_failed(db, migration_id, str(exc))
            raise
        _mark_applied(db, migration_id)
        applied.append(migration_id)
        settings.LOGGER.info("Applied migration %s", migration_id)

    return applied
