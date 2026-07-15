"""Publisher JSON-LD contexts (extension terms for issued credentials)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.repo_configs.loader import load_publisher_extension_context

router = APIRouter(prefix="/contexts", tags=["Contexts"])


@router.get("/publisher/v1")
async def get_publisher_extension_context():
    """JSON-LD context for publisher terms (``SimpleRefreshQuery``, ``OCABundle``)."""
    try:
        document = load_publisher_extension_context()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(
        status_code=200,
        content=document,
        media_type="application/ld+json",
    )
