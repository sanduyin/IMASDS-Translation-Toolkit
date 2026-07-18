# tests/utils/test_gld_format.py
"""gld_format 模块单元测试。

测试颜色转换、像素打包/解包、GLD 头部与 footer 解析、往返一致性。
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from src.utils.gld_format import (
    GLD_MAGIC,
    FOOTER_TYPE_1,
    FOOTER_TYPE_2,
    SPRITE_FORMAT_1,
    SPRITE_FORMAT_2,
    SPRITE_FORMAT_3,
    SPRITE_FORMAT_4,
    SPRITE_FORMAT_6,
    SPRITE_DELETED,
    MAX_PALETTE_SIZE,
    BIT_DEPTH,
    PIXELS_PER_BYTE,
    bgr555_to_rgb888,
    bgr555_is_transparent,
    rgb888_to_bgr555,
    unpack_2bits_le,
    unpack_4bits_le,
    pack_2bits_le,
    pack_4bits_le,
    color_distance,
    get_color_index,
    GldHeader,
    GldFooterEntry,
    GldFile,
    parse_gld,
    write_gld,
)


# ----------------------------------------------------------------------
# 常量校验
# ----------------------------------------------------------------------

class TestGLDConstants:
    def test_magic(self) -> None:
        assert GLD_MAGIC == b'\x00DLG'

    def test_footer_types(self) -> None:
        assert FOOTER_TYPE_1 == 1
        assert FOOTER_TYPE_2 == 2

    def test_sprite_formats(self) -> None:
        assert SPRITE_FORMAT_1 == 1
        assert SPRITE_FORMAT_2 == 2
        assert SPRITE_FORMAT_3 == 3
        assert SPRITE_FORMAT_4 == 4
        assert SPRITE_FORMAT_6 == 6

    def test_deleted_flag(self) -> None:
        assert SPRITE_DELETED == 0x8000

    def test_max_palette_size(self) -> None:
        assert MAX_PALETTE_SIZE[1] == 32
        assert MAX_PALETTE_SIZE[2] == 4
        assert MAX_PALETTE_SIZE[3] == 16
        assert MAX_PALETTE_SIZE[4] == 256
        assert MAX_PALETTE_SIZE[6] == 8

    def test_bit_depth(self) -> None:
        assert BIT_DEPTH[1] == 8
        assert BIT_DEPTH[2] == 2
        assert BIT_DEPTH[3] == 4
        assert BIT_DEPTH[4] == 8
        assert BIT_DEPTH[6] == 8

    def test_pixels_per_byte(self) -> None:
        assert PIXELS_PER_BYTE[2] == 4
        assert PIXELS_PER_BYTE[3] == 2


# ----------------------------------------------------------------------
# BGR555 颜色转换
# ----------------------------------------------------------------------

class TestBGR555Conversion:
    def test_bgr555_to_rgb888_black(self) -> None:
        assert bgr555_to_rgb888(0x0000) == (0, 0, 0)

    def test_bgr555_to_rgb888_white(self) -> None:
        # 全 1 (5 bit) = 0x7FFF
        r, g, b = bgr555_to_rgb888(0x7FFF)
        assert r == 255 and g == 255 and b == 255

    def test_bgr555_to_rgb888_red(self) -> None:
        # r=0x1F, g=0, b=0
        r, g, b = bgr555_to_rgb888(0x001F)
        assert r == 255 and g == 0 and b == 0

    def test_bgr555_to_rgb888_green(self) -> None:
        # g=0x1F << 5 = 0x3E0
        r, g, b = bgr555_to_rgb888(0x03E0)
        assert r == 0 and g == 255 and b == 0

    def test_bgr555_to_rgb888_blue(self) -> None:
        # b=0x1F << 10 = 0x7C00
        r, g, b = bgr555_to_rgb888(0x7C00)
        assert r == 0 and g == 0 and b == 255

    def test_bgr555_high_bit_backfill_is_lossless(self) -> None:
        # 5bit → 8bit 高位回填：x << 3 | x >> 2，应可逆
        for x in range(32):
            r, _, _ = bgr555_to_rgb888(x)
            # r = x << 3 | x >> 2
            assert r == (x << 3) | (x >> 2)

    def test_bgr555_is_transparent(self) -> None:
        assert bgr555_is_transparent(0x8000) is True
        assert bgr555_is_transparent(0x8001) is True
        assert bgr555_is_transparent(0x7FFF) is False
        assert bgr555_is_transparent(0x0000) is False

    def test_rgb888_to_bgr555_basic(self) -> None:
        # 0,0,0 → 0
        assert rgb888_to_bgr555(0, 0, 0) == 0x0000
        # 255,255,255 → 0x7FFF
        assert rgb888_to_bgr555(255, 255, 255) == 0x7FFF
        # 255,0,0 → 0x001F
        assert rgb888_to_bgr555(255, 0, 0) == 0x001F

    def test_rgb888_to_bgr555_transparent_flag(self) -> None:
        result = rgb888_to_bgr555(0, 0, 0, transparent=True)
        assert result == 0x8000

    def test_rgb888_to_bgr555_is_lossy_truncation(self) -> None:
        # 8bit → 5bit 是有损截断：x >> 3
        # 例如 0xFF → 0x1F，0xFE → 0x1F，0xF8 → 0x1F
        assert rgb888_to_bgr555(255, 0, 0) == rgb888_to_bgr555(248, 0, 0) == 0x001F


# ----------------------------------------------------------------------
# 像素打包/解包
# ----------------------------------------------------------------------

class TestPixelPacking:
    def test_unpack_2bits_le(self) -> None:
        # 0b11_10_01_00 = 0xE4 → [0, 1, 2, 3]
        result = unpack_2bits_le(b"\xE4")
        assert result == [0, 1, 2, 3]

    def test_unpack_4bits_le(self) -> None:
        # 0x21 → [0x1, 0x2]
        result = unpack_4bits_le(b"\x21")
        assert result == [0x1, 0x2]

    def test_pack_unpack_2bits_round_trip(self) -> None:
        original = [0, 1, 2, 3, 0, 3, 2, 1]
        packed = pack_2bits_le(original)
        unpacked = unpack_2bits_le(packed)
        assert unpacked == original

    def test_pack_unpack_4bits_round_trip(self) -> None:
        original = [0x1, 0x2, 0xF, 0xA, 0x5, 0x6]
        packed = pack_4bits_le(original)
        unpacked = unpack_4bits_le(packed)
        assert unpacked == original

    def test_pack_2bits_partial_last_byte(self) -> None:
        # 不足 4 像素的最后一字节用 last_byte 填充
        data = [1, 2]  # 只 2 个像素，需填充到 4
        packed = pack_2bits_le(data, last_byte=0xFF)
        # b0=1, b1=2, b2=3 (from 0xFF >> 4 & 0x3), b3=3 (from 0xFF >> 6 & 0x3)
        # = 1 | (2<<2) | (3<<4) | (3<<6) = 0xF9
        assert packed == b"\xF9"

    def test_pack_4bits_partial_last_byte(self) -> None:
        data = [0x5]  # 只 1 个像素
        packed = pack_4bits_le(data, last_byte=0xF0)
        # b0=0x5, b1=0xF (from 0xF0 >> 4)
        # = 0x5 | (0xF << 4) = 0xF5
        assert packed == b"\xF5"

    def test_pack_2bits_empty(self) -> None:
        assert pack_2bits_le([]) == b""

    def test_pack_4bits_empty(self) -> None:
        assert pack_4bits_le([]) == b""


# ----------------------------------------------------------------------
# 调色板匹配
# ----------------------------------------------------------------------

class TestPaletteMatching:
    def test_color_distance_zero_for_identical(self) -> None:
        # 同色（5bit 各通道相同）距离为 0
        color = 0x001F  # r=0x1F
        dist = color_distance(0xFF, 0, 0, False, color)
        assert dist == 0

    def test_color_distance_transparent_penalty(self) -> None:
        # 透明标志不同时加 100 惩罚
        palette_color = 0x8000  # 透明
        dist_with_penalty = color_distance(0, 0, 0, False, palette_color)
        dist_without = color_distance(0, 0, 0, True, palette_color)
        assert dist_with_penalty == 100
        assert dist_without == 0

    def test_get_color_index_exact_match(self) -> None:
        palette = [0x0000, 0x001F, 0x03E0]  # 黑、红、绿
        # 完全匹配红色
        idx = get_color_index(0xFF, 0, 0, False, palette)
        assert idx == 1

    def test_get_color_index_nearest(self) -> None:
        palette = [0x0000, 0x7FFF]  # 黑、白
        # 灰色 (128,128,128) 应更接近白色
        idx = get_color_index(128, 128, 128, False, palette)
        assert idx == 1

    def test_get_color_index_empty_palette_returns_zero(self) -> None:
        idx = get_color_index(0, 0, 0, False, [])
        assert idx == 0


# ----------------------------------------------------------------------
# GldHeader
# ----------------------------------------------------------------------

class TestGldHeader:
    def test_header_round_trip(self) -> None:
        header = GldHeader(
            magic=GLD_MAGIC,
            data_04=2,
            data_06=1,
            total_size=1000,
            data_0c=500,
            data_10=0,
            pixel_data_size=400,
            palette_data_size=100,
            footer_entry_count=5,
        )
        encoded = header.to_bytes()
        assert len(encoded) == 32
        decoded = GldHeader.from_bytes(encoded)
        assert decoded.magic == GLD_MAGIC
        assert decoded.data_04 == 2
        assert decoded.data_06 == 1
        assert decoded.total_size == 1000
        assert decoded.pixel_data_size == 400
        assert decoded.palette_data_size == 100
        assert decoded.footer_entry_count == 5


# ----------------------------------------------------------------------
# GldFooterEntry
# ----------------------------------------------------------------------

class TestGldFooterEntry:
    def test_is_deleted_flag(self) -> None:
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_1 | SPRITE_DELETED,
            crop_width=8, crop_height=8,
            render_width_id=0, render_height_id=0,
            crop_x=0, crop_y=0,
        )
        assert entry.is_deleted is True

    def test_format_id_strips_flags(self) -> None:
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_3 | SPRITE_DELETED,
            crop_width=8, crop_height=8,
            render_width_id=0, render_height_id=0,
            crop_x=0, crop_y=0,
        )
        assert entry.format_id == SPRITE_FORMAT_3

    def test_max_palette_size_per_format(self) -> None:
        for fmt, expected in [(1, 32), (2, 4), (3, 16), (4, 256), (6, 8)]:
            entry = GldFooterEntry(
                pixels_offset=0, palette_offset=0,
                sprite_format=fmt,
                crop_width=8, crop_height=8,
                render_width_id=0, render_height_id=0,
                crop_x=0, crop_y=0,
            )
            assert entry.max_palette_size == expected


# ----------------------------------------------------------------------
# GldFile 完整往返（构造 + parse_gld/write_gld）
# ----------------------------------------------------------------------

class TestGldFileRoundTrip:
    def _build_minimal_gld(self) -> GldFile:
        """构造最小合法 GLD 文件（1 个 Type1 footer entry，8bpp Format 1）"""
        header = GldHeader(
            magic=GLD_MAGIC,
            data_04=2,
            data_06=FOOTER_TYPE_1,  # Type1 footer (24B/entry)
            total_size=0,  # 稍后计算
            data_0c=64,
            data_10=0,
            pixel_data_size=64,
            palette_data_size=64,  # 32 色 × 2 字节
            footer_entry_count=1,
        )
        # 64 字节像素数据（全 0）
        pixel_data = bytearray(b"\x00" * 64)
        # 调色板：32 色 × 2 字节 = 64 字节
        palette_data = bytearray(b"\x00" * 64)
        # Type1 footer entry = 24 字节
        footer_entry = GldFooterEntry(
            pixels_offset=0,
            palette_offset=0,
            sprite_format=SPRITE_FORMAT_1,
            crop_width=8,
            crop_height=8,
            render_width_id=0,
            render_height_id=0,
            crop_x=0,
            crop_y=0,
            join_x=0,
            join_y=0,
        )
        # 计算总大小：header(32) + pixel(64) + palette(64) + footer(24) = 184
        header.total_size = 184
        return GldFile(
            file_stem="test",
            header=header,
            pixel_data=pixel_data,
            palette_data=palette_data,
            footer_entries=[footer_entry],
        )

    def test_parse_and_write_round_trip(self, tmp_path: Path) -> None:
        gld = self._build_minimal_gld()
        gld_path = tmp_path / "test.GLD"
        write_gld(gld, gld_path)
        assert gld_path.exists()

        restored = parse_gld(gld_path)
        assert restored.header.magic == GLD_MAGIC
        assert restored.header.data_06 == FOOTER_TYPE_1
        assert restored.header.footer_entry_count == 1
        assert len(restored.footer_entries) == 1
        assert restored.footer_entries[0].sprite_format == SPRITE_FORMAT_1
        assert restored.footer_entries[0].crop_width == 8

    def test_byte_level_round_trip(self, tmp_path: Path) -> None:
        gld = self._build_minimal_gld()
        gld_path = tmp_path / "test2.GLD"
        write_gld(gld, gld_path)

        # 读取后再次写入应得到相同字节
        restored = parse_gld(gld_path)
        gld_path2 = tmp_path / "test2_restored.GLD"
        write_gld(restored, gld_path2)

        original_bytes = gld_path.read_bytes()
        restored_bytes = gld_path2.read_bytes()
        assert original_bytes == restored_bytes

    def test_invalid_magic_raises(self, tmp_path: Path) -> None:
        # 写入错误魔数
        bad_path = tmp_path / "bad.GLD"
        bad_path.write_bytes(b"XXXX" + b"\x00" * 28)
        with pytest.raises(ValueError):
            parse_gld(bad_path)


# ----------------------------------------------------------------------
# get_palettes / get_palette / render_width
# ----------------------------------------------------------------------

class TestGldFilePalettes:
    def _build_gld_two_palettes(self) -> GldFile:
        """构造含 2 个 sprite、不同 palette_offset 的 GLD。"""
        # 调色板区：16 字节 = 2 个调色板 × 4 色 × 2 字节
        palette_data = bytearray()
        # palette @0: 黑、红、绿、蓝
        palette_data += struct.pack('<4H', 0x0000, 0x001F, 0x03E0, 0x7C00)
        # palette @8: 白、黄、青、紫
        palette_data += struct.pack('<4H', 0x7FFF, 0x7FF, 0x7FE0, 0x7C1F)

        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=4, data_10=0,
            pixel_data_size=4, palette_data_size=len(palette_data),
            footer_entry_count=2,
        )
        entries = [
            GldFooterEntry(
                pixels_offset=0, palette_offset=0,
                sprite_format=SPRITE_FORMAT_4,  # 256 色 max，但实际 4 色
                crop_width=2, crop_height=1,
                render_width_id=0, render_height_id=0,
                crop_x=0, crop_y=0,
            ),
            GldFooterEntry(
                pixels_offset=2, palette_offset=8,
                sprite_format=SPRITE_FORMAT_4,
                crop_width=2, crop_height=1,
                render_width_id=0, render_height_id=0,
                crop_x=0, crop_y=0,
            ),
        ]
        gld = GldFile(
            file_stem="twin",
            header=header,
            pixel_data=bytearray(b"\x00\x01\x02\x03"),
            palette_data=palette_data,
            footer_entries=entries,
        )
        return gld

    def test_get_palettes_returns_dict_keyed_by_offset(self) -> None:
        gld = self._build_gld_two_palettes()
        pals = gld.get_palettes()
        assert set(pals.keys()) == {0, 8}
        assert pals[0] == [0x0000, 0x001F, 0x03E0, 0x7C00]
        assert pals[8] == [0x7FFF, 0x07FF, 0x7FE0, 0x7C1F]

    def test_get_palettes_all_deleted_returns_empty(self) -> None:
        gld = self._build_gld_two_palettes()
        for e in gld.footer_entries:
            e.sprite_format |= SPRITE_DELETED
        assert gld.get_palettes() == {}

    def test_get_palette_truncates_to_max_size(self) -> None:
        # Format 2 max=4，但调色板区可能有更多颜色
        gld = self._build_gld_two_palettes()
        # 改为 Format 2 (max 4 色)，palette_offset=0 实际有 4 色
        gld.footer_entries[0].sprite_format = SPRITE_FORMAT_2
        pal = gld.get_palette(gld.footer_entries[0])
        assert len(pal) == 4

    def test_get_palette_unknown_offset_returns_empty(self) -> None:
        gld = self._build_gld_two_palettes()
        # 设置一个不存在的 palette_offset
        gld.footer_entries[0].palette_offset = 999
        pal = gld.get_palette(gld.footer_entries[0])
        assert pal == []


class TestRenderWidth:
    def _make_entry(self, data_06: int, crop_x: int, crop_width: int,
                    render_width_id: int = 0) -> tuple[GldFile, GldFooterEntry]:
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=data_06,
            total_size=0, data_0c=0, data_10=0,
            pixel_data_size=0, palette_data_size=0,
            footer_entry_count=1,
        )
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_1,
            crop_width=crop_width, crop_height=1,
            render_width_id=render_width_id, render_height_id=0,
            crop_x=crop_x, crop_y=0,
        )
        gld = GldFile("t", header, bytearray(), bytearray(), [entry])
        return gld, entry

    def test_type1_next_power_of_two(self) -> None:
        # max_x=5 → next_pow2 = 8
        gld, e = self._make_entry(FOOTER_TYPE_1, crop_x=0, crop_width=5)
        assert gld.render_width(e) == 8
        # max_x=8 → next_pow2 = 8
        gld, e = self._make_entry(FOOTER_TYPE_1, crop_x=0, crop_width=8)
        assert gld.render_width(e) == 8
        # max_x=9 → next_pow2 = 16
        gld, e = self._make_entry(FOOTER_TYPE_1, crop_x=0, crop_width=9)
        assert gld.render_width(e) == 16

    def test_type1_max_x_zero_returns_one(self) -> None:
        # max_x = crop_x + crop_width = 0 → 返回 1
        gld, e = self._make_entry(FOOTER_TYPE_1, crop_x=0, crop_width=0)
        assert gld.render_width(e) == 1

    def test_type1_with_crop_x(self) -> None:
        # crop_x=3, crop_width=5 → max_x=8 → next_pow2=8
        gld, e = self._make_entry(FOOTER_TYPE_1, crop_x=3, crop_width=5)
        assert gld.render_width(e) == 8

    def test_type2_eight_shift(self) -> None:
        # Type2: 8 << render_width_id
        for rid, expected in [(0, 8), (1, 16), (2, 32), (3, 64)]:
            gld, e = self._make_entry(FOOTER_TYPE_2, crop_x=0, crop_width=4,
                                       render_width_id=rid)
            assert gld.render_width(e) == expected


# ----------------------------------------------------------------------
# extract_sprite_rgba：覆盖所有 format 分支
# ----------------------------------------------------------------------

class TestExtractSpriteRgba:
    @staticmethod
    def _build_format1_gld() -> GldFile:
        """Format 1 (8bpp, 32色, [color:5][alpha:3])。
        crop 2x2，render_width=2，2 字节像素 = 2 像素/行 × 2 行。
        """
        # 像素：[color:5][alpha:3]
        # pixel 0 = color 0, alpha 0 → 0x00
        # pixel 1 = color 1, alpha 7 (0xE0) → 0x01 | 0xE0 = 0xE1
        pixel_data = bytearray([0x00, 0xE1, 0x00, 0xE1])
        palette_data = bytearray(struct.pack('<4H', 0x0000, 0x001F, 0x03E0, 0x7C00))

        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=4, data_10=0,
            pixel_data_size=len(pixel_data), palette_data_size=len(palette_data),
            footer_entry_count=1,
        )
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_1,
            crop_width=2, crop_height=2,
            render_width_id=0, render_height_id=0,
            crop_x=0, crop_y=0,
        )
        return GldFile("f1", header, pixel_data, palette_data, [entry])

    def test_extract_format1(self) -> None:
        gld = self._build_format1_gld()
        w, h, rgba = gld.extract_sprite_rgba(0)
        assert (w, h) == (2, 2)
        assert len(rgba) == 2 * 2 * 4
        # pixel 0: color 0 = black, alpha 0 → (0,0,0,0)
        assert rgba[0:4] == bytes([0, 0, 0, 0])
        # pixel 1: color 1 = red (0x1F), alpha 7 → alpha = 0xE0|0x1C|0x03 = 0xFF
        # bgr555 0x001F → r=255, g=0, b=0
        assert rgba[4:8] == bytes([255, 0, 0, 0xFF])

    def test_extract_deleted_sprite_returns_transparent(self) -> None:
        gld = self._build_format1_gld()
        gld.footer_entries[0].sprite_format |= SPRITE_DELETED
        w, h, rgba = gld.extract_sprite_rgba(0)
        assert (w, h) == (2, 2)
        # 全透明
        assert rgba == bytes(2 * 2 * 4)

    @staticmethod
    def _build_format4_gld() -> GldFile:
        """Format 4 (8bpp, 256色, 纯索引)。"""
        pixel_data = bytearray([0, 1, 2, 3])
        palette_data = bytearray(struct.pack('<4H', 0x0000, 0x001F, 0x03E0, 0x7C00))
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=4, data_10=0,
            pixel_data_size=4, palette_data_size=len(palette_data),
            footer_entry_count=1,
        )
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_4,
            crop_width=2, crop_height=2,
            render_width_id=0, render_height_id=0,
            crop_x=0, crop_y=0,
        )
        return GldFile("f4", header, pixel_data, palette_data, [entry])

    def test_extract_format4(self) -> None:
        gld = self._build_format4_gld()
        w, h, rgba = gld.extract_sprite_rgba(0)
        # pixel 0 = color 0 = black, 不透明
        assert rgba[0:4] == bytes([0, 0, 0, 0xFF])
        # pixel 1 = color 1 = red
        assert rgba[4:8] == bytes([255, 0, 0, 0xFF])

    def test_extract_format4_transparent_palette(self) -> None:
        gld = self._build_format4_gld()
        # 把 palette[2] 设为透明 (bit15)
        struct.pack_into('<H', gld.palette_data, 4, 0x83E0)  # 透明绿
        w, h, rgba = gld.extract_sprite_rgba(0)
        # pixel 2 = color 2 = 透明绿 → alpha=0
        assert rgba[8:12] == bytes([0, 255, 0, 0])

    @staticmethod
    def _build_format3_gld() -> GldFile:
        """Format 3 (4bpp, 16色)。crop 4x1，render_width=4，4*4//8=2 字节。"""
        # 2 字节 = 4 像素：byte0=[p0,p1], byte1=[p2,p3]
        pixel_data = bytearray([0x10, 0x32])  # p0=0,p1=1,p2=2,p3=3
        palette_data = bytearray(struct.pack('<4H', 0x0000, 0x001F, 0x03E0, 0x7C00))
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=2, data_10=0,
            pixel_data_size=2, palette_data_size=len(palette_data),
            footer_entry_count=1,
        )
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_3,
            crop_width=4, crop_height=1,
            render_width_id=0, render_height_id=0,
            crop_x=0, crop_y=0,
        )
        return GldFile("f3", header, pixel_data, palette_data, [entry])

    def test_extract_format3_4bpp(self) -> None:
        gld = self._build_format3_gld()
        w, h, rgba = gld.extract_sprite_rgba(0)
        assert (w, h) == (4, 1)
        # p0=black, p1=red, p2=green, p3=blue
        assert rgba[0:4] == bytes([0, 0, 0, 0xFF])
        assert rgba[4:8] == bytes([255, 0, 0, 0xFF])
        assert rgba[8:12] == bytes([0, 255, 0, 0xFF])
        assert rgba[12:16] == bytes([0, 0, 255, 0xFF])

    @staticmethod
    def _build_format2_gld() -> GldFile:
        """Format 2 (2bpp, 4色)。crop 4x1，render_width=4，4*2//8=1 字节。"""
        # 1 字节 = 4 像素：[p0,p1,p2,p3] = [0,1,2,3] = 0xE4
        pixel_data = bytearray([0xE4])
        palette_data = bytearray(struct.pack('<4H', 0x0000, 0x001F, 0x03E0, 0x7C00))
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=1, data_10=0,
            pixel_data_size=1, palette_data_size=len(palette_data),
            footer_entry_count=1,
        )
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_2,
            crop_width=4, crop_height=1,
            render_width_id=0, render_height_id=0,
            crop_x=0, crop_y=0,
        )
        return GldFile("f2", header, pixel_data, palette_data, [entry])

    def test_extract_format2_2bpp(self) -> None:
        gld = self._build_format2_gld()
        w, h, rgba = gld.extract_sprite_rgba(0)
        assert (w, h) == (4, 1)
        assert rgba[0:4] == bytes([0, 0, 0, 0xFF])
        assert rgba[4:8] == bytes([255, 0, 0, 0xFF])
        assert rgba[8:12] == bytes([0, 255, 0, 0xFF])
        assert rgba[12:16] == bytes([0, 0, 255, 0xFF])

    @staticmethod
    def _build_format6_gld() -> GldFile:
        """Format 6 (8bpp, 8色, [color:3][alpha:5])。"""
        # pixel 0 = color 0, alpha 0 → 0x00
        # pixel 1 = color 1, alpha 31 (0xF8) → 0x01 | 0xF8 = 0xF9
        pixel_data = bytearray([0x00, 0xF9])
        palette_data = bytearray(struct.pack('<4H', 0x0000, 0x001F, 0x03E0, 0x7C00))
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=2, data_10=0,
            pixel_data_size=2, palette_data_size=len(palette_data),
            footer_entry_count=1,
        )
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_6,
            crop_width=2, crop_height=1,
            render_width_id=0, render_height_id=0,
            crop_x=0, crop_y=0,
        )
        return GldFile("f6", header, pixel_data, palette_data, [entry])

    def test_extract_format6(self) -> None:
        gld = self._build_format6_gld()
        w, h, rgba = gld.extract_sprite_rgba(0)
        # pixel 0: color 0 = black, alpha 0
        assert rgba[0:4] == bytes([0, 0, 0, 0])
        # pixel 1: color 1 = red, alpha 31 → alpha = 31<<3 | 31>>2 = 0xF8 | 0x07 = 0xFF
        assert rgba[4:8] == bytes([255, 0, 0, 0xFF])

    def test_extract_pixel_beyond_data_defaults_zero(self) -> None:
        """像素索引超出 pixels 数组时返回 0（黑色不透明或按 format 处理）。"""
        gld = self._build_format4_gld()
        # 扩大 crop 使其超出 pixel_data
        gld.footer_entries[0].crop_width = 4
        # render_width = next_pow2(4) = 4, render_byte_count = 4*2 = 8
        # 但 pixel_data 只有 4 字节，超出部分 pixel_val=0
        w, h, rgba = gld.extract_sprite_rgba(0)
        assert (w, h) == (4, 2)
        # 第 5-8 像素应为 color 0 = black
        assert rgba[16:20] == bytes([0, 0, 0, 0xFF])

    def test_extract_palette_index_out_of_range(self) -> None:
        """pixel_val 超出 palette 长度时返回 0,0,0,0。"""
        gld = self._build_format4_gld()
        # palette 只有 4 色，但 pixel 值设为 5
        gld.pixel_data = bytearray([5, 0, 0, 0])
        w, h, rgba = gld.extract_sprite_rgba(0)
        # pixel 0 = color 5 超出 → (0,0,0,0)
        assert rgba[0:4] == bytes([0, 0, 0, 0])


