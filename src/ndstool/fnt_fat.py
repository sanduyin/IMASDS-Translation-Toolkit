# src/ndstool/fnt_fat.py
"""
NDS ROM FNT (文件名表) 与 FAT (文件分配表) 的解析与重建。

参考实现：reference/dearlystars_tool/ndstool/src/rom_source.rs (read_fat)
        reference/dearlystars_tool/ndstool/src/rom_source/rom_tree_node.rs (read_fnt_dir)

## FNT 结构

FNT 由两部分连续存储：**目录表 (Directory Table)** + **子表 (Subtable)**。

### 目录表
从 `fnt_offset` 开始，每个目录占 8 字节，按 dir_id 索引：

    0x00  subtable_offset  u32  当前目录子表相对 FNT 起点的偏移
    0x04  first_file_id    u16  当前目录第一个文件的 FAT file_id
    0x06  parent_id        u16  父目录 ID（根目录为 0xF000）

目录 ID 从 0xF000 开始：root=0xF000, 第一个子目录=0xF001, ...

### 子表
从 `fnt_offset + subtable_offset` 开始，每条记录：

    0x00  length   u8   名称长度（低 7 位）；bit 7 = 1 表示子目录，0 表示文件
    0x01  name     u8[length & 0x7F]  名称（UTF-8）
    if (length & 0x80):
        0x01+L  dir_id  u16  子目录 ID

子表以 length=0 终止。

### 目录 ID → 目录表偏移
`dir_offset_in_fnt = 8 * (dir_id & 0x0FFF)`

## FAT 结构

从 `fat_offset` 开始，每条记录 8 字节：

    0x00  top     u32  文件起始偏移（0 表示空槽）
    0x04  bottom  u32  文件结束偏移
    size = bottom - top
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator, Optional

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

FAT_ENTRY_SIZE = 0x08
FNT_DIR_ENTRY_SIZE = 0x08
ROOT_DIR_ID = 0xF000
"""根目录 ID。"""
DIR_ID_MASK = 0x0FFF
"""dir_id 与目录表索引之间的掩码（剥离 0xF000 高 4 位）。"""
SUBTABLE_FLAG = 0x80
"""子表项 length 字节的 bit 7：1=子目录，0=文件。"""
NAME_LENGTH_MASK = 0x7F
"""子表项 length 字节的低 7 位：名称长度。"""


# ----------------------------------------------------------------------
# FAT
# ----------------------------------------------------------------------

@dataclass
class FATEntry:
    """FAT 单条记录。"""

    top: int = 0
    """文件起始偏移（0 表示空槽）。"""

    bottom: int = 0
    """文件结束偏移。"""

    @property
    def size(self) -> int:
        """文件大小 = bottom - top。"""
        return self.bottom - self.top

    @property
    def is_empty(self) -> bool:
        """是否为空槽。"""
        return self.top == 0 and self.bottom == 0

    @classmethod
    def parse(cls, data: bytes | bytearray, offset: int = 0) -> "FATEntry":
        if len(data) < offset + FAT_ENTRY_SIZE:
            raise ValueError(
                f"FAT 表项数据过短：期望 {offset + FAT_ENTRY_SIZE}，实际 {len(data)}"
            )
        top, bottom = struct.unpack_from("<II", data, offset)
        return cls(top=top, bottom=bottom)

    def build(self) -> bytes:
        return struct.pack("<II", self.top & 0xFFFFFFFF, self.bottom & 0xFFFFFFFF)


@dataclass
class FAT:
    """完整 FAT 表。"""

    entries: list[FATEntry] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, idx: int) -> FATEntry:
        return self.entries[idx]

    def __iter__(self) -> Iterator[FATEntry]:
        return iter(self.entries)

    @classmethod
    def parse(cls, data: bytes | bytearray) -> "FAT":
        """解析整个 FAT 表。data 长度必须是 8 的倍数。"""
        if len(data) % FAT_ENTRY_SIZE != 0:
            raise ValueError(
                f"FAT 表长度不是 {FAT_ENTRY_SIZE} 的整数倍：{len(data)}"
            )
        count = len(data) // FAT_ENTRY_SIZE
        return cls(entries=[FATEntry.parse(data, i * FAT_ENTRY_SIZE) for i in range(count)])

    def build(self) -> bytes:
        return b"".join(e.build() for e in self.entries)

    def get_file_location(self, file_id: int) -> FATEntry:
        """按 file_id 取文件位置。"""
        if file_id < 0 or file_id >= len(self.entries):
            raise IndexError(f"file_id {file_id} 超出 FAT 范围 (0..{len(self.entries)})")
        return self.entries[file_id]


# ----------------------------------------------------------------------
# FNT
# ----------------------------------------------------------------------

@dataclass
class FNTFileNode:
    """FNT 中的文件节点（叶子）。"""

    name: str
    file_id: int


@dataclass
class FNTDirNode:
    """FNT 中的目录节点（可包含文件与子目录）。"""

    name: str
    dir_id: int
    parent_id: int = 0
    first_file_id: int = 0
    files: list[FNTFileNode] = field(default_factory=list)
    subdirs: list["FNTDirNode"] = field(default_factory=list)

    def walk(self, prefix: str = "") -> Iterator[tuple[str, FNTFileNode]]:
        """深度优先遍历，yield (path, file_node)。"""
        for f in self.files:
            yield (prefix + f.name, f)
        for d in self.subdirs:
            yield from d.walk(prefix + d.name + "/")


@dataclass
class FNT:
    """完整 FNT 表。"""

    root: FNTDirNode
    """根目录（dir_id=0xF000）。"""

    @classmethod
    def parse(cls, data: bytes | bytearray, fnt_offset: int = 0, fat: Optional[FAT] = None) -> "FNT":
        """
        解析 FNT 表。

        Args:
            data: FNT 段字节数据（从 fnt_offset 处开始读的内容）
            fnt_offset: 仅用于错误提示，不影响解析逻辑
            fat: 可选 FAT，目前不参与 FNT 解析（仅 file_id 用于关联）
        """
        # 根目录总是 0xF000
        root = _parse_fnt_dir(data, dir_id=ROOT_DIR_ID, name="")
        return cls(root=root)

    def walk(self) -> Iterator[tuple[str, FNTFileNode]]:
        """深度优先遍历整个 FNT，yield (path, file_node)。"""
        yield from self.root.walk()

    def list_all_files(self) -> list[tuple[str, int]]:
        """返回所有文件：[(path, file_id), ...]。"""
        return [(path, f.file_id) for path, f in self.walk()]

    def build(self) -> bytes:
        """重建 FNT 二进制数据。"""
        return _build_fnt(self.root)

    def find_node(self, path: str) -> Optional[FNTDirNode | FNTFileNode]:
        """按 path（如 "data/scn/0001.bin"）查找节点。"""
        if not path or path == "/":
            return self.root
        parts = [p for p in path.split("/") if p]
        return _find_node(self.root, parts)


# ----------------------------------------------------------------------
# FNT 解析内部实现
# ----------------------------------------------------------------------

def _parse_fnt_dir(data: bytes | bytearray, dir_id: int, name: str) -> FNTDirNode:
    """递归解析一个 FNT 目录。"""
    idx = dir_id & DIR_ID_MASK
    entry_offset = idx * FNT_DIR_ENTRY_SIZE
    if entry_offset + FNT_DIR_ENTRY_SIZE > len(data):
        raise ValueError(
            f"dir_id 0x{dir_id:04X} 超出 FNT 目录表范围：偏移 0x{entry_offset:X}，FNT 长度 {len(data)}"
        )

    subtable_offset, first_file_id, parent_id = struct.unpack_from(
        "<IHH", data, entry_offset
    )

    node = FNTDirNode(
        name=name,
        dir_id=dir_id,
        parent_id=parent_id,
        first_file_id=first_file_id,
    )

    # 解析子表
    if subtable_offset >= len(data):
        raise ValueError(
            f"dir_id 0x{dir_id:04X} 子表偏移 0x{subtable_offset:X} 超出 FNT 长度 {len(data)}"
        )

    pos = subtable_offset
    next_file_id = first_file_id
    while pos < len(data):
        length_byte = data[pos]
        pos += 1
        if length_byte == 0:
            break  # 子表结束

        name_length = length_byte & NAME_LENGTH_MASK
        is_dir = (length_byte & SUBTABLE_FLAG) != 0

        if pos + name_length > len(data):
            raise ValueError(
                f"dir_id 0x{dir_id:04X} 子表项名称越界：pos=0x{pos:X}, len={name_length}, FNT 长度 {len(data)}"
            )

        child_name_bytes = bytes(data[pos:pos + name_length])
        pos += name_length
        child_name = child_name_bytes.decode("utf-8", errors="replace")

        if is_dir:
            if pos + 2 > len(data):
                raise ValueError(
                    f"dir_id 0x{dir_id:04X} 子表项 dir_id 越界：pos=0x{pos:X}, FNT 长度 {len(data)}"
                )
            child_dir_id = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            child_dir = _parse_fnt_dir(data, dir_id=child_dir_id, name=child_name)
            node.subdirs.append(child_dir)
        else:
            node.files.append(FNTFileNode(name=child_name, file_id=next_file_id))
            next_file_id += 1

    return node


# ----------------------------------------------------------------------
# FNT 重建内部实现
# ----------------------------------------------------------------------

def _build_fnt(root: FNTDirNode) -> bytes:
    """
    重建 FNT 二进制数据。

    策略：
        1. 给所有目录分配 dir_id（root=0xF000，按 DFS 顺序递增）
        2. 给所有文件分配 file_id（按 DFS 顺序，从 0 开始）
        3. 目录表在前，子表在后

    注意：调用方应保证 root.dir_id == 0xF000，且 first_file_id 与外部 FAT 一致。
    本函数**不会**重置 dir_id/file_id，而是使用 node 中现有的值，便于外部精确控制。

    3.1 修复：重建时校验 dir_id 与 DFS 前序位置一致（0xF000+i），
    避免来源 ROM 的 dir_id 分配顺序与本工具 DFS 前序不一致时重建出损坏的 FNT。
    3.2 修复：根目录条目的 parent_id 字段实际语义是目录总数，重建时用 len(dirs) 覆盖。
    """
    # 1) 收集所有目录（DFS 顺序），并校验 dir_id
    dirs: list[FNTDirNode] = []
    _collect_dirs(root, dirs)
    # 3.1 修复：校验 dir_id 按表内位置分配（0xF000+i）
    for i, d in enumerate(dirs):
        expected_id = ROOT_DIR_ID + i
        if d.dir_id != expected_id:
            raise ValueError(
                f"FNT 重建 dir_id 校验失败：DFS 前序第 {i} 个目录的 dir_id 应为 "
                f"0x{expected_id:04X}，实际为 0x{d.dir_id:04X}。"
                f"可能是来源 ROM 的目录表顺序与 DFS 前序不一致，请先重新分配 dir_id。"
            )

    # 2) 构建目录表 + 子表
    # 先计算每个目录的子表大小，再分配 subtable_offset
    subtable_blobs: list[bytes] = []
    subtable_sizes: list[int] = []
    for d in dirs:
        blob = _build_subtable(d)
        subtable_blobs.append(blob)
        subtable_sizes.append(len(blob))

    # 目录表起始 = 0
    dir_table_size = len(dirs) * FNT_DIR_ENTRY_SIZE
    # 子表紧接在目录表后
    subtable_offsets: list[int] = []
    cur = dir_table_size
    for sz in subtable_sizes:
        subtable_offsets.append(cur)
        cur += sz

    # 3) 拼接最终数据
    out = bytearray()
    dir_count = len(dirs)
    for i, d in enumerate(dirs):
        # 3.2 修复：根目录条目的 parent_id 字段是目录总数，非父目录 ID
        # 根目录（i==0）写入目录总数；其他目录写入其父目录的 dir_id
        if i == 0:
            parent_id_field = dir_count
        else:
            parent_id_field = d.parent_id
        out += struct.pack(
            "<IHH",
            subtable_offsets[i],
            d.first_file_id & 0xFFFF,
            parent_id_field & 0xFFFF,
        )
    for blob in subtable_blobs:
        out += blob

    return bytes(out)


def _collect_dirs(node: FNTDirNode, out: list[FNTDirNode]) -> None:
    """DFS 收集所有目录。root 必须是 0xF000。"""
    out.append(node)
    for sub in node.subdirs:
        _collect_dirs(sub, out)


def _build_subtable(node: FNTDirNode) -> bytes:
    """构建单个目录的子表。"""
    out = bytearray()
    for f in node.files:
        name_bytes = f.name.encode("utf-8")
        if len(name_bytes) > NAME_LENGTH_MASK:
            raise ValueError(
                f"文件名过长 ({len(name_bytes)} > {NAME_LENGTH_MASK}): {f.name!r}"
            )
        out.append(len(name_bytes) & NAME_LENGTH_MASK)  # 文件：bit 7 = 0
        out += name_bytes
    for d in node.subdirs:
        name_bytes = d.name.encode("utf-8")
        if len(name_bytes) > NAME_LENGTH_MASK:
            raise ValueError(
                f"目录名过长 ({len(name_bytes)} > {NAME_LENGTH_MASK}): {d.name!r}"
            )
        out.append((len(name_bytes) & NAME_LENGTH_MASK) | SUBTABLE_FLAG)
        out += name_bytes
        out += struct.pack("<H", d.dir_id & 0xFFFF)
    out.append(0x00)  # 子表结束标记
    return bytes(out)


def _find_node(node: FNTDirNode, parts: list[str]) -> Optional[FNTDirNode | FNTFileNode]:
    """按路径分段查找节点。"""
    if not parts:
        return node
    head = parts[0]
    rest = parts[1:]
    for f in node.files:
        if f.name == head and not rest:
            return f
    for d in node.subdirs:
        if d.name == head:
            return _find_node(d, rest)
    return None


# ----------------------------------------------------------------------
# 便捷入口
# ----------------------------------------------------------------------

def parse_fnt(data: bytes | bytearray, fat: Optional[FAT] = None) -> FNT:
    """便捷函数：解析 FNT。"""
    return FNT.parse(data, fat=fat)


def build_fnt(root: FNTDirNode) -> bytes:
    """便捷函数：重建 FNT 二进制。"""
    return _build_fnt(root)


def parse_fat(data: bytes | bytearray) -> FAT:
    """便捷函数：解析 FAT。"""
    return FAT.parse(data)


def build_fat(entries: list[FATEntry]) -> bytes:
    """便捷函数：重建 FAT 二进制。"""
    return b"".join(e.build() for e in entries)
