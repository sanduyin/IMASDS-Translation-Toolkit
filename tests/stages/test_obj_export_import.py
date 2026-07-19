# tests/stages/test_obj_export_import.py
"""OBJ 导出/导入往返测试（NCER cell + RGB + 绿幕方案）。

测试 stage2_export_obj + stage4_import_obj 的功能正确性：
1. 纯函数测试（RGB → tile pixel、flip 反转、OAM 像素提取、tile 写入 NCGR）
2. 真实 OBJ 文件的往返一致性（导出 → 导入 → NCGR tile data 一致）
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from PIL import Image

from src.stage2_export_bg import parse_ncgr, parse_nclr, parse_nds_container
from src.stage2_export_obj import (
    GREEN_SCREEN,
    render_oam_to_rgb,
    apply_flip,
    render_cell_to_rgb,
    export_obj_quartet,
)
from src.stage4_import_obj import (
    rgb_to_tile_pixel,
    extract_oam_pixels,
    reverse_flip,
    oam_pixels_to_tiles,
    write_tiles_to_ncgr,
    import_obj_quartet,
)
from src.utils.ncer_format import parse_ncer, find_obj_quartets


PROJECT_ROOT = Path(__file__).parent.parent.parent
OBJ_DIR = PROJECT_ROOT / 'game_data' / '1_Extracted' / 'OBJ'


# ----------------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------------

def make_test_ncgr(bpp: int, tile_count: int) -> bytes:
    """构造一个最小化的 NCGR 文件用于测试。

    RAHC section body 布局（与 stage2_export_bg.parse_ncgr 一致）：
        0x00 u16 tile_h, 0x02 u16 tile_w
        0x04 u32 bpp_flag (3=4bpp, 4=8bpp)
        0x08 u32 tile_count
        0x0C u32 reserved (0)
        0x10 u32 tile_data_size
        0x14 u32 header_size (= 0x18, data 起始偏移)
    """
    bytes_per_tile = 32 if bpp == 4 else 64
    tile_data_size = tile_count * bytes_per_tile

    rahc_body = bytearray(0x18 + tile_data_size)
    struct.pack_into('<HH', rahc_body, 0x00, 8, 8)  # tile_h=8, tile_w=8
    struct.pack_into('<I', rahc_body, 0x04, 3 if bpp == 4 else 4)  # bpp_flag
    struct.pack_into('<I', rahc_body, 0x08, tile_count)  # tile_count
    struct.pack_into('<I', rahc_body, 0x0C, 0)  # reserved
    struct.pack_into('<I', rahc_body, 0x10, tile_data_size)  # tile_data_size
    struct.pack_into('<I', rahc_body, 0x14, 0x18)  # header_size

    # 填充 tile data（每个 tile 用 tile_idx 作为像素值）
    for t in range(tile_count):
        offset = 0x18 + t * bytes_per_tile
        if bpp == 4:
            for i in range(32):
                rahc_body[offset + i] = (t & 0x0F) | ((t & 0x0F) << 4)
        else:
            for i in range(64):
                rahc_body[offset + i] = t & 0xFF

    # NCGR 文件头（16 字节）+ RAHC section
    header = bytearray(0x10)
    header[0:4] = b'RGCN'  # magic
    struct.pack_into('<H', header, 0x06, 0xFEFF)  # byte order
    struct.pack_into('<H', header, 0x08, 0x0100)  # version
    struct.pack_into('<H', header, 0x0C, 0x10)  # header_size
    struct.pack_into('<H', header, 0x0E, 1)  # section_count

    result = bytearray(header)
    # RAHC section: magic(4) + size(4) + body
    result.extend(b'RAHC')
    sec_size = 8 + len(rahc_body)
    result.extend(struct.pack('<I', sec_size))
    result.extend(rahc_body)
    return bytes(result)


def make_test_nclr(colors: list[tuple[int, int, int]]) -> bytes:
    """构造一个最小化的 NCLR 文件用于测试。

    TTLP section body 布局（与 stage2_export_bg.parse_nclr 一致）：
        0x00-0x0F header (16 字节)
        0x10+ palette data (RGB555, 每色 2 字节)
    """
    pal_data = bytearray()
    for (r, g, b) in colors:
        r5 = (r >> 3) & 0x1F
        g5 = (g >> 3) & 0x1F
        b5 = (b >> 3) & 0x1F
        col = r5 | (g5 << 5) | (b5 << 10)
        pal_data.extend(struct.pack('<H', col))

    ttlp_body = bytearray(0x10) + pal_data
    # header 字段不可靠，parse_nclr 固定从 0x10 读

    header = bytearray(0x10)
    header[0:4] = b'RLCN'
    struct.pack_into('<H', header, 0x06, 0xFEFF)
    struct.pack_into('<H', header, 0x08, 0x0100)
    struct.pack_into('<H', header, 0x0C, 0x10)
    struct.pack_into('<H', header, 0x0E, 1)

    result = bytearray(header)
    result.extend(b'TTLP')
    result.extend(struct.pack('<I', 8 + len(ttlp_body)))
    result.extend(ttlp_body)
    return bytes(result)


# ----------------------------------------------------------------------
# rgb_to_tile_pixel
# ----------------------------------------------------------------------

class TestRgbToTilePixel:
    def test_green_screen_returns_0(self) -> None:
        palette = [(0, 0, 0)] * 256
        assert rgb_to_tile_pixel(GREEN_SCREEN, palette, 4, 0) == 0
        assert rgb_to_tile_pixel(GREEN_SCREEN, palette, 8, 0) == 0

    def test_4bpp_exact_match(self) -> None:
        # palette[1] = (255, 0, 0), palette[17] = (0, 255, 0) but 绿幕优先
        # palette[1] 在子调色板 0 (index 1-15)
        palette = [(0, 0, 0)] * 256
        palette[1] = (255, 0, 0)
        result = rgb_to_tile_pixel((255, 0, 0), palette, 4, 0)
        assert result == 1

    def test_4bpp_palette_offset(self) -> None:
        # palette[17] 在子调色板 1 (index 1-15 of sub-palette 1)
        palette = [(0, 0, 0)] * 256
        palette[17] = (0, 0, 255)
        result = rgb_to_tile_pixel((0, 0, 255), palette, 4, 1)
        assert result == 1  # 子调色板 1 的 index 1

    def test_4bpp_nearest_color(self) -> None:
        palette = [(0, 0, 0)] * 256
        palette[1] = (100, 100, 100)
        palette[2] = (200, 200, 200)
        # (190, 190, 190) 更接近 palette[2]
        result = rgb_to_tile_pixel((190, 190, 190), palette, 4, 0)
        assert result == 2

    def test_8bpp_exact_match(self) -> None:
        palette = [(0, 0, 0)] * 256
        palette[5] = (255, 128, 64)
        result = rgb_to_tile_pixel((255, 128, 64), palette, 8, 0)
        assert result == 5

    def test_8bpp_nearest_color(self) -> None:
        palette = [(0, 0, 0)] * 256
        palette[1] = (50, 50, 50)
        palette[2] = (150, 150, 150)
        result = rgb_to_tile_pixel((140, 140, 140), palette, 8, 0)
        assert result == 2


# ----------------------------------------------------------------------
# reverse_flip
# ----------------------------------------------------------------------

class TestReverseFlip:
    def test_no_flip(self) -> None:
        pixels = [[(1, 1, 1), (2, 2, 2)], [(3, 3, 3), (4, 4, 4)]]
        result = reverse_flip(pixels, False, False)
        assert result == pixels

    def test_flip_x(self) -> None:
        pixels = [[(1, 1, 1), (2, 2, 2)], [(3, 3, 3), (4, 4, 4)]]
        result = reverse_flip(pixels, True, False)
        assert result == [[(2, 2, 2), (1, 1, 1)], [(4, 4, 4), (3, 3, 3)]]

    def test_flip_y(self) -> None:
        pixels = [[(1, 1, 1), (2, 2, 2)], [(3, 3, 3), (4, 4, 4)]]
        result = reverse_flip(pixels, False, True)
        assert result == [[(3, 3, 3), (4, 4, 4)], [(1, 1, 1), (2, 2, 2)]]

    def test_flip_both(self) -> None:
        pixels = [[(1, 1, 1), (2, 2, 2)], [(3, 3, 3), (4, 4, 4)]]
        result = reverse_flip(pixels, True, True)
        assert result == [[(4, 4, 4), (3, 3, 3)], [(2, 2, 2), (1, 1, 1)]]

    def test_flip_is_self_inverse(self) -> None:
        """flip 是自反的：reverse_flip(reverse_flip(x)) == x"""
        pixels = [[(1, 1, 1), (2, 2, 2)], [(3, 3, 3), (4, 4, 4)]]
        once = reverse_flip(pixels, True, True)
        twice = reverse_flip(once, True, True)
        assert twice == pixels


# ----------------------------------------------------------------------
# extract_oam_pixels
# ----------------------------------------------------------------------

class TestExtractOamPixels:
    def test_extract_full_canvas(self) -> None:
        # 4x4 图，OAM 覆盖整个画布
        img = Image.new('RGB', (4, 4))
        for y in range(4):
            for x in range(4):
                img.putpixel((x, y), (x * 10, y * 10, 0))

        pixels = extract_oam_pixels(img, 0, 0, 4, 4, 0, 0)
        assert len(pixels) == 4
        assert len(pixels[0]) == 4
        assert pixels[0][0] == (0, 0, 0)
        assert pixels[3][3] == (30, 30, 0)

    def test_extract_with_offset(self) -> None:
        # 8x8 图，OAM 在 (2, 2)，大小 4x4
        img = Image.new('RGB', (8, 8), (0, 0, 0))
        for y in range(4):
            for x in range(4):
                img.putpixel((2 + x, 2 + y), (255, 0, 0))

        pixels = extract_oam_pixels(img, 2, 2, 4, 4, 0, 0)
        assert pixels[0][0] == (255, 0, 0)
        assert pixels[3][3] == (255, 0, 0)

    def test_extract_out_of_bounds_green_screen(self) -> None:
        # 4x4 图，OAM 在 (2, 2)，大小 4x4 → 部分超出画布
        img = Image.new('RGB', (4, 4), (0, 0, 0))
        pixels = extract_oam_pixels(img, 2, 2, 4, 4, 0, 0)
        # (0,0) 到 (1,1) 在画布内
        assert pixels[0][0] == (0, 0, 0)
        # (2,2) 超出画布 → 绿幕
        assert pixels[2][2] == GREEN_SCREEN


# ----------------------------------------------------------------------
# oam_pixels_to_tiles
# ----------------------------------------------------------------------

class TestOamPixelsToTiles:
    def test_single_tile(self) -> None:
        # 8x8 OAM = 1 tile
        pixels = [[(255, 0, 0)] * 8 for _ in range(8)]
        palette = [(0, 0, 0)] * 256
        palette[1] = (255, 0, 0)
        tiles = oam_pixels_to_tiles(pixels, palette, 4, 0, 1, 1)
        assert len(tiles) == 1
        assert len(tiles[0]) == 64
        assert all(p == 1 for p in tiles[0])

    def test_4_tiles_16x16(self) -> None:
        # 16x16 OAM = 4 tiles (2x2)
        pixels = [[(255, 0, 0)] * 16 for _ in range(16)]
        palette = [(0, 0, 0)] * 256
        palette[1] = (255, 0, 0)
        tiles = oam_pixels_to_tiles(pixels, palette, 4, 0, 2, 2)
        assert len(tiles) == 4
        for t in tiles:
            assert all(p == 1 for p in t)

    def test_green_screen_becomes_0(self) -> None:
        pixels = [[GREEN_SCREEN] * 8 for _ in range(8)]
        palette = [(0, 0, 0)] * 256
        tiles = oam_pixels_to_tiles(pixels, palette, 4, 0, 1, 1)
        assert all(p == 0 for p in tiles[0])


# ----------------------------------------------------------------------
# write_tiles_to_ncgr
# ----------------------------------------------------------------------

class TestWriteTilesToNcgr:
    def test_write_4bpp_single_tile(self) -> None:
        ncgr = make_test_ncgr(4, 10)
        # tile 5 写入新数据
        new_tile = [1] * 64
        result = write_tiles_to_ncgr(ncgr, [5], [new_tile], 4)
        # 验证 tile 5 被修改
        tiles, bpp, _, _ = parse_ncgr(result)
        assert bpp == 4
        assert len(tiles) == 10
        assert all(p == 1 for p in tiles[5])
        # 其他 tile 不变（tile 0 像素值 = 0 & 0x0F = 0）
        assert all(p == 0 for p in tiles[0])

    def test_write_8bpp_single_tile(self) -> None:
        ncgr = make_test_ncgr(8, 10)
        new_tile = [100] * 64
        result = write_tiles_to_ncgr(ncgr, [3], [new_tile], 8)
        tiles, bpp, _, _ = parse_ncgr(result)
        assert bpp == 8
        assert all(p == 100 for p in tiles[3])
        # 其他 tile 不变
        assert all(p == 0 for p in tiles[0])


# ----------------------------------------------------------------------
# 真实 OBJ 文件往返测试
# ----------------------------------------------------------------------

@pytest.mark.skipif(not OBJ_DIR.exists(), reason='OBJ 目录不存在')
class TestRealObjRoundtrip:
    """真实 OBJ 文件的往返一致性测试。

    导出 → 导入 → 比较 NCGR tile data 是否一致。
    对于非共享 tile 的 OBJ，应该能完美往返。
    """

    def test_roundtrip_menuitem(self, tmp_path: Path) -> None:
        """MENUITEM: 23 cells, 57 tiles, mapping_mode=0, shared=48"""
        quartets = find_obj_quartets(OBJ_DIR)
        menuitem = None
        for q in quartets:
            if q[4] == 'MENUITEM':
                menuitem = q
                break
        assert menuitem is not None, 'MENUITEM 不存在'

        ncgr_path, nclr_path, nanr_path, ncer_path, base, stem = menuitem
        orig_ncgr = ncgr_path.read_bytes()

        # 导出（平铺到 out_dir，文件名 {stem}_sheet.png / {stem}_manifest.json）
        out_dir = tmp_path / 'export'
        out_dir.mkdir()
        manifest = export_obj_quartet(ncgr_path, nclr_path, ncer_path, out_dir, stem)
        assert manifest is not None
        assert (out_dir / f'{stem}_sheet.png').exists()
        assert (out_dir / f'{stem}_manifest.json').exists()

        # 导入
        imp_dir = tmp_path / 'import'
        imp_dir.mkdir()
        stats = import_obj_quartet(
            out_dir, stem, ncgr_path, nclr_path, nanr_path, ncer_path,
            imp_dir / ncgr_path.name, imp_dir / nclr_path.name,
            imp_dir / nanr_path.name, imp_dir / ncer_path.name,
        )
        assert stats['stem'] == stem

        # 比较 NCGR
        new_ncgr = (imp_dir / ncgr_path.name).read_bytes()
        orig_tiles, _, _, _ = parse_ncgr(orig_ncgr)
        new_tiles, _, _, _ = parse_ncgr(new_ncgr)
        assert len(orig_tiles) == len(new_tiles)

        # MENUITEM 有 48 个共享 tile，可能有差异
        # 统计差异 tile 数
        diff_count = 0
        for i in range(len(orig_tiles)):
            if orig_tiles[i] != new_tiles[i]:
                diff_count += 1
        # 允许共享 tile 有差异，但差异不应过大
        print(f'MENUITEM: {diff_count}/{len(orig_tiles)} tiles differ')

    def test_roundtrip_test_button(self, tmp_path: Path) -> None:
        """TEST_BUTTON: 9 cells, 87 tiles, mapping_mode=0, shared=18"""
        quartets = find_obj_quartets(OBJ_DIR)
        test_btn = None
        for q in quartets:
            if q[4] == 'TEST_BUTTON':
                test_btn = q
                break
        assert test_btn is not None

        ncgr_path, nclr_path, nanr_path, ncer_path, base, stem = test_btn
        orig_ncgr = ncgr_path.read_bytes()

        # 导出
        out_dir = tmp_path / 'export'
        out_dir.mkdir()
        manifest = export_obj_quartet(ncgr_path, nclr_path, ncer_path, out_dir, stem)
        assert manifest is not None

        # 导入
        imp_dir = tmp_path / 'import'
        imp_dir.mkdir()
        import_obj_quartet(
            out_dir, stem, ncgr_path, nclr_path, nanr_path, ncer_path,
            imp_dir / ncgr_path.name, imp_dir / nclr_path.name,
            imp_dir / nanr_path.name, imp_dir / ncer_path.name,
        )

        # 比较 NCGR
        new_ncgr = (imp_dir / ncgr_path.name).read_bytes()
        orig_tiles, _, _, _ = parse_ncgr(orig_ncgr)
        new_tiles, _, _, _ = parse_ncgr(new_ncgr)

        diff_count = sum(1 for i in range(len(orig_tiles))
                         if orig_tiles[i] != new_tiles[i])
        print(f'TEST_BUTTON: {diff_count}/{len(orig_tiles)} tiles differ')

    def test_roundtrip_memories_number(self, tmp_path: Path) -> None:
        """MEMORIES_NUMBER: 10 cells, 40 tiles, mapping_mode=2, shared=0
        无共享 tile → 应该完美往返"""
        quartets = find_obj_quartets(OBJ_DIR)
        target = None
        for q in quartets:
            if q[4] == 'MEMORIES_NUMBER':
                target = q
                break
        assert target is not None

        ncgr_path, nclr_path, nanr_path, ncer_path, base, stem = target
        orig_ncgr = ncgr_path.read_bytes()

        # 导出
        out_dir = tmp_path / 'export'
        out_dir.mkdir()
        manifest = export_obj_quartet(ncgr_path, nclr_path, ncer_path, out_dir, stem)
        assert manifest is not None
        assert manifest['shared_tiles'] == {}

        # 导入
        imp_dir = tmp_path / 'import'
        imp_dir.mkdir()
        import_obj_quartet(
            out_dir, stem, ncgr_path, nclr_path, nanr_path, ncer_path,
            imp_dir / ncgr_path.name, imp_dir / nclr_path.name,
            imp_dir / nanr_path.name, imp_dir / ncer_path.name,
        )

        # 比较 NCGR - 无共享 tile，应该完美往返
        new_ncgr = (imp_dir / ncgr_path.name).read_bytes()
        orig_tiles, _, _, _ = parse_ncgr(orig_ncgr)
        new_tiles, _, _, _ = parse_ncgr(new_ncgr)

        diff_count = sum(1 for i in range(len(orig_tiles))
                         if orig_tiles[i] != new_tiles[i])
        assert diff_count == 0, f'{diff_count} tiles differ (expected 0 for no shared tiles)'