# ----------------------------------------------------------------------
# inject_sprite_rgba：覆盖所有 format 分支 + 2bpp/4bpp 非对齐 RMW
# ----------------------------------------------------------------------

class TestInjectSpriteRgba:
    def test_inject_deleted_sprite_noop(self) -> None:
        gld = TestExtractSpriteRgba._build_format4_gld()
        gld.footer_entries[0].sprite_format |= SPRITE_DELETED
        original = bytes(gld.pixel_data)
        gld.inject_sprite_rgba(0, bytes(2 * 2 * 4), 2, 2)
        # pixel_data 应不变
        assert bytes(gld.pixel_data) == original

    def test_inject_size_mismatch_raises(self) -> None:
        gld = TestExtractSpriteRgba._build_format4_gld()
        with pytest.raises(ValueError, match="尺寸不匹配"):
            gld.inject_sprite_rgba(0, bytes(100), 3, 3)

    def test_inject_format4_8bpp(self) -> None:
        gld = TestExtractSpriteRgba._build_format4_gld()
        # 注入全红 RGBA
        rgba = bytes([255, 0, 0, 0xFF] * (2 * 2))
        gld.inject_sprite_rgba(0, rgba, 2, 2)
        # pixel 0..3 应都匹配红色 = color 1
        assert list(gld.pixel_data[:4]) == [1, 1, 1, 1]

    def test_inject_format1_8bpp_with_alpha(self) -> None:
        gld = TestExtractSpriteRgba._build_format1_gld()
        # 注入半透明红色：alpha=128 → alpha_bank = 128>>5 = 4
        rgba = bytes([255, 0, 0, 128] * (2 * 2))
        gld.inject_sprite_rgba(0, rgba, 2, 2)
        # 期望像素 = color 1 (red) | (4 << 5) = 0x01 | 0x80 = 0x81
        assert list(gld.pixel_data[:4]) == [0x81, 0x81, 0x81, 0x81]

    def test_inject_format6_8bpp_with_alpha(self) -> None:
        gld = TestExtractSpriteRgba._build_format6_gld()
        # 注入红色 alpha=248 → alpha_bank = 248>>3 = 31
        # crop 2x1 = 2 像素，需 8 字节 RGBA
        rgba = bytes([255, 0, 0, 248] * 2)
        gld.inject_sprite_rgba(0, rgba, 2, 1)
        # 期望像素 = color 1 | (31 << 3) = 0x01 | 0xF8 = 0xF9
        assert list(gld.pixel_data[:2]) == [0xF9, 0xF9]

    def test_inject_format3_4bpp_aligned(self) -> None:
        """4bpp 对齐 crop_x=0：直接修改 crop 区域。"""
        gld = TestExtractSpriteRgba._build_format3_gld()
        # 原像素 [0,1,2,3]，注入全红 (color 1)
        rgba = bytes([255, 0, 0, 0xFF] * 4)
        gld.inject_sprite_rgba(0, rgba, 4, 1)
        # 重新提取验证
        w, h, rgba2 = gld.extract_sprite_rgba(0)
        for i in range(4):
            assert rgba2[i * 4:i * 4 + 4] == bytes([255, 0, 0, 0xFF])

    def test_inject_format3_4bpp_unaligned_crop_x(self) -> None:
        """4bpp 非对齐 crop_x：触发 read-modify-write 路径（关键 bug fix）。
        验证 crop 区域外的 nibble 被保留。
        """
        # 构造：render_width=4 (4 像素), crop_x=1, crop_width=2
        # 原像素 [0,1,2,3]，修改 [1,2] 为 color 3
        # 修改后期望像素 [0,3,3,3]（注意 crop 区域外 pixel 0 和 pixel 3 保留）
        # 但 pixel 3 在 crop 外（crop_x+crop_width=3，pixel 3 不在 crop 内）
        # 实际：crop_x=1, crop_width=2 → 修改 pixel[1], pixel[2]
        # 期望像素 [0,3,3,3]
        pixel_data = bytearray([0x10, 0x32])  # [0,1,2,3]
        palette_data = bytearray(struct.pack('<4H', 0x0000, 0x001F, 0x03E0, 0x7C00))
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=2, data_10=0,
            pixel_data_size=2, palette_data_size=len(palette_data),
            footer_entry_count=1,
        )
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_3,
            crop_width=2, crop_height=1,
            render_width_id=0, render_height_id=0,
            crop_x=1, crop_y=0,
        )
        gld = GldFile("f3u", header, pixel_data, palette_data, [entry])

        # 注入蓝色 (color 3) 到 crop 区域 (2 像素)
        rgba = bytes([0, 0, 255, 0xFF] * 2)
        gld.inject_sprite_rgba(0, rgba, 2, 1)

        # 验证：pixel[0]=0 (保留), pixel[1]=3 (蓝), pixel[2]=3 (蓝), pixel[3]=3 (保留)
        # 打包：byte0 = 0 | (3<<4) = 0x30, byte1 = 3 | (3<<4) = 0x33
        assert list(gld.pixel_data[:2]) == [0x30, 0x33]

    def test_inject_format2_2bpp_unaligned_crop_x(self) -> None:
        """2bpp 非对齐 crop_x：触发 RMW 路径。"""
        # render_width=4 (1 字节), crop_x=1, crop_width=2
        # 原像素 [0,1,2,3]，修改 [1,2] 为 color 0
        # 期望像素 [0,0,0,3]
        pixel_data = bytearray([0xE4])  # [0,1,2,3]
        palette_data = bytearray(struct.pack('<4H', 0x0000, 0x001F, 0x03E0, 0x7C00))
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=1, data_10=0,
            pixel_data_size=1, palette_data_size=len(palette_data),
            footer_entry_count=1,
        )
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_2,
            crop_width=2, crop_height=1,
            render_width_id=0, render_height_id=0,
            crop_x=1, crop_y=0,
        )
        gld = GldFile("f2u", header, pixel_data, palette_data, [entry])

        # 注入黑色 (color 0) 到 crop 区域
        rgba = bytes([0, 0, 0, 0xFF] * 2)
        gld.inject_sprite_rgba(0, rgba, 2, 1)

        # 期望像素 [0,0,0,3] → 0b11_00_00_00 = 0xC0
        assert list(gld.pixel_data[:1]) == [0xC0]

    def test_inject_then_extract_round_trip(self) -> None:
        """注入后提取应得到注入的 RGBA（允许调色板量化误差）。"""
        gld = TestExtractSpriteRgba._build_format4_gld()
        # 注入红/绿/蓝/黑
        rgba_in = bytes([
            255, 0, 0, 0xFF,
            0, 255, 0, 0xFF,
            0, 0, 255, 0xFF,
            0, 0, 0, 0xFF,
        ])
        gld.inject_sprite_rgba(0, rgba_in, 2, 2)
        w, h, rgba_out = gld.extract_sprite_rgba(0)
        assert rgba_out == rgba_in


