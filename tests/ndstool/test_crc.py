# tests/ndstool/test_crc.py
"""ndstool/crc 模块单元测试。"""
from __future__ import annotations

import struct

import pytest

from src.ndstool.crc import (
    crc16_header,
    crc16_logo,
    verify_header_crc,
    _CRC16_TABLE,
)


# ----------------------------------------------------------------------
# CRC16 算法
# ----------------------------------------------------------------------

class TestCRC16:
    def test_empty_data(self) -> None:
        # 空数据的 CRC 应为初始值 0xFFFF
        assert crc16_header(b"") == 0xFFFF

    def test_single_zero_byte(self) -> None:
        # CRC(0x00) = (0xFFFF >> 8) ^ TABLE[(0xFFFF ^ 0x00) & 0xFF]
        # = 0xFF ^ TABLE[0xFF] = 0xFF ^ 0x4040 = 0x40BF
        assert crc16_header(b"\x00") == 0x40BF

    def test_single_byte_0x01(self) -> None:
        # CRC(0x01) = (0xFFFF >> 8) ^ TABLE[(0xFFFF ^ 0x01) & 0xFF]
        # = 0xFF ^ TABLE[0xFE] = 0xFF ^ 0x8081 = 0x807E
        assert crc16_header(b"\x01") == 0x807E

    def test_known_vector_modbus(self) -> None:
        # CRC-16/MODBUS 标准测试向量："123456789" → 0x4B37
        # 注：本实现初始值 0xFFFF，多项式 0xA001，与 MODBUS 一致
        assert crc16_header(b"123456789") == 0x4B37

    def test_crc16_logo_equals_crc16_header(self) -> None:
        # crc16_logo 只是 crc16_header 的别名
        data = b"\x01\x02\x03\x04"
        assert crc16_logo(data) == crc16_header(data)

    def test_table_length(self) -> None:
        assert len(_CRC16_TABLE) == 256

    def test_table_first_entry_is_zero(self) -> None:
        # CRC 表第 0 项总是 0
        assert _CRC16_TABLE[0] == 0x0000

    def test_crc_is_deterministic(self) -> None:
        data = b"Hello, World!"
        assert crc16_header(data) == crc16_header(data)

    def test_crc_returns_16_bit_value(self) -> None:
        # 结果应总是 16 位
        for byte_val in range(256):
            result = crc16_header(bytes([byte_val]))
            assert 0 <= result <= 0xFFFF


# ----------------------------------------------------------------------
# verify_header_crc
# ----------------------------------------------------------------------

class TestVerifyHeaderCRC:
    def _build_valid_header(self) -> bytes:
        """构造一个 0x160 字节的合法 NDS header，含正确 CRC。"""
        header = bytearray(0x160)
        # 填充一些非零数据使 CRC 非平凡
        for i in range(0x15E):
            header[i] = (i * 7 + 3) & 0xFF
        # 计算并写入 Logo CRC (0x15C)
        logo_crc = crc16_logo(bytes(header[0x0C0:0x15C]))
        header[0x15C:0x15E] = struct.pack("<H", logo_crc)
        # 计算并写入 Header CRC (0x15E)
        # 注意：Header CRC 计算 0x000~0x15D，包含刚写入的 Logo CRC
        header_crc = crc16_header(bytes(header[0x000:0x15E]))
        header[0x15E:0x160] = struct.pack("<H", header_crc)
        return bytes(header)

    def test_valid_header_passes(self) -> None:
        header = self._build_valid_header()
        logo_ok, header_ok = verify_header_crc(header)
        assert logo_ok is True
        assert header_ok is True

    def test_corrupted_logo_crc_fails(self) -> None:
        header = bytearray(self._build_valid_header())
        # 破坏 Logo CRC
        header[0x15C] ^= 0xFF
        logo_ok, header_ok = verify_header_crc(bytes(header))
        assert logo_ok is False
        # Header CRC 也应失败（因为它包含 Logo CRC 字节）
        assert header_ok is False

    def test_corrupted_header_crc_fails(self) -> None:
        header = bytearray(self._build_valid_header())
        # 只破坏 Header CRC（不影响 Logo CRC）
        header[0x15E] ^= 0xFF
        logo_ok, header_ok = verify_header_crc(bytes(header))
        assert logo_ok is True
        assert header_ok is False

    def test_short_header_raises(self) -> None:
        with pytest.raises(ValueError, match="过短"):
            verify_header_crc(b"\x00" * 0x100)
