from config import settings
from fastapi import HTTPException
from app.models.credential import Credential
from app.plugins.mongodb import MongoClient
from app.services.composer import (
    compose_credential,
    ensure_render_method_context,
)
from app.validators.untp import UntpValidationError, validate_untp_document
from base58 import b58encode
from canonicaljson import encode_canonical_json
import hashlib


class PublisherCoordinatorError(Exception):
    """Generic PublisherCoordinator Error."""


def _status_entries(vc: dict) -> list[dict]:
    raw = (vc or {}).get("credentialStatus")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def mark_refresh_status_bit(mongo: MongoClient, vc: dict) -> None:
    """Flip the bitstring ``refresh`` bit for ``vc`` (signals update available)."""
    for entry in _status_entries(vc):
        if entry.get("statusPurpose") != "refresh":
            continue
        index = entry.get("statusListIndex")
        endpoint = entry.get("statusListCredential")
        if index is None or not endpoint:
            raise HTTPException(
                status_code=500,
                detail="Credential refresh status entry is incomplete",
            )
        if not mongo.set_status_list_bit(
            endpoint=str(endpoint),
            index=int(index),
            value=True,
        ):
            raise HTTPException(
                status_code=500,
                detail="Failed to update refresh status list bit",
            )
        return
    # Refresh BitstringStatusListEntry is not attached on issue (UNTP
    # ConformityCredential.json only allows a single credentialStatus object).
    # Skip bit flip until multi-status / statusReference is schema-valid.
    # Debug (not warning): this is expected for every re-issue today.
    settings.LOGGER.debug(
        "No refresh status entry on credential; skipping refresh bit update"
    )


class PublisherCoordinator:
    """Publish orchestration: cardinality, compose, status, refresh."""

    async def format_credential(self, options):
        entity_id = options.get("entityId")
        cardinality_id = options.get("cardinalityId")
        credential_type = options.get("template")

        mongo = MongoClient()
        credential_registration = mongo.find_one(
            "CredentialTemplateRecord", {"type": credential_type}
        )
        if not credential_registration:
            raise HTTPException(status_code=404, detail="Unregistered credential type.")

        # Compose reloads trusted ``template.yaml`` from disk; the Mongo record is
        # only the registration gate (issuer / type / version / OCA snapshot).
        issuer = mongo.find_one(
            "IssuerInstanceRecord", {"id": credential_registration["issuer"]}
        )
        if not issuer:
            raise HTTPException(status_code=404, detail="Issuer not registered.")
        try:
            credential = compose_credential(
                options=options,
                type_record=credential_registration,
                issuer=issuer,
            )
        except UntpValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"UNTP validation failed: {exc}",
            ) from exc

        # Append publisher-managed fields, then validate the final document.
        ensure_render_method_context(credential)

        issuer_id = credential_registration.get("issuer")
        # TODO: release claimed status-list indexes if Traction issue/sign or
        # CredentialRecord insert fails after this (bits are popped before success).
        if not issuer_id:
            raise HTTPException(
                status_code=500,
                detail="No status list for purpose 'revocation'",
            )
        claimed = mongo.claim_status_list_index(
            issuer_id=issuer_id,
            purpose="revocation",
        )
        if not claimed or claimed.get("endpoint") is None:
            raise HTTPException(
                status_code=500,
                detail="No status list for purpose 'revocation'",
            )
        # BUG: UNTP ConformityCredential.json (v0.7.0) types credentialStatus as a
        # single BitstringStatusListEntry object (not an array), types
        # statusListIndex as integer (while describing a base-10 string), and
        # forbids statusReference. Until the schema allows multi-status / VCDM
        # refresh fields, only emit revocation as one object.
        credential["credentialStatus"] = {
            "type": "BitstringStatusListEntry",
            "statusPurpose": "revocation",
            "statusListIndex": int(claimed["index"]),
            "statusListCredential": claimed["endpoint"],
        }
        # BUG: suspension / refresh status entries disabled — see BUG note above.
        # refresh_url = f"{publisher_origin()}/credentials/refresh?" + urlencode(
        #     {
        #         "type": credential_type or "",
        #         "entity": entity_id or "",
        #         "cardinality": cardinality_id or "",
        #     }
        # )
        # for purpose in ["suspension", "refresh"]:
        #     claimed = mongo.claim_status_list_index(
        #         issuer_id=issuer_id,
        #         purpose=purpose,
        #     )
        #     ...
        #     if purpose == "refresh":
        #         entry["statusReference"] = refresh_url

        try:
            validate_untp_document(credential)
        except UntpValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"UNTP validation failed: {exc}",
            ) from exc

        credential = Credential(
            context=credential.get("@context"),
            type=credential.get("type"),
            id=credential.get("id"),
            name=credential.get("name"),
            issuer=credential.get("issuer"),
            validFrom=credential.get("validFrom"),
            validUntil=credential.get("validUntil") or None,
            credentialSubject=credential.get("credentialSubject"),
            credentialStatus=credential.get("credentialStatus"),
            renderMethod=credential.get("renderMethod"),
        ).model_dump()

        return credential

    async def check_cardinality(self, options):
        hash_input = {
            "template": options.get("template"),
            "version": options.get("version"),
            "data": options.get("data") or {},
        }
        cardinality_hash = b58encode(
            hashlib.sha256(encode_canonical_json(hash_input)).digest()
        ).decode()
        cardinality_hash = f"z{cardinality_hash}"
        settings.LOGGER.info(cardinality_hash)

        settings.LOGGER.info("Looking for existing credential records.")
        mongo = MongoClient()
        credential_collection = mongo.find(
            "CredentialRecord",
            {
                "type": options.get("template"),
                "entity_id": options.get("entityId"),
                "cardinality_id": options.get("cardinalityId"),
                "refresh": False,
            },
        )
        records_count = len(list(credential_collection.clone()))
        settings.LOGGER.info(f"Found {records_count} matching records.")
        if records_count >= 1:
            for record in credential_collection:
                if cardinality_hash == record.get("cardinality_hash"):
                    settings.LOGGER.info("No change detected, keeping credential record.")
                    return None
                settings.LOGGER.info("Change detected, updating credential record.")
                # Signal external consumers before flipping the Mongo refresh flag.
                mark_refresh_status_bit(mongo, record.get("vc") or {})
                record["refresh"] = True
                mongo.replace(
                    "CredentialRecord",
                    {"id": record.get("id")},
                    record,
                )
                record_id = record.get("id")
                settings.LOGGER.info(f"Credential record {record_id} refresh status updated.")
        return cardinality_hash
