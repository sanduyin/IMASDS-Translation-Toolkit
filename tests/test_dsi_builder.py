from __future__ import annotations

import struct

from src.ndstool.dsi_builder import (
    DSI_EXTRA_FIELDS_OFFSET,
    DSI_EXTRA_FIELDS_SIZE,
    DsiExtraFields,
    aes_ctr,
    get_key_ivs,
    modcrypt,
    rebuild_dsi_rom,
    verify_dsi_integrity,
)
from src.ndstool.header import HEADER_SIZE, NDSHeader
from tools.audit_final_rom import Result, audit_dsi_layout, audit_header


def patterned(size: int, seed: int) -> bytes:
    return bytes(((index * 37 + seed) & 0xFF) for index in range(size))


def test_header_and_dsi_extra_roundtrip_at_absolute_0x180() -> None:
    base = patterned(HEADER_SIZE, 3)
    extra = patterned(DSI_EXTRA_FIELDS_SIZE, 9)

    assert NDSHeader.parse(base).build(update_crc=False) == base
    assert DsiExtraFields.parse(extra).build() == extra

    full = bytearray(0x1000)
    full[:HEADER_SIZE] = base
    full[DSI_EXTRA_FIELDS_OFFSET:] = extra
    assert full[0x1C0:0x1C4] == extra[0x40:0x44]


def test_aes_ctr_fixed_vector() -> None:
    data = bytearray(range(32))
    aes_ctr(
        data,
        0x00112233445566778899AABBCCDDEEFF,
        0xFFEEDDCCBBAA99887766554433221100,
    )
    assert data.hex() == (
        "355af83826f7e5097544a81335342f7d"
        "7eed855abb5e7d3a619701815b2abd72"
    )


def make_original_dsi_rom(path) -> tuple[bytes, bytes]:
    header = NDSHeader(
        title=b"IMASDS TEST\0",
        gamecode=b"VIMJ",
        makercode=b"BN",
        unitcode=2,
        devicecap=4,
        dsi_flags=2,
        arm9_rom_offset=0x4000,
        arm9_size=0x10000,
        arm7_rom_offset=0x15000,
        arm7_size=0x1000,
        banner_offset=0x16000,
        rom_header_size=0x4000,
    )
    dsi = DsiExtraFields(
        dsi9_rom_offset=0x103000,
        dsi9_ram_address=0x02E80000,
        dsi9_size=0x400,
        dsi7_rom_offset=0x103400,
        dsi7_ram_address=0x02F80000,
        dsi7_size=0x400,
        banner_size=0x840,
        modcrypt1_start=0x103000,
        modcrypt1_size=0x400,
        modcrypt2_start=0x103400,
        modcrypt2_size=0x400,
        hmac_arm9=patterned(0x14, 0x11),
        hmac_arm7=patterned(0x14, 0x22),
        hmac_arm9i=patterned(0x14, 0x33),
        tid_low=0x4A4D4956,
        tid_high=0x00030000,
    )
    arm9i_plain = b"A9I0" + patterned(0x3FC, 0x41)
    arm7i_plain = b"A7I0" + patterned(0x3FC, 0x57)

    original = bytearray(b"\xFF" * 0x200000)
    original[:HEADER_SIZE] = header.build(update_crc=True)
    original[DSI_EXTRA_FIELDS_OFFSET:0x1000] = dsi.build()
    original[dsi.dsi9_rom_offset : dsi.dsi9_rom_offset + len(arm9i_plain)] = arm9i_plain
    original[dsi.dsi7_rom_offset : dsi.dsi7_rom_offset + len(arm7i_plain)] = arm7i_plain
    modcrypt(original, header.gamecode, dsi)
    path.write_bytes(original)
    return arm9i_plain, arm7i_plain


def make_ntr_base() -> bytes:
    header = NDSHeader(
        title=b"IMASDS TEST\0",
        gamecode=b"VIMJ",
        makercode=b"BN",
        unitcode=2,
        devicecap=4,
        dsi_flags=2,
        arm9_rom_offset=0x4000,
        arm9_entry_address=0x02004800,
        arm9_ram_address=0x02004000,
        arm9_size=0x10000,
        arm7_rom_offset=0x15000,
        arm7_entry_address=0x037F8000,
        arm7_ram_address=0x037F8000,
        arm7_size=0x1000,
        banner_offset=0x16000,
        rom_header_size=0x4000,
    )
    ntr = bytearray(b"\xFF" * 0x20000)
    ntr[:HEADER_SIZE] = header.build(update_crc=True)
    struct.pack_into("<II", ntr, 0x4000, 0xE7FFDEFF, 0xE7FFDEFF)
    ntr[0x4008:0x14000] = patterned(0xFFF8, 0x19)
    ntr[0x15000:0x16000] = patterned(0x1000, 0x27)
    ntr[0x16000:0x16840] = patterned(0x840, 0x31)
    return bytes(ntr)


def test_complete_dsi_rebuild_roundtrip(tmp_path) -> None:
    original_path = tmp_path / "original.nds"
    arm9i_plain, arm7i_plain = make_original_dsi_rom(original_path)

    rebuilt, report = rebuild_dsi_rom(make_ntr_base(), original_path)
    assert len(rebuilt) == 0x200000
    assert report.application_end_offset < report.dsi9_rom_offset
    assert report.modcrypt1_start == report.dsi9_rom_offset
    assert report.modcrypt2_start == report.dsi7_rom_offset

    verification = verify_dsi_integrity(rebuilt)
    assert verification["all_ok"], verification

    header = NDSHeader.parse(rebuilt[:HEADER_SIZE])
    dsi = DsiExtraFields.parse(rebuilt[DSI_EXTRA_FIELDS_OFFSET:0x1000])
    canonical = bytearray(rebuilt)
    modcrypt(canonical, header.gamecode, dsi)
    assert canonical[dsi.dsi9_rom_offset : dsi.dsi9_rom_offset + len(arm9i_plain)] == arm9i_plain
    assert canonical[dsi.dsi7_rom_offset : dsi.dsi7_rom_offset + len(arm7i_plain)] == arm7i_plain

    # 绝对 Header 偏移门禁：Pico Loader 从这些位置直接读取。
    assert struct.unpack_from("<I", rebuilt, 0x1C0)[0] == dsi.dsi9_rom_offset
    assert struct.unpack_from("<I", rebuilt, 0x1D0)[0] == dsi.dsi7_rom_offset

    rebuilt_path = tmp_path / "rebuilt.nds"
    rebuilt_path.write_bytes(rebuilt)
    audit_result = Result(rebuilt_path, rebuilt)
    fields = audit_header(rebuilt, audit_result)
    audit_dsi_layout(rebuilt, fields, audit_result)
    assert audit_result.error_count == 0, audit_result.to_dict()
