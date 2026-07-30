from config import settings
from fastapi import HTTPException
from app.models.credential import Credential
from app.plugins.mongodb import MongoClient
from app.services.composer import (
    compose_credential,
    ensure_render_method_context,
    publisher_origin,
)
from app.validators.untp import UntpValidationError, validate_untp_document
from base58 import b58encode
from canonicaljson import encode_canonical_json
import hashlib


class PublisherCoordinatorError(Exception):
    """Generic PublisherCoordinator Error."""


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
        origin = publisher_origin()

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
            validate_untp_document(credential)
        except UntpValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"UNTP validation failed: {exc}",
            ) from exc

        # After UNTP checks: append render-method context for TemplateRenderMethod.
        ensure_render_method_context(credential)

        issuer_id = credential_registration.get("issuer")
        status_entries = []
        # TODO: release claimed status-list indexes if Traction issue/sign or
        # CredentialRecord insert fails after this (bits are popped before success).
        refresh_url = (
            f"{origin}/credentials/refresh?type={credential_type}"
            f"&entity={entity_id}&cardinality={cardinality_id}"
        )
        for purpose in ["revocation", "suspension", "refresh"]:
            if not issuer_id:
                raise HTTPException(
                    status_code=500,
                    detail=f"No status list for purpose {purpose!r}",
                )
            claimed = mongo.claim_status_list_index(
                issuer_id=issuer_id,
                purpose=purpose,
            )
            if not claimed or claimed.get("endpoint") is None:
                raise HTTPException(
                    status_code=500,
                    detail=f"No status list for purpose {purpose!r}",
                )
            entry: dict = {
                "type": "BitstringStatusListEntry",
                "statusPurpose": purpose,
                "statusListIndex": str(claimed["index"]),
                "statusListCredential": claimed["endpoint"],
            }
            if purpose == "refresh":
                entry["statusReference"] = refresh_url
            status_entries.append(entry)
        credential["credentialStatus"] = status_entries

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
                record["refresh"] = True
                mongo.replace(
                    "CredentialRecord",
                    {"id": record.get("id")},
                    record,
                )
                record_id = record.get("id")
                settings.LOGGER.info(f"Credential record {record_id} refresh status updated.")
        return cardinality_hash