# ----------------------------------------------------------------------
# Type2 footer 序列化与解析
# ----------------------------------------------------------------------

class TestType2Footer:
    def test_type2_footer_round_trip(self, tmp_path: Path) -> None:
        # Type2 footer = 28 字节/entry
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_2,
            total_size=0, data_0c=4, data_10=0,
            pixel_data_size=4, palette_data_size=8,
            footer_entry_count=1,
        )
        entry = GldFooterEntry(
            pixels_offset=0, palette_offset=0,
            sprite_format=SPRITE_FORMAT_1,
            crop_width=2, crop_height=2,
            render_width_id=1, render_height_id=0,
            crop_x=0, crop_y=0,
            data_14=0xDEADBEEF,  # Type2 专有字段
            join_x=-1, join_y=2,
        )
        gld = GldFile("t2", header, bytearray(b"\x00" * 4),
                       bytearray(b"\x00" * 8), [entry])

        gld_path = tmp_path / "type2.GLD"
        write_gld(gld, gld_path)
        restored = parse_gld(gld_path)

        assert restored.header.data_06 == FOOTER_TYPE_2
        assert len(restored.footer_entries) == 1
        e = restored.footer_entries[0]
        assert e.render_width_id == 1
        assert e.data_14 == 0xDEADBEEF
        assert e.join_x == -1
        assert e.join_y == 2


