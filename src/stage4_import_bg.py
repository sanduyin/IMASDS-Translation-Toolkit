# src/stage4_import_bg.py
from __future__ import annotations

import os
import sys
import struct
from pathlib import Path
from typing import cast

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, PATCHED_DIR
from src.stage2_export_bg import parse_nds_container, parse_ncgr, parse_nclr, parse_nscr, find_bg_triplets

def read_bmp_8bpp(bmp_path: Path) -> tuple[int, int, bytes, bytes]:
    with open(bmp_path, 'rb') as f:
        magic = f.read(2)
        if magic != b'BM': raise ValueError("非 BMP 格式")
        f.read(12)
        dib_size = struct.unpack('<I', f.read(4))[0]
        width, height = struct.unpack('<ii', f.read(8))
        f.read(2)
        if struct.unpack('<H', f.read(2))[0] != 8:
            raise ValueError("必须是 8位索引颜色 (256色) BMP！")
        
        f.seek(14 + dib_size)
        raw_palette = f.read(1024)
        
        f.seek(14 + dib_size + 1024)
        flip_height, row_padding = abs(height), (4 - (width % 4)) % 4
        rows =[]
        for _ in range(flip_height):
            rows.append(f.read(width + row_padding)[:width])
        if height > 0: rows.reverse()
        
    return width, flip_height, b''.join(rows), raw_palette

def read_bmp_bpp(bmp_path: Path) -> int:
    """读取 BMP 位深度（offset 0x1C 处的 u16）。"""
    with open(bmp_path, 'rb') as f:
        f.seek(0x1C)
        bpp = struct.unpack('<H', f.read(2))[0]
    return int(bpp)

def read_bmp_rgb(bmp_path: Path) -> tuple[int, int, bytes]:
    """读取 24bpp 或 32bpp RGB BMP，返回 (width, height, rgb_data)。

    rgb_data 每像素 3 字节 (R, G, B)，按行从上到下排列（已处理 BMP 自下而上翻转）。
    32bpp 模式会丢弃 alpha 通道。
    """
    with open(bmp_path, 'rb') as f:
        magic = f.read(2)
        if magic != b'BM': raise ValueError("非 BMP 格式")
        f.read(12)  # 文件大小 + 保留
        dib_size = struct.unpack('<I', f.read(4))[0]
        width, height = struct.unpack('<ii', f.read(8))
        f.read(2)  # planes
        bpp = struct.unpack('<H', f.read(2))[0]
        if bpp not in (24, 32):
            raise ValueError(f"RGB BMP 仅支持 24/32 bpp，当前 {bpp} bpp")

        # RGB BMP 无调色板，直接跳到像素数据
        f.seek(14 + dib_size)

        flip_height, bytes_per_pixel = abs(height), bpp // 8
        row_stride = (width * bytes_per_pixel + 3) & ~3  # 4 字节对齐
        rows: list[bytes] = []
        for _ in range(flip_height):
            row = f.read(row_stride)
            # BMP 像素排列是 BGR，统一转换为 RGB
            rgb = bytearray(width * 3)
            for x in range(width):
                b = row[x*bytes_per_pixel]
                g = row[x*bytes_per_pixel+1]
                r = row[x*bytes_per_pixel+2]
                rgb[x*3] = r
                rgb[x*3+1] = g
                rgb[x*3+2] = b
            rows.append(bytes(rgb))
        if height > 0: rows.reverse()

    return width, flip_height, b''.join(rows)

