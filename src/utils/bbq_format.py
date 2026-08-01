# src/utils/bbq_format.py
from __future__ import annotations

import struct
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from src.utils.binary_io import read_uint32, read_string_bytes


BBQ_MAGIC = b".BBQ1.00"
BBQ_HEADER_SIZE = 0x18
BBQ_ENTRY_SIZE = 0x14
BBQ_FOOTER_SIZE = 0x18


@dataclass(frozen=True)
class RawBbqSection:
    """A lossless low-level BBQ section.

    ``offsets`` are relative to the start of ``data``.  Keeping the section
    payload raw is important for text injection: Types 2/3/5/6 can contain
    game-specific structures that must not be interpreted or rewritten just
    to replace Type 7 strings.
    """

    data_type: int
    offsets: tuple[int, ...]
    data: bytes


@dataclass(frozen=True)
class RawBbqFile:
    datetime: int
    sections: tuple[RawBbqSection, ...]
    footer: tuple[int, ...]
    trailing_data: bytes = b""


def parse_raw_bbq(data: bytes) -> RawBbqFile:
    """Parse and bounds-check a BBQ without decoding section payloads."""

    size = len(data)
    if size < BBQ_HEADER_SIZE:
        raise ValueError(f"BBQ file too small: {size} < {BBQ_HEADER_SIZE}")
    if data[:8] != BBQ_MAGIC:
        raise ValueError(f"BBQ magic mismatch: {data[:8]!r}")

    datetime_val, magic2, entries_offset, entry_count = struct.unpack_from(
        "<IIII", data, 8
    )
    if magic2 != 1:
        raise ValueError(f"BBQ magic2 mismatch: {magic2} != 1")
    if entries_offset != BBQ_HEADER_SIZE:
        raise ValueError(
            f"BBQ entries_offset mismatch: {entries_offset} != {BBQ_HEADER_SIZE}"
        )
    if entry_count <= 0:
        raise ValueError("BBQ contains no sections")

    directory_end = entries_offset + entry_count * BBQ_ENTRY_SIZE
    if directory_end > size:
        raise ValueError(
            f"BBQ section directory exceeds file: {directory_end} > {size}"
        )

    sections: list[RawBbqSection] = []
    footer_offset = 0
    seen_types: set[int] = set()
    for index in range(entry_count):
        entry_pos = entries_offset + index * BBQ_ENTRY_SIZE
        data_type, offsets_rel, data_count, data_rel, data_size = struct.unpack_from(
            "<IIIII", data, entry_pos
        )
        if data_type in seen_types:
            raise ValueError(f"duplicate BBQ Type{data_type} section")
        seen_types.add(data_type)

        offsets_pos = entry_pos + offsets_rel
        offsets_end = offsets_pos + data_count * 4
        data_pos = entry_pos + data_rel
        data_end = data_pos + data_size
        if offsets_pos < directory_end or offsets_end > size:
            raise ValueError(
                f"Type{data_type} offsets out of bounds: "
                f"0x{offsets_pos:X}..0x{offsets_end:X}"
            )
        if data_pos < offsets_end or data_end > size:
            raise ValueError(
                f"Type{data_type} data out of bounds: "
                f"0x{data_pos:X}..0x{data_end:X}"
            )

        offsets = tuple(
            struct.unpack_from("<I", data, offsets_pos + item * 4)[0]
            for item in range(data_count)
        )
        previous = -1
        for item, offset in enumerate(offsets):
            if offset < previous or offset > data_size:
                raise ValueError(
                    f"Type{data_type} offset[{item}]={offset} is invalid "
                    f"for data_size={data_size}"
                )
            previous = offset

        sections.append(
            RawBbqSection(data_type, offsets, data[data_pos:data_end])
        )
        if index == entry_count - 1:
            footer_offset = data_end

    footer_end = footer_offset + BBQ_FOOTER_SIZE
    if footer_end > size:
        raise ValueError(
            f"BBQ footer exceeds file: 0x{footer_end:X} > 0x{size:X}"
        )
    footer = struct.unpack_from("<6I", data, footer_offset)
    return RawBbqFile(
        datetime=datetime_val,
        sections=tuple(sections),
        footer=tuple(footer),
        trailing_data=data[footer_end:],
    )


