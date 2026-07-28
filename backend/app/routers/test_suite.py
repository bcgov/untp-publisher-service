"""UNTP validation HTTP surface for CI / harness (enabled only when ``TEST_SUITE`` is true)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query

from app.models.publications import MINES_ACT_PUBLISH_EXAMPLE, PublicationRequest
from app.services.composer import compose_unsigned_credential_from_publication
from app.validators.untp import UntpArtefactKind, validate_untp_document_with_checks

router = APIRouter(prefix="/test-suite", tags=["Test suite"])


@router.post("/validate")
async def post_validate(
    body: dict[str, Any] = Body(...),
    kind: UntpArtefactKind | None = Query(
        None,
        description="Optional artefact kind; when omitted, inferred from the document `type`.",
    ),
) -> dict[str, Any]:
    """
    Run the full UNTP pipeline (JSON Schema, JSON-LD, Pydantic) and return structured checks.

    Same logic as :func:`app.validators.untp.validate_untp_document_with_checks`.
    """
    run = validate_untp_document_with_checks(body, kind=kind)
    out: dict[str, Any] = {
        "success": run.success,
        "validation_checks": run.checks,
        "artefact_kind": run.document.kind.value if run.document else None,
    }
    if run.raising is not None:
        out["error"] = str(run.raising)
    return out


@router.post("/build-credential")
async def post_build_credential(
    body: Annotated[
        PublicationRequest,
        Body(
            openapi_examples={
                "mines_act": {
                    "summary": "BC Mines Act Permit Q-20",
                    "description": "From configs/credentials/BCMinesActPermitCredential/v1.1/payload.json",
                    "value": MINES_ACT_PUBLISH_EXAMPLE,
                }
            }
        ),
    ],
) -> dict[str, Any]:
    """
    Build an unsigned credential from a publication request
    (``template`` + ``version`` + ``data``).

    Entity and cardinality are resolved from ``data`` via ``x-publisher-pointers``
    in the credential ``data.schema.json``.

    The assembled credential is validated (JSON Schema, JSON-LD, Pydantic) before return;
    invalid output yields HTTP 400.
    """
    credential = compose_unsigned_credential_from_publication(
        publication=body.model_dump(exclude_none=True),
    )
    return {"credential": credential}