def rgb_to_indexed(
    rgb_data: bytes,
    pixel_count: int,
    palette_rgb: list[tuple[int, int, int]],
) -> bytes:
    """RGB 像素 → 8bpp 索引像素（最近色匹配，欧氏距离）。

    Args:
        rgb_data: 每像素 3 字节 (R, G, B) 的 RGB 数据
        pixel_count: 像素总数（= width * height）
        palette_rgb: NCLR 调色板的 RGB888 列表（最多 256 色，超出截断）

    策略：
      - 用 dict 缓存 (R,G,B) → index，相同颜色只算一次
      - 一旦找到完全匹配 (distance=0) 立即停止
      - 调色板不足 256 色时补 (0,0,0) 占位（与 parse_nclr 行为一致）
    """
    pal = palette_rgb[:256]
    n_pal = len(pal)
    cache: dict[tuple[int, int, int], int] = {}
    result = bytearray(pixel_count)

    for i in range(pixel_count):
        r, g, b = rgb_data[i*3], rgb_data[i*3+1], rgb_data[i*3+2]
        key = (r, g, b)
        idx = cache.get(key)
        if idx is None:
            best_idx = 0
            best_dist = -1
            for p in range(n_pal):
                pr, pg, pb = pal[p]
                dr = r - pr
                dg = g - pg
                db = b - pb
                d = dr*dr + dg*dg + db*db
                if best_dist < 0 or d < best_dist:
                    best_dist = d
                    best_idx = p
                    if d == 0:
                        break
            cache[key] = best_idx
            idx = best_idx
        result[i] = idx

    return bytes(result)

