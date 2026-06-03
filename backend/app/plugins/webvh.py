"""DID WebVH (did:webvh:1.0) log entry helpers for BCVH-style registries."""

from __future__ import annotations

import copy
import json
from typing import Any

from did_webvh.core.state import DocumentState
from did_webvh.provision import provision_did
from fastapi import HTTPException

from app.models.did_document import DidDocument, VerificationMethod
from app.plugins.traction import TractionController
from app.utils import multikey_to_jwk, timestamp
from config import settings


def is_log_entry_template(payload: dict[str, Any]) -> bool:
    return (
        isinstance(payload, dict)
        and "versionId" in payload
        and "parameters" in payload
        and "state" in payload
    )


def provisional_did_from_template(template: dict[str, Any]) -> str:
    state = template.get("state") or {}
    did = state.get("id") or state.get("@id")
    if not did:
        raise HTTPException(
            status_code=502,
            detail="DID WebVH template missing state.id",
        )
    return did


def build_issuer_state(
    *,
    template: dict[str, Any],
    registration: dict[str, Any],
    authorized_key: str,
) -> dict[str, Any]:
    """Merge server log-entry hint with issuer DID document keys in ``state``.

    Note: BCVH server models normalize unknown DID Document fields before SCID
    verification. Keep genesis ``state`` limited to fields accepted by the
    registry model, or server-side normalization will change SCID input.
    """
    did = provisional_did_from_template(template)
    default_kid = "key-01"
    multikey_kid = f"{did}#{default_kid}-multikey"
    jwk_kid = f"{did}#{default_kid}-jwk"

    did_document = DidDocument(
        id=did,
        controller=did,
        authentication=[multikey_kid, jwk_kid],
        assertionMethod=[multikey_kid, jwk_kid],
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

    if registration.get("multikey"):
        multikey = registration["multikey"]
        delegated_kid_multikey = f"{did}#{multikey}"
        delegated_kid_jwk = f"{did}#{multikey}-jwk"
        did_document.authentication.append(delegated_kid_multikey)
        did_document.authentication.append(delegated_kid_jwk)
        did_document.assertionMethod.append(delegated_kid_multikey)
        did_document.assertionMethod.append(delegated_kid_jwk)
        did_document.verificationMethod.append(
            VerificationMethod(
                id=delegated_kid_multikey,
                type="Multikey",
                controller=did,
                publicKeyMultibase=multikey,
            )
        )
        did_document.verificationMethod.append(
            VerificationMethod(
                id=delegated_kid_jwk,
                type="JsonWebKey",
                controller=did,
                publicKeyJwk=multikey_to_jwk(multikey),
            )
        )

    return did_document.model_dump()


def _witness_params() -> dict[str, Any] | None:
    witness_id = settings.PUBLISHER_WITNESS_ID
    if not witness_id:
        return None
    return {
        "threshold": 1,
        "witnesses": [{"id": witness_id}],
    }


def build_genesis_document_state(
    *,
    template: dict[str, Any],
    registration: dict[str, Any],
    authorized_key: str,
) -> DocumentState:
    """Provision genesis state (SCID + versionId) via ``did-webvh``."""
    document = build_issuer_state(
        template=template,
        registration=registration,
        authorized_key=authorized_key,
    )
    params = copy.deepcopy(template.get("parameters") or {})
    params["updateKeys"] = [authorized_key]
    witness = _witness_params()
    if witness:
        params["witness"] = witness
    return provision_did(
        document,
        params=params,
        timestamp=template.get("versionTime"),
    )


def unsigned_log_entry(state: DocumentState) -> dict[str, Any]:
    """Canonical unsigned log line (no proofs) for Traction DI signing."""
    line = state.history_line()
    return {k: v for k, v in line.items() if k != "proof"}


def witness_unsigned_document(log_entry: dict[str, Any]) -> dict[str, Any]:
    """Payload the witness DI proof is computed over (no ``proof`` field).

    BCVH verifies witness proofs against the `witnessSignature` container, which
    currently contains only `versionId` plus `proof`.
    """
    return {
        "versionId": log_entry["versionId"],
    }


def log_submission_payload(
    *,
    namespace: str,
    identifier: str,
    log_entry: dict[str, Any],
    witness_signature: dict[str, Any] | None = None,
    unsigned_log_entry: dict[str, Any] | None = None,
) -> None:
    """Emit the WebVH POST body to server logs (uvicorn terminal) for SCID / proof debugging."""
    if unsigned_log_entry is not None:
        settings.LOGGER.info(
            "WebVH unsigned log entry (%s/%s):\n%s",
            namespace,
            identifier,
            json.dumps(unsigned_log_entry, indent=2, sort_keys=True),
        )

    issuer_proofs = log_entry.get("proof")
    if issuer_proofs:
        settings.LOGGER.info(
            "WebVH issuer proof(s) (%s/%s):\n%s",
            namespace,
            identifier,
            json.dumps(issuer_proofs, indent=2, sort_keys=True),
        )

    settings.LOGGER.info(
        "WebVH submission (%s/%s) logEntry:\n%s",
        namespace,
        identifier,
        json.dumps(log_entry, indent=2, sort_keys=True),
    )

    if witness_signature is not None:
        settings.LOGGER.info(
            "WebVH witness unsigned document (%s/%s):\n%s",
            namespace,
            identifier,
            json.dumps(witness_unsigned_document(log_entry), indent=2, sort_keys=True),
        )
        witness_proofs = witness_signature.get("proof")
        if witness_proofs:
            settings.LOGGER.info(
                "WebVH witness proof(s) (%s/%s):\n%s",
                namespace,
                identifier,
                json.dumps(witness_proofs, indent=2, sort_keys=True),
            )
        settings.LOGGER.info(
            "WebVH submission (%s/%s) witnessSignature:\n%s",
            namespace,
            identifier,
            json.dumps(witness_signature, indent=2, sort_keys=True),
        )


def _proof_options_from_template(
    template_proof: dict[str, Any] | None,
    *,
    verification_method: str,
) -> dict[str, Any]:
    options = {
        "type": "DataIntegrityProof",
        "cryptosuite": "eddsa-jcs-2022",
        "proofPurpose": "assertionMethod",
        "verificationMethod": verification_method,
        "created": timestamp(),
    }
    if isinstance(template_proof, dict):
        options.update(
            {k: v for k, v in template_proof.items() if k not in ("proofValue", "verificationMethod")}
        )
        options["verificationMethod"] = verification_method
        options.setdefault("created", timestamp())
    return options


def _secured_proof(secured: dict[str, Any] | None) -> dict[str, Any]:
    if not secured:
        raise HTTPException(status_code=502, detail="Traction did not return a secured document")
    proof = secured.get("proof")
    if isinstance(proof, list):
        if not proof:
            raise HTTPException(status_code=502, detail="Traction returned empty proof list")
        return proof[0]
    if isinstance(proof, dict):
        return proof
    raise HTTPException(status_code=502, detail="Secured document has no proof")


def sign_log_entry(
    traction: TractionController,
    log_entry: dict[str, Any],
    *,
    verification_method: str,
    template_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add Data Integrity proof to the log entry (issuer / update key)."""
    unsigned = {k: v for k, v in log_entry.items() if k != "proof"}
    options = _proof_options_from_template(template_proof, verification_method=verification_method)
    secured = traction.add_di_proof(unsigned, options)
    signed = copy.deepcopy(unsigned)
    signed["proof"] = [_secured_proof(secured)]
    return signed


def sign_witness_signature(
    traction: TractionController,
    log_entry: dict[str, Any],
    *,
    witness_verification_method: str,
    template_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ``witnessSignature`` for NewLogEntry using the publisher witness key."""
    version_id = log_entry["versionId"]
    witness_doc = witness_unsigned_document(log_entry)
    options = _proof_options_from_template(
        template_proof,
        verification_method=witness_verification_method,
    )
    options["proofPurpose"] = "assertionMethod"
    secured = traction.add_di_proof(witness_doc, options)
    proof = _secured_proof(secured)
    return {
        "versionId": version_id,
        "proof": [proof],
    }


def resolve_did_from_log_entry(log_entry: dict[str, Any]) -> str:
    state = log_entry.get("state") or {}
    did = state.get("id") or state.get("@id")
    if not did or "{SCID}" in did:
        raise HTTPException(
            status_code=502,
            detail="Registry response did not assign a final DID (SCID still placeholder)",
        )
    return did
