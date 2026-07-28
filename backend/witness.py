"""Witness did:key → multikey helpers (no app imports; safe for config.py)."""

from multiformats import multibase


def did_key_to_multikey(did_key: str) -> str:
    """
    Extract an Ed25519 multikey (publicKeyMultibase) from a ``did:key`` identifier.

    The method-specific id must be a base58btc multibase string with the multikey
    prefix ``0xed 0x01`` (same encoding used in ``did:key:{multikey}`` witness URLs).
    """
    did_key = did_key.strip()
    if not did_key.startswith("did:key:"):
        raise ValueError("PUBLISHER_WITNESS_ID must be a did:key identifier")
    method_id = did_key.removeprefix("did:key:").split("#", 1)[0]
    if not method_id:
        raise ValueError("did:key has no method-specific identifier")
    try:
        raw = multibase.decode(method_id)
    except Exception as exc:
        raise ValueError("did:key method id is not valid multibase") from exc
    if len(raw) != 34 or raw[0] != 0xED or raw[1] != 0x01:
        raise ValueError(
            "did:key must encode an Ed25519 multikey (0xed01 prefix, 32-byte public key)"
        )
    return method_id


def did_key_verification_method(did_key: str) -> str:
    """``did:key:{multikey}#{multikey}`` verificationMethod for an Ed25519 ``did:key``."""
    multikey = did_key_to_multikey(did_key)
    return f"did:key:{multikey}#{multikey}"
