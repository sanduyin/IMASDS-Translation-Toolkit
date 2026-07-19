# tests/utils/test_ncer_format.py
"""ncer_format 模块单元测试。

测试 OAM 解码、tile 索引计算、bbox 计算、4 件套配对、共享 tile 检测。
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from src.utils.ncer_format import (
    OAM_SIZE_TABLE,
    OamEntry,
    Cell,
    decode_oam,
    parse_ncer,
    find_obj_quartets,
    compute_shared_tiles,
)


# ----------------------------------------------------------------------
# decode_oam
# ----------------------------------------------------------------------

class TestDecodeOam:
    def test_square_8x8_no_flip(self) -> None:
        # a0: shape=0(Square), size=0, color_mode=0(4bpp), y=0
        # a1: x=0, size=0, no flip
        # a2: char_name=0, palette=0, priority=0
        a0 = 0x0000  # y=0, no flags
        a1 = 0x0000  # x=0, no flip, size=0
        a2 = 0x0000  # char_name=0, palette=0
        oam = decode_oam(a0, a1, a2)
        assert oam.width == 8 and oam.height == 8
        assert oam.shape == 0 and oam.size == 0
        assert oam.flip_x is False and oam.flip_y is False
        assert oam.color_mode == 0
        assert oam.char_name == 0
        assert oam.palette == 0
        assert oam.disabled is False

    def test_square_16x16(self) -> None:
        # size=1 → 16x16
        a0 = 0x0000
        a1 = 0x4000  # size=1
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.width == 16 and oam.height == 16
        assert oam.size == 1

    def test_wide_32x16(self) -> None:
        # shape=1(Wide), size=2 → 32x16
        a0 = 0x4000  # shape=1
        a1 = 0x8000  # size=2
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.width == 32 and oam.height == 16
        assert oam.shape == 1

    def test_tall_16x32(self) -> None:
        # shape=2(Tall), size=2 → 16x32
        a0 = 0x8000  # shape=2
        a1 = 0x8000  # size=2
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.width == 16 and oam.height == 32
        assert oam.shape == 2

    def test_8bpp_color_mode(self) -> None:
        # color_mode=1 (8bpp)
        a0 = 0x2000  # bit 13 = 1
        a1 = 0x0000
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.color_mode == 1

    def test_flip_x(self) -> None:
        a0 = 0x0000
        a1 = 0x1000  # bit 12 = flip_x
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.flip_x is True
        assert oam.flip_y is False

    def test_flip_y(self) -> None:
        a0 = 0x0000
        a1 = 0x2000  # bit 13 = flip_y
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.flip_x is False
        assert oam.flip_y is True

    def test_negative_x(self) -> None:
        # x is 9-bit signed: 0x1FF = -1, 0x100 = -256
        a0 = 0x0000
        a1 = 0x01FF  # x = 0x1FF = 511 → -1
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.x == -1

    def test_x_256_becomes_negative_256(self) -> None:
        a0 = 0x0000
        a1 = 0x0100  # x = 256 → -256
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.x == -256

    def test_char_name_and_palette(self) -> None:
        # char_name = 0x3FF (max 10 bits), palette = 0xF (max 4 bits)
        a0 = 0x0000
        a1 = 0x0000
        a2 = 0x3FF | (0xF << 12)  # char_name=0x3FF, palette=0xF
        oam = decode_oam(a0, a1, a2)
        assert oam.char_name == 0x3FF
        assert oam.palette == 0xF

    def test_disabled_flag(self) -> None:
        # bit 9 (double_size/disabled) without affine → disabled
        a0 = 0x0200  # bit 9 = 1
        a1 = 0x0000
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.disabled is True

    def test_affine_flag(self) -> None:
        a0 = 0x0100  # bit 8 = affine
        a1 = 0x0000
        a2 = 0x0000
        oam = decode_oam(a0, a1, a2)
        assert oam.affine is True


# ----------------------------------------------------------------------
# OamEntry.tile_indices
# ----------------------------------------------------------------------

class TestTileIndices:
    def test_4bpp_1d_32k_single_tile(self) -> None:
        # 4bpp, mapping_mode=0 (1D_32K), char_name=0 → base = 32*0//32 = 0
        oam = OamEntry(
            x=0, y=0, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        assert oam.tile_indices(100, mapping_mode=0) == [0]

    def test_4bpp_1d_64k_multiple_tiles(self) -> None:
        # 4bpp, mapping_mode=1 (1D_64K), char_name=1 → base = 64*1//32 = 2
        oam = OamEntry(
            x=0, y=0, width=16, height=16, shape=0, size=1,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=1, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        assert oam.tile_indices(100, mapping_mode=1) == [2, 3, 4, 5]

    def test_4bpp_1d_128k_multiple_tiles(self) -> None:
        # 4bpp, mapping_mode=2 (1D_128K), char_name=1 → base = 128*1//32 = 4
        oam = OamEntry(
            x=0, y=0, width=16, height=16, shape=0, size=1,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=1, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        assert oam.tile_indices(100, mapping_mode=2) == [4, 5, 6, 7]

    def test_8bpp_1d_64k_single_tile(self) -> None:
        # 8bpp, mapping_mode=1 (1D_64K), char_name=5 → base = 64*5//64 = 5
        oam = OamEntry(
            x=0, y=0, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=1,
            char_name=5, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        assert oam.tile_indices(100, mapping_mode=1) == [5]

    def test_8bpp_1d_32k_half_offset(self) -> None:
        # 8bpp, mapping_mode=0 (1D_32K), char_name=5 → base = 32*5//64 = 2 (integer div)
        oam = OamEntry(
            x=0, y=0, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=1,
            char_name=5, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        assert oam.tile_indices(100, mapping_mode=0) == [2]

    def test_out_of_range_filtered(self) -> None:
        # 4bpp, mapping_mode=1 (1D_64K), char_name=10 → base = 64*10//32 = 20
        # tiles=[20,21,22,23], but ncgr_tile_count=22 → only [20,21]
        oam = OamEntry(
            x=0, y=0, width=16, height=16, shape=0, size=1,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=10, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        assert oam.tile_indices(22, mapping_mode=1) == [20, 21]

    def test_tiles_per_row(self) -> None:
        oam = OamEntry(
            x=0, y=0, width=32, height=16, shape=1, size=2,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        assert oam.tiles_per_row == 4
        assert oam.tiles_per_col == 2
        assert oam.tile_count == 8


# ----------------------------------------------------------------------
# Cell.compute_bbox
# ----------------------------------------------------------------------

class TestComputeBbox:
    def test_single_oam(self) -> None:
        oam = OamEntry(
            x=10, y=20, width=16, height=16, shape=0, size=1,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        cell = Cell(cell_idx=0, n_oam=1, cell_attr=0, bbox=None, oam=[oam])
        assert cell.compute_bbox() == (10, 20, 26, 36)

    def test_multiple_oam(self) -> None:
        oam1 = OamEntry(
            x=0, y=0, width=16, height=16, shape=0, size=1,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        oam2 = OamEntry(
            x=20, y=10, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        cell = Cell(cell_idx=0, n_oam=2, cell_attr=0, bbox=None, oam=[oam1, oam2])
        # oam1: (0,0)-(16,16), oam2: (20,10)-(28,18)
        # bbox = (min_x=0, min_y=0, max_x=28, max_y=18)
        assert cell.compute_bbox() == (0, 0, 28, 18)

    def test_disabled_oam_skipped(self) -> None:
        oam1 = OamEntry(
            x=0, y=0, width=16, height=16, shape=0, size=1,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        oam2 = OamEntry(
            x=100, y=100, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=True, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        cell = Cell(cell_idx=0, n_oam=2, cell_attr=0, bbox=None, oam=[oam1, oam2])
        assert cell.compute_bbox() == (0, 0, 16, 16)

    def test_explicit_bbox_returned(self) -> None:
        oam = OamEntry(
            x=0, y=0, width=16, height=16, shape=0, size=1,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        cell = Cell(cell_idx=0, n_oam=1, cell_attr=0, bbox=(5, 6, 7, 8), oam=[oam])
        assert cell.compute_bbox() == (5, 6, 7, 8)

    def test_empty_cell(self) -> None:
        cell = Cell(cell_idx=0, n_oam=0, cell_attr=0, bbox=None, oam=[])
        assert cell.compute_bbox() == (0, 0, 0, 0)


# ----------------------------------------------------------------------
# find_obj_quartets
# ----------------------------------------------------------------------

class TestFindObjQuartets:
    def test_paired_files(self, tmp_path: Path) -> None:
        # 创建连续编号的 4 件套
        for ext in ['NCGR', 'NCLR', 'NANR', 'NCER']:
            pass
        (tmp_path / '0012_MENUITEM.NCGR').write_bytes(b'')
        (tmp_path / '0013_MENUITEM.NCLR').write_bytes(b'')
        (tmp_path / '0014_MENUITEM.NANR').write_bytes(b'')
        (tmp_path / '0015_MENUITEM.NCER').write_bytes(b'')
        # 不配对的文件
        (tmp_path / '0020_NO_PAIR.NCGR').write_bytes(b'')

        quartets = find_obj_quartets(tmp_path)
        assert len(quartets) == 1
        ncgr, nclr, nanr, ncer, base, stem = quartets[0]
        assert base == 'MENUITEM'
        assert ncgr.name == '0012_MENUITEM.NCGR'
        assert nclr.name == '0013_MENUITEM.NCLR'
        assert nanr.name == '0014_MENUITEM.NANR'
        assert ncer.name == '0015_MENUITEM.NCER'
        assert stem == '0012_MENUITEM'

    def test_multiple_quartets(self, tmp_path: Path) -> None:
        for i, name in enumerate(['ALPHA', 'BETA']):
            for j, ext in enumerate(['NCGR', 'NCLR', 'NANR', 'NCER']):
                (tmp_path / f'{i*4+j:04d}_{name}.{ext}').write_bytes(b'')

        quartets = find_obj_quartets(tmp_path)
        assert len(quartets) == 2
        # 按字典序排序：ALPHA < BETA
        assert quartets[0][4] == 'ALPHA'
        assert quartets[1][4] == 'BETA'

    def test_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / '0001_test.ncgr').write_bytes(b'')
        (tmp_path / '0002_test.nclr').write_bytes(b'')
        (tmp_path / '0003_test.nanr').write_bytes(b'')
        (tmp_path / '0004_test.ncer').write_bytes(b'')

        quartets = find_obj_quartets(tmp_path)
        assert len(quartets) == 1

    def test_no_quartets(self, tmp_path: Path) -> None:
        (tmp_path / '0001_ONLY_NCGR.NCGR').write_bytes(b'')
        quartets = find_obj_quartets(tmp_path)
        assert len(quartets) == 0

    def test_no_prefix(self, tmp_path: Path) -> None:
        # 无数字前缀的文件名
        for ext in ['NCGR', 'NCLR', 'NANR', 'NCER']:
            (tmp_path / f'PLAIN.{ext}').write_bytes(b'')
        quartets = find_obj_quartets(tmp_path)
        assert len(quartets) == 1
        assert quartets[0][4] == 'PLAIN'


# ----------------------------------------------------------------------
# compute_shared_tiles
# ----------------------------------------------------------------------

class TestComputeSharedTiles:
    def test_no_shared(self) -> None:
        oam1 = OamEntry(
            x=0, y=0, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        oam2 = OamEntry(
            x=0, y=0, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=1, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        cell1 = Cell(cell_idx=0, n_oam=1, cell_attr=0, bbox=None, oam=[oam1])
        cell2 = Cell(cell_idx=1, n_oam=1, cell_attr=0, bbox=None, oam=[oam2])
        # 4bpp, mapping_mode=0: char_name=0 → tile 0; char_name=1 → tile 1
        shared = compute_shared_tiles([cell1, cell2], 100)
        assert shared == {}

    def test_shared_tile(self) -> None:
        # 两个 cell 都引用 tile 0
        oam1 = OamEntry(
            x=0, y=0, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        oam2 = OamEntry(
            x=0, y=0, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        cell1 = Cell(cell_idx=0, n_oam=1, cell_attr=0, bbox=None, oam=[oam1])
        cell2 = Cell(cell_idx=1, n_oam=1, cell_attr=0, bbox=None, oam=[oam2])
        shared = compute_shared_tiles([cell1, cell2], 100)
        # 4bpp, mapping_mode=0: char_name=0 → tile 0
        assert 0 in shared
        assert shared[0] == [0, 1]

    def test_disabled_oam_skipped(self) -> None:
        oam1 = OamEntry(
            x=0, y=0, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=False, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        oam2 = OamEntry(
            x=0, y=0, width=8, height=8, shape=0, size=0,
            flip_x=False, flip_y=False, color_mode=0,
            char_name=0, palette=0, priority=0,
            disabled=True, affine=False, raw_a0=0, raw_a1=0, raw_a2=0,
        )
        cell1 = Cell(cell_idx=0, n_oam=1, cell_attr=0, bbox=None, oam=[oam1])
        cell2 = Cell(cell_idx=1, n_oam=1, cell_attr=0, bbox=None, oam=[oam2])
        shared = compute_shared_tiles([cell1, cell2], 100)
        assert shared == {}


# ----------------------------------------------------------------------
# parse_ncer (用真实 NCER 文件)
# ----------------------------------------------------------------------

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent
OBJ_DIR = PROJECT_ROOT / 'game_data' / '1_Extracted' / 'OBJ'


@pytest.mark.skipif(not OBJ_DIR.exists(), reason='OBJ 目录不存在')
class TestParseNcerReal:
    def test_menuitem_n_cells(self) -> None:
        ncer_path = OBJ_DIR / '0015_MENUITEM.NCER'
        if not ncer_path.exists():
            pytest.skip('MENUITEM.NCER 不存在')
        data = ncer_path.read_bytes()
        info = parse_ncer(data)
        assert info['n_cells'] == 23
        assert len(info['cells']) == 23

    def test_test_button_n_cells(self) -> None:
        ncer_path = OBJ_DIR / '0027_TEST_BUTTON.NCER'
        if not ncer_path.exists():
            pytest.skip('TEST_BUTTON.NCER 不存在')
        data = ncer_path.read_bytes()
        info = parse_ncer(data)
        assert info['n_cells'] == 9
        assert len(info['cells']) == 9

    def test_cells_have_oam(self) -> None:
        ncer_path = OBJ_DIR / '0015_MENUITEM.NCER'
        if not ncer_path.exists():
            pytest.skip('MENUITEM.NCER 不存在')
        data = ncer_path.read_bytes()
        info = parse_ncer(data)
        # 每个 cell 至少应该有 OAM 数据结构
        for cell in info['cells']:
            assert isinstance(cell.n_oam, int)
            assert isinstance(cell.oam, list)
