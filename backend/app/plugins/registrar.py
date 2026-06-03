from config import settings
from fastapi import HTTPException
import requests
from app.models.credential import Credential
from app.plugins import MongoClient, TractionController
from app.plugins.orgbook import OrgbookClient
from app.plugins.untp import DigitalConformityCredential
from app.plugins import webvh as webvh_log
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
        """Register a new issuer on a DID WebVH registry (BCVH / did:webvh:1.0)."""
        namespace = registration.get("scope").replace(" ", "-").lower()
        identifier = registration.get("name").replace(" ", "-").lower()

        r = requests.get(
            f"{self.did_web_server}/",
            params={"namespace": namespace, "identifier": identifier},
            timeout=60,
        )
        if not r.ok:
            self._raise_registry_error(r, "Failed to fetch DID WebVH log entry template")

        try:
            template = r.json()
        except ValueError:
            self._raise_registry_error(r, "DID WebVH template is not JSON")

        if not webvh_log.is_log_entry_template(template):
            raise HTTPException(
                status_code=502,
                detail="DID WebVH server did not return a log entry template",
            )

        provisional_did = webvh_log.provisional_did_from_template(template)
        default_kid = "key-01"

        traction = TractionController()
        traction.authorize()
        traction.ensure_publisher_witness()

        # WebVH genesis update key is a did:key in Traction (not the provisional did:webvh id).
        authorized_key = traction.create_issuer_update_multikey()

        doc_state = webvh_log.build_genesis_document_state(
            template=template,
            registration=registration,
            authorized_key=authorized_key,
        )
        template_proof = template.get("proof") if isinstance(template.get("proof"), dict) else None
        log_entry = webvh_log.unsigned_log_entry(doc_state)

        issuer_vm = f"did:key:{authorized_key}#{authorized_key}"
        signed_log_entry = webvh_log.sign_log_entry(
            traction,
            log_entry,
            verification_method=issuer_vm,
            template_proof=template_proof,
        )

        witness_did = settings.PUBLISHER_WITNESS_ID
        witness_vm = f"{witness_did}#{self.publisher_multikey}"
        witness_signature = webvh_log.sign_witness_signature(
            traction,
            signed_log_entry,
            witness_verification_method=witness_vm,
            template_proof=template_proof,
        )

        webvh_log.log_submission_payload(
            namespace=namespace,
            identifier=identifier,
            log_entry=signed_log_entry,
            witness_signature=witness_signature,
            unsigned_log_entry=log_entry,
        )

        r = requests.post(
            f"{self.did_web_server}/{namespace}/{identifier}",
            json={
                "logEntry": signed_log_entry,
                "witnessSignature": witness_signature,
            },
            timeout=120,
        )
        if not r.ok:
            self._raise_registry_error(r, "Failed to submit DID WebVH log entry")

        try:
            result = r.json()
        except ValueError:
            self._raise_registry_error(r, "DID WebVH registry returned non-JSON response")

        final_log_entry = result.get("logEntry", result)
        final_state = final_log_entry.get("state", final_log_entry)
        resolved_did = webvh_log.resolve_did_from_log_entry(
            final_log_entry if "state" in final_log_entry else {"state": final_state}
        )

        issuer_multikey_kid = f"{resolved_did}#{default_kid}-multikey"
        traction.bind_key(authorized_key, issuer_multikey_kid)

        return final_state, authorized_key

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
        credential_template = credential_registration.get("template")

        credential = credential_template.copy()

        credential_id = options.get("credentialId")
        credential["id"] = f"https://{settings.DOMAIN}/credentials/{credential_id}"

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

        credential["refreshService"] = [
            {
                "type": "SimpleRefreshQuery",
                "id": f"https://{settings.DOMAIN}/credentials/refresh?type={credential_type}&entity={entity_id}&cardinality={cardinality_id}",
            }
        ]

        status_list_id = credential_registration["status_lists"][-1]
        status_list_record = mongo.find_one("StatusListRecord", {"id": status_list_id})
        credential["credentialStatus"] = [
            (
                {
                    "type": "BitstringStatusListEntry",
                    "statusPurpose": purpose,
                    "statusListIndex": str(status_list_record["indexes"].pop()),
                    "statusListCredential": status_list_record["endpoint"],
                }
            )
            for purpose in ["revocation", "suspension", "refresh"]
        ]
        mongo.replace("StatusListRecord", {"id": status_list_id}, status_list_record)

        entity_id_value = self._get_at_path(
            credential, credential_registration["core_paths"]["entityId"]
        )
        cardinality_id_value = self._get_at_path(
            credential, credential_registration["core_paths"]["cardinalityId"]
        )
        if entity_id_value != entity_id:
            pass
        if cardinality_id_value != cardinality_id:
            pass
        if credential["issuer"]["id"] != credential_registration["issuer"]:
            pass

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
