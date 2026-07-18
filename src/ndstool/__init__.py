# src/ndstool/__init__.py
"""
ndstool - 纯 Python 实现的 NDS/DSi ROM 构建内核。

目标：消除对 ndspy 的依赖，提供与 faraplay/dearlystars_tool (Rust) 等价的
NDS Header / FNT / FAT / ARM9 / Overlay / DSi 扩展处理能力。

子模块：
    header      - NDS Header (0x200) 解析与生成
    fnt_fat     - FNT (文件名表) 与 FAT (文件分配表) 的解析与重建
    overlay     - ARM9 Overlay 表解析与重建
    rom_builder - ARM9/Overlay 解压/压缩与 ROM 重建
    dsi_builder - DSi (TWL) 扩展：arm9i/arm7i、digest、HMAC-SHA1、modcrypt
    crc         - NDS 专用 CRC16 与 Nintendo Logo 校验
"""

from .header import NDSHeader, parse_header, build_header
from .crc import crc16_header, crc16_logo, verify_header_crc
from .overlay import (
    OverlayTableEntry,
    parse_overlay_table,
    build_overlay_table,
)
from .fnt_fat import (
    FATEntry,
    FAT,
    FNTFileNode,
    FNTDirNode,
    FNT,
    parse_fat,
    build_fat,
    parse_fnt,
    build_fnt,
)
from .rom_builder import RomImage, OverlayState, load_rom, save_rom
from .dsi_builder import (
    DsiExtraFields,
    DSI_EXTRA_FIELDS_SIZE,
    sha1_hmac,
    get_key_ivs,
    aes_ctr,
    modcrypt,
    encrypt_arm9,
    decrypt_arm9,
    encrypt_secure_area,
    decrypt_secure_area,
    write_digests,
    write_hashes,
)

__all__ = [
    # header
    "NDSHeader",
    "parse_header",
    "build_header",
    # crc
    "crc16_header",
    "crc16_logo",
    "verify_header_crc",
    # overlay
    "OverlayTableEntry",
    "parse_overlay_table",
    "build_overlay_table",
    # fnt / fat
    "FATEntry",
    "FAT",
    "FNTFileNode",
    "FNTDirNode",
    "FNT",
    "parse_fat",
    "build_fat",
    "parse_fnt",
    "build_fnt",
    # rom image
    "RomImage",
    "OverlayState",
    "load_rom",
    "save_rom",
    # dsi builder (P0-5)
    "DsiExtraFields",
    "DSI_EXTRA_FIELDS_SIZE",
    "sha1_hmac",
    "get_key_ivs",
    "aes_ctr",
    "modcrypt",
    "encrypt_arm9",
    "decrypt_arm9",
    "encrypt_secure_area",
    "decrypt_secure_area",
    "write_digests",
    "write_hashes",
]
