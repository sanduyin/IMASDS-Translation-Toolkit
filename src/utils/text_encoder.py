# src/utils/text_encoder.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

# 保护区：与原版注入器完全一致
PROTECTED_RANGES: list[tuple[int, int]] =[
    (0x20, 0xDF),       # ASCII + 半角片假名
    (0x8140, 0x8799),   # 全角标点 ~ 特殊符号
]


class TextEncodingError(ValueError):
    """Raised when translated text contains a character absent from the font map."""


def is_protected(code: int) -> bool:
    """检查编码是否在保护区域内"""
    for (start, end) in PROTECTED_RANGES:
        if start <= code <= end:
            return True
    return False


def load_mapping(mapping_path: str | Path) -> dict[str, int]:
    """加载 JSON 映射表"""
    if not os.path.exists(mapping_path):
        return {}
    with open(mapping_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return cast(dict[str, int], data)


def text_to_bytes(text: str | None, mapping: dict[str, int]) -> bytearray:
    """
    通用函数：将文本按照 mapping 转换为字节流
    支持换行符转义，自动判断单/双字节，结尾自动补 0x00
    """
    result = bytearray()
    if text is None: text = ""

    # 规范化换行符
    text = str(text).replace('\r\n', '\n').replace('\r', '\n')

    for index, char in enumerate(text):
        if char == '\n':
            result.append(0x0A)
        elif char in mapping:
            code = mapping[char]
            if not 0 <= code <= 0xFFFF:
                raise TextEncodingError(
                    f"字符 {char!r} 的映射 0x{code:X} 超出 1/2 字节编码范围"
                )
            if code < 0x100:
                result.append(code)
            else:
                result.extend([(code >> 8) & 0xFF, code & 0xFF])
        else:
            raise TextEncodingError(
                f"字符 {char!r} (U+{ord(char):04X}) 不在 font_mapping 中 "
                f"(文本索引 {index})"
            )

    # NDS 游戏文本必须以 \x00 结尾
    result.append(0x00)
    return result
