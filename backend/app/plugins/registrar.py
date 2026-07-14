from config import settings
from fastapi import HTTPException
import requests
from app.models.credential import Credential
from app.models.did_document import DidDocument, VerificationMethod
from app.plugins import MongoClient, TractionController
from app.plugins.orgbook import OrgbookClient
from app.plugins.untp import DigitalConformityCredential
from app.services.dcc_builder import build_dcc_from_publication, publisher_origin
from app.validators.untp import UntpValidationError, validate_untp_document
from app.utils import multikey_to_jwk
from base58 import b58encode
import re
from datetime import datetime, timezone
from jsonpath_ng import parse
from canonicaljson import encode_canonical_json
import hashlib


class PublisherRegistrarError(Exception):
    """Generic PublisherRegistrar Error."""


class PublisherRegistrar:
    def __init__(self):
        self.did_web_server = settings.DID_WEB_SERVER_URL.rstrip("/")
        self.publisher_multikey = settings.PUBLISHER_WITNESS_MULTIKEY

    def _raise_registry_error(self, response: requests.Response, message: str) -> None:
        detail = response.text
        try:
            body = response.json()
            detail = body.get("detail", body)
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=f"{message}: {detail}")

    @staticmethod
    def _is_json_pointer(path: str) -> bool:
        return isinstance(path, str) and path.startswith("/")

    @staticmethod
    def _pointer_tokens(pointer: str) -> list[str]:
        if pointer == "/":
            return [""]
        return [
            token.replace("~1", "/").replace("~0", "~")
            for token in pointer.lstrip("/").split("/")
        ]

    def _set_by_pointer(self, document: dict, pointer: str, value) -> None:
        tokens = self._pointer_tokens(pointer)
        current = document
        for idx, token in enumerate(tokens):
            last = idx == len(tokens) - 1
            next_token = tokens[idx + 1] if not last else None
            if isinstance(current, list):
                position = int(token)
                while len(current) <= position:
                    current.append({} if not str(next_token or "").isdigit() else [])
                if last:
                    current[position] = value
                    return
                current = current[position]
                continue
            if last:
                current[token] = value
                return
            if token not in current or current[token] is None:
                current[token] = [] if str(next_token).isdigit() else {}
            current = current[token]

    def _get_by_pointer(self, document: dict, pointer: str):
        current = document
        for token in self._pointer_tokens(pointer):
            if isinstance(current, list):
                position = int(token)
                if position >= len(current):
                    raise KeyError(pointer)
                current = current[position]
                continue
            if token not in current:
                raise KeyError(pointer)
            current = current[token]
        return current

    def _set_at_path(self, document: dict, path: str, value) -> None:
        if self._is_json_pointer(path):
            self._set_by_pointer(document, path, value)
            return
        parse(path).update(document, value)

    def _get_at_path(self, document: dict, path: str):
        if self._is_json_pointer(path):
            return self._get_by_pointer(document, path)
        matches = parse(path).find(document)
        if not matches:
            raise KeyError(path)
        return matches[0].value

    async def register_issuer(self, registration):
        """Register a new issuer with the TDW server."""
        # Derive did path components from registration
        namespace = registration.get("scope").replace(" ", "-").lower()
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
        publisher_proof_options["verificationMethod"] = (
            f"did:key:{self.publisher_multikey}#{self.publisher_multikey}"
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

    async def template_credential(self, credential_registration):
        mongo = MongoClient()
        issuer = mongo.find_one(
            "IssuerRecord", {"id": credential_registration["issuer"]}
        )
        if not issuer:
            raise HTTPException(status_code=404, detail="Issuer not registered.")
        credential_type = credential_registration["type"]
        credential_version = credential_registration["version"]

        credential_template = {
            "@context": ["https://www.w3.org/ns/credentials/v2"],
            "type": ["VerifiableCredential"],
            "name": " ".join(
                re.findall("[A-Z][^A-Z]*", credential_registration["subjectType"])
            )
            .strip(),
            "issuer": {"id": issuer["id"], "name": issuer["name"]},
            "credentialSubject": {"type": []},
        }

        if credential_registration.get("additionalType"):
            if (
                credential_registration.get("additionalType")
                == "DigitalConformityCredential"
            ):
                credential_template = DigitalConformityCredential().extend_template(
                    credential_registration=credential_registration,
                    credential_template=credential_template,
                )

        credential_template["@context"].append(
            f"https://{settings.DOMAIN}/contexts/{credential_type}/{credential_version}"
        )
        credential_template["type"].append(credential_type)
        credential_template["credentialSubject"]["type"].append(
            credential_registration["subjectType"]
        )
        return credential_template

    async def format_credential(self, credential_input, options):
        entity_id = options.get("entityId")
        cardinality_id = options.get("cardinalityId")

        mongo = MongoClient()
        credential_type = credential_input.get("type")
        credential_registration = mongo.find_one(
            "CredentialTypeRecord", {"type": credential_type}
        )
        if not credential_registration:
            raise HTTPException(status_code=404, detail="Unregistered credential type.")

        credential_template = credential_registration.get("template")
        credential_id = options.get("credentialId")
        origin = publisher_origin()

        if credential_registration.get("template_ref"):
            issuer = mongo.find_one("IssuerRecord", {"id": credential_registration["issuer"]})
            if not issuer:
                raise HTTPException(status_code=404, detail="Issuer not registered.")
            entity = OrgbookClient().fetch_buisness_info(entity_id)
            try:
                credential = build_dcc_from_publication(
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
        else:
            credential = await self._format_credential_legacy(
                credential_input=credential_input,
                options=options,
                credential_registration=credential_registration,
                credential_template=credential_template,
                entity_id=entity_id,
                credential_id=credential_id,
                origin=origin,
            )

        credential["refreshService"] = [
            {
                "type": "SimpleRefreshQuery",
                "id": (
                    f"{origin}/credentials/refresh?type={credential_type}"
                    f"&entity={entity_id}&cardinality={cardinality_id}"
                ),
            }
        ]

        status_list_id = credential_registration["status_lists"][-1]
        status_list_record = mongo.find_one("StatusListRecord", {"id": status_list_id})
        credential["credentialStatus"] = [
            {
                "type": "BitstringStatusListEntry",
                "statusPurpose": purpose,
                "statusListIndex": str(status_list_record["indexes"].pop()),
                "statusListCredential": status_list_record["endpoint"],
            }
            for purpose in ["revocation", "suspension", "refresh"]
        ]
        mongo.replace("StatusListRecord", {"id": status_list_id}, status_list_record)

        credential = Credential(
            context=credential_template.get("@context"),
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

    async def _format_credential_legacy(
        self,
        *,
        credential_input,
        options,
        credential_registration,
        credential_template,
        entity_id,
        credential_id,
        origin,
    ):
        credential = credential_template.copy()
        credential["id"] = f"{origin}/credentials/{credential_id}"

        credential["validFrom"] = credential_input.get("validFrom") or datetime.now(
            timezone.utc
        ).isoformat("T", "seconds")
        if credential_input.get("validUntil"):
            credential["validUntil"] = credential_input.get("validUntil")

        credential["credentialSubject"] |= credential_input["credentialSubject"]
        if credential_registration.get("additional_type"):
            if (
                credential_registration.get("additional_type")
                == "DigitalConformityCredential"
            ):
                entity = OrgbookClient().fetch_buisness_info(entity_id)
                credential["credentialSubject"]["issuedToParty"] |= {
                    "id": entity["id"],
                    "name": entity["name"],
                    "registeredId": entity_id,
                }

                if credential_registration.get("additional_paths"):
                    for attribute in credential_registration["additional_paths"]:
                        value = options["additionalData"][attribute]
                        path = credential_registration["additional_paths"][attribute]
                        self._set_at_path(credential, path, value)

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
