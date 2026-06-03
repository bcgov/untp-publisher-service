"""Resolve BC Laws legal act metadata from a registered issuer."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.plugins import MongoClient, bclaws


def legal_act_for_issuer_id(issuer_id: str) -> dict[str, Any]:
    """Look up issuer scope in MongoDB and resolve the statute via BC Laws."""
    mongo = MongoClient()
    issuer = mongo.find_one("IssuerRecord", {"id": issuer_id})
    if not issuer:
        raise HTTPException(status_code=404, detail="Issuer not registered.")
    return legal_act_for_issuer(issuer)


def legal_act_for_issuer(issuer: dict[str, Any]) -> dict[str, Any]:
    scope = (issuer.get("scope") or "").strip()
    if not scope:
        raise HTTPException(
            status_code=400,
            detail="Issuer record has no scope; re-register the issuer with a BC Laws act scope",
        )
    return bclaws.resolve_legal_act_from_scope(scope)
