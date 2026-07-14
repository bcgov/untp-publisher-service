"""Idempotent local issuer records from ``configs/issuers.yaml``."""

from __future__ import annotations

from typing import Any

from app.models.mongodb import IssuerInstanceRecord
from app.plugins.mongodb import MongoClient
from config import settings


def scope_from_issuer_config(issuer: dict[str, Any]) -> str | None:
    """Prefer explicit scope; else namespace from alias (``namespace:name``)."""
    scope = (issuer.get("scope") or "").strip()
    if scope:
        return scope
    alias = (issuer.get("alias") or "").strip()
    if ":" in alias and not alias.startswith("did:"):
        return alias.split(":", 1)[0] or None
    return None


def ensure_issuer_record(issuer: dict[str, Any], *, mongo: MongoClient | None = None) -> dict[str, Any]:
    """Create or refresh local ``IssuerInstanceRecord`` from a yaml issuer entry.

    Does not require Traction / DID resolution. ``authorized_key`` is set when
    ``verificationMethod`` is present on the config; DID/key checks may update it later.
    """
    mongo = mongo or MongoClient()
    issuer_id = (issuer.get("id") or "").strip()
    if not issuer_id:
        raise ValueError("issuer id is required")

    name = (issuer.get("name") or issuer_id).strip()
    scope = scope_from_issuer_config(issuer)
    configured_key = (issuer.get("verificationMethod") or "").strip() or None

    existing = mongo.find_one("IssuerInstanceRecord", {"id": issuer_id})
    if not existing:
        record = IssuerInstanceRecord(
            id=issuer_id,
            name=name,
            scope=scope,
            authorized_key=configured_key,
        ).model_dump()
        mongo.insert("IssuerInstanceRecord", record)
        settings.LOGGER.info("Local issuer record created for %s.", issuer_id)
        return record

    updates: dict[str, Any] = {}
    if name and existing.get("name") != name:
        updates["name"] = name
    if scope and existing.get("scope") != scope:
        updates["scope"] = scope
    if configured_key:
        existing_key = existing.get("authorized_key")
        if not existing_key:
            updates["authorized_key"] = configured_key
        elif existing_key != configured_key:
            settings.LOGGER.warning(
                "Issuer %s local authorized_key mismatch "
                "(record=%s, config=%s); leave unchanged.",
                issuer_id,
                existing_key,
                configured_key,
            )

    if updates:
        refreshed = {**existing, **updates}
        # Drop Mongo projection artifacts if any leaked in.
        refreshed.pop("_id", None)
        mongo.replace("IssuerInstanceRecord", {"id": issuer_id}, refreshed)
        settings.LOGGER.info(
            "Local issuer record updated for %s (%s).",
            issuer_id,
            ", ".join(updates.keys()),
        )
        return refreshed

    return existing