# ----------------------------------------------------------------------
# parse_gld 错误处理
# ----------------------------------------------------------------------

class TestParseGldErrors:
    def test_parse_too_small_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "small.GLD"
        bad.write_bytes(b"\x00DLG" + b"\x00" * 10)  # 14 字节 < 32
        with pytest.raises(ValueError, match="太小"):
            parse_gld(bad)

    def test_parse_bad_data_04_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad04.GLD"
        # magic 正确，data_04=3 (错误)，补齐到 32 字节
        # GLD_MAGIC(4) + HH(4) + 24 = 32
        header = GLD_MAGIC + struct.pack('<HH', 3, 1) + b"\x00" * 24
        assert len(header) == 32
        bad.write_bytes(header)
        with pytest.raises(ValueError, match="data_04"):
            parse_gld(bad)

    def test_parse_truncated_footer(self, tmp_path: Path) -> None:
        """footer_entry_count 声明 2 但数据只够 1 个 entry → break 跳过。"""
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=0, data_10=0,
            pixel_data_size=0, palette_data_size=0,
            footer_entry_count=2,  # 声明 2 个
        )
        # 只写 1 个 entry (24 字节)
        data = bytearray(header.to_bytes())
        data += struct.pack('<IHHHHHHHHhh', 0, 0, 1, 8, 8, 0, 0, 0, 0, 0, 0)
        bad = tmp_path / "trunc.GLD"
        bad.write_bytes(data)
        # 不应抛错，只是少读一个 entry
        restored = parse_gld(bad)
        assert len(restored.footer_entries) == 1
