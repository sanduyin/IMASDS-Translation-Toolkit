# src/utils/bbq_mail.py
"""
邮件 BBQ 文件专用解析与重建（faraplay 兼容 5 列 ML CSV 格式）。

faraplay v0.5.2 的邮件处理逻辑（参考 dearlystars_tool (Rust) bbq.rs）：
- 邮件 BBQ 文件命名约定：`XXXX_ML_*.BBQ`（前 5 字符为索引，第 6 字符起为 "ML_"）
- 每个 BBQ 含 4 个 section：
    Type2 = 元数据 [0, 65536, 0, 1, 248, 1, 0]（248 = Type5 字节数）
    Type5 = 邮件索引表（248 字节 = 62 个 u32）
    Type6 = 空命令列表
    Type7 = 字符串池（索引 0 为占位空字符串）

Type5 数据布局：
    [0]         邮件正文行数 N1
    [1..31]     N1 个邮件正文行的字符串索引（不足 30 用 0 填充）
    [31]        回信正文行数 N2
    [32..62]    N2 个回信正文行的字符串索引（不足 30 用 0 填充）

faraplay ML CSV 格式（5 列）：
    Filename,Mail,Translated Mail,Reply,Translated Reply
    XXXX_ML_*.BBQ,"行1\\n行2\\n...","译文1\\n译文2\\n...","回信1\\n回信2\\n...","回信译文1\\n..."

约定：
- 邮件正文 / 回信正文均最多 30 行，超过报错（ValueError）
- 每行 Shift-JIS 字节数 ≤ 32，超过告警（不强制截断，保留原文）
- 字符串池去重：相同文本复用同一索引（faraplay get_index 语义）
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from src.utils.text_encoder import text_to_bytes

# BBQ 文件魔数与固定字段
BBQ_HEADER_MAGIC: bytes = b"\x2E\x42\x42\x51\x31\x2E\x30\x30"  # .BBQ1.00
BBQ_HEADER_SIZE: int = 0x18  # 24 字节固定 header
BBQ_ENTRY_SIZE: int = 0x14   # 20 字节每个 section entry
BBQ_FOOTER_SIZE: int = 0x18  # 24 字节 footer（6 个 u32）

# 邮件索引表常量
MAIL_MAX_LINES: int = 30
MAIL_LINE_MAX_BYTES: int = 32  # Shift-JIS 字节硬限制
TYPE5_TOTAL_U32: int = 62      # 1 + 30 + 1 + 30
TYPE5_TOTAL_BYTES: int = TYPE5_TOTAL_U32 * 4  # 248

# Type2 元数据（faraplay inject_mail 中固定写入的值）
MAIL_TYPE2_DATA: tuple[int, ...] = (0, 65536, 0, 1, 248, 1, 0)


# ----------------------------------------------------------------------
# 低层 BBQ 读写
# ----------------------------------------------------------------------

def _read_bbq_raw(data: bytes) -> dict[str, Any]:
    """解析 BBQ 文件二进制，返回完整结构。

    Returns:
        {
            "datetime": int,
            "sections": {type_id: {"offsets": [u32,...], "data": bytes}},
            "footer": [u32 * 6],
        }
    """
    if len(data) < BBQ_HEADER_SIZE:
        raise ValueError(f"BBQ 文件过小：{len(data)} < {BBQ_HEADER_SIZE}")
    if data[:8] != BBQ_HEADER_MAGIC:
        raise ValueError(f"BBQ 魔数错误：{data[:8]!r}")
    datetime_val = struct.unpack_from("<I", data, 8)[0]
    magic2 = struct.unpack_from("<I", data, 12)[0]
    if magic2 != 1:
        raise ValueError(f"BBQ magic2 != 1: {magic2}")
    entries_offset = struct.unpack_from("<I", data, 16)[0]
    entry_count = struct.unpack_from("<I", data, 20)[0]

    sections: dict[int, dict[str, Any]] = {}
    for i in range(entry_count):
        entry_pos = entries_offset + i * BBQ_ENTRY_SIZE
        data_type, offsets_off, data_count, data_off, data_size = struct.unpack_from(
            "<IIIII", data, entry_pos
        )
        # offsets 绝对位置 = entry 自身位置 + offsets_off
        offsets_abs = entry_pos + offsets_off
        # data 绝对位置 = entry 自身位置 + data_off
        data_abs = entry_pos + data_off

        offsets = [
            struct.unpack_from("<I", data, offsets_abs + j * 4)[0]
            for j in range(data_count)
        ]
        section_data = data[data_abs:data_abs + data_size]
        sections[data_type] = {"offsets": offsets, "data": section_data}

    # footer 紧跟在最后一个 section 的 data 之后
    last_entry_pos = entries_offset + (entry_count - 1) * BBQ_ENTRY_SIZE
    _, _, _, last_data_off, last_data_size = struct.unpack_from(
        "<IIIII", data, last_entry_pos
    )
    footer_abs = last_entry_pos + last_data_off + last_data_size
    footer = [
        struct.unpack_from("<I", data, footer_abs + j * 4)[0]
        for j in range(6)
    ]

    return {"datetime": datetime_val, "sections": sections, "footer": footer}


def _read_type7_strings(type7_section: dict[str, Any]) -> list[str]:
    """从 Type7 section 解析字符串列表。"""
    offsets = type7_section["offsets"]
    raw = type7_section["data"]
    strings: list[str] = []
    for i, start in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else len(raw)
        chunk = raw[start:end]
        # 去 NUL
        text_bytes = chunk.rstrip(b"\x00")
        try:
            text = text_bytes.decode("cp932")
        except UnicodeDecodeError:
            text = f"<HEX:{text_bytes.hex()}>"
        strings.append(text)
    return strings


def _read_type5_indices(type5_section: dict[str, Any]) -> list[int]:
    """从 Type5 section 解析 u32 索引数组。"""
    raw = type5_section["data"]
    n = len(raw) // 4
    return [struct.unpack_from("<I", raw, i * 4)[0] for i in range(n)]


# ----------------------------------------------------------------------
# 邮件解析
# ----------------------------------------------------------------------

def parse_mail_bbq(file_path: str | Path) -> tuple[str, str]:
    """解析邮件 BBQ，返回 (mail1, mail2)。

    mail1 = 邮件正文，用 \\n 拼接的多行字符串
    mail2 = 回信正文，用 \\n 拼接的多行字符串

    如果文件不是邮件格式（无 Type5 或 Type7），返回 ("", "")。
    """
    mail1, mail2, _, _ = parse_mail_bbq_meta(file_path)
    return (mail1, mail2)


def parse_mail_bbq_meta(file_path: str | Path) -> tuple[str, str, int, int]:
    """解析邮件 BBQ，返回 (mail1, mail2, mail1_start_idx, mail2_start_idx)。

    mail1_start_idx / mail2_start_idx = 邮件正文 / 回信正文首行在 Type7 字符串池
    中的索引（xlsx 邮件 sheet 的 Index 列展示用，注入时不消费）。
    如果文件不是邮件格式（无 Type5 或 Type7），返回 ("", "", 0, 0)。
    """
    data = Path(file_path).read_bytes()
    if not data or data[:8] != BBQ_HEADER_MAGIC:
        return ("", "", 0, 0)

    bbq = _read_bbq_raw(data)
    sections = bbq["sections"]

    if 5 not in sections or 7 not in sections:
        return ("", "", 0, 0)

    indices = _read_type5_indices(sections[5])
    strings = _read_type7_strings(sections[7])

    if len(indices) < 32:
        return ("", "", 0, 0)

    # 邮件正文：indices[0] = 行数 N1，indices[1..1+N1] = 字符串索引
    n1 = indices[0]
    mail1_indices = indices[1:1 + min(n1, MAIL_MAX_LINES)]
    mail1_parts = [
        strings[idx] if 0 <= idx < len(strings) else ""
        for idx in mail1_indices
    ]
    mail1 = "\n".join(mail1_parts)
    mail1_start_idx = mail1_indices[0] if mail1_indices else 0

    # 回信正文：indices[31] = 行数 N2，indices[32..32+N2] = 字符串索引
    n2 = indices[31]
    mail2_indices = indices[32:32 + min(n2, MAIL_MAX_LINES)]
    mail2_parts = [
        strings[idx] if 0 <= idx < len(strings) else ""
        for idx in mail2_indices
    ]
    mail2 = "\n".join(mail2_parts)
    mail2_start_idx = mail2_indices[0] if mail2_indices else 0

    return (mail1, mail2, mail1_start_idx, mail2_start_idx)


# ----------------------------------------------------------------------
# 邮件重建
# ----------------------------------------------------------------------

def _can_encode_with_charmap(text: str, char_map: dict[str, int]) -> bool:
    """检查 text 的所有字符是否都在 char_map 中（或为换行符）。

    用于决定该字符串用 text_to_bytes（自定义字库编码）还是 cp932（原版编码）。
    字库中缺失的字符会被 text_to_bytes 替换为 '?'，故含缺失字符的字符串
    必须回退到 cp932 以保留原文字节。
    """
    return all(ch in char_map or ch == '\n' for ch in text)


def _sj_len(text: str, char_map: dict[str, int] | None = None) -> int:
    """Shift-JIS / 自定义字库 字节长度（faraplay sj_len 等价实现）。

    有 char_map 且所有字符都在字库中时用 text_to_bytes（与游戏 patched font 一致）；
    否则回退到 cp932（原版游戏编码，保留字库未覆盖的日文字符）。
    返回值不含 NUL 终止符。
    """
    if char_map is not None and _can_encode_with_charmap(text, char_map):
        # text_to_bytes 返回值含末尾 NUL，减 1
        return len(text_to_bytes(text, char_map)) - 1
    try:
        return len(text.encode("cp932"))
    except UnicodeEncodeError as e:
        bad_char = text[e.start:e.end]
        raise ValueError(
            f"无法用 cp932 编码文本：{text!r}，"
            f"缺失字符 {bad_char!r} (U+{ord(bad_char):04X})"
        ) from e


def _get_index(strings: list[str], text: str) -> int:
    """字符串去重插入，返回索引（faraplay get_index 语义）。"""
    for i, s in enumerate(strings):
        if s == text:
            return i
    strings.append(text)
    return len(strings) - 1


def _build_type5_bytes(
    mail_lines1: list[str],
    mail_lines2: list[str],
    strings: list[str],
    char_map: dict[str, int] | None = None,
) -> bytes:
    """根据 mail 行列表构建 Type5 二进制（248 字节）。

    strings 池会被原地修改：strings[0] 必须是 ""（占位），新行通过 _get_index 添加。
    """
    if len(mail_lines1) > MAIL_MAX_LINES:
        raise ValueError(
            f"邮件正文行数超过上限：{len(mail_lines1)} > {MAIL_MAX_LINES}"
        )
    if len(mail_lines2) > MAIL_MAX_LINES:
        raise ValueError(
            f"回信正文行数超过上限：{len(mail_lines2)} > {MAIL_MAX_LINES}"
        )

    lines1_count = len(mail_lines1)
    lines2_count = len(mail_lines2)

    # 补齐到 30 行（不足补空字符串；超过上限已在上方报错）
    mail_lines1_resized = mail_lines1[:MAIL_MAX_LINES] + [""] * (MAIL_MAX_LINES - len(mail_lines1))
    mail_lines2_resized = mail_lines2[:MAIL_MAX_LINES] + [""] * (MAIL_MAX_LINES - len(mail_lines2))

    # 字节长度校验告警
    for line in mail_lines1_resized[:lines1_count] + mail_lines2_resized[:lines2_count]:
        line_len = _sj_len(line, char_map)
        if line_len > MAIL_LINE_MAX_BYTES:
            print(f"  ⚠️ 邮件行超过 32 字节限制: {line[:30]!r} ({line_len}B)")

    # 构建 u32 数组
    u32s: list[int] = [lines1_count]
    for line in mail_lines1_resized:
        idx = _get_index(strings, line)
        u32s.append(idx)
    u32s.append(lines2_count)
    for line in mail_lines2_resized:
        idx = _get_index(strings, line)
        u32s.append(idx)

    # 不足 62 个 u32 用 0 填充
    while len(u32s) < TYPE5_TOTAL_U32:
        u32s.append(0)

    return struct.pack(f"<{TYPE5_TOTAL_U32}I", *u32s[:TYPE5_TOTAL_U32])


def _build_type7_bytes(
    strings: list[str],
    char_map: dict[str, int] | None = None,
) -> tuple[bytes, list[int]]:
    """根据字符串列表构建 Type7 二进制，返回 (data, offsets)。

    每个字符串以 NUL 结尾，整个数据 4 字节对齐填充。

    编码策略（逐字符串判断，支持邮件中译文与原文共存）：
    - 有 char_map 且该字符串所有字符都在字库中 → text_to_bytes（自定义字库编码，
      中文译文必需，mapped 日文字符与 cp932 字节一致）
    - 否则 → cp932（原版编码，保留字库未覆盖的日文字符，避免被替换为 '?'）
    """
    offsets: list[int] = []
    data = bytearray()
    for s in strings:
        offsets.append(len(data))
        if char_map is not None and _can_encode_with_charmap(s, char_map):
            # text_to_bytes 返回值已含末尾 NUL，直接 extend
            data.extend(text_to_bytes(s, char_map))
        else:
            try:
                encoded = s.encode("cp932")
            except UnicodeEncodeError as e:
                bad_char = s[e.start:e.end]
                raise ValueError(
                    f"无法用 cp932 编码字符串：{s!r}，"
                    f"缺失字符 {bad_char!r} (U+{ord(bad_char):04X})"
                ) from e
            data.extend(encoded)
            data.append(0)  # NUL terminator

    # 4 字节对齐
    while len(data) % 4 != 0:
        data.append(0)

    return (bytes(data), offsets)


def _build_type2_bytes() -> bytes:
    """Type2 元数据，faraplay inject_mail 固定写入 [0, 65536, 0, 1, 248, 1, 0]。"""
    return struct.pack("<7I", *MAIL_TYPE2_DATA)


def _build_type6_bytes() -> bytes:
    """Type6 空命令列表。"""
    return b""


def _serialize_bbq(
    datetime_val: int,
    sections: list[tuple[int, bytes, list[int]]],
    footer: list[int],
) -> bytes:
    """序列化 BBQ 二进制。

    Args:
        datetime_val: 原 BBQ 的 datetime 字段
        sections: [(type_id, data_bytes, offsets), ...] 按 type_id 升序
        footer: 6 个 u32
    """
    section_count = len(sections)

    # header 固定部分（24 字节）
    header = bytearray()
    header.extend(BBQ_HEADER_MAGIC)
    header.extend(struct.pack("<I", datetime_val))
    header.extend(struct.pack("<I", 1))  # magic2
    header.extend(struct.pack("<I", BBQ_HEADER_SIZE))  # entries_offset
    header.extend(struct.pack("<I", section_count))

    # section entries + data
    data_part = bytearray()
    for i, (type_id, sec_data, offsets) in enumerate(sections):
        # offsets_offset = (section_count - i) * 0x14 + 之前 sections 累计的 data 字节数
        offsets_offset = (section_count - i) * BBQ_ENTRY_SIZE + len(data_part)
        data_count = len(offsets)
        data_offset = offsets_offset + 4 * data_count
        data_size = len(sec_data)

        # 写入 entry
        header.extend(struct.pack("<IIIII", type_id, offsets_offset, data_count, data_offset, data_size))
        # 写入 data 部分（offsets + 字节数据）
        for off in offsets:
            data_part.extend(struct.pack("<I", off))
        data_part.extend(sec_data)

    # 合并 header + data
    header.extend(data_part)

    # footer
    for v in footer:
        header.extend(struct.pack("<I", v))

    return bytes(header)


def validate_mail_bbq(data: bytes) -> None:
    """验证 BBQ 文件结构完整性。

    检查项：
    - 魔数、header、section 目录在文件边界内
    - Type2/5/6/7 各 section 的相对偏移与数据在文件边界内
    - Type5 数据恰好 248 字节（62 个 u32）
    - Type5 indices[0] 与 indices[31] <= 30
    - Type5 所有字符串索引 < Type7 字符串数
    - Type7 偏移单调递增、在边界内、每个字符串以 NUL 结尾

    任何检查失败时抛出 ValueError。
    """
    n = len(data)

    # 1. header 边界与魔数
    if n < BBQ_HEADER_SIZE:
        raise ValueError(f"BBQ 文件过小：{n} < {BBQ_HEADER_SIZE}")
    if data[:8] != BBQ_HEADER_MAGIC:
        raise ValueError(f"BBQ 魔数错误：{data[:8]!r}")
    magic2 = struct.unpack_from("<I", data, 12)[0]
    if magic2 != 1:
        raise ValueError(f"BBQ magic2 != 1: {magic2}")
    entries_offset = struct.unpack_from("<I", data, 16)[0]
    entry_count = struct.unpack_from("<I", data, 20)[0]

    # 2. section 目录在文件边界内
    dir_end = entries_offset + entry_count * BBQ_ENTRY_SIZE
    if entries_offset < BBQ_HEADER_SIZE or dir_end > n:
        raise ValueError(
            f"BBQ section 目录越界：entries_offset={entries_offset}, "
            f"entry_count={entry_count}, dir_end={dir_end}, file_size={n}"
        )

    # 3. 解析每个 entry，校验相对偏移与数据在文件边界内
    sections: dict[int, dict[str, Any]] = {}
    for i in range(entry_count):
        entry_pos = entries_offset + i * BBQ_ENTRY_SIZE
        data_type, offsets_off, data_count, data_off, data_size = struct.unpack_from(
            "<IIIII", data, entry_pos
        )
        offsets_abs = entry_pos + offsets_off
        data_abs = entry_pos + data_off
        offsets_end = offsets_abs + data_count * 4
        data_end = data_abs + data_size
        if offsets_end > n:
            raise ValueError(
                f"Type{data_type} offsets 越界：offsets_abs={offsets_abs}, "
                f"data_count={data_count}, offsets_end={offsets_end}, file_size={n}"
            )
        if data_end > n:
            raise ValueError(
                f"Type{data_type} data 越界：data_abs={data_abs}, "
                f"data_size={data_size}, data_end={data_end}, file_size={n}"
            )
        offsets = [
            struct.unpack_from("<I", data, offsets_abs + j * 4)[0]
            for j in range(data_count)
        ]
        sections[data_type] = {
            "offsets": offsets,
            "data": data[data_abs:data_abs + data_size],
        }

    # 4. Type5 数据恰好 248 字节
    if 5 not in sections:
        raise ValueError("BBQ 缺少 Type5 section")
    type5 = sections[5]
    if len(type5["data"]) != TYPE5_TOTAL_BYTES:
        raise ValueError(
            f"Type5 数据长度错误：{len(type5['data'])} != {TYPE5_TOTAL_BYTES}"
        )
    indices = _read_type5_indices(type5)
    if len(indices) != TYPE5_TOTAL_U32:
        raise ValueError(
            f"Type5 u32 数量错误：{len(indices)} != {TYPE5_TOTAL_U32}"
        )

    # 5. indices[0] 与 indices[31] <= MAIL_MAX_LINES
    if indices[0] > MAIL_MAX_LINES:
        raise ValueError(
            f"Type5 indices[0] 超过上限：{indices[0]} > {MAIL_MAX_LINES}"
        )
    if indices[31] > MAIL_MAX_LINES:
        raise ValueError(
            f"Type5 indices[31] 超过上限：{indices[31]} > {MAIL_MAX_LINES}"
        )

    # 6. Type7 字符串池校验
    if 7 not in sections:
        raise ValueError("BBQ 缺少 Type7 section")
    type7 = sections[7]
    type7_offsets = type7["offsets"]
    type7_raw = type7["data"]
    type7_size = len(type7_raw)
    str_count = len(type7_offsets)

    # Type7 偏移单调递增、在边界内、每个字符串以 NUL 结尾
    prev = -1
    for k, off in enumerate(type7_offsets):
        next_off = type7_offsets[k + 1] if k + 1 < str_count else type7_size
        if off <= prev:
            raise ValueError(
                f"Type7 offsets 非单调递增：index={k}, offset={off}, prev={prev}"
            )
        if off >= type7_size:
            raise ValueError(
                f"Type7 offset 越界：index={k}, offset={off}, data_size={type7_size}"
            )
        nul_pos = type7_raw.find(b"\x00", off, next_off)
        if nul_pos == -1:
            raise ValueError(
                f"Type7 字符串缺少 NUL 终止符：index={k}, offset={off}, "
                f"next_offset={next_off}"
            )
        prev = off

    # 7. Type5 所有字符串索引 < Type7 字符串数
    #    indices[0] 与 indices[31] 为行数计数，非字符串索引，跳过
    for i, idx in enumerate(indices):
        if i == 0 or i == 31:
            continue
        if idx >= str_count:
            raise ValueError(
                f"Type5 字符串索引越界：indices[{i}]={idx} "
                f">= Type7 字符串数={str_count}"
            )


def rebuild_mail_bbq(
    src_path: str | Path,
    dst_path: str | Path,
    mail1: str,
    mail2: str,
    char_map: dict[str, int] | None = None,
) -> None:
    """根据 mail1/mail2 重建邮件 BBQ 文件。

    读取原 BBQ 的 datetime 和 footer，重建 Type2/5/6/7。
    其他 Type（如 Type3）如果存在，会被丢弃（邮件 BBQ 通常不含 Type3）。

    Args:
        src_path:  原 BBQ 文件路径（用于读取 datetime、footer）
        dst_path:  输出 BBQ 文件路径
        mail1:     邮件正文（\\n 分隔多行）
        mail2:     回信正文（\\n 分隔多行）
        char_map:  自定义字库映射表（注入中文译文时必需；为 None 时用 cp932，
                   仅适用于原版日文文本的结构自检）
    """
    src_data = Path(src_path).read_bytes()
    bbq = _read_bbq_raw(src_data)
    datetime_val = bbq["datetime"]
    footer = bbq["footer"]

    # 拆分邮件行
    mail_lines1 = mail1.split("\n") if mail1 else []
    mail_lines2 = mail2.split("\n") if mail2 else []

    # 构建 strings 池（索引 0 必须是空字符串占位）
    strings: list[str] = [""]

    # 构建 Type5（含字节长度告警）
    type5_data = _build_type5_bytes(mail_lines1, mail_lines2, strings, char_map)

    # 构建 Type7（用 char_map 编码以支持中文译文）
    type7_data, type7_offsets = _build_type7_bytes(strings, char_map)

    # 构建 Type2 / Type6
    type2_data = _build_type2_bytes()
    type6_data = _build_type6_bytes()

    # 序列化（type_id 升序：2, 5, 6, 7）
    sections = [
        (2, type2_data, [0]),       # Type2 只有 1 个数据项，offsets=[0]
        (5, type5_data, [0]),       # Type5 只有 1 个数据项
        (6, type6_data, [0]),       # Type6 只有 1 个数据项（空 commands）
        (7, type7_data, type7_offsets),
    ]

    out_bytes = _serialize_bbq(datetime_val, sections, footer)

    # 结构校验：重新解析输出并检查完整性
    # （Type5 长度/索引、字符串索引范围、Type7 偏移单调/NUL 终止符）
    validate_mail_bbq(out_bytes)

    Path(dst_path).write_bytes(out_bytes)


# ----------------------------------------------------------------------
# 自检
# ----------------------------------------------------------------------

def self_test(src_path: str | Path) -> bool:
    """自检：解析 → 重建 → 再次解析，验证 mail1/mail2 是否一致。

    Returns:
        True 如果 roundtrip 一致
    """
    src_path = Path(src_path)
    mail1_orig, mail2_orig = parse_mail_bbq(src_path)

    # 重建到临时内存（写到 src 同目录的 .roundtrip.BBQ）
    tmp_path = src_path.parent / f"{src_path.stem}.roundtrip.BBQ"
    rebuild_mail_bbq(src_path, tmp_path, mail1_orig, mail2_orig)

    # 重新解析
    mail1_new, mail2_new = parse_mail_bbq(tmp_path)

    # 清理
    tmp_path.unlink()

    ok = (mail1_orig == mail1_new) and (mail2_orig == mail2_new)
    if not ok:
        print(f"  ❌ Roundtrip 失败:")
        print(f"     mail1 orig: {mail1_orig!r}")
        print(f"     mail1 new:  {mail1_new!r}")
        print(f"     mail2 orig: {mail2_orig!r}")
        print(f"     mail2 new:  {mail2_new!r}")
    else:
        print(f"  ✅ Roundtrip 通过: mail1={len(mail1_orig)}B mail2={len(mail2_orig)}B")
    return ok
