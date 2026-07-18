# tests/packez/test_ezt_parser.py
"""packez/ezt_parser 模块单元测试。"""
from __future__ import annotations

import struct

import pytest

from src.packez.ezt_parser import (
    EZT_MAGIC1,
    EZP_MAGIC1,
    EZ_MAGIC2,
    EZT_HEADER_SIZE,
    EZ_ENTRY_SIZE,
    EZP_HEADER_SIZE,
    _COMPRESSED_FLAG,
    _SIZE_MASK,
    EzTableHeader,
    EzTableEntry,
    EzpHeader,
    read_idx,
    build_idx,
)


# ----------------------------------------------------------------------
# 常量校验
# ----------------------------------------------------------------------

class TestEZTConstants:
    def test_ezt_magic1(self) -> None:
        assert EZT_MAGIC1 == bytes([0x45, 0x5A, 0x54, 0x00, 0x0E, 0x07, 0xD9, 0x07])

    def test_ezp_magic1(self) -> None:
        assert EZP_MAGIC1 == bytes([0x45, 0x5A, 0x50, 0x00, 0x0E, 0x07, 0xD9, 0x07])

    def test_magic2(self) -> None:
        assert EZ_MAGIC2 == 0x16

    def test_sizes(self) -> None:
        assert EZT_HEADER_SIZE == 16
        assert EZ_ENTRY_SIZE == 12
        assert EZP_HEADER_SIZE == 16

    def test_compressed_flag_and_mask(self) -> None:
        assert _COMPRESSED_FLAG == 0x10000000
        assert _SIZE_MASK == 0x0FFFFFFF


# ----------------------------------------------------------------------
# EzTableHeader
# ----------------------------------------------------------------------

class TestEzTableHeader:
    def test_round_trip(self) -> None:
        header = EzTableHeader(something1=0x0001, entry_count=42)
        encoded = header.to_bytes()
        assert len(encoded) == EZT_HEADER_SIZE
        decoded = EzTableHeader.from_bytes(encoded)
        assert decoded.something1 == 0x0001
        assert decoded.entry_count == 42

    def test_short_data_raises(self) -> None:
        with pytest.raises(ValueError, match="过短"):
            EzTableHeader.from_bytes(b"\x00" * 10)

    def test_bad_magic1_raises(self) -> None:
        bad = bytearray(EZT_MAGIC1)
        bad[0] = 0xFF
        data = bytes(bad) + struct.pack("<HHI", 1, EZ_MAGIC2, 0)
        with pytest.raises(ValueError, match="magic1"):
            EzTableHeader.from_bytes(data)

    def test_bad_magic2_raises(self) -> None:
        data = EZT_MAGIC1 + struct.pack("<HHI", 1, 0x9999, 0)
        with pytest.raises(ValueError, match="magic2"):
            EzTableHeader.from_bytes(data)


# ----------------------------------------------------------------------
# EzTableEntry
# ----------------------------------------------------------------------

class TestEzTableEntry:
    def test_round_trip(self) -> None:
        entry = EzTableEntry(
            file_offset=0x1000,
            size_and_compressed_flag=0x200 | _COMPRESSED_FLAG,
            name_offset=0x50,
        )
        encoded = entry.to_bytes()
        assert len(encoded) == EZ_ENTRY_SIZE
        decoded = EzTableEntry.from_bytes(encoded)
        assert decoded.file_offset == 0x1000
        assert decoded.size_and_compressed_flag == 0x200 | _COMPRESSED_FLAG
        assert decoded.name_offset == 0x50

    def test_decompressed_size_property(self) -> None:
        entry = EzTableEntry(
            file_offset=0,
            size_and_compressed_flag=0x12345 | _COMPRESSED_FLAG,
            name_offset=0,
        )
        assert entry.decompressed_size == 0x12345

    def test_is_compressed_property(self) -> None:
        compressed = EzTableEntry(0, 0x100 | _COMPRESSED_FLAG, 0)
        uncompressed = EzTableEntry(0, 0x100, 0)
        assert compressed.is_compressed is True
        assert uncompressed.is_compressed is False

    def test_make_factory(self) -> None:
        entry = EzTableEntry.make(
            file_offset=0x2000,
            decompressed_size=0xFF,
            is_compressed=True,
            name_offset=0x10,
        )
        assert entry.file_offset == 0x2000
        assert entry.decompressed_size == 0xFF
        assert entry.is_compressed is True
        assert entry.name_offset == 0x10

    def test_make_factory_uncompressed(self) -> None:
        entry = EzTableEntry.make(
            file_offset=0,
            decompressed_size=0x1000,
            is_compressed=False,
            name_offset=0,
        )
        assert entry.is_compressed is False
        assert entry.decompressed_size == 0x1000


# ----------------------------------------------------------------------
# EzpHeader
# ----------------------------------------------------------------------

class TestEzpHeader:
    def test_round_trip(self) -> None:
        header = EzpHeader(something1=0x0001, names_offset=0x8000)
        encoded = header.to_bytes()
        assert len(encoded) == EZP_HEADER_SIZE
        decoded = EzpHeader.from_bytes(encoded)
        assert decoded.something1 == 0x0001
        assert decoded.names_offset == 0x8000

    def test_short_data_raises(self) -> None:
        with pytest.raises(ValueError, match="过短"):
            EzpHeader.from_bytes(b"\x00" * 10)

    def test_bad_magic1_raises(self) -> None:
        bad = bytearray(EZP_MAGIC1)
        bad[0] = 0xFF
        data = bytes(bad) + struct.pack("<HHI", 1, EZ_MAGIC2, 0)
        with pytest.raises(ValueError, match="magic1"):
            EzpHeader.from_bytes(data)


# ----------------------------------------------------------------------
# read_idx / build_idx 往返
# ----------------------------------------------------------------------

class TestReadBuildIdx:
    def test_round_trip(self) -> None:
        # 构造一个 IDX 文件
        original_entries = [
            EzTableEntry.make(0x100, 0x200, True, 0x10),
            EzTableEntry.make(0x400, 0x300, False, 0x20),
            EzTableEntry.make(0x800, 0x500, True, 0x30),
        ]
        names_offset = 0x1000
        encoded = build_idx(something1=1, entries=original_entries, names_offset=names_offset)

        # read_idx 解析
        header, entries = read_idx(encoded)
        assert header.something1 == 1
        assert header.entry_count == len(original_entries)
        assert len(entries) == len(original_entries)
        for orig, parsed in zip(original_entries, entries):
            assert parsed.file_offset == orig.file_offset
            assert parsed.decompressed_size == orig.decompressed_size
            assert parsed.is_compressed == orig.is_compressed
            assert parsed.name_offset == orig.name_offset

    def test_read_idx_empty_entries(self) -> None:
        # entry_count=0 的空 IDX
        header = EzTableHeader(something1=1, entry_count=0)
        encoded = header.to_bytes()
        parsed_header, entries = read_idx(encoded)
        assert parsed_header.entry_count == 0
        assert entries == []
