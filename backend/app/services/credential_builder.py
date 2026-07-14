"""Build credentials from publication payloads and Jinja templates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.repo_configs.loader import load_credential_template_source_optional
from app.services.publication_templates import (
    materialize_credential_document,
    publication_template_context,
)
from app.utils import format_utc_datetime
from config import settings


def publisher_origin() -> str:
    domain = (settings.PUBLISHER_DOMAIN or "").strip()
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain.rstrip("/")
    return f"https://{domain}"


def publisher_extension_context_url() -> str:
    """Public URL for the publisher JSON-LD extension context."""
    return f"{publisher_origin()}/contexts/publisher/v1"


def ensure_publisher_extension_context(credential: dict[str, Any]) -> None:
    """Append the publisher extension context so ``SimpleRefreshQuery`` / ``OCABundle`` resolve."""
    url = publisher_extension_context_url()
    ctx = credential.get("@context")
    if ctx is None:
        credential["@context"] = [url]
        return
    if isinstance(ctx, str):
        if ctx != url:
            credential["@context"] = [ctx, url]
        return
    if isinstance(ctx, list) and url not in ctx:
        credential["@context"] = [*ctx, url]


def validate_publication(*, options: dict[str, Any]) -> None:
    cardinality_id = options.get("cardinalityId")
    if cardinality_id is None or str(cardinality_id).strip() == "":
        raise HTTPException(
            status_code=400,
            detail="cardinalityId is required",
        )
    entity_id = options.get("entityId")
    if entity_id is None or str(entity_id).strip() == "":
        raise HTTPException(status_code=400, detail="entityId is required")


def build_credential(
    *,
    template: dict[str, Any],
    options: dict[str, Any],
    type_record: dict[str, Any],
    issuer: dict[str, Any],
    entity: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a credential by rendering ``template.yaml`` for the publication request."""
    validate_publication(options=options)

    template_source = load_credential_template_source_optional(type_record.get("type"))
    if not template_source:
        raise HTTPException(
            status_code=500,
            detail=(
                f"No credential template for type {type_record.get('type')!r}; "
                "add configs/credentials/{type}/{version}/template.yaml"
            ),
        )

    text_context = publication_template_context(
        options=options,
        organization=entity,
    )
    credential = materialize_credential_document(template_source, text_context)

    # Prefer configured issuer (Mongo / publication config) over stub values.
    credential["issuer"] = {
        "type": ["CredentialIssuer"],
        "id": issuer["id"],
        "name": issuer["name"],
    }
    if template.get("renderMethod"):
        credential["renderMethod"] = template["renderMethod"]

    published_at = format_utc_datetime(datetime.now(timezone.utc))
    credential_id = options.get("credentialId")
    credential["id"] = f"{publisher_origin()}/credentials/{credential_id}"
    if options.get("validFrom"):
        credential["validFrom"] = options["validFrom"]
    else:
        credential["validFrom"] = published_at
    if options.get("validUntil"):
        credential["validUntil"] = options["validUntil"]

    return credential
