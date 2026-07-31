"""Unit tests for BitstringStatusList encode / bit flip helpers."""

from __future__ import annotations

import pytest

from app.plugins.status_list import BitstringStatusList, BitstringStatusListError


def test_set_status_bit_round_trip():
    bst = BitstringStatusList()
    encoded = bst.generate("0" * 128)
    updated = bst.set_status_bit(encoded, 7, True)
    bits = bst.expand(updated)
    assert bits[7] == "1"
    assert bits.count("1") == 1
    cleared = bst.set_status_bit(updated, 7, False)
    assert bst.expand(cleared)[7] == "0"


def test_set_status_bit_rejects_out_of_range():
    bst = BitstringStatusList()
    encoded = bst.generate("0" * 32)
    with pytest.raises(BitstringStatusListError):
        bst.set_status_bit(encoded, 32, True)


def test_expand_accepts_multibase_u_prefix():
    bst = BitstringStatusList()
    encoded = bst.generate("01" + "0" * 30)
    bits = bst.expand("u" + encoded)
    assert bits[0] == "0"
    assert bits[1] == "1"
