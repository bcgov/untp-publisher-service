"""Tests for DID WebVH log entry helpers."""

import pytest

from app.plugins import webvh as webvh_log

TEMPLATE = {
    "versionId": "{SCID}",
    "versionTime": "2026-05-28T04:59:01Z",
    "parameters": {
        "scid": "{SCID}",
        "method": "did:webvh:1.0",
        "updateKeys": [],
    },
    "state": {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": "did:webvh:{SCID}:sandbox.bcvh.vonx.io:my-scope:my-issuer",
    },
    "proof": {
        "type": "DataIntegrityProof",
        "cryptosuite": "eddsa-jcs-2022",
        "proofPurpose": "assertionMethod",
    },
}


def test_is_log_entry_template():
    assert webvh_log.is_log_entry_template(TEMPLATE)
    assert not webvh_log.is_log_entry_template({"didDocument": {"id": "x"}})


def test_build_issuer_state():
    state = webvh_log.build_issuer_state(
        template=TEMPLATE,
        registration={
            "name": "My Issuer",
            "description": "Test issuer",
        },
        authorized_key="z6MkekByGjKYvP6dpMBJEHt5UN72rKPVoDgzcS9Hoq6bjfr1",
    )
    assert state["id"] == TEMPLATE["state"]["id"]
    assert "@context" in state
    assert "name" not in state  # BCVH registry rejects extra DID doc fields before SCID
    assert len(state["verificationMethod"]) == 2


def test_build_genesis_document_state_resolves_scid():
    doc_state = webvh_log.build_genesis_document_state(
        template=TEMPLATE,
        registration={"name": "My Issuer", "description": "Test issuer"},
        authorized_key="z6MkekByGjKYvP6dpMBJEHt5UN72rKPVoDgzcS9Hoq6bjfr1",
    )
    assert doc_state.version_id.startswith("1-")
    assert "{SCID}" not in doc_state.document_id
    unsigned = webvh_log.unsigned_log_entry(doc_state)
    assert unsigned["versionId"] == doc_state.version_id
    assert "proof" not in unsigned


def test_resolve_did_rejects_placeholder():
    with pytest.raises(Exception):
        webvh_log.resolve_did_from_log_entry(TEMPLATE)
