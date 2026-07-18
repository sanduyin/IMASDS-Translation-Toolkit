# tests/stages/test_stage5_build_rom.py
"""stage5_build_rom 模块核心函数测试。

只测试纯函数 crc16_nds（与 ndstool/crc.crc16_header 算法一致）。
"""
from __future__ import annotations

import pytest

from src.stage5_build_rom import crc16_nds
from src.ndstool.crc import crc16_header


class TestCRC16Nds:
    def test_empty_data(self) -> None:
        # 空数据应返回初始值 0xFFFF
        assert crc16_nds(b"") == 0xFFFF

    def test_matches_ndstool_crc16_header(self) -> None:
        # stage5 的 crc16_nds 应与 ndstool/crc.crc16_header 结果一致
        # （两者都是 CRC-16/ARC，多项式 0xA001，初始值 0xFFFF）
        test_cases = [
            b"",
            b"\x00",
            b"\x01",
            b"Hello",
            b"123456789",
            b"\x00" * 100,
            bytes(range(256)),
        ]
        for data in test_cases:
            assert crc16_nds(data) == crc16_header(data), f"Mismatch for {data!r}"

    def test_returns_16_bit_value(self) -> None:
        for byte_val in range(256):
            result = crc16_nds(bytes([byte_val]))
            assert 0 <= result <= 0xFFFF

    def test_known_vector_modbus(self) -> None:
        # CRC-16/MODBUS 标准测试向量
        assert crc16_nds(b"123456789") == 0x4B37

    def test_accepts_bytearray(self) -> None:
        # 应接受 bytearray 类型
        data = bytearray(b"Hello")
        assert crc16_nds(data) == crc16_nds(bytes(data))

    def test_deterministic(self) -> None:
        data = b"Hello, World!"
        assert crc16_nds(data) == crc16_nds(data)
