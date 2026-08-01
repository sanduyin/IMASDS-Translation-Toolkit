# src/ndstool/rom_builder.py
"""
NDS ROM 加载、修改与重建。

参考实现：dearlystars_tool (Rust) ndstool/rom_source.rs (read_from_rom)
        dearlystars_tool (Rust) ndstool/write_rom.rs (write_arm9 / write_arm9_overlay)

提供 RomImage 类，支持：
    - 从 .nds 文件加载完整 ROM（Header + FAT + FNT + ARM9 + ARM7 + Overlay + 文件系统）
    - 自动 BLZ 解压 ARM9 和所有 Overlay
    - 就地修改 ARM9/Overlay/普通文件
    - 重建 ROM（重新压缩 ARM9/Overlay，更新 Header CRC，可选 DSi 嫁接）

DSi 扩展（arm9i/arm7i/digest/HMAC/modcrypt）由 dsi_builder.py 负责，
本模块仅处理 NTR 区域。
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .header import NDSHeader, parse_header, build_header, HEADER_SIZE
from .fnt_fat import FAT, FNT, parse_fat, parse_fnt, build_fnt, FATEntry, FNTDirNode, FNTFileNode
from .overlay import (
    OverlayTableEntry,
    parse_overlay_table,
    build_overlay_table,
)
from ..utils.blz import blz_decompress, blz_compress

OverlayProcessor = Literal["arm9", "arm7"]

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

NITROCODE: int = 0xDEC00621
"""ARM9 footer 的 magic number（小端序：21 06 C0 DE）。"""

ARM9_FOOTER_SIZE: int = 0x0C
"""ARM9 footer 长度（12 字节）。"""

ARM9_MIN_UNCOMPRESSED: int = 0x4000
"""ARM9 BLZ 压缩时头部不参与压缩的字节数。"""

ARM9_RAM_BASE: int = 0x02004000
"""ARM9 标准加载基址（与 arm9SKILL.md 一致）。"""

ARM9_COMPRESSED_END_PTR_OFFSET: int = 0x0FC4
"""压缩后 ARM9.bin 内部 0x0FC4 偏移处写入"压缩数据结束内存地址"的字段位置。"""

OVERLAY_COMPRESSED_FLAG: int = 0x03
"""Overlay compressed_size_flag 高字节 = 3 表示 BLZ 压缩。"""

OVERLAY_UNCOMPRESSED_FLAG: int = 0x02
"""Overlay compressed_size_flag 高字节 = 2 表示普通未压缩文件。"""

FILE_ALIGNMENT: int = 0x200
"""NDS 文件对齐粒度（512 字节）。本模块当前未使用，保留供完整重建模式使用。"""


# ----------------------------------------------------------------------
# 数据类
# ----------------------------------------------------------------------

@dataclass
class OverlayState:
    """单个 Overlay 的运行时状态。"""

    entry: OverlayTableEntry
    """Overlay 表项（含 file_id、RAM 地址等元数据）。"""

    decompressed: bytes
    """解压后的 Overlay 二进制。"""

    file_id: int
    """FAT 中的 file_id。"""

    decompressed_original: bytes = b""
    """原版解压数据快照，用于 save 时判断是否真被修改（由 RomImage 加载时填充）。"""

    @property
    def is_compressed(self) -> bool:
        return self.entry.is_compressed

    @property
    def overlay_id(self) -> int:
        return self.entry.id


# ----------------------------------------------------------------------
# RomImage
# ----------------------------------------------------------------------

class RomImage:
    """
    NDS ROM 内存镜像。

    加载流程：
        1. 读取并解析 Header (0x200 字节)
        2. 读取 FAT，得到所有文件的 (top, bottom) 位置
        3. 读取 FNT，重建文件目录树
        4. 读取 ARM9（含 footer 检测）并 BLZ 解压
        5. 读取 ARM7
        6. 读取 ARM9 Overlay 表并逐个解压

    保存流程（就地重建）：
        1. 拷贝原始 ROM 二进制
        2. 对修改过的 ARM9：BLZ 重压缩 + 更新 0x0FC4 字段 + 写回原位置（或新位置）
        3. 对修改过的 Overlay：BLZ 重压缩 + 更新 FAT 位置 + 更新 overlay table
        4. 对修改过的普通文件：写回 FAT 指向的位置
        5. 重算 Header CRC16 并写回 0x15E
        6. （可选）DSi 嫁接：保留原版 TWL 扩展数据
    """

    def __init__(self, rom_data: bytes | bytearray):
        self._raw: bytearray = bytearray(rom_data)
        self.header: NDSHeader = parse_header(self._raw)
        self.fat: FAT = parse_fat(self._raw[self.header.fat_offset : self.header.fat_offset + self.header.fat_size])
        self.fnt: FNT = parse_fnt(self._raw[self.header.fnt_offset : self.header.fnt_offset + self.header.fnt_size], fat=self.fat)

        # ARM9
        self._arm9_compressed_original: bytes = bytes(
            self._raw[self.header.arm9_rom_offset : self.header.arm9_rom_offset + self.header.arm9_size]
        )
        self.arm9_has_footer: bool = self._detect_arm9_footer()
        self._arm9_decompressed: bytes = self._load_arm9_decompressed()
        # 保存"原版解压数据"的快照，用于 save 时判断 ARM9 是否真的被修改
        # 若未修改则直接复用原版压缩流，避免无谓重压缩以及哈希加速导致的压缩率下降
        self._arm9_decompressed_original: bytes = self._arm9_decompressed
        self._arm9_dirty: bool = False

        # ARM7
        self._arm7_original: bytes = bytes(
            self._raw[self.header.arm7_rom_offset : self.header.arm7_rom_offset + self.header.arm7_size]
        )
        self._arm7_dirty: bool = False

        # ARM9 Overlay 表
        self.arm9_overlays: list[OverlayState] = []
        if self.header.arm9_overlay_size > 0:
            ovl_table = parse_overlay_table(
                self._raw[self.header.arm9_overlay_offset : self.header.arm9_overlay_offset + self.header.arm9_overlay_size]
            )
            for entry in ovl_table:
                ov_state = self._load_overlay(entry)
                self.arm9_overlays.append(ov_state)
        # ARM7 Overlay 表（本项目为空，仅保留接口）
        self.arm7_overlays: list[OverlayState] = []
        if self.header.arm7_overlay_size > 0:
            ovl7_table = parse_overlay_table(
                self._raw[self.header.arm7_overlay_offset : self.header.arm7_overlay_offset + self.header.arm7_overlay_size]
            )
            for entry in ovl7_table:
                self.arm7_overlays.append(self._load_overlay(entry))
        self._overlays_dirty: dict[tuple[OverlayProcessor, int], bool] = {
            ("arm9", ov.overlay_id): False for ov in self.arm9_overlays
        }
        self._overlays_dirty.update({
            ("arm7", ov.overlay_id): False for ov in self.arm7_overlays
        })

        # 普通文件修改缓冲：file_id -> new_data
        self._file_overrides: dict[int, bytes] = {}

    # ------------------------------------------------------------------
    # 加载辅助
    # ------------------------------------------------------------------

    def _detect_arm9_footer(self) -> bool:
        """检测 ARM9 binary 末尾是否有 12 字节 footer (nitrocode 0xDEC00621)。"""
        footer_pos = self.header.arm9_rom_offset + self.header.arm9_size
        if footer_pos + ARM9_FOOTER_SIZE > len(self._raw):
            return False
        nitrocode = struct.unpack_from("<I", self._raw, footer_pos)[0]
        return bool(nitrocode == NITROCODE)

    def _load_arm9_decompressed(self) -> bytes:
        """加载并 BLZ 解压 ARM9（含 footer）。"""
        decompressed = blz_decompress(self._arm9_compressed_original)
        if self.arm9_has_footer:
            footer_pos = self.header.arm9_rom_offset + self.header.arm9_size
            footer_data = bytes(self._raw[footer_pos : footer_pos + ARM9_FOOTER_SIZE])
            decompressed = decompressed + footer_data
        return decompressed

    def _load_overlay(self, entry: OverlayTableEntry) -> OverlayState:
        """加载并解压单个 Overlay。"""
        if entry.file_id >= len(self.fat):
            raise ValueError(
                f"Overlay {entry.id} 的 file_id={entry.file_id} 超出 FAT 范围"
            )
        loc = self.fat.entries[entry.file_id]
        raw = bytes(self._raw[loc.top : loc.bottom])
        if entry.is_compressed:
            decompressed = blz_decompress(raw)
        else:
            decompressed = raw
        return OverlayState(
            entry=entry,
            decompressed=decompressed,
            file_id=entry.file_id,
            decompressed_original=decompressed,
        )

    # ------------------------------------------------------------------
    # 访问 API
    # ------------------------------------------------------------------

    @property
    def arm9(self) -> bytes:
        """解压后的 ARM9 二进制（含 footer，如果原版有）。"""
        return self._arm9_decompressed

    @arm9.setter
    def arm9(self, data: bytes | bytearray) -> None:
        """更新 ARM9（解压后形式）。保存时会自动 BLZ 压缩。"""
        self._arm9_decompressed = bytes(data)
        self._arm9_dirty = True

    @property
    def arm9_footer(self) -> bytes:
        """获取 ARM9 footer（12B），若无则返回空。"""
        if not self.arm9_has_footer:
            return b""
        return self._arm9_decompressed[-ARM9_FOOTER_SIZE:]

    @property
    def arm9_payload(self) -> bytes:
        """获取 ARM9 主体（不含 footer）。"""
        if self.arm9_has_footer:
            return self._arm9_decompressed[:-ARM9_FOOTER_SIZE]
        return self._arm9_decompressed

    def set_arm9_payload(self, data: bytes | bytearray, keep_footer: bool = True) -> None:
        """更新 ARM9 主体（不含 footer）。保留原 footer（如果存在且 keep_footer=True）。"""
        if keep_footer and self.arm9_has_footer:
            self._arm9_decompressed = bytes(data) + self._arm9_decompressed[-ARM9_FOOTER_SIZE:]
        else:
            self._arm9_decompressed = bytes(data)
            self.arm9_has_footer = False
        self._arm9_dirty = True

    @property
    def arm7(self) -> bytes:
        return self._arm7_original

    @arm7.setter
    def arm7(self, data: bytes | bytearray) -> None:
        self._arm7_original = bytes(data)
        self._arm7_dirty = True

    def _resolve_overlay(
        self,
        overlay_id: int,
        processor: OverlayProcessor | None,
    ) -> tuple[OverlayProcessor, OverlayState]:
        pools: list[tuple[OverlayProcessor, list[OverlayState]]]
        pools = [("arm9", self.arm9_overlays), ("arm7", self.arm7_overlays)]
        matches = [
            (kind, overlay)
            for kind, overlays in pools
            if processor is None or kind == processor
            for overlay in overlays
            if overlay.overlay_id == overlay_id
        ]
        if not matches:
            scope = f" ({processor})" if processor is not None else ""
            raise KeyError(f"Overlay {overlay_id}{scope} 不存在")
        if len(matches) > 1:
            raise ValueError(
                f"Overlay ID {overlay_id} 同时存在于 ARM9/ARM7；"
                "请显式传 processor='arm9' 或 'arm7'"
            )
        return matches[0]

    def get_overlay(
        self,
        overlay_id: int,
        processor: OverlayProcessor | None = None,
    ) -> bytes:
        """获取解压后的 Overlay；ID 冲突时必须指定处理器。"""
        _processor, overlay = self._resolve_overlay(overlay_id, processor)
        return overlay.decompressed

    def set_overlay(
        self,
        overlay_id: int,
        data: bytes | bytearray,
        processor: OverlayProcessor | None = None,
    ) -> None:
        """更新解压后的 Overlay 数据。保存时会自动 BLZ 压缩。"""
        resolved_processor, overlay = self._resolve_overlay(overlay_id, processor)
        overlay.decompressed = bytes(data)
        self._overlays_dirty[(resolved_processor, overlay_id)] = True

    def get_file_by_id(self, file_id: int) -> bytes:
        """按 file_id 读取文件原始字节（如果已被 override，返回新数据）。"""
        if file_id in self._file_overrides:
            return self._file_overrides[file_id]
        if file_id >= len(self.fat):
            raise IndexError(f"file_id {file_id} 超出 FAT 范围")
        loc = self.fat.entries[file_id]
        return bytes(self._raw[loc.top : loc.bottom])

    def set_file_by_id(self, file_id: int, data: bytes | bytearray) -> None:
        """按 file_id 设置文件新内容。"""
        if file_id >= len(self.fat):
            raise IndexError(f"file_id {file_id} 超出 FAT 范围")
        self._file_overrides[file_id] = bytes(data)

    def get_file_by_name(self, name: str) -> bytes:
        """按 FNT 路径读取文件（如 'F_SCN.BIN' 或 'SOUND/BGM000.bin'）。"""
        node = self.fnt.find_node(name)
        if node is None or not isinstance(node, FNTFileNode):
            raise KeyError(f"FNT 中找不到文件：{name!r}")
        return self.get_file_by_id(node.file_id)

    def set_file_by_name(self, name: str, data: bytes | bytearray) -> None:
        """按 FNT 路径设置文件内容。"""
        node = self.fnt.find_node(name)
        if node is None or not isinstance(node, FNTFileNode):
            raise KeyError(f"FNT 中找不到文件：{name!r}")
        self.set_file_by_id(node.file_id, data)

    def list_files(self) -> list[tuple[str, int]]:
        """返回 [(path, file_id), ...]。"""
        return self.fnt.list_all_files()

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------

    def save(self, output_path: str | Path, mode: str = "inplace") -> bytes:
        """
        重建并保存 ROM。

        Args:
            output_path: 输出 .nds 文件路径
            mode: 重建模式
                - "inplace": 就地修改模式。拷贝原 ROM，替换被修改的 ARM9/Overlay/文件，
                             保留原版整体布局，重算 Header CRC。**推荐用于本项目汉化**。
                             保留 DSi TWL 扩展数据。

        Returns:
            重建后的 ROM 字节数据。
        """
        if mode != "inplace":
            raise ValueError(f"不支持的重建模式：{mode!r}")

        # 5.4 DSi ROM 警告
        if self.header.is_dsi and (self._arm9_dirty or any(self._overlays_dirty.values()) or self._file_overrides):
            import warnings
            warnings.warn(
                "DSi ROM 的 ARM9/Overlay/文件被修改后，DSi 头中的 hmac_*/digest/modcrypt "
                "字段会失效。如需在 DSi 模式运行，请调用 dsi_builder.write_digests/"
                "write_hashes/modcrypt 重新计算。",
                stacklevel=2,
            )

        # 拷贝原 ROM
        out = bytearray(self._raw)

        # ---------- 1. ARM9 ----------
        if self._arm9_dirty:
            out = self._rewrite_arm9(out)

        # ---------- 2. Overlays ----------
        for (processor, ov_id), dirty in self._overlays_dirty.items():
            if dirty:
                out = self._rewrite_overlay(out, processor, ov_id)

        # ---------- 3. 普通文件 ----------
        for file_id, new_data in self._file_overrides.items():
            out = self._rewrite_file(out, file_id, new_data)

        # ---------- 4. 写回 FAT (5.1 修复：overlay/file 缩小后需同步 FAT bottom) ----------
        fat_offset = self.header.fat_offset
        fat_size = self.header.fat_size
        out[fat_offset : fat_offset + fat_size] = self.fat.build()

        # ---------- 5. 更新 Header CRC ----------
        # NTR 区域结束位置
        ntr_size = self.header.application_end_offset
        if len(out) < ntr_size:
            # 5.9 修复：app_end 之前用 0x00 补齐（与 Rust set_file_size 一致）
            out.extend(b"\x00" * (ntr_size - len(out)))

        # 5.2 修复：ARM9 被重写后重算 secure_area_crc (0x06C)
        # secure area 位于 ARM9 前 0x4000 字节（arm9_rom_offset 通常为 0x4000）
        if self._arm9_dirty and self.header.arm9_rom_offset == 0x4000 and len(out) >= 0x8000:
            from .crc import crc16_header
            self.header.secure_area_crc = crc16_header(bytes(out[0x4000:0x8000]))

        # 重算 Header CRC（直接用 build(update_crc=True)）
        out[0x000:HEADER_SIZE] = self.header.build(update_crc=True)

        # 写入文件
        Path(output_path).write_bytes(out)

        # 5.11 修复：同步实例状态，避免二次 save 基于旧 _raw 重复处理
        self._raw = out
        if self._arm9_dirty:
            self._arm9_compressed_original = bytes(
                out[self.header.arm9_rom_offset : self.header.arm9_rom_offset + self.header.arm9_size]
            )
            self._arm9_decompressed_original = self._arm9_decompressed
            self._arm9_dirty = False
        for ov in self.arm9_overlays:
            arm9_key: tuple[OverlayProcessor, int] = ("arm9", ov.overlay_id)
            if self._overlays_dirty.get(arm9_key, False):
                ov.decompressed_original = ov.decompressed
                self._overlays_dirty[arm9_key] = False
        for ov in self.arm7_overlays:
            arm7_key: tuple[OverlayProcessor, int] = ("arm7", ov.overlay_id)
            if self._overlays_dirty.get(arm7_key, False):
                ov.decompressed_original = ov.decompressed
                self._overlays_dirty[arm7_key] = False
        self._file_overrides.clear()

        return bytes(out)

    # ------------------------------------------------------------------
    # 就地重写内部实现
    # ------------------------------------------------------------------

    def _rewrite_arm9(self, out: bytearray) -> bytearray:
        """
        就地重写 ARM9：BLZ 压缩 → 更新 0x0FC4 → 写回原位置 → 更新 Header。

        优化策略：
            1. 若解压后数据与原版完全相同（例如仅触发 dirty 但未实际修改），
               直接复用原版压缩流，避免无谓重压缩以及哈希加速导致的压缩率下降。
            2. 若数据真被修改，使用 max_search_positions=0（无限制）进行最优压缩，
               与 Rust 实现行为一致，确保压缩率 ≥ faraplay。

        如果压缩后大小超过原位置空间，则会破坏 ROM 布局，抛出异常。
        本项目汉化通常不修改 ARM9 大小，仅原地替换文本，所以这是安全假设。
        """
        arm9_data = self._arm9_decompressed

        # 优化 1：数据未实际修改 → 复用原版压缩流（已在 out 中），不做任何操作
        if arm9_data == self._arm9_decompressed_original:
            return out

        footer = b""
        if self.arm9_has_footer:
            # 拆出 footer（最后 12 字节）
            if len(arm9_data) < ARM9_FOOTER_SIZE:
                raise ValueError("ARM9 数据比 footer 还短，状态损坏")
            footer = arm9_data[-ARM9_FOOTER_SIZE:]
            arm9_payload = arm9_data[:-ARM9_FOOTER_SIZE]
        else:
            arm9_payload = arm9_data

        # BLZ 压缩（头部 0x4000 字节不压缩）
        # 使用 max_search_positions=0（无限制）以获得最优压缩率，与 Rust 实现一致
        compressed = blz_compress(
            arm9_payload,
            min_uncompressed_region_size=ARM9_MIN_UNCOMPRESSED,
            max_search_positions=0,
        )
        if compressed is None:
            raise RuntimeError(
                "ARM9 BLZ 压缩失败：压缩后体积 >= 原始体积，无法生成有效压缩流"
            )

        # 在 0x0FC4 偏移写入"压缩数据结束内存地址"（ARM9_RAM_BASE + compressed_size）
        compressed_size = len(compressed)
        compressed_ba = bytearray(compressed)
        end_addr = ARM9_RAM_BASE + compressed_size
        struct.pack_into("<I", compressed_ba, ARM9_COMPRESSED_END_PTR_OFFSET, end_addr & 0xFFFFFFFF)
        compressed = bytes(compressed_ba)

        # 计算总占用空间（压缩数据 + footer）
        new_arm9_total = compressed_size + len(footer)
        # 4 字节对齐
        aligned_total = (new_arm9_total + 3) & ~3

        old_arm9_size = self.header.arm9_size
        old_footer_size = ARM9_FOOTER_SIZE if self.arm9_has_footer else 0
        old_total = old_arm9_size + old_footer_size

        if aligned_total > old_total:
            raise RuntimeError(
                f"新 ARM9 ({aligned_total} 字节) 大于原版 ({old_total} 字节)，"
                f"就地重写会破坏 ROM 布局。请使用完整重建模式（暂未实现）。"
            )

        # 写回原位置
        arm9_pos = self.header.arm9_rom_offset
        out[arm9_pos : arm9_pos + compressed_size] = compressed
        if footer:
            out[arm9_pos + compressed_size : arm9_pos + compressed_size + len(footer)] = footer
        # 5.10 性能修复：用切片赋值代替逐字节循环
        pad = old_total - new_arm9_total
        if pad > 0:
            out[arm9_pos + new_arm9_total : arm9_pos + old_total] = b"\xFF" * pad

        # 更新 Header.arm9_size（仅压缩部分，对齐到 4 字节）
        self.header.arm9_size = (compressed_size + 3) & ~3
        return out

    def _rewrite_overlay(
        self,
        out: bytearray,
        processor: OverlayProcessor,
        ov_id: int,
    ) -> bytearray:
        """就地重写 Overlay：BLZ 压缩 → 写回 FAT 指向的位置 → 更新 overlay table + FAT。

        优化：若解压后数据与原版相同，直接复用原版数据，不做任何操作。
        """
        _processor, ov_state = self._resolve_overlay(ov_id, processor)
        is_arm7 = processor == "arm7"

        # 优化：数据未实际修改 → 复用原版数据（已在 out 中），不做任何操作
        if ov_state.decompressed == ov_state.decompressed_original:
            return out

        # BLZ 压缩 Overlay（min_uncompressed=0），使用最优压缩
        compressed = blz_compress(
            ov_state.decompressed,
            min_uncompressed_region_size=0,
            max_search_positions=0,
        )

        loc = self.fat.entries[ov_state.file_id]
        old_size = loc.size
        # 5.3 修复：越界写入校验
        if loc.top + old_size > len(out):
            raise RuntimeError(
                f"Overlay {ov_id} FAT 位置越界：top=0x{loc.top:X}, size=0x{old_size:X}, "
                f"out_len=0x{len(out):X}"
            )

        if compressed is not None and len(compressed) < old_size:
            # 使用压缩数据
            new_data = compressed
            new_flag = (OVERLAY_COMPRESSED_FLAG << 24) | (len(compressed) & 0x00FFFFFF)
            ov_state.entry.compressed_size_flag = new_flag
        else:
            # 压缩无收益，使用未压缩数据
            new_data = ov_state.decompressed
            new_flag = (OVERLAY_UNCOMPRESSED_FLAG << 24) | (len(new_data) & 0x00FFFFFF)
            ov_state.entry.compressed_size_flag = new_flag

        new_size = len(new_data)
        if new_size > old_size:
            raise RuntimeError(
                f"新 Overlay {ov_id} ({new_size} 字节) 大于原版 ({old_size} 字节)，"
                f"就地重写会破坏 ROM 布局。"
            )

        # 写回原位置
        out[loc.top : loc.top + new_size] = new_data
        # 5.10 性能修复：用切片赋值代替逐字节循环
        pad = old_size - new_size
        if pad > 0:
            out[loc.top + new_size : loc.top + old_size] = b"\xFF" * pad

        # 5.1 修复：缩小后必须更新 FAT.bottom，否则后续按 FAT 读取会把 0xFF 填充
        # 误当 BLZ footer/header，导致 BLZ 解压失败。
        # 只缩不涨（new_size <= old_size 已校验），安全。
        loc.bottom = loc.top + new_size

        # 更新 overlay table（仅 ARM9；ARM7 overlay 暂不支持修改）
        if not is_arm7:
            self._write_overlay_table(out)
        else:
            # 5.5 修复：ARM7 overlay 修改时写回 arm7_overlay_offset
            if self.header.arm7_overlay_size > 0:
                ovl7_off = self.header.arm7_overlay_offset
                ovl7_data = build_overlay_table([ov.entry for ov in self.arm7_overlays])
                out[ovl7_off : ovl7_off + len(ovl7_data)] = ovl7_data

        return out

    def _rewrite_file(self, out: bytearray, file_id: int, new_data: bytes) -> bytearray:
        """就地重写普通文件。"""
        loc = self.fat.entries[file_id]
        old_size = loc.size
        new_size = len(new_data)
        if new_size > old_size:
            raise RuntimeError(
                f"文件 file_id={file_id} 新大小 ({new_size}) 超过原大小 ({old_size})，"
                f"就地重写会破坏 ROM 布局。"
            )
        # 5.3 修复：越界写入校验
        if loc.top + old_size > len(out):
            raise RuntimeError(
                f"文件 file_id={file_id} FAT 位置越界：top=0x{loc.top:X}, size=0x{old_size:X}, "
                f"out_len=0x{len(out):X}"
            )
        out[loc.top : loc.top + new_size] = new_data
        # 5.10 性能修复：用切片赋值代替逐字节循环
        pad = old_size - new_size
        if pad > 0:
            out[loc.top + new_size : loc.top + old_size] = b"\xFF" * pad
        # 5.1 修复：同步 FAT.bottom
        loc.bottom = loc.top + new_size
        return out

    def _write_overlay_table(self, out: bytearray) -> None:
        """把当前的 ARM9 overlay 表写回 ROM。"""
        if not self.arm9_overlays:
            return
        ovl_offset = self.header.arm9_overlay_offset
        ovl_data = build_overlay_table([ov.entry for ov in self.arm9_overlays])
        out[ovl_offset : ovl_offset + len(ovl_data)] = ovl_data


# ----------------------------------------------------------------------
# 便捷函数
# ----------------------------------------------------------------------

def load_rom(path: str | Path) -> RomImage:
    """从文件加载 RomImage。"""
    return RomImage(Path(path).read_bytes())


def save_rom(image: RomImage, path: str | Path, mode: str = "inplace") -> bytes:
    """便捷函数：保存 RomImage 到文件。"""
    return image.save(path, mode=mode)
