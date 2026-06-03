"""BC Laws (CiviX) integration — browse public statutes for credential registration."""

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.plugins import bclaws

router = APIRouter(prefix="/bclaws", tags=["BC Laws"])


@router.get("/acts")
async def list_acts(
    letter: str | None = Query(
        None,
        min_length=1,
        max_length=1,
        description="Filter by first letter of act name (A–Z)",
    ),
    q: str | None = Query(None, description="Search act name or full title"),
    include_repealed: bool = Query(
        False, description="Include acts marked Repealed in the catalog"
    ),
    resolve_documents: bool = Query(
        False,
        description=(
            "Resolve each act's document id via an extra BC Laws request "
            "(slower; use when guessed ids fail)"
        ),
    ),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """List public Acts from BC Laws (``complete/statreg`` via the CiviX Content API)."""
    result = bclaws.list_public_acts(
        letter=letter,
        q=q,
        include_repealed=include_repealed,
        resolve_documents=resolve_documents,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(content=result)


@router.get("/acts/{document_id}")
async def get_act(document_id: str):
    """Fetch metadata (name, effective date) for one statute by CiviX document id."""
    return JSONResponse(content=bclaws.get_act_metadata(document_id))


@router.get("/roles")
async def list_roles(
    q: str = Query(..., min_length=2, description="Search BC Government Directory title"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search BC Government Directory roles (people by title)."""
    return JSONResponse(content=bclaws.list_directory_roles(q=q, limit=limit))