def bmp_palette_to_nds(raw_palette_bgr0: bytes) -> bytes:
    nds_palette = bytearray()
    for i in range(256):
        b, g, r = raw_palette_bgr0[i*4], raw_palette_bgr0[i*4+1], raw_palette_bgr0[i*4+2]
        col = (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
        nds_palette.extend(struct.pack('<H', col))
    return bytes(nds_palette)

def extract_tiles_from_bmp(
    pixel_data: bytes,
    bmp_w: int,
    bmp_h: int,
    entries: list[tuple[int, bool, bool, int]],
    map_w_tiles: int,
    map_h_tiles: int,
    original_tile_count: int,
    bpp: int,
) -> list[list[int]]:
    """从 BMP 像素数据提取 tile。

    Args:
        pixel_data: BMP 像素数据（8bpp 索引）
        bmp_w, bmp_h: BMP 宽高（像素）
        entries: NSCR map entries [(tile_idx, flip_h, flip_v, pal_idx), ...]
        map_w_tiles, map_h_tiles: NSCR 的 tile 列数/行数（P3-3 修复：必须用 NSCR 的，
                                  而非 BMP 宽度//8，否则 BMP 尺寸与 NSCR 不一致时错位）
        original_tile_count: 原始 NCGR 中的 tile 数量
        bpp: 4 或 8
    """
    tiles: list[list[int] | None] = [None] * original_tile_count
    # P3-3 修复：用 NSCR 的 map_w_tiles 计算 col/row，而非 bmp_w // 8
    # entries 按 NSCR 的 map_w_tiles 排列，必须与之对齐
    nscr_map_w_tiles = map_w_tiles

    for entry_idx, (tile_idx, flip_h, flip_v, pal_idx) in enumerate(entries):
        if tile_idx >= original_tile_count: continue
        if tiles[tile_idx] is not None: continue

        col, row = entry_idx % nscr_map_w_tiles, entry_idx // nscr_map_w_tiles
        # P3-3 修复：越界保护 — 如果 col/row 超出 NSCR map 尺寸，跳过
        if col >= nscr_map_w_tiles or row >= map_h_tiles:
            continue
        tile_pixels: list[int] = []
        pal_offset = pal_idx * 16 if bpp == 4 else 0

        for py in range(8):
            src_py = (7 - py) if flip_v else py
            for px in range(8):
                src_px = (7 - px) if flip_h else px
                bmp_x, bmp_y = col * 8 + src_px, row * 8 + src_py
                # P3-3 修复：越界保护 — 如果 bmp_x/bmp_y 超出 BMP 尺寸，用 0 填充
                if bmp_x >= bmp_w or bmp_y >= bmp_h or bmp_x < 0 or bmp_y < 0:
                    raw_idx = 0
                else:
                    raw_idx = (pixel_data[bmp_y * bmp_w + bmp_x] - pal_offset) & 0xFF
                if bpp == 4: raw_idx &= 0x0F
                tile_pixels.append(raw_idx)

        tiles[tile_idx] = tile_pixels

    for i in range(original_tile_count):
        if tiles[i] is None: tiles[i] = [0] * 64
    return cast(list[list[int]], tiles)

def _nearest_in_subpalette(r: int, g: int, b: int, subpal: list[tuple[int, int, int]]) -> int:
    """在子调色板（最多 16 色）内找最近色索引（欧氏距离）。"""
    best_idx = 0
    best_dist = -1
    for i, (pr, pg, pb) in enumerate(subpal):
        dr, dg, db = r - pr, g - pg, b - pb
        d = dr*dr + dg*dg + db*db
        if best_dist < 0 or d < best_dist:
            best_dist = d
            best_idx = i
            if d == 0:
                break
    return best_idx

def extract_tiles_from_bmp_rgb(
    rgb_data: bytes,
    bmp_w: int,
    bmp_h: int,
    entries: list[tuple[int, bool, bool, int]],
    map_w_tiles: int,
    map_h_tiles: int,
    original_tile_count: int,
    palette_rgb: list[tuple[int, int, int]],
) -> list[list[int]]:
    """从 RGB BMP 像素数据提取 4bpp tile（每个 entry 在子调色板内做最近色匹配）。

    与 extract_tiles_from_bmp 的区别：
      - 输入是 RGB 像素（每像素 3 字节），而非 8bpp 索引
      - 4bpp 模式下，对每个 NSCR entry 用 pal_idx 指定的子调色板做最近色匹配，
        避免全局匹配选错子调色板导致 & 0x0F 截断后 raw_idx 错误
      - 仅用于 4bpp RGB BMP 导入（8bpp RGB 用 rgb_to_indexed + extract_tiles_from_bmp）
    """
    tiles: list[list[int] | None] = [None] * original_tile_count
    nscr_map_w_tiles = map_w_tiles

    # 预计算 16 个子调色板（每个 16 色）
    subpalettes: list[list[tuple[int, int, int]]] = []
    for i in range(16):
        subpalettes.append(palette_rgb[i * 16 : i * 16 + 16])

    # 缓存：(pal_idx, r, g, b) → raw_idx，相同颜色只算一次
    cache: dict[tuple[int, int, int, int], int] = {}

    for entry_idx, (tile_idx, flip_h, flip_v, pal_idx) in enumerate(entries):
        if tile_idx >= original_tile_count: continue
        if tiles[tile_idx] is not None: continue

        col, row = entry_idx % nscr_map_w_tiles, entry_idx // nscr_map_w_tiles
        if col >= nscr_map_w_tiles or row >= map_h_tiles:
            continue

        subpal = subpalettes[pal_idx] if pal_idx < 16 else subpalettes[0]
        tile_pixels: list[int] = []

        for py in range(8):
            src_py = (7 - py) if flip_v else py
            for px in range(8):
                src_px = (7 - px) if flip_h else px
                bmp_x, bmp_y = col * 8 + src_px, row * 8 + src_py
                if bmp_x >= bmp_w or bmp_y >= bmp_h or bmp_x < 0 or bmp_y < 0:
                    raw_idx = 0
                else:
                    rgb_offset = (bmp_y * bmp_w + bmp_x) * 3
                    r, g, b = rgb_data[rgb_offset], rgb_data[rgb_offset + 1], rgb_data[rgb_offset + 2]
                    key = (pal_idx, r, g, b)
                    idx = cache.get(key)
                    if idx is None:
                        idx = _nearest_in_subpalette(r, g, b, subpal)
                        cache[key] = idx
                    raw_idx = idx
                tile_pixels.append(raw_idx)

        tiles[tile_idx] = tile_pixels

    for i in range(original_tile_count):
        if tiles[i] is None: tiles[i] = [0] * 64
    return cast(list[list[int]], tiles)

def encode_tiles(tiles: list[list[int]], bpp: int) -> bytes:
    raw = bytearray()
    for tile in tiles:
        if bpp == 8: raw.extend(tile)
        else:
            for i in range(0, 64, 2):
                raw.append((tile[i] & 0x0F) | ((tile[i+1] & 0x0F) << 4))
    return bytes(raw)

def rebuild_nds_container(original_data: bytes, section_magic_list: list[str], new_payload: bytes | bytearray) -> bytes:
    header_size = struct.unpack_from('<H', original_data, 0x0C)[0]
    section_count = struct.unpack_from('<H', original_data, 0x0E)[0]
    result = bytearray(original_data[:header_size])
    offset = header_size

    for _ in range(section_count):
        magic = original_data[offset:offset+4].decode('ascii', errors='replace')
        sec_size = struct.unpack_from('<I', original_data, offset + 4)[0]
        sec_data = original_data[offset:offset+sec_size]

        if magic in section_magic_list:
            if len(new_payload) != sec_size - 8:
                raise ValueError(f"体积校验失败 ({magic})！")
            result.extend(original_data[offset:offset+8])
            result.extend(new_payload)
        else:
            result.extend(sec_data)
        offset += sec_size
    return bytes(result)

def import_bg_triplet(
    bmp_path: Path,
    ncgr_path: Path,
    nclr_path: Path,
    nscr_path: Path,
    out_ncgr: Path,
    out_nclr: Path,
    out_nscr: Path,
) -> None:
    nclr_data, ncgr_data, nscr_data = nclr_path.read_bytes(), ncgr_path.read_bytes(), nscr_path.read_bytes()
    tiles_original, bpp, _, _ = parse_ncgr(ncgr_data)
    # P3-3 修复：使用 NSCR 的 map_w_tiles/map_h_tiles，而非 BMP 宽度//8
    entries, map_w_tiles, map_h_tiles, _, _ = parse_nscr(nscr_data)

    # 选项 B：根据 BMP 位深度分派
    #   - 8bpp：原逻辑，用 BMP 自带调色板覆盖 NCLR
    #   - 24/32bpp RGB：用原 NCLR 调色板做最近色匹配生成索引，NCLR 保持不变
    #     （PS 编辑 RGB BMP 时只能改像素，调色板由原 NCLR 决定）
    #   - 4bpp RGB：用 extract_tiles_from_bmp_rgb 在子调色板内匹配（避免全局匹配
    #     选错子调色板导致 & 0x0F 截断后 raw_idx 错误）
    #   - 8bpp RGB：用 rgb_to_indexed 全局匹配（8bpp 无子调色板，全局匹配正确）
    bmp_bpp = read_bmp_bpp(bmp_path)
    if bmp_bpp == 8:
        width, height, pixel_data, raw_palette = read_bmp_8bpp(bmp_path)
        nds_palette: bytes | None = bmp_palette_to_nds(raw_palette)
        new_tiles = extract_tiles_from_bmp(pixel_data, width, height, entries,
                                           map_w_tiles, map_h_tiles, len(tiles_original), bpp)
    elif bmp_bpp in (24, 32):
        width, height, rgb_data = read_bmp_rgb(bmp_path)
        palette_rgb = parse_nclr(nclr_data)  # RGB888 列表（与 NDS 硬件 <<3 行为一致）
        nds_palette = None  # RGB 模式保留原始 NCLR 调色板
        if bpp == 4:
            # 4bpp：子调色板内匹配，避免跨子调色板的颜色冲突
            new_tiles = extract_tiles_from_bmp_rgb(rgb_data, width, height, entries,
                                                    map_w_tiles, map_h_tiles,
                                                    len(tiles_original), palette_rgb)
        else:
            # 8bpp：全局匹配
            pixel_data = rgb_to_indexed(rgb_data, width * height, palette_rgb)
            new_tiles = extract_tiles_from_bmp(pixel_data, width, height, entries,
                                               map_w_tiles, map_h_tiles, len(tiles_original), bpp)
    else:
        raise ValueError(f"不支持的 BMP 位深度: {bmp_bpp}（仅支持 8/24/32 bpp）")

    new_tiles_data = encode_tiles(new_tiles, bpp)

    sec = parse_nds_container(ncgr_data)
    rahc_bytes = sec.get('RAHC') or sec.get('CHAR')
    if rahc_bytes is None: raise ValueError("未找到图块 Section")
    rahc = bytearray(rahc_bytes)
    # RAHC header 字段（GBATEK + 实测验证，与 stage2_export_bg.parse_ncgr 一致）：
    #   0x10  u32  tile_data_size（旧代码误当作 offset）
    #   0x14  u32  header_size = data 起始偏移（通常 0x18）
    # 旧代码误用 0x10 字段，导致 4bpp BG 导入时 data_off=5120 超出 RAHC body，
    # 全部 46 个 BG 报"体积校验失败 (RAHC)"。
    header_size_field = struct.unpack_from('<I', rahc, 0x14)[0]
    data_off = header_size_field if 0 < header_size_field < len(rahc) else 0x18
    rahc[data_off : data_off + len(new_tiles_data)] = new_tiles_data
    final_ncgr = rebuild_nds_container(ncgr_data, ['RAHC', 'CHAR'], rahc)

    if nds_palette is not None:
        # 8bpp 模式：用 BMP 自带调色板覆盖 NCLR
        sec_nclr = parse_nds_container(nclr_data)
        ttlp_bytes = sec_nclr.get('TTLP') or sec_nclr.get('PLTT')
        if ttlp_bytes is None: raise ValueError("未找到调色板 Section")
        ttlp = bytearray(ttlp_bytes)

        # TTLP/PLTT 调色板数据固定从 0x10 开始（与 stage2_export_bg.parse_nclr 一致）
        # 旧代码从 0x08/0x0C 字段读取 pal_size/pal_off，但这两个字段实测不可靠
        # （color_count=512, bit_depth=0x10 碰巧凑出了正确的 0x10 偏移，但并非有意设计）。
        # 固定 pal_off=0x10，pal_size=256色×2字节=512，覆盖整个调色板区域。
        pal_off = 0x10
        pal_size = 256 * 2  # 512 字节
        if pal_off + pal_size > len(ttlp):
            pal_size = len(ttlp) - pal_off

        ttlp[pal_off : pal_off + pal_size] = nds_palette[:pal_size]
        final_nclr = rebuild_nds_container(nclr_data, ['TTLP', 'PLTT'], ttlp)
    else:
        # RGB 模式：保留原始 NCLR 不变
        final_nclr = nclr_data

    out_ncgr.write_bytes(final_ncgr)
    out_nclr.write_bytes(final_nclr)
    out_nscr.write_bytes(nscr_data)

def main() -> None:
    print("=" * 50)
    print(" NDS 背景图 (BG) 无损逆向回写工具")
    print("=" * 50)
    
    img_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / "BG"
    orig_dir = EXTRACT_DIR / "BG"
    out_dir = PATCHED_DIR / "BG_CHS_PATCHED"
    
    if not img_dir.exists(): return

    out_dir.mkdir(parents=True, exist_ok=True)
    triplets = find_bg_triplets(orig_dir)
    
    success = 0
    for ncgr, nclr, nscr, base, stem in triplets:
        bmp_path = img_dir / f"{stem}.bmp"
        if not bmp_path.exists(): continue
        print(f"📥 正在回写: {stem} ...")
        try:
            import_bg_triplet(bmp_path, ncgr, nclr, nscr, out_dir / ncgr.name, out_dir / nclr.name, out_dir / nscr.name)
            success += 1
        except Exception as e: print(f"  ❌ 失败: {e}")
            
    print(f"\n🎉 成功回写了 {success} 组背景图到 Patched 目录！")

if __name__ == "__main__":
    main()