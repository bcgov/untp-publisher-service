from config import settings
from fastapi import HTTPException
from app.models.credential import Credential
from app.plugins.mongodb import MongoClient
from app.services.composer import (
    compose_credential,
    ensure_publisher_extension_context,
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

        credential_template = credential_registration.get("template")
        if not credential_template:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"No stored template for credential type {credential_type!r}; "
                    "provision from configs/credentials/"
                ),
            )
        origin = publisher_origin()

        issuer = mongo.find_one(
            "IssuerInstanceRecord", {"id": credential_registration["issuer"]}
        )
        if not issuer:
            raise HTTPException(status_code=404, detail="Issuer not registered.")
        try:
            credential = compose_credential(
                template=credential_template,
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

        # After UNTP checks: publisher terms need the extension context.
        ensure_publisher_extension_context(credential)
        credential["refreshService"] = [
            {
                "type": "SimpleRefreshQuery",
                "id": (
                    f"{origin}/credentials/refresh?type={credential_type}"
                    f"&entity={entity_id}&cardinality={cardinality_id}"
                ),
            }
        ]

        issuer_id = credential_registration.get("issuer")
        status_entries = []
        for purpose in ["revocation", "suspension", "refresh"]:
            status_list_record = None
            if issuer_id:
                status_list_record = mongo.find_one(
                    "StatusListRecord",
                    {"issuer": issuer_id, "purpose": purpose, "active": True},
                )
            if not status_list_record:
                raise HTTPException(
                    status_code=500,
                    detail=f"No status list for purpose {purpose!r}",
                )
            status_entries.append(
                {
                    "type": "BitstringStatusListEntry",
                    "statusPurpose": purpose,
                    "statusListIndex": str(status_list_record["indexes"].pop()),
                    "statusListCredential": status_list_record["endpoint"],
                }
            )
            mongo.replace(
                "StatusListRecord",
                {"id": status_list_record["id"]},
                status_list_record,
            )
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
            refreshService=credential.get("refreshService"),
            renderMethod=credential_template.get("renderMethod"),
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