def serialize_raw_bbq(bbq: RawBbqFile) -> bytes:
    """Serialize a raw BBQ with correct per-section ``data_size`` values."""

    if not bbq.sections:
        raise ValueError("BBQ contains no sections")
    if len(bbq.footer) != 6:
        raise ValueError(f"BBQ footer must contain 6 u32 values, got {len(bbq.footer)}")

    section_count = len(bbq.sections)
    header = bytearray(struct.pack(
        "<8sIIII", BBQ_MAGIC, bbq.datetime, 1, BBQ_HEADER_SIZE, section_count
    ))
    payload = bytearray()
    seen_types: set[int] = set()

    for index, section in enumerate(bbq.sections):
        if section.data_type in seen_types:
            raise ValueError(f"duplicate BBQ Type{section.data_type} section")
        seen_types.add(section.data_type)
        previous = -1
        for item, offset in enumerate(section.offsets):
            if offset < previous or offset > len(section.data):
                raise ValueError(
                    f"Type{section.data_type} offset[{item}]={offset} is invalid "
                    f"for data_size={len(section.data)}"
                )
            previous = offset

        offsets_rel = (section_count - index) * BBQ_ENTRY_SIZE + len(payload)
        data_rel = offsets_rel + len(section.offsets) * 4
        # data_size is only the data blob length.  It must not include the
        # relative distance from the section entry to that blob.
        data_size = len(section.data)
        header.extend(struct.pack(
            "<IIIII",
            section.data_type,
            offsets_rel,
            len(section.offsets),
            data_rel,
            data_size,
        ))
        for offset in section.offsets:
            payload.extend(struct.pack("<I", offset))
        payload.extend(section.data)

    output = header + payload
    output.extend(struct.pack("<6I", *bbq.footer))
    output.extend(bbq.trailing_data)
    result = bytes(output)
    # The parser is also the structural gate used by injectors.  Re-read the
    # output before it is allowed to reach the packing stage.
    parse_raw_bbq(result)
    return result


def raw_section_strings(section: RawBbqSection) -> tuple[bytes, ...]:
    """Return NUL-free strings, allowing valid shared/duplicate pointers."""

    strings: list[bytes] = []
    for index, start in enumerate(section.offsets):
        nul = section.data.find(b"\x00", start)
        if nul < 0:
            raise ValueError(
                f"Type{section.data_type} string {index} has no terminating NUL"
            )
        if index + 1 < len(section.offsets):
            next_start = section.offsets[index + 1]
            # Equal offsets deliberately share a string.  A later distinct
            # pointer must not begin inside the preceding string payload.
            if next_start > start and nul >= next_start:
                raise ValueError(
                    f"Type{section.data_type} string {index} overlaps offset "
                    f"{index + 1} ({next_start})"
                )
        strings.append(section.data[start:nul])
    return tuple(strings)


def replace_type7_strings(
    data: bytes,
    replacements: Mapping[int, bytes],
) -> bytes:
    """Rebuild a BBQ Type 7 pool while preserving all other data and footer.

    Replacement values are encoded string payloads *without* their NUL
    terminator.  Entry count and order are immutable; callers identify text by
    its existing numeric index, so image/BBQ numbering cannot drift.
    """

    bbq = parse_raw_bbq(data)
    type7_positions = [
        index for index, section in enumerate(bbq.sections)
        if section.data_type == 7
    ]
    if len(type7_positions) != 1:
        raise ValueError(f"expected exactly one Type7 section, got {len(type7_positions)}")

    section_index = type7_positions[0]
    section = bbq.sections[section_index]
    string_count = len(section.offsets)
    invalid_indices = sorted(index for index in replacements if not 0 <= index < string_count)
    if invalid_indices:
        raise IndexError(
            f"Type7 replacement indices out of range 0..{string_count - 1}: "
            f"{invalid_indices}"
        )

    new_offsets: list[int] = []
    new_pool = bytearray()
    original_strings = raw_section_strings(section)
    for index, original_payload in enumerate(original_strings):
        payload = replacements.get(index, original_payload)
        if b"\x00" in payload:
            raise ValueError(f"Type7 replacement {index} contains an embedded NUL")
        new_offsets.append(len(new_pool))
        new_pool.extend(payload)
        new_pool.append(0)

    while len(new_pool) % 4:
        new_pool.append(0)

    sections = list(bbq.sections)
    sections[section_index] = replace(
        section,
        offsets=tuple(new_offsets),
        data=bytes(new_pool),
    )
    return serialize_raw_bbq(replace(bbq, sections=tuple(sections)))


