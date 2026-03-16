# src/utils/text_encoder.py
import json
import os

# 保护区：与原版注入器完全一致
PROTECTED_RANGES =[
    (0x20, 0xDF),       # ASCII + 半角片假名
    (0x8140, 0x8799),   # 全角标点 ~ 特殊符号
]

def is_protected(code):
    """检查编码是否在保护区域内"""
    for (start, end) in PROTECTED_RANGES:
        if start <= code <= end:
            return True
    return False

def load_mapping(mapping_path):
    """加载 JSON 映射表"""
    if not os.path.exists(mapping_path):
        return {}
    with open(mapping_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def text_to_bytes(text, mapping):
    """
    通用函数：将文本按照 mapping 转换为字节流
    支持换行符转义，自动判断单/双字节，结尾自动补 0x00
    """
    result = bytearray()
    if text is None: text = ""
    
    # 规范化换行符
    text = str(text).replace('\r\n', '\n').replace('\r', '\n')
    
    for char in text:
        if char == '\n': 
            result.append(0x0A)
        elif char in mapping:
            code = mapping[char]
            if code < 0x100: 
                result.append(code)
            else: 
                result.extend([(code >> 8) & 0xFF, code & 0xFF])
        else: 
            # 遇到字库中没有的字符，用 '?' 替代
            result.append(0x3F)
            
    # NDS 游戏文本必须以 \x00 结尾
    result.append(0x00)
    return result