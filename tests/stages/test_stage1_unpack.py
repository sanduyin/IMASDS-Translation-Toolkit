# tests/stages/test_stage1_unpack.py
"""stage1_unpack 模块核心函数测试。

只测试纯函数 decompress_ring_lz（不依赖 ROM 文件的解压逻辑）。
"""
from __future__ import annotations

import struct

import pytest

from src.stage1_unpack import decompress_ring_lz


class TestDecompressRingLZ:
    def test_empty_input(self) -> None:
        # 空输入应返回空 bytearray
        result = decompress_ring_lz(b"", 0)
        assert result == bytearray()

    def test_single_literal_byte(self) -> None:
        # 构造：flag=0x80 (bit7=1 表示... 等等，flag bit=1 是回指，bit=0 是原文)
        # 实际：flag=0x00 (bit7=0) → 原文字节
        # 数据：flag(0x00) + byte(0xAB)
        data = bytes([0x00, 0xAB])
        result = decompress_ring_lz(data, 1)
        assert result == bytearray([0xAB])

    def test_multiple_literal_bytes(self) -> None:
        # flag=0x00 → 8 个原文字节
        data = bytes([0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])
        result = decompress_ring_lz(data, 8)
        assert result == bytearray([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])

    def test_decompress_stops_at_target_size(self) -> None:
        # 即使 flag 还有位，达到 decompressed_size 也应停止
        # flag=0x00, 4 个原文字节，但 target=3
        data = bytes([0x00, 0x01, 0x02, 0x03, 0x04])
        result = decompress_ring_lz(data, 3)
        assert len(result) == 3
        assert result == bytearray([0x01, 0x02, 0x03])

    def test_returns_bytearray_type(self) -> None:
        result = decompress_ring_lz(b"\x00\x41", 1)
        assert isinstance(result, bytearray)

    def test_with_backreference(self) -> None:
        # 构造：flag=0b10000000 (bit7=1 第一个 token 是回指)
        # 但回指需要已解压数据，故先有原文再有回指
        # flag=0b00000001 (bit0=1，第 8 个 token 是回指) → 前 7 个原文 + 1 回指
        # 但这样太复杂。简化：先放 1 个原文，再用回指
        # flag=0b01000000=0x40 (bit6=1，第 2 个 token 是回指)
        # token1: literal 0x41 ('A')
        # token2: backref, b1=0x10, b2=0x00 → length=(0x10>>4)+3=4, disp=(0x00<<8|0x00)+1=1...
        # 等等，看实现：val=(b1<<8)|b2, length=((val>>12)&0xF)+3, read_pos=(r_cursor-(val&0xFFF)-1)%0x1000
        # 想要 length=3, disp=1 (回指到上一字节) → val = (3-3)<<12 | (1-1) = 0x0000
        # b1=0x00, b2=0x00
        data = bytes([0x40, 0x41, 0x00, 0x00])  # flag=0x40, literal 'A', backref(0x0000)
        # flag bit6=1 时第 2 个 token 是回指
        # 但 flag 处理是 MSB 优先：bit7 先，bit6 次
        # 所以 flag=0x40: bit7=0 (literal), bit6=1 (backref)
        # 第 1 token: literal 0x41 → out=[0x41]
        # 第 2 token: backref val=0x0000, length=3, read_pos=(1-0-1)%0x1000=0
        #   → 复制 3 次 ring_buffer[0]（此时 ring_buffer[0]=0x41, 但 r_cursor 已变为 1）
        #   实际：r_cursor=1, read_pos=(1-0-1)%0x1000=0, ring_buffer[0]=0x41
        #   → out=[0x41, 0x41, 0x41, 0x41]
        result = decompress_ring_lz(data, 4)
        assert result == bytearray([0x41, 0x41, 0x41, 0x41])
