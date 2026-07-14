from config import settings
from fastapi import HTTPException
import requests
from app.models.credential import Credential
from app.models.did_document import DidDocument, VerificationMethod
from app.plugins import MongoClient, TractionController
from app.services.entity import entity_from_options
from app.services.credential_builder import (
    build_credential,
    ensure_publisher_extension_context,
    publisher_origin,
)
from app.validators.untp import UntpValidationError, validate_untp_document
from app.utils import multikey_to_jwk
from base58 import b58encode
from canonicaljson import encode_canonical_json
import hashlib
from witness import did_key_verification_method


class PublisherRegistrarError(Exception):
    """Generic PublisherRegistrar Error."""


class PublisherRegistrar:
    def __init__(self):
        self.did_web_server = settings.WEBVH_SERVER_URL.rstrip("/")
        self.publisher_witness_id = settings.PUBLISHER_WITNESS_ID

    def _raise_registry_error(self, response: requests.Response, message: str) -> None:
        detail = response.text
        try:
            body = response.json()
            detail = body.get("detail", body)
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=f"{message}: {detail}")

    async def register_issuer(self, registration):
        """Register a new issuer with the TDW server."""
        # Derive did path components from registration
        namespace = (registration.get("namespace") or "").replace(" ", "-").lower()
        identifier = registration.get("name").replace(" ", "-").lower()

        # Request identifier from TDW server
        r = requests.get(
            f"{self.did_web_server}?namespace={namespace}&identifier={identifier}"
        )
        try:
            did = r.json()["didDocument"]["id"]
        except (ValueError, KeyError):
            raise HTTPException(status_code=r.status_code, detail=r.text)

        # Register Authorized key in traction
        default_kid = "key-01"
        multikey_kid = f"{did}#{default_kid}-multikey"
        jwk_kid = f"{did}#{default_kid}-jwk"

        traction = TractionController()
        traction.authorize()
        try:
            authorized_key = traction.get_multikey(did)
            if not authorized_key:
                authorized_key = traction.create_did_web(did)
                traction.bind_key(authorized_key, multikey_kid)
            try:
                traction.bind_key(authorized_key, multikey_kid)
            except:
                pass
        except:
            authorized_key = traction.create_did_web(did)
            traction.bind_key(authorized_key, multikey_kid)

        # Create initial DID document
        did_document = DidDocument(
            id=did,
            name=registration.get("name"),
            description=registration.get("description"),
            authentication=[
                multikey_kid,
                jwk_kid,
            ],
            assertionMethod=[
                multikey_kid,
                jwk_kid,
            ],
            verificationMethod=[
                VerificationMethod(
                    id=multikey_kid,
                    type="Multikey",
                    controller=did,
                    publicKeyMultibase=authorized_key,
                ),
                VerificationMethod(
                    id=jwk_kid,
                    type="JsonWebKey",
                    controller=did,
                    publicKeyJwk=multikey_to_jwk(authorized_key),
                ),
            ],
        )

        # Bind an delegated issuing multikey if provided
        if registration.get("multikey"):
            multikey = registration.get("multikey")
            delegated_kid = "key-02"
            delegated_kid_multikey = f"{did}#{delegated_kid}-multikey"
            delegated_kid_jwk = f"{did}#{delegated_kid}-jwk"
            did_document.authentication.append(delegated_kid_multikey)
            did_document.assertionMethod.append(delegated_kid_multikey)
            did_document.verificationMethod.append(
                VerificationMethod(
                    id=delegated_kid_multikey,
                    type="Multikey",
                    controller=did,
                    publicKeyMultibase=multikey,
                )
            )
            did_document.authentication.append(delegated_kid_jwk)
            did_document.assertionMethod.append(delegated_kid_jwk)
            did_document.verificationMethod.append(
                VerificationMethod(
                    id=delegated_kid_jwk,
                    type="JsonWebKey",
                    controller=did,
                    publicKeyJwk=multikey_to_jwk(multikey),
                )
            )

        did_document = did_document.model_dump()

        # Sign DID document
        client_proof_options = r.json()["proofOptions"].copy()
        client_proof_options["verificationMethod"] = (
            f"did:key:{authorized_key}#{authorized_key}"
        )
        signed_did_document = traction.add_di_proof(
            document=did_document, 
            options=client_proof_options
        )

        # Endorse DID document
        publisher_proof_options = r.json()["proofOptions"].copy()
        publisher_proof_options["verificationMethod"] = did_key_verification_method(
            self.publisher_witness_id
        )
        endorsed_did_document = traction.add_di_proof(
            document=signed_did_document, 
            options=publisher_proof_options
        )

        r = requests.post(self.did_web_server, json={"didDocument": endorsed_did_document})
        if r.status_code != 201:
            raise HTTPException(status_code=r.status_code, detail='Error registering DID.')
        # try:
        #     log_entry = r.json()["logEntry"]
        # except (ValueError, KeyError):
        #     raise HTTPException(status_code=r.status_code, detail=r.text)

        # # Sign log entry with authorized key
        # signed_log_entry = traction.add_di_proof(
        #     document=log_entry, 
        #     options={
        #         "type": "DataIntegrityProof",
        #         "cryptosuite": "eddsa-jcs-2022",
        #         "proofPurpose": "assertionMethod",
        #         "verificationMethod": f"did:key:{authorized_key}#{authorized_key}",
        #     }
        # )
        # r = requests.post(
        #     f"{self.did_web_server}/{namespace}/{identifier}",
        #     json={"logEntry": signed_log_entry},
        # )
        # try:
        #     log_entry = r.json()
        # except (ValueError, KeyError):
        #     raise HTTPException(status_code=r.status_code, detail=r.text)

        return did_document, authorized_key

    async def format_credential(self, credential_input, options):
        entity_id = options.get("entityId")
        cardinality_id = options.get("cardinalityId")

        mongo = MongoClient()
        credential_type = credential_input.get("type")
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
        entity = entity_from_options(options)
        try:
            credential = build_credential(
                template=credential_template,
                credential_input=credential_input,
                options=options,
                type_record=credential_registration,
                issuer=issuer,
                entity=entity,
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

    async def check_cardinality(self, credential_input, options):
        if options.get("additionalData"):
            credential_input["credentialSubject"] |= options.get("additionalData")

        cardinality_hash = b58encode(
            hashlib.sha256(encode_canonical_json(credential_input)).digest()
        ).decode()
        cardinality_hash = f"z{cardinality_hash}"
        settings.LOGGER.info(cardinality_hash)

        settings.LOGGER.info("Looking for existing credential records.")
        mongo = MongoClient()
        credential_collection = mongo.find(
            "CredentialRecord",
            {
                "type": credential_input.get("type"),
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
