from __future__ import annotations

import pytest

from src.utils.imasds_arm9_patch import (
    PATCH_SITES,
    PatchError,
    inspect_arm9,
    patch_arm9_bytes,
)


def make_arm9(*, patched: bool = False) -> bytes:
    size = max(site.offset + len(site.original) for site in PATCH_SITES)
    data = bytearray(size)
    for site in PATCH_SITES:
        payload = site.patched if patched else site.original
        data[site.offset : site.offset + len(payload)] = payload
    return bytes(data)


def test_original_arm9_is_patched_at_all_four_known_sites() -> None:
    patched, original_states = patch_arm9_bytes(make_arm9())
    assert all(state == "original" for _site, state in original_states)
    assert all(state == "patched" for _site, state in inspect_arm9(patched))


def test_complete_patch_is_idempotent() -> None:
    source = make_arm9(patched=True)
    output, states = patch_arm9_bytes(source)
    assert output == source
    assert all(state == "patched" for _site, state in states)


def test_unknown_machine_code_is_rejected_without_guessing() -> None:
    source = bytearray(make_arm9())
    source[PATCH_SITES[0].offset] ^= 0x01
    with pytest.raises(PatchError, match="机器码不匹配"):
        patch_arm9_bytes(bytes(source))

