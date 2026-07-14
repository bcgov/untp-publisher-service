import pytest

from witness import did_key_to_multikey, did_key_verification_method

WITNESS_MULTIKEY = "z6MkekByGjKYvP6dpMBJEHt5UN72rKPVoDgzcS9Hoq6bjfr1"
WITNESS_DID = f"did:key:{WITNESS_MULTIKEY}"


def test_did_key_to_multikey():
    assert did_key_to_multikey(WITNESS_DID) == WITNESS_MULTIKEY


def test_did_key_to_multikey_with_fragment():
    assert did_key_to_multikey(f"{WITNESS_DID}#{WITNESS_MULTIKEY}") == WITNESS_MULTIKEY


def test_did_key_to_multikey_rejects_non_did_key():
    with pytest.raises(ValueError, match="did:key"):
        did_key_to_multikey(WITNESS_MULTIKEY)


def test_did_key_verification_method():
    assert (
        did_key_verification_method(WITNESS_DID)
        == f"{WITNESS_DID}#{WITNESS_MULTIKEY}"
    )