def parse_bbq_file(file_path: str | Path, is_scn: bool = True, skip_empty: bool = True) -> list[dict[str, Any]]:
    """
    解析游戏的 .bbq 封包文件格式。
    增加 is_scn 开关：如果是 TBL，强行跳过 Section 5 角色解析，避免串号乱码。
    skip_empty: 为 False 时保留空行（CSV 模式需要，行顺序=字符串索引，兼容 faraplay 行顺序匹配）。
    """
    with open(file_path, 'rb') as f:
        header_sig = f.read(8)
        if header_sig[:4] != b'\x2E\x42\x42\x51':  # '.BBQ'
            return[] 

        f.read(8) 
        header_size = read_uint32(f)
        n_sections = read_uint32(f)
        
        if n_sections == 0 or n_sections > 200: 
            return[]
            
        if f.tell() != header_size: 
            f.seek(header_size)

        sections: dict[int, dict[str, Any]] = {}
        for _ in range(n_sections):
            offset = f.tell()
            sect_id = read_uint32(f)
            values =[read_uint32(f) for _ in range(4)]
            sections[sect_id] = {'offset': offset, 'values': values}

        if 7 not in sections: 
            return[]
            
        sec7 = sections[7]
        ptr_table_offset = sec7['offset'] + sec7['values'][0]
        num_str = sec7['values'][1]
        pool_offset = sec7['offset'] + sec7['values'][2]

        f.seek(ptr_table_offset)
        pointers = [read_uint32(f) for _ in range(num_str)]
        
        raw_texts = []
        max_bytes_list = []
        text_offsets =[]
        pointer_locs =[]

        # 1. 无损读取所有文本
        for i in range(num_str):
            current_ptr_loc = ptr_table_offset + (i * 4)
            pointer_locs.append(current_ptr_loc)
            curr_addr = pool_offset + pointers[i]
            text_offsets.append(curr_addr)

            raw_bytes = read_string_bytes(f, curr_addr)
            try: 
                txt = raw_bytes.decode('cp932')
            except UnicodeDecodeError: 
                txt = f"<HEX:{raw_bytes.hex()}>"
            raw_texts.append(txt)

            if i < num_str - 1:
                gap = (pool_offset + pointers[i+1]) - curr_addr
                max_bytes_list.append(len(raw_bytes) if gap <= 0 else gap - 1)
            else: 
                max_bytes_list.append(len(raw_bytes))

        # 2. 仅当是 SCN 剧情文件时，才去解析 Section 5 角色映射表！
        speaker_map = {}
        type_map = {}
        if is_scn and 5 in sections:
            sec5 = sections[5]
            if sec5['values'][3] >= 16:
                num_views = sec5['values'][3] // 16
                view_base = sec5['offset'] + sec5['values'][2]
                f.seek(view_base)
                
                for _ in range(num_views):
                    f.read(4) 
                    idx_name, idx_l1, idx_l2 = struct.unpack('<iii', f.read(12))
                    
                    name_str = raw_texts[idx_name] if 0 <= idx_name < num_str else ""
                    if 0 <= idx_name < num_str: type_map[idx_name] = "角色名"
                    if 0 <= idx_l1 < num_str:
                        speaker_map[idx_l1] = name_str
                        type_map[idx_l1] = "对话1"
                    if 0 <= idx_l2 < num_str:
                        speaker_map[idx_l2] = name_str
                        type_map[idx_l2] = "对话2"

        # 3. 组装数据并过滤空行
        entries =[]
        for i in range(num_str):
            txt = raw_texts[i]
            max_bytes = max_bytes_list[i]
            
            # 过滤“幽灵空行”（CSV 模式 skip_empty=False 时保留，确保行顺序=字符串索引）
            if skip_empty and max_bytes == 0 and not txt.strip():
                continue
                
            # 根据是否为 SCN 决定显示样式
            if is_scn:
                speaker_display = speaker_map.get(i, "System/TBL")
                type_display = type_map.get(i, "脚本/其他")
            else:
                speaker_display = "×"  # TBL 专属：填个×
                type_display = "系统文本"

            entries.append({
                'Original_Text': txt,
                'Speaker': speaker_display,
                'Translated_Text': "", 
                'File': os.path.basename(file_path),
                'Text_Offset': f"0x{text_offsets[i]:X}",
                'Pointer_Locs': f"0x{pointer_locs[i]:X}",
                'Max_Bytes': max_bytes,
                'Index': i,
                'Type': type_display
            })
            
        return entries
