# src/utils/bbq_yaml.py
"""
BBQ 文件格式的无损 YAML 互转引擎。

参考实现：dearlystars_tool (Rust) bbq.rs (+ byte_convert.rs, yaml_convert.rs)

## BBQ 文件结构

    [0x18 字节 Header]
    [entry_count × 0x14 字节 HeaderEntry]
    [Section 0: offsets[] + data blob (4字节对齐)]
    [Section 1: offsets[] + data blob]
    ...
    [Footer: 6 × u32 = 24 字节]

## Header (0x18 = 24 字节)

    0x00  magic[8]           = b".BBQ1.00"
    0x08  datetime: u32      时间戳（原样保留）
    0x0C  magic2: u32        必须为 1
    0x10  entries_offset: u32 恒为 0x18
    0x14  entry_count: u32   section 数量

## HeaderEntry (0x14 = 20 字节)

    data_type: u32       2/3/5/6/7
    offsets_offset: u32  相对本 entry 起点的偏移到 offsets 数组
    data_count: u32      数据项数
    data_offset: u32     相对本 entry 起点的偏移到 data blob
    data_size: u32       data blob 的（已填充对齐后的）总字节数

## 数据类型

    Type2: [u32; 7]  = 28 字节
    Type3: [u32; 7] + child_count(u32) + children([u32;4] × n) = 32 + 16n 字节
    Type5: raw bytes（任意长度）
    Type6: commands list, each Command = code1(u8)+code2(u8)+arg_count(u16)+place(u32)+args(Vec<u32>)
    Type7: SHIFT-JIS string + null terminator

## YAML 格式

    datetime: <u32>
    type2data:
    - data: [v0, v1, v2, v3, v4, v5, v6]
    type3data:
    - data: [v0, ..., v6]
      children:
      - [c0, c1, c2, c3]
    type5data:
    - bytes:
      - b0
      - b1
    type6data:
    - commands:
      - {code1: 0, code2: 0, arg_count: 0, place: 0, args: []}
    type7data:
    - |
    - 你好
    - |-
      第一行
      第二行
    footer: [f0, f1, f2, f3, f4, f5]
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any, Callable

# BBQ 魔数
BBQ_HEADER_MAGIC = b".BBQ1.00"
BBQ_MAGIC2 = 1
BBQ_ENTRIES_OFFSET = 0x18
BBQ_HEADER_SIZE = 0x18
BBQ_ENTRY_SIZE = 0x14
BBQ_FOOTER_SIZE = 24  # 6 × u32


# ----------------------------------------------------------------------
# 数据类型
# ----------------------------------------------------------------------

@dataclass
class Type2Data:
    """Type2: 7 个 u32，固定 28 字节"""
    data: list[int]  # 长度必须为 7

    def to_bytes(self) -> bytes:
        return struct.pack('<7I', *self.data)

    @classmethod
    def read(cls, data: bytes, size: int) -> 'Type2Data':
        if size != 28:
            raise ValueError(f"Type2 size mismatch: {size} != 28")
        return cls(list(struct.unpack_from('<7I', data, 0)))


@dataclass
class Type3Data:
    """Type3: 7 个 u32 + child_count + children([u32;4] × n)"""
    data: list[int]           # 长度必须为 7
    children: list[list[int]]  # 每个 child 是 4 个 u32

    def to_bytes(self) -> bytes:
        result = bytearray(struct.pack('<7I', *self.data))
        result += struct.pack('<I', len(self.children))
        for child in self.children:
            result += struct.pack('<4I', *child)
        return bytes(result)

    @classmethod
    def read(cls, data: bytes, size: int) -> 'Type3Data':
        d = list(struct.unpack_from('<7I', data, 0))
        child_count = struct.unpack_from('<I', data, 28)[0]
        expected_size = 32 + 16 * child_count
        if size != expected_size:
            raise ValueError(f"Type3 size mismatch: {size} != {expected_size}")
        children = []
        for i in range(child_count):
            off = 32 + i * 16
            children.append(list(struct.unpack_from('<4I', data, off)))
        return cls(d, children)


@dataclass
class Type5Data:
    """Type5: 原始字节"""
    bytes_data: bytearray

    def to_bytes(self) -> bytes:
        return bytes(self.bytes_data)

    @classmethod
    def read(cls, data: bytes, size: int) -> 'Type5Data':
        if size == 0:
            raise ValueError("Type5 size is 0")
        return cls(bytearray(data[:size]))


@dataclass
class Command:
    """Type6 Command: code1(u8) + code2(u8) + arg_count(u16) + place(u32) + args(Vec<u32>)"""
    code1: int
    code2: int
    arg_count: int
    place: int
    args: list[int]

    def to_bytes(self) -> bytes:
        result = bytearray()
        result.append(self.code1)
        result.append(self.code2)
        result += struct.pack('<H', self.arg_count)
        result += struct.pack('<I', self.place)
        for arg in self.args:
            result += struct.pack('<I', arg)
        return bytes(result)

    @property
    def size(self) -> int:
        return 8 + 4 * len(self.args)

    @classmethod
    def read(cls, data: bytes, offset: int) -> tuple['Command', int]:
        """读取一个 command，返回 (command, bytes_consumed)"""
        code1 = data[offset]
        code2 = data[offset + 1]
        arg_count = struct.unpack_from('<H', data, offset + 2)[0]
        place = struct.unpack_from('<I', data, offset + 4)[0]
        args = []
        for i in range(arg_count):
            args.append(struct.unpack_from('<I', data, offset + 8 + i * 4)[0])
        return cls(code1, code2, arg_count, place, args), 8 + arg_count * 4


@dataclass
class Type6Data:
    """Type6: commands list"""
    commands: list[Command]

    def to_bytes(self) -> bytes:
        result = bytearray()
        for cmd in self.commands:
            result += cmd.to_bytes()
        return bytes(result)

    @classmethod
    def read(cls, data: bytes, size: int) -> 'Type6Data':
        commands = []
        offset = 0
        while offset < size:
            cmd, consumed = Command.read(data, offset)
            commands.append(cmd)
            offset += consumed
        if offset != size:
            raise ValueError(f"Type6 size mismatch: consumed {offset} != {size}")
        return cls(commands)


@dataclass
class Type7Data:
    """Type7: SHIFT-JIS string + null terminator"""
    text: str

    def to_bytes(self) -> bytes:
        """编码为 SHIFT-JIS + null 终止符"""
        return self.text.encode('cp932') + b'\x00'

    @classmethod
    def read(cls, data: bytes, size: int) -> 'Type7Data':
        """读取 size 字节，剥离所有尾部 0（null + 对齐填充），SHIFT-JIS 解码"""
        if size == 0:
            raise ValueError("Type7 size is 0")
        # 从后向前剥离所有连续的 0 字节
        end = size
        while end > 0 and data[end - 1] == 0:
            end -= 1
        text = data[:end].decode('cp932')
        return cls(text)


# ----------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------

@dataclass
class BbqHeader:
    datetime: int
    entry_count: int

    @classmethod
    def from_bytes(cls, data: bytes) -> 'BbqHeader':
        magic = data[:8]
        if magic != BBQ_HEADER_MAGIC:
            raise ValueError(f"BBQ magic mismatch: {magic!r}")
        datetime_val = struct.unpack_from('<I', data, 8)[0]
        magic2 = struct.unpack_from('<I', data, 12)[0]
        if magic2 != BBQ_MAGIC2:
            raise ValueError(f"BBQ magic2 mismatch: {magic2} != {BBQ_MAGIC2}")
        entries_offset = struct.unpack_from('<I', data, 16)[0]
        entry_count = struct.unpack_from('<I', data, 20)[0]
        if entries_offset != BBQ_ENTRIES_OFFSET:
            raise ValueError(f"BBQ entries_offset mismatch: {entries_offset} != {BBQ_ENTRIES_OFFSET}")
        return cls(datetime_val, entry_count)

    def to_bytes(self) -> bytes:
        return struct.pack('<8sIIII',
                           BBQ_HEADER_MAGIC, self.datetime, BBQ_MAGIC2,
                           BBQ_ENTRIES_OFFSET, self.entry_count)


@dataclass
class BbqHeaderEntry:
    data_type: int
    offsets_offset: int
    data_count: int
    data_offset: int
    data_size: int

    @classmethod
    def from_bytes(cls, data: bytes) -> 'BbqHeaderEntry':
        return cls(*struct.unpack_from('<5I', data, 0))

    def to_bytes(self) -> bytes:
        return struct.pack('<5I', self.data_type, self.offsets_offset,
                           self.data_count, self.data_offset, self.data_size)


# ----------------------------------------------------------------------
# BbqFile
# ----------------------------------------------------------------------

@dataclass
class BbqFile:
    datetime: int
    type2data: Optional[list[Type2Data]] = None
    type3data: Optional[list[Type3Data]] = None
    type5data: Optional[list[Type5Data]] = None
    type6data: Optional[list[Type6Data]] = None
    type7data: Optional[list[Type7Data]] = None
    footer: list[int] = field(default_factory=lambda: [0] * 6)

    # -- 二进制解析 --

    @classmethod
    def read_bbq(cls, data: bytes) -> 'BbqFile':
        """从字节流解析 BBQ 文件"""
        if len(data) < BBQ_HEADER_SIZE:
            raise ValueError(f"BBQ file too small: {len(data)} bytes")

        header = BbqHeader.from_bytes(data[:BBQ_HEADER_SIZE])

        # 读取 HeaderEntry 列表
        entries: list[BbqHeaderEntry] = []
        entry_offset = BBQ_ENTRIES_OFFSET
        for i in range(header.entry_count):
            entry_data = data[entry_offset:entry_offset + BBQ_ENTRY_SIZE]
            entries.append(BbqHeaderEntry.from_bytes(entry_data))
            entry_offset += BBQ_ENTRY_SIZE

        # 按 data_type 分发解析
        type2data = type3data = type5data = type6data = type7data = None

        for i, entry in enumerate(entries):
            entry_start = BBQ_ENTRIES_OFFSET + i * BBQ_ENTRY_SIZE
            # offsets 数组位置（绝对）
            offsets_pos = entry_start + entry.offsets_offset
            offsets = list(struct.unpack_from(f'<{entry.data_count}I',
                                              data, offsets_pos)) if entry.data_count > 0 else []
            # data blob 位置（绝对）
            data_pos = entry_start + entry.data_offset
            # 哨兵：data_size 作为最后一个 item 的结束边界
            bounds = offsets + [entry.data_size]

            items = []
            for j in range(entry.data_count):
                start = bounds[j]
                end = bounds[j + 1]
                item_size = end - start
                item_data = data[data_pos + start:data_pos + end]
                items.append((item_data, item_size))

            if entry.data_type == 2:
                type2data = [Type2Data.read(d, s) for d, s in items]
            elif entry.data_type == 3:
                type3data = [Type3Data.read(d, s) for d, s in items]
            elif entry.data_type == 5:
                type5data = [Type5Data.read(d, s) for d, s in items]
            elif entry.data_type == 6:
                type6data = [Type6Data.read(d, s) for d, s in items]
            elif entry.data_type == 7:
                type7data = [Type7Data.read(d, s) for d, s in items]

        # 读取 footer
        last_entry = entries[-1]
        last_entry_start = BBQ_ENTRIES_OFFSET + (len(entries) - 1) * BBQ_ENTRY_SIZE
        footer_offset = last_entry_start + last_entry.data_offset + last_entry.data_size
        footer = list(struct.unpack_from('<6I', data, footer_offset))

        return cls(header.datetime, type2data, type3data, type5data,
                   type6data, type7data, footer)

    # -- 二进制序列化 --

    def to_bytes(self) -> bytes:
        """将 BBQ 序列化为字节流"""
        # 收集所有 section（按 type2→3→5→6→7 顺序）
        sections: list[tuple[int, list[Any]]] = []
        if self.type2data is not None:
            sections.append((2, self.type2data))
        if self.type3data is not None:
            sections.append((3, self.type3data))
        if self.type5data is not None:
            sections.append((5, self.type5data))
        if self.type6data is not None:
            sections.append((6, self.type6data))
        if self.type7data is not None:
            sections.append((7, self.type7data))

        section_count = len(sections)

        # 为每个 section 计算 offsets + data blob
        section_blobs = []
        for data_type, items in sections:
            offsets = []
            data_blob = bytearray()
            current_offset = 0
            for item in items:
                offsets.append(current_offset)
                item_bytes = item.to_bytes()
                data_blob += item_bytes
                current_offset += len(item_bytes)
            # 4 字节对齐填充
            pad = (4 - len(data_blob) % 4) % 4
            data_blob += b'\x00' * pad
            section_blobs.append((offsets, bytes(data_blob)))

        # 计算 HeaderEntry 字段
        # offsets_offset = (section_count - i) * 0x14 + 已写入 data 长度
        # data_offset = offsets_offset + 4 * data_count
        # data_size = padded blob 长度
        header_entries_data = bytearray()
        all_offsets_and_data = bytearray()

        data_so_far = 0
        for i, (data_type, items) in enumerate(sections):
            offsets, blob_bytes = section_blobs[i]
            offsets_offset = (section_count - i) * BBQ_ENTRY_SIZE + data_so_far
            data_count = len(offsets)
            data_offset = offsets_offset + 4 * data_count
            data_size = len(blob_bytes)

            entry = BbqHeaderEntry(data_type, offsets_offset, data_count,
                                   data_offset, data_size)
            header_entries_data += entry.to_bytes()

            # 写入 offsets
            all_offsets_and_data += struct.pack(f'<{data_count}I', *offsets) if data_count > 0 else b''
            # 写入 data blob
            all_offsets_and_data += blob_bytes

            data_so_far += 4 * data_count + len(blob_bytes)

        # 组装完整文件
        result = bytearray()
        header = BbqHeader(self.datetime, section_count)
        result += header.to_bytes()
        result += header_entries_data
        result += all_offsets_and_data
        # footer
        result += struct.pack('<6I', *self.footer)

        return bytes(result)


# ----------------------------------------------------------------------
# YAML 转换
# ----------------------------------------------------------------------

def _to_array_string(data: list[int]) -> str:
    """[1, 2, 3] → "[1, 2, 3]" """
    return "[" + ", ".join(str(x) for x in data) + "]"


def _from_array_string(s: str) -> list[int]:
    """"[1, 2, 3]" → [1, 2, 3] """
    s = s.strip()
    if not s.startswith('[') or not s.endswith(']'):
        raise ValueError(f"Invalid array string: {s!r}")
    inner = s[1:-1].strip()
    if not inner:
        return []
    return [int(x.strip()) for x in inner.split(',')]


# -- 各 Type 的 YAML 转换 --

def type2_yaml_lines(item: Type2Data) -> list[str]:
    return [f"data: {_to_array_string(item.data)}"]


def type2_from_yaml(lines: list[str]) -> Type2Data:
    if len(lines) != 1:
        raise ValueError(f"Type2 expects 1 line, got {len(lines)}")
    s = lines[0]
    if not s.startswith("data: "):
        raise ValueError(f"Type2 line must start with 'data: ': {s!r}")
    data = _from_array_string(s[6:])
    if len(data) != 7:
        raise ValueError(f"Type2 data must have 7 elements, got {len(data)}")
    return Type2Data(data)


def type3_yaml_lines(item: Type3Data) -> list[str]:
    lines = [f"data: {_to_array_string(item.data)}"]
    lines.append("children:")
    for child in item.children:
        lines.append(f"- {_to_array_string(child)}")
    return lines


def type3_from_yaml(lines: list[str]) -> Type3Data:
    if len(lines) < 2:
        raise ValueError(f"Type3 expects at least 2 lines, got {len(lines)}")
    s = lines[0]
    if not s.startswith("data: "):
        raise ValueError(f"Type3 first line must start with 'data: ': {s!r}")
    data = _from_array_string(s[6:])
    if len(data) != 7:
        raise ValueError(f"Type3 data must have 7 elements, got {len(data)}")
    if lines[1] != "children:":
        raise ValueError(f"Type3 second line must be 'children:': {lines[1]!r}")
    children = []
    for line in lines[2:]:
        if not line.startswith("- "):
            raise ValueError(f"Type3 child line must start with '- ': {line!r}")
        child = _from_array_string(line[2:])
        if len(child) != 4:
            raise ValueError(f"Type3 child must have 4 elements, got {len(child)}")
        children.append(child)
    return Type3Data(data, children)


def type5_yaml_lines(item: Type5Data) -> list[str]:
    lines = ["bytes:"]
    for b in item.bytes_data:
        lines.append(f"- {b}")
    return lines


def type5_from_yaml(lines: list[str]) -> Type5Data:
    if len(lines) < 1:
        raise ValueError("Type5 expects at least 1 line")
    if lines[0] != "bytes:":
        raise ValueError(f"Type5 first line must be 'bytes:': {lines[0]!r}")
    bytes_data = bytearray()
    for line in lines[1:]:
        if not line.startswith("- "):
            raise ValueError(f"Type5 byte line must start with '- ': {line!r}")
        bytes_data.append(int(line[2:]))
    return Type5Data(bytes_data)


def _command_yaml_line(cmd: Command) -> str:
    args_str = _to_array_string(cmd.args)
    return (f"{{code1: {cmd.code1}, code2: {cmd.code2}, "
            f"arg_count: {cmd.arg_count}, place: {cmd.place}, args: {args_str}}}")


def _command_from_yaml(line: str) -> Command:
    s = line.strip()
    if not s.startswith('{') or not s.endswith('}'):
        raise ValueError(f"Command line must be wrapped in {{}}: {line!r}")
    s = s[1:-1]
    # args 是最后一个字段，且其数组内部含 ', ' 分隔符，不能用简单 split。
    # 直接定位 'args: ' 标记，标记之后的所有内容都是数组字符串。
    args_marker = "args: "
    args_pos = s.find(args_marker)
    if args_pos < 0:
        raise ValueError(f"Command line missing 'args: ': {line!r}")
    args_str = s[args_pos + len(args_marker):]
    prefix = s[:args_pos]
    # 前缀部分（code1/code2/arg_count/place）不含逗号歧义，可安全 split
    parts = {}
    for field_str in prefix.split(', '):
        key, _, val = field_str.partition(': ')
        parts[key] = val

    code1 = int(parts['code1'])
    code2 = int(parts['code2'])
    arg_count = int(parts['arg_count'])
    place = int(parts['place'])
    args = _from_array_string(args_str)

    if len(args) != arg_count:
        raise ValueError(f"Command arg_count={arg_count} but args has {len(args)} elements")
    return Command(code1, code2, arg_count, place, args)


def type6_yaml_lines(item: Type6Data) -> list[str]:
    lines = ["commands:"]
    for cmd in item.commands:
        lines.append(f"- {_command_yaml_line(cmd)}")
    return lines


def type6_from_yaml(lines: list[str]) -> Type6Data:
    if len(lines) < 1:
        raise ValueError("Type6 expects at least 1 line")
    if lines[0] != "commands:":
        raise ValueError(f"Type6 first line must be 'commands:': {lines[0]!r}")
    commands = []
    for line in lines[1:]:
        if not line.startswith("- "):
            raise ValueError(f"Type6 command line must start with '- ': {line!r}")
        commands.append(_command_from_yaml(line[2:]))
    return Type6Data(commands)


def type7_yaml_lines(item: Type7Data) -> list[str]:
    """Type7 YAML 块标量语法：
    - 空字符串 → "|"
    - 单行非空 → 直接是文本
    - 多行不结尾换行 → "|-" + 行
    - 多行结尾换行 → "|+" + 行（去掉 split 末尾空串）
    """
    text = item.text
    if '\n' not in text:
        if text == "":
            return ["|"]
        return [text]

    # 多行
    parts = text.split('\n')
    if parts[-1] == '':
        # 以换行结尾 → |+
        return ["|+"] + parts[:-1]
    else:
        # 不以换行结尾 → |-
        return ["|-"] + parts


def type7_from_yaml(lines: list[str]) -> Type7Data:
    if len(lines) == 0:
        raise ValueError("Type7 expects at least 1 line")

    first = lines[0]
    if len(lines) == 1:
        if first == "|":
            return Type7Data("")
        return Type7Data(first)

    # 多行
    if first == "|-":
        # 不以换行结尾，从尾部 pop 所有空行
        body = list(lines[1:])
        while body and body[-1] == "":
            body.pop()
        return Type7Data("\n".join(body))
    elif first == "|+":
        # 以换行结尾，push 一个空串恢复末尾换行
        body = list(lines[1:])
        body.append("")
        return Type7Data("\n".join(body))
    else:
        raise ValueError(f"Type7 multi-line must start with '|-' or '|+': {first!r}")


# -- yamlify / deyamlify_chunk --

def _yamlify(items: list[Any], yaml_lines_fn: Callable[[Any], list[str]]) -> list[str]:
    """将数据项列表转为 YAML 行（首行 '- '，续行 2 空格缩进）"""
    lines = []
    for item in items:
        item_lines = yaml_lines_fn(item)
        for i, line in enumerate(item_lines):
            if i == 0:
                lines.append(f"- {line}")
            else:
                lines.append(f"  {line}")
    return lines


def _deyamlify_chunk(lines: list[str], from_yaml_fn: Callable[[list[str]], Any]) -> list[Any]:
    """将 YAML 行列表切分为数据项（按 '- ' 开头分组）"""
    items: list[Any] = []
    current_chunk: list[str] = []

    for line in lines:
        if line.startswith("- "):
            if current_chunk:
                items.append(from_yaml_fn(current_chunk))
            current_chunk = [line[2:]]
        else:
            # 续行：strip 2 空格缩进
            if line.startswith("  "):
                current_chunk.append(line[2:])
            else:
                current_chunk.append(line)

    if current_chunk:
        items.append(from_yaml_fn(current_chunk))

    return items


# -- BbqFile 的 YAML 转换 --

def bbq_to_yaml_lines(bbq: BbqFile) -> list[str]:
    """BBQ → YAML 行列表"""
    lines = [f"datetime: {bbq.datetime}"]

    if bbq.type2data is not None:
        lines.append("type2data:")
        lines.extend(_yamlify(bbq.type2data, type2_yaml_lines))
    if bbq.type3data is not None:
        lines.append("type3data:")
        lines.extend(_yamlify(bbq.type3data, type3_yaml_lines))
    if bbq.type5data is not None:
        lines.append("type5data:")
        lines.extend(_yamlify(bbq.type5data, type5_yaml_lines))
    if bbq.type6data is not None:
        lines.append("type6data:")
        lines.extend(_yamlify(bbq.type6data, type6_yaml_lines))
    if bbq.type7data is not None:
        lines.append("type7data:")
        lines.extend(_yamlify(bbq.type7data, type7_yaml_lines))

    if bbq.footer != [0] * 6:
        lines.append(f"footer: {_to_array_string(bbq.footer)}")

    return lines


def bbq_from_yaml_lines(lines: list[str]) -> BbqFile:
    """YAML 行列表 → BBQ"""
    # 顶层分组：section 头（如 "type2data:"）之后的所有续行（以 "- " 或 "  " 开头）归为一组
    chunks = []
    current_chunk = []

    for line in lines:
        if line.startswith("- ") or line.startswith("  "):
            current_chunk.append(line)
        else:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
            current_chunk = [line]

    if current_chunk:
        chunks.append(current_chunk)

    # 第一个 chunk 是 datetime 行
    if not chunks:
        raise ValueError("YAML has no data!")

    datetime_chunk = chunks[0]
    datetime_line = datetime_chunk[0]
    if not datetime_line.startswith("datetime: "):
        raise ValueError(f"First line must be 'datetime: ...': {datetime_line!r}")
    datetime_val = int(datetime_line[10:])

    type2data = type3data = type5data = type6data = type7data = None
    footer = [0] * 6

    for chunk in chunks[1:]:
        header = chunk[0]
        if header == "type2data:":
            type2data = _deyamlify_chunk(chunk[1:], type2_from_yaml)
        elif header == "type3data:":
            type3data = _deyamlify_chunk(chunk[1:], type3_from_yaml)
        elif header == "type5data:":
            type5data = _deyamlify_chunk(chunk[1:], type5_from_yaml)
        elif header == "type6data:":
            type6data = _deyamlify_chunk(chunk[1:], type6_from_yaml)
        elif header == "type7data:":
            type7data = _deyamlify_chunk(chunk[1:], type7_from_yaml)
        elif header.startswith("footer: "):
            footer = _from_array_string(header[8:])
        else:
            raise ValueError(f"Unrecognised yaml chunk: {header!r}")

    return BbqFile(datetime_val, type2data, type3data, type5data,
                   type6data, type7data, footer)


# ----------------------------------------------------------------------
# 文件读写
# ----------------------------------------------------------------------

def read_bbq(path: Path | str) -> BbqFile:
    """读取 BBQ 文件"""
    path = Path(path)
    with open(path, 'rb') as f:
        data = f.read()
    if not data:
        raise ValueError(f"{path.name} is empty")
    return BbqFile.read_bbq(data)


def write_bbq(bbq: BbqFile, path: Path | str) -> None:
    """写入 BBQ 文件"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(bbq.to_bytes())


def bbq_to_yaml(bbq_path: Path | str, yaml_path: Path | str) -> None:
    """BBQ 文件 → YAML 文件"""
    bbq = read_bbq(bbq_path)
    lines = bbq_to_yaml_lines(bbq)
    yaml_path = Path(yaml_path)
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def yaml_to_bbq(yaml_path: Path | str, bbq_path: Path | str) -> None:
    """YAML 文件 → BBQ 文件"""
    yaml_path = Path(yaml_path)
    with open(yaml_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # 分割为行，丢弃末尾空行
    lines = content.split('\n')
    while lines and lines[-1] == '':
        lines.pop()
    bbq = bbq_from_yaml_lines(lines)
    write_bbq(bbq, bbq_path)
