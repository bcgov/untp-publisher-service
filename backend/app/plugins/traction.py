from config import settings
import requests
from fastapi import HTTPException
from app.utils import verkey_to_multikey, timestamp
from app.plugins.mongodb import MongoClient
from witness import did_key_verification_method


class TractionControllerError(Exception):
    """Generic TractionController Error."""


class TractionController:
    def __init__(self):
        self.default_kid = "key-01"
        self.publisher_witness_id = settings.PUBLISHER_WITNESS_ID
        self.endpoint = settings.TRACTION_API_URL
        self.tenant_id = settings.TRACTION_TENANT_ID
        self.api_key = settings.TRACTION_API_KEY
        self.headers = {}

    def _traction_error(self, response: requests.Response, operation: str) -> HTTPException:
        detail = response.text
        try:
            body = response.json()
            detail = body.get("detail", body)
        except ValueError:
            pass
        settings.LOGGER.error("Traction %s failed (%s): %s", operation, response.status_code, detail)
        return HTTPException(
            status_code=502,
            detail=f"Traction {operation} failed ({response.status_code}): {detail}",
        )

    def _try_response(self, response, response_key=None):
        if not response.ok:
            raise self._traction_error(response, response_key or "request")
        try:
            payload = response.json()
        except ValueError as err:
            settings.LOGGER.error("Traction returned non-JSON: %s", response.text)
            raise HTTPException(status_code=502, detail="Traction returned non-JSON") from err
        if response_key is None:
            return payload
        if response_key not in payload:
            raise HTTPException(
                status_code=502,
                detail=f"Traction response missing '{response_key}'",
            )
        return payload[response_key]

    async def provision(self):
        """Idempotent startup provisioning from ``configs/issuers.yaml``."""
        self.authorize()
        from app.repo_configs.loader import list_issuer_instances
        from app.services.provisioning import (
            ensure_credential_type,
            ensure_issuer_record,
            ensure_issuer_status_lists,
        )

        issuers = list_issuer_instances()
        settings.LOGGER.info("Provisioning %s issuer(s) from issuers.yaml.", len(issuers))
        mongo = MongoClient()
        from migrations import run_migrations

        run_migrations(mongo.db)

        for issuer in issuers:
            issuer_id = (issuer.get("id") or "").strip()
            name = issuer.get("name") or issuer_id
            expected_vm = (issuer.get("verificationMethod") or "").strip()
            settings.LOGGER.info("Provisioning issuer %s (%s)", name, issuer_id)
            if not issuer_id:
                settings.LOGGER.warning("Skipping issuer with empty id.")
                continue

            ensure_issuer_record(issuer, mongo=mongo)
            await self._check_issuer_did_and_key(
                mongo=mongo,
                issuer_id=issuer_id,
                expected_vm=expected_vm,
            )
            await ensure_issuer_status_lists(issuer_id, mongo=mongo)

            for credential in issuer.get("credentials") or []:
                if not isinstance(credential, dict):
                    continue
                ensure_credential_type(
                    issuer=issuer,
                    credential=credential,
                    mongo=mongo,
                )

    async def _check_issuer_did_and_key(
        self,
        *,
        mongo: MongoClient,
        issuer_id: str,
        expected_vm: str,
    ) -> None:
        """Optional DID/key checks after the local IssuerInstanceRecord exists.

        Operations are logged as Skip when a check fails (no auto-create yet).
        """
        if not expected_vm:
            settings.LOGGER.info(
                "Issuer %s has no verificationMethod in configs; skip DID/key checks.",
                issuer_id,
            )
            return

        did_document = self.resolve(issuer_id, required=False)
        if not did_document:
            settings.LOGGER.info(
                "Skip: DID %s does not resolve (would register/create DID).",
                issuer_id,
            )
            return

        if not self._did_document_has_multikey(did_document, expected_vm):
            settings.LOGGER.info(
                "Skip: verificationMethod %s not on DID document for %s "
                "(would publish/update DID document).",
                expected_vm,
                issuer_id,
            )
            return

        if not self.has_local_multikey(issuer_id, expected_vm):
            settings.LOGGER.info(
                "Skip: Traction tenant has no local key %s for %s "
                "(would import/bind key).",
                expected_vm,
                issuer_id,
            )
            return

        settings.LOGGER.info(
            "Issuer %s checks OK (DID resolves, VM present, key local).",
            issuer_id,
        )
        issuer_record = mongo.find_one("IssuerInstanceRecord", {"id": issuer_id})
        if issuer_record and issuer_record.get("authorized_key") != expected_vm:
            refreshed = {**issuer_record, "authorized_key": expected_vm}
            refreshed.pop("_id", None)
            mongo.replace("IssuerInstanceRecord", {"id": issuer_id}, refreshed)
            settings.LOGGER.info(
                "Updated authorized_key on local issuer record for %s.",
                issuer_id,
            )

    def resolve(self, did, *, required: bool = True):
        r = requests.get(
            f"{self.endpoint}/resolver/resolve/{did}",
            headers=self.headers,
        )
        if not r.ok:
            if not required:
                settings.LOGGER.warning(
                    "Could not resolve DID %s (%s): %s",
                    did,
                    r.status_code,
                    r.text,
                )
                return None
            raise self._traction_error(r, "did_document")
        return self._try_response(r, "did_document")

    @staticmethod
    def _did_document_has_multikey(did_document: dict, multikey: str) -> bool:
        """True if ``multikey`` appears on any verificationMethod (id or publicKeyMultibase)."""
        if not multikey:
            return False
        for method in did_document.get("verificationMethod") or []:
            if not isinstance(method, dict):
                continue
            pk = (method.get("publicKeyMultibase") or "").strip()
            method_id = (method.get("id") or "").strip()
            if pk == multikey:
                return True
            if method_id.endswith(f"#{multikey}") or multikey in method_id:
                return True
        return False

    def get_multikey(self, did, *, required: bool = True):
        r = requests.get(f"{self.endpoint}/wallet/did?did={did}", headers=self.headers)
        if not r.ok:
            if not required:
                settings.LOGGER.warning(
                    "Could not look up wallet DID %s (%s): %s",
                    did,
                    r.status_code,
                    r.text,
                )
                return None
            raise self._traction_error(r, "wallet_did")
        did_info = self._try_response(r, "results")
        if not did_info:
            return None
        return verkey_to_multikey(did_info[0]["verkey"])

    def has_local_multikey(self, issuer_did: str, multikey: str) -> bool:
        """True if the tenant wallet holds ``multikey`` for the issuer DID or as did:key."""
        for did in (issuer_did, f"did:key:{multikey}"):
            found = self.get_multikey(did, required=False)
            if found and found == multikey:
                return True
        return False

    def authorize(self):
        r = requests.post(
            f"{self.endpoint}/multitenancy/tenant/{self.tenant_id}/token",
            json={"api_key": self.api_key},
        )
        token = self._try_response(r, "token")
        if not token:
            raise HTTPException(status_code=502, detail="Traction did not return a tenant token")
        self.headers = {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _multikey_from_did_key(did: str) -> str:
        """Return the method-specific multikey from a ``did:key`` identifier."""
        if not did.startswith("did:key:"):
            raise HTTPException(status_code=502, detail=f"Expected did:key, got {did}")
        return did.removeprefix("did:key:").split("#", 1)[0]

    def create_did_key(self) -> str:
        """Create a ``did:key`` in the tenant wallet and return its multikey."""
        r = requests.post(
            f"{self.endpoint}/wallet/did/create",
            headers=self.headers,
            json={"method": "key", "options": {"key_type": "ed25519"}},
        )
        did_info = self._try_response(r, "result")
        return self._multikey_from_did_key(did_info["did"])

    def get_multikey(self, did):
        r = requests.get(f"{self.endpoint}/wallet/did?did={did}", headers=self.headers)
        did_info = self._try_response(r, "results")
        if not did_info:
            return None
        return verkey_to_multikey(did_info[0]["verkey"])

    def create_did_web(self, did):
        """Create a ``did:web`` in the wallet (must be a valid ``did:web:…`` identifier)."""
        r = requests.post(
            f"{self.endpoint}/wallet/did/create",
            headers=self.headers,
            json={"method": "web", "options": {"did": did, "key_type": "ed25519"}},
        )
        did_info = self._try_response(r, "result")
        if not did_info or not did_info.get("verkey"):
            raise HTTPException(
                status_code=502,
                detail="Traction did not return a verkey for did:web creation",
            )
        return verkey_to_multikey(did_info["verkey"])

    def create_key(self, kid=None):
        r = requests.post(
            f"{self.endpoint}/wallet/keys",
            headers=self.headers,
            json={"kid": kid} if kid else {},
        )
        return self._try_response(r, "multikey")

    def bind_key(self, multikey, kid):
        r = requests.put(
            f"{self.endpoint}/wallet/keys",
            headers=self.headers,
            json={"multikey": multikey, "kid": kid},
        )
        return self._try_response(r, "kid")

    def sign_vc_jwt(self, document):
        did = (
            document.get("issuer")
            if isinstance(document.get("issuer"), str)
            else document.get("issuer").get("id")
        )
        if did.startswith("did:web:"):
            verification_method = f"{did}#{self.default_kid}-multikey"
        elif did.startswith("did:key:"):
            verification_method = did_key_verification_method(did)
        else:
            verification_method = f"{did}#{self.default_kid}-jwk"
        r = requests.post(
            f"{self.endpoint}/wallet/jwt/sign",
            headers=self.headers,
            json={
                "did": did,
                "verificationMethod": verification_method,
                "headers": {"typ": "vc+jwt"},
                "payload": document,
            },
        )
        if not r.ok:
            raise self._traction_error(r, "jwt/sign")
        try:
            payload = r.json()
        except ValueError as err:
            raise HTTPException(status_code=502, detail="Traction returned non-JSON") from err
        # Traction may return the compact JWT as a JSON string.
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            for key in ("jwt", "token", "signed", "vc+jwt"):
                if key in payload and isinstance(payload[key], str):
                    return payload[key]
        raise HTTPException(status_code=502, detail="Traction jwt/sign missing JWT string")

    @staticmethod
    def as_enveloped_vc(vc_jwt: str) -> dict:
        """Wrap a compact vc+jwt as a VCDM 2.0 EnvelopedVerifiableCredential."""
        return {
            "@context": ["https://www.w3.org/ns/credentials/v2"],
            "id": f"data:application/vc+jwt,{vc_jwt}",
            "type": "EnvelopedVerifiableCredential",
        }

    def issue_vc(self, credential):
        settings.LOGGER.info("Issuing Credential")
        did = credential.get('issuer') if isinstance(credential.get('issuer'), str) else credential.get('issuer').get('id')
        if did.startswith('did:web:'):
            verification_method = f"{did}#{self.default_kid}-multikey"
        elif did.startswith('did:key:'):
            verification_method = did_key_verification_method(did)
        proof_options = {
            "type": "DataIntegrityProof",
            "cryptosuite": "eddsa-jcs-2022",
            "proofPurpose": "assertionMethod",
            "verificationMethod": verification_method,
            "created": timestamp(),
        }
        return self.add_di_proof(credential, proof_options)

    def create_vp(self, vc):
        settings.LOGGER.info("Creating Presentation")
        did = vc["issuer"]["id"]
        if did.startswith('did:web:'):
            verification_method = f"{did}#{self.default_kid}-multikey"
        elif did.startswith('did:key:'):
            verification_method = did_key_verification_method(did)
        presentation = {
            '@context': [
                'https://www.w3.org/ns/credentials/v2'
            ],
            'type': ['VerifiablePresentation'],
            'verifiableCredential': [vc]
        }
        proof_options = {
            "type": "DataIntegrityProof",
            "cryptosuite": "eddsa-jcs-2022",
            "proofPurpose": "authentication",
            "verificationMethod": verification_method,
            "created": timestamp(),
        }
        return self.add_di_proof(presentation, proof_options)

    def add_di_proof(self, document, options):
        r = requests.post(
            f"{self.endpoint}/vc/di/add-proof",
            headers=self.headers,
            json={
                "document": document,
                "options": options,
            },
        )
        return self._try_response(r, "securedDocument")

    def endorse(self, document, options):
        options["verificationMethod"] = did_key_verification_method(
            self.publisher_witness_id
        )
        r = requests.post(
            f"{self.endpoint}/vc/di/add-proof",
            headers=self.headers,
            json={
                "document": document,
                "options": options,
            },
        )
        return self._try_response(r, "securedDocument")

    def verify_di_proof(self, secured_document):
        r = requests.post(
            f"{self.endpoint}/vc/di/verify",
            headers=self.headers,
            json={
                "securedDocument": secured_document,
            },
        )
        return self._try_response(r, "verified")
