"""NCER (Cell Resource) 解析与 OAM 解码工具。

参考实现：NitroPaint (C) ncer.c (CellReadNcer)
GBATEK: https://problemkaputt.de/gbatek.htm#ds2dnitrocellresourcencer

NCER 文件结构（NDS Nitro 标准容器）：
    文件头 (0x10) + CEBK section + LABL section (可选) + UEXT section (可选)
    文件 magic: "RECN" (LE) → 读取时 section magic 为 "CEBK"

CEBK section body 字段布局（参考 NitroPaint ncer.c CellReadNcer）：
    0x00  u16  nCells              cell 数量
    0x02  u16  bankAttribs         1=含 bounding rectangle, 0=不含
    0x04  u32  cellDataOffset      cell 数据起始偏移（相对 CEBK body）
    0x08  u32  mappingMode         VRAM 映射模式 (0-4)
    0x0C  u32  vramTransferOffset  VRAM transfer 表偏移 (0/0xFFFFFFFF=无)
    0x14  u32  userExtendedOffset  用户扩展属性偏移 (0=无)

每 cell 数据（在 cellDataOffset 处，perCellSize = 8 + (8 if bankAttribs==1 else 0)）：
    0x00  u16  nOAM                OAM 条目数
    0x02  u16  cellAttr            cell 属性
    0x04  u32  pOamAttrs           该 cell 的 OAM 数据偏移（相对 oamData 起始）
    若 bankAttribs==1，追加 8 字节 bounding rectangle：
        0x08  i16  maxX
        0x0A  i16  maxY
        0x0C  i16  minX
        0x0E  i16  minY

OAM 数据（在 cellDataOffset + nCells * perCellSize 处）：
    每 OAM 6 字节：a0/u16, a1/u16, a2/u16（GBATEK OAM 属性格式）

OAM 属性解码（GBATEK）：
    a0: bits[0-7]=Y, [8]=affine, [9]=doubleSize/disabled, [10-11]=mode,
        [12]=mosaic, [13]=colorMode(0=4bpp,1=8bpp), [14-15]=shape
    a1: bits[0-8]=X(i9), [12]=flipX(non-affine), [13]=flipY(non-affine),
        [14-15]=size
    a2: bits[0-9]=charName(tile base), [10-11]=priority, [12-15]=palette

OAM 尺寸表 (shape × size → w×h)：
    Square(0): 8x8, 16x16, 32x32, 64x64
    Wide(1):   16x8, 32x8, 32x16, 64x32
    Tall(2):   8x16, 8x32, 16x32, 32x64

char_name 单位（tile base index）：
    4bpp: 1 unit = 32B = 2 tiles (1 tile = 16B)  →  实际 tile 起始 = char_name * 2
    8bpp: 1 unit = 32B = 1 tile (1 tile = 32B)   →  实际 tile 起始 = char_name
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.stage2_export_bg import parse_nds_container


# OAM 尺寸表：[shape][size] -> (width, height)
# shape: 0=Square, 1=Wide, 2=Tall, 3=Illegal(不处理)
# size: 0-3
OAM_SIZE_TABLE: list[list[tuple[int, int]]] = [
    [(8, 8), (16, 16), (32, 32), (64, 64)],    # Square
    [(16, 8), (32, 8), (32, 16), (64, 32)],    # Wide
    [(8, 16), (8, 32), (16, 32), (32, 64)],    # Tall
    [(8, 8), (16, 16), (32, 32), (64, 64)],    # Illegal（兜底用 Square）
]


@dataclass
class OamEntry:
    """单个 OAM 条目（已解码）。"""
    # 几何位置（相对 cell 原点）
    x: int
    y: int
    width: int
    height: int
    # OAM 属性
    shape: int            # 0=Square, 1=Wide, 2=Tall
    size: int             # 0-3
    flip_x: bool          # 水平翻转（非 affine 模式）
    flip_y: bool          # 垂直翻转（非 affine 模式）
    color_mode: int       # 0=4bpp, 1=8bpp
    char_name: int        # tile base index（4bpp 时实际 tile = char_name*2）
    palette: int          # 4bpp 时的子调色板索引 (0-15)
    priority: int         # 渲染优先级 (0-3)
    disabled: bool        # 是否禁用渲染
    affine: bool          # 是否为 affine 模式（旋转/缩放）
    # 原始属性（保留用于回写）
    raw_a0: int
    raw_a1: int
    raw_a2: int

    @property
    def tiles_per_row(self) -> int:
        """OAM 一行包含多少个 8x8 tile。"""
        return self.width // 8

    @property
    def tiles_per_col(self) -> int:
        """OAM 一列包含多少个 8x8 tile。"""
        return self.height // 8

    @property
    def tile_count(self) -> int:
        """OAM 引用的 8x8 tile 总数。"""
        return self.tiles_per_row * self.tiles_per_col

    def tile_indices(self, ncgr_tile_count: int, mapping_mode: int = 0) -> list[int]:
        """返回该 OAM 引用的 NCGR tile 索引列表（按行优先）。

        char_name 到 tile_index 的转换参考 NitroPaint NCGR_CHNAME 宏：
            BYTE_BOUNDARY = 32 << mapping_mode  (1D modes: 32/64/128/256)
            tile_index = BYTE_BOUNDARY * char_name / (bpp_bits * 8)

        mapping_mode (NCER CEBK offset 8):
            0 = 1D_32K, 1 = 1D_64K, 2 = 1D_128K, 3 = 1D_256K, 4 = 2D

        4bpp 转换表:
            1D_32K:  tile = char_name
            1D_64K:  tile = 2 * char_name
            1D_128K: tile = 4 * char_name
            1D_256K: tile = 8 * char_name
        8bpp 转换表:
            1D_32K:  tile = char_name / 2
            1D_64K:  tile = char_name
            1D_128K: tile = 2 * char_name
            1D_256K: tile = 4 * char_name

        越界的 tile 索引会被过滤掉（返回有效部分）。
        """
        if mapping_mode == 4:
            # 2D mapping：暂用 1D_32K 兜底（OBJ 极少用 2D）
            byte_boundary = 32
        else:
            byte_boundary = 32 << mapping_mode  # 32, 64, 128, 256

        bpp_bits = 4 if self.color_mode == 0 else 8
        base = byte_boundary * self.char_name // (bpp_bits * 8)

        result: list[int] = []
        for row in range(self.tiles_per_col):
            for col in range(self.tiles_per_row):
                idx = base + row * self.tiles_per_row + col
                if idx < ncgr_tile_count:
                    result.append(idx)
        return result


@dataclass
class Cell:
    """单个 cell（含 OAM 列表）。"""
    cell_idx: int
    n_oam: int
    cell_attr: int
    bbox: tuple[int, int, int, int] | None  # (min_x, min_y, max_x, max_y)
    oam: list[OamEntry] = field(default_factory=list)

    def compute_bbox(self) -> tuple[int, int, int, int]:
        """根据 OAM 几何计算 bounding box（若无 bbox 字段）。"""
        if self.bbox is not None:
            return self.bbox
        min_x = min_y = 10**9
        max_x = max_y = -10**9
        for o in self.oam:
            if o.disabled:
                continue
            if o.x < min_x:
                min_x = o.x
            if o.y < min_y:
                min_y = o.y
            if o.x + o.width > max_x:
                max_x = o.x + o.width
            if o.y + o.height > max_y:
                max_y = o.y + o.height
        if max_x <= min_x or max_y <= min_y:
            return (0, 0, 0, 0)
        return (min_x, min_y, max_x, max_y)


def decode_oam(a0: int, a1: int, a2: int) -> OamEntry:
    """解码 OAM 的 3 个 u16 属性为可读字段（GBATEK 格式）。"""
    y = a0 & 0xFF
    affine = bool(a0 & 0x100)
    double_size = bool(a0 & 0x200)
    mode = (a0 >> 10) & 0x3
    mosaic = bool(a0 & 0x1000)
    color_mode = (a0 >> 13) & 0x1
    shape = (a0 >> 14) & 0x3

    # X 是 9 位有符号整数
    x = a1 & 0x1FF
    if x >= 256:
        x -= 512
    # 非 affine 模式下 bit12=flipX, bit13=flipY
    # affine 模式下这 5 位是 matrix selection
    flip_x = bool(a1 & 0x1000) and not affine
    flip_y = bool(a1 & 0x2000) and not affine
    size = (a1 >> 14) & 0x3

    char_name = a2 & 0x3FF
    priority = (a2 >> 10) & 0x3
    palette = (a2 >> 12) & 0xF

    # disabled: 非 affine 模式下 bit9=disabled
    disabled = bool(a0 & 0x200) and not affine

    w, h = OAM_SIZE_TABLE[shape][size]

    return OamEntry(
        x=x, y=y, width=w, height=h,
        shape=shape, size=size,
        flip_x=flip_x, flip_y=flip_y,
        color_mode=color_mode, char_name=char_name,
        palette=palette, priority=priority,
        disabled=disabled, affine=affine,
        raw_a0=a0, raw_a1=a1, raw_a2=a2,
    )


def parse_ncer(ncer_data: bytes) -> dict[str, Any]:
    """解析 NCER 文件，返回 cell bank 信息。

    Returns:
        {
            'n_cells': int,
            'bank_attribs': int,
            'mapping_mode': int,
            'cells': [Cell, ...],
        }
    """
    secs = parse_nds_container(ncer_data)
    # section magic 在容器中存为 LE，所以 'CEBK' 在文件里是 'KBEC'？
    # 实际 parse_nds_container 按 offset+0..3 读 4 字节作为 magic（不反转），
    # 而我们观察到文件里是 'KBEC'（CEBK 的字节反转）。所以这里要兼容两种。
    cebk = secs.get('CEBK') or secs.get('KBEC')
    if not cebk:
        raise ValueError("未找到 CEBK section")

    n_cells = struct.unpack_from('<H', cebk, 0)[0]
    bank_attribs = struct.unpack_from('<H', cebk, 2)[0]
    cell_data_off = struct.unpack_from('<I', cebk, 4)[0]
    mapping_mode = struct.unpack_from('<I', cebk, 8)[0]

    per_cell_size = 8 + (8 if bank_attribs == 1 else 0)
    oam_data_start = cell_data_off + n_cells * per_cell_size

    cells: list[Cell] = []
    for i in range(n_cells):
        cd_off = cell_data_off + i * per_cell_size
        if cd_off + 8 > len(cebk):
            break
        n_oam = struct.unpack_from('<H', cebk, cd_off)[0]
        cell_attr = struct.unpack_from('<H', cebk, cd_off + 2)[0]
        p_oam_attrs = struct.unpack_from('<I', cebk, cd_off + 4)[0]

        bbox: tuple[int, int, int, int] | None = None
        if bank_attribs == 1 and cd_off + 16 <= len(cebk):
            max_x = struct.unpack_from('<h', cebk, cd_off + 8)[0]
            max_y = struct.unpack_from('<h', cebk, cd_off + 10)[0]
            min_x = struct.unpack_from('<h', cebk, cd_off + 12)[0]
            min_y = struct.unpack_from('<h', cebk, cd_off + 14)[0]
            bbox = (min_x, min_y, max_x, max_y)

        oam_list: list[OamEntry] = []
        for j in range(n_oam):
            oam_off = oam_data_start + p_oam_attrs + j * 6
            if oam_off + 6 > len(cebk):
                break
            a0, a1, a2 = struct.unpack_from('<HHH', cebk, oam_off)
            oam_list.append(decode_oam(a0, a1, a2))

        cells.append(Cell(
            cell_idx=i, n_oam=n_oam, cell_attr=cell_attr,
            bbox=bbox, oam=oam_list,
        ))

    return {
        'n_cells': n_cells,
        'bank_attribs': bank_attribs,
        'mapping_mode': mapping_mode,
        'cells': cells,
    }


def find_obj_quartets(input_dir: Path) -> list[tuple[Path, Path, Path, Path, str, str]]:
    """配对 OBJ 目录下的 4 件套（NCGR+NCLR+NANR+NCER）。

    OBJ 文件名是连续编号（0012_MENUITEM.NCGR / 0013_MENUITEM.NCLR /
    0014_MENUITEM.NANR / 0015_MENUITEM.NCER），不能用 with_suffix 切换扩展名,
    必须按 stem 主体（去掉数字前缀）配对。

    Returns:
        list of (ncgr_path, nclr_path, nanr_path, ncer_path, base, stem)
    """
    def strip_prefix(stem: str) -> str:
        parts = stem.split('_', 1)
        return parts[1] if len(parts) == 2 and parts[0].isdigit() else stem

    ncgr_files = {strip_prefix(f.stem): f for f in input_dir.glob('*.[nN][cC][gG][rR]')}
    nclr_files = {strip_prefix(f.stem): f for f in input_dir.glob('*.[nN][cC][lL][rR]')}
    nanr_files = {strip_prefix(f.stem): f for f in input_dir.glob('*.[nN][aA][nN][rR]')}
    ncer_files = {strip_prefix(f.stem): f for f in input_dir.glob('*.[nN][cC][eE][rR]')}

    quartets: list[tuple[Path, Path, Path, Path, str, str]] = []
    for base, ncgr_path in sorted(ncgr_files.items()):
        if base in nclr_files and base in nanr_files and base in ncer_files:
            quartets.append((
                ncgr_path, nclr_files[base],
                nanr_files[base], ncer_files[base],
                base, ncgr_path.stem,
            ))
    return quartets


def compute_shared_tiles(
    cells: list[Cell], ncgr_tile_count: int, mapping_mode: int = 0,
) -> dict[int, list[int]]:
    """统计被多个 cell 引用的 tile 索引。

    Returns:
        {tile_idx: [cell_idx, ...]}  只包含被 ≥2 个 cell 引用的 tile
    """
    tile_to_cells: dict[int, set[int]] = {}
    for cell in cells:
        for oam in cell.oam:
            if oam.disabled:
                continue
            for tile_idx in oam.tile_indices(ncgr_tile_count, mapping_mode):
                tile_to_cells.setdefault(tile_idx, set()).add(cell.cell_idx)

    return {idx: sorted(cell_ids) for idx, cell_ids in tile_to_cells.items() if len(cell_ids) >= 2}
