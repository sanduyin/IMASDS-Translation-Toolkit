# src/ndstool/overlay.py
"""
ARM9 Overlay Table 解析与重建。

参考实现：dearlystars_tool (Rust) ndstool/overlay.rs

每条 Overlay 表项 32 字节 (0x20)，结构如下：
    0x00  id                u32  Overlay ID
    0x04  ram_address       u32  RAM 加载地址
    0x08  ram_size          u32  RAM 占用大小
    0x0C  bss_size          u32  BSS 段大小
    0x10  sinit_init        u32  Static Initializer Start
    0x14  sinit_init_end    u32  Static Initializer End
    0x18  file_id           u32  FAT 文件 ID
    0x1C  compressed_size_flag  u32  压缩标志与大小
                                  - 高字节 == 0x03 表示 BLZ 压缩
                                  - 高字节 == 0x02 表示普通未压缩文件
                                  - 低 24 位 = 压缩后大小（faraplay/ndstool 约定；
                                    NDS 官方规范未明确定义此字段）
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable, Sequence

ENTRY_SIZE = 0x20
"""单条 Overlay 表项字节数。"""

COMPRESSION_FLAG_BYTE = 0x03
"""compressed_size_flag 的高字节为 3 时表示 BLZ 压缩。"""

UNCOMPRESSED_FLAG_BYTE = 0x02
"""compressed_size_flag 的高字节为 2 时表示未压缩。"""


@dataclass
class OverlayTableEntry:
    """单条 ARM9 Overlay 表项。"""

    id: int = 0
    ram_address: int = 0
    ram_size: int = 0
    bss_size: int = 0
    sinit_init: int = 0
    sinit_init_end: int = 0
    file_id: int = 0
    compressed_size_flag: int = 0

    @classmethod
    def parse(cls, data: bytes | bytearray, offset: int = 0) -> "OverlayTableEntry":
        """从字节偏移解析一条 Overlay 表项。"""
        if len(data) < offset + ENTRY_SIZE:
            raise ValueError(
                f"Overlay 表项数据过短：期望至少 {offset + ENTRY_SIZE} 字节，实际 {len(data)}"
            )
        (
            id_, ram_address, ram_size, bss_size,
            sinit_init, sinit_init_end, file_id, compressed_size_flag
        ) = struct.unpack_from("<8I", data, offset)
        return cls(
            id=id_,
            ram_address=ram_address,
            ram_size=ram_size,
            bss_size=bss_size,
            sinit_init=sinit_init,
            sinit_init_end=sinit_init_end,
            file_id=file_id,
            compressed_size_flag=compressed_size_flag,
        )

    def build(self) -> bytes:
        """序列化为 32 字节 bytes。"""
        return struct.pack(
            "<8I",
            self.id & 0xFFFFFFFF,
            self.ram_address & 0xFFFFFFFF,
            self.ram_size & 0xFFFFFFFF,
            self.bss_size & 0xFFFFFFFF,
            self.sinit_init & 0xFFFFFFFF,
            self.sinit_init_end & 0xFFFFFFFF,
            self.file_id & 0xFFFFFFFF,
            self.compressed_size_flag & 0xFFFFFFFF,
        )

    # ------------------------------------------------------------------
    # 便捷属性
    # ------------------------------------------------------------------

    @property
    def is_compressed(self) -> bool:
        """是否为 BLZ 压缩（compressed_size_flag 高字节 == 3）。"""
        return (self.compressed_size_flag >> 24) == COMPRESSION_FLAG_BYTE

    @property
    def compressed_size(self) -> int:
        """压缩后大小（低 24 位）。

        4.1 修复：原 decompressed_size 命名错误。faraplay/ndstool 在 write_rom.rs
        中写入的是 `0x03000000 | compressed_size`（压缩后大小），而非解压后大小。
        gbatek 同样规定此字段为 ROM 中存储的字节数（压缩后）。
        """
        return self.compressed_size_flag & 0x00FFFFFF

    @property
    def overlay_filename(self) -> str:
        """标准文件名：overlay_XXXX.bin。"""
        return f"overlay_{self.id:04d}.bin"

    def mark_compressed(self, compressed_size: int | None = None) -> None:
        """将该表项标记为 BLZ 压缩。可同时更新低 24 位的压缩后大小字段。"""
        size_field = compressed_size if compressed_size is not None else (self.compressed_size_flag & 0x00FFFFFF)
        self.compressed_size_flag = (COMPRESSION_FLAG_BYTE << 24) | (size_field & 0x00FFFFFF)

    def mark_uncompressed(self, size: int | None = None) -> None:
        """将该表项标记为未压缩。可同时更新低 24 位的大小字段。

        4.3 修复：与 Rust（0x02000000）和 rom_builder（OVERLAY_UNCOMPRESSED_FLAG=0x02）一致，
        高字节写 0x02。
        """
        size_field = size if size is not None else (self.compressed_size_flag & 0x00FFFFFF)
        self.compressed_size_flag = (UNCOMPRESSED_FLAG_BYTE << 24) | (size_field & 0x00FFFFFF)


# ----------------------------------------------------------------------
# 表级别 API
# ----------------------------------------------------------------------

def parse_overlay_table(data: bytes | bytearray) -> list[OverlayTableEntry]:
    """解析整个 Overlay 表。data 长度必须是 32 的倍数。"""
    if len(data) % ENTRY_SIZE != 0:
        raise ValueError(
            f"Overlay 表长度不是 {ENTRY_SIZE} 的整数倍：{len(data)}"
        )
    count = len(data) // ENTRY_SIZE
    return [OverlayTableEntry.parse(data, i * ENTRY_SIZE) for i in range(count)]


def build_overlay_table(entries: Sequence[OverlayTableEntry]) -> bytes:
    """将多条 Overlay 表项序列化为 bytes。"""
    return b"".join(e.build() for e in entries)


def iter_overlay_entries(entries: Iterable[OverlayTableEntry]) -> Iterable[OverlayTableEntry]:
    """便捷迭代器（与 Rust IntoIterator 对应）。"""
    yield from entries
