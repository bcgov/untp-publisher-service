from config import settings
import requests
from fastapi import HTTPException  # used by ensure_publisher_witness
from app.utils import verkey_to_multikey, timestamp
from app.plugins.mongodb import MongoClient
from app.models.mongodb import IssuerRecord


class TractionControllerError(Exception):
    """Generic TractionController Error."""


class TractionController:
    def __init__(self):
        self.default_kid = "key-01"
        self.publisher_multikey = settings.PUBLISHER_WITNESS_MULTIKEY
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
        from app.repo_configs import list_issuer_instances
        from app.services.status_lists import ensure_issuer_status_lists

        issuers = list_issuer_instances()
        settings.LOGGER.info("Provisioning %s issuer(s) from issuers.yaml.", len(issuers))
        mongo = MongoClient()
        mongo.provision()

        for issuer in issuers:
            issuer_id = (issuer.get("id") or "").strip()
            name = issuer.get("name") or issuer_id
            expected_vm = (issuer.get("verificationMethod") or "").strip()
            settings.LOGGER.info("Provisioning issuer %s (%s)", name, issuer_id)
            if not issuer_id:
                settings.LOGGER.warning("Skipping issuer with empty id.")
                continue

            await self._provision_issuer_did_and_key(
                mongo=mongo,
                issuer_id=issuer_id,
                name=name,
                expected_vm=expected_vm,
            )
            await ensure_issuer_status_lists(issuer_id, mongo=mongo)

    async def _provision_issuer_did_and_key(
        self,
        *,
        mongo: MongoClient,
        issuer_id: str,
        name: str,
        expected_vm: str,
    ) -> None:
        """DID/key checks; operations skipped for now when a check fails."""
        if not expected_vm:
            settings.LOGGER.warning(
                "Issuer %s has no verificationMethod; skip DID/key checks.",
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
        issuer_record = mongo.find_one("IssuerRecord", {"id": issuer_id})
        if not issuer_record:
            mongo.insert(
                "IssuerRecord",
                IssuerRecord(
                    id=issuer_id,
                    name=name,
                    authorized_key=expected_vm,
                ).model_dump(),
            )
            settings.LOGGER.info("Local issuer record created for %s.", issuer_id)
        elif issuer_record.get("authorized_key") != expected_vm:
            settings.LOGGER.warning(
                "Issuer %s local authorized_key mismatch "
                "(record=%s, config=%s); leave unchanged.",
                issuer_id,
                issuer_record.get("authorized_key"),
                expected_vm,
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

    def create_issuer_update_multikey(self) -> str:
        """
        Provision the issuer genesis ``updateKeys`` multikey for DID WebVH registration.

        ACA-Py cannot register ``did:webvh:…`` placeholders in the wallet; the update key is a
        normal ``did:key`` used to sign the log entry and listed in ``parameters.updateKeys``.
        """
        return self.create_did_key()

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

    def ensure_publisher_witness(self) -> str:
        """
        Ensure ``PUBLISHER_WITNESS_ID`` is usable in this tenant and return its multikey.

        The witness ``did:key`` must be present in the wallet (bound to the configured multikey).
        """
        witness_did = settings.PUBLISHER_WITNESS_ID
        multikey = self.publisher_multikey
        if not witness_did or not multikey:
            raise HTTPException(
                status_code=500,
                detail="PUBLISHER_WITNESS_ID is not configured or is invalid",
            )
        existing = self.get_multikey(witness_did)
        witness_vm = f"{witness_did}#{multikey}"
        if not existing:
            self.bind_key(multikey, witness_vm)
        elif existing != multikey:
            settings.LOGGER.warning(
                "Wallet multikey for witness DID does not match PUBLISHER_WITNESS_ID"
            )
        return multikey

    def sign_vc_jwt(self, document):
        did = document.get('issuer') if isinstance(document.get('issuer'), str) else document.get('issuer').get('id')
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
        return r.json()

    def issue_vc(self, credential):
        settings.LOGGER.info("Issuing Credential")
        did = credential.get('issuer') if isinstance(credential.get('issuer'), str) else credential.get('issuer').get('id')
        if did.startswith('did:web:'):
            verification_method = f"{did}#{self.default_kid}-multikey"
        elif did.startswith('did:key:'):
            verification_method = f"{did}#{settings.PUBLISHER_WITNESS_MULTIKEY}"
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
            verification_method = f"{did}#{settings.PUBLISHER_WITNESS_MULTIKEY}"
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
        options["verificationMethod"] = (
            f"did:key:{self.publisher_multikey}#{self.publisher_multikey}"
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
