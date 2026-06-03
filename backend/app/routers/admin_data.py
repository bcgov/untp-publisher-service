"""Admin read API for MongoDB collections (publisher operations)."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.admin_collections import (
    ADMIN_WORKFLOW,
    collection_names,
    collection_public_meta,
    get_collection_meta,
)
from app.models.registrations import IssuerRegistration
from app.plugins import MongoClient
from app.security import check_api_key_header
from app.services.issuer_registration import register_issuer as register_issuer_service
from app.services.legal_act import legal_act_for_issuer_id

router = APIRouter(
    prefix="/admin/api",
    tags=["Admin data"],
    dependencies=[Depends(check_api_key_header)],
)


def _sanitize_document(doc: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(doc, default=str))
    for field in meta.get("redact_fields", []):
        if field in out and out[field]:
            out[field] = "***"
    for field, max_len in meta.get("truncate_fields", {}).items():
        if field not in out:
            continue
        value = out[field]
        if isinstance(value, str) and len(value) > max_len:
            out[field] = value[:max_len] + "…"
        elif isinstance(value, dict):
            encoded = json.dumps(value, default=str)
            if len(encoded) > max_len:
                out[field] = encoded[:max_len] + "…"
    return out


def _build_query(meta: dict[str, Any], q: str | None) -> dict[str, Any]:
    if not q or not q.strip():
        return {}
    term = q.strip()
    id_field = meta["id_field"]
    columns = meta.get("list_columns", [id_field])
    or_clauses: list[dict[str, Any]] = []
    for col in columns:
        or_clauses.append({col: {"$regex": re.escape(term), "$options": "i"}})
    return {"$or": or_clauses}


@router.get("/workflow")
async def get_admin_workflow():
    return JSONResponse(content={"workflow": ADMIN_WORKFLOW})


@router.post("/issuers")
async def admin_register_issuer(request_body: IssuerRegistration):
    """Register a new issuer (same as POST /registrations/issuers, with structured response)."""
    result = await register_issuer_service(request_body.model_dump())
    return JSONResponse(status_code=201, content=result)


@router.get("/issuers/{issuer_id}/legal-act")
async def admin_issuer_legal_act(issuer_id: str):
    """Resolve BC Laws statute metadata from the issuer's registered scope."""
    return JSONResponse(content=legal_act_for_issuer_id(issuer_id))


@router.get("/collections")
async def list_admin_collections():
    items = []
    mongo = MongoClient()
    for name in collection_names():
        item = collection_public_meta(name)
        item["count"] = mongo.count(name, {})
        items.append(item)
    return JSONResponse(
        content={
            "workflow": ADMIN_WORKFLOW,
            "collections": items,
        }
    )


@router.get("/collections/{collection_name}")
async def list_collection_records(
    collection_name: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Search across list columns"),
):
    try:
        meta = get_collection_meta(collection_name)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown collection") from None

    query = _build_query(meta, q)
    mongo = MongoClient()
    total = mongo.count(collection_name, query)
    rows = mongo.find_page(collection_name, query, skip=skip, limit=limit)
    items = [_sanitize_document(row, meta) for row in rows]

    return JSONResponse(
        content={
            "collection": collection_name,
            **collection_public_meta(collection_name),
            "total": total,
            "skip": skip,
            "limit": limit,
            "items": items,
        }
    )


@router.get("/collections/{collection_name}/records/{record_id}")
async def get_collection_record(collection_name: str, record_id: str):
    try:
        meta = get_collection_meta(collection_name)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown collection") from None

    id_field = meta["id_field"]
    mongo = MongoClient()
    doc = mongo.find_one(collection_name, {id_field: record_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Record not found")

    return JSONResponse(
        content={
            **collection_public_meta(collection_name),
            "record": _sanitize_document(doc, meta),
        }
    )
