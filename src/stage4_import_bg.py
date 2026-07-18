# src/stage4_import_bg.py
from __future__ import annotations

import os
import sys
import struct
from pathlib import Path
from typing import cast

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, PATCHED_DIR
from src.stage2_export_bg import parse_nds_container, parse_ncgr, parse_nscr, find_bg_triplets

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

    width, height, pixel_data, raw_palette = read_bmp_8bpp(bmp_path)

    new_tiles = extract_tiles_from_bmp(pixel_data, width, height, entries,
                                       map_w_tiles, map_h_tiles, len(tiles_original), bpp)
    new_tiles_data = encode_tiles(new_tiles, bpp)

    sec = parse_nds_container(ncgr_data)
    rahc_bytes = sec.get('RAHC') or sec.get('CHAR')
    if rahc_bytes is None: raise ValueError("未找到图块 Section")
    rahc = bytearray(rahc_bytes)
    data_off = struct.unpack_from('<I', rahc, 0x10)[0]
    rahc[data_off : data_off + len(new_tiles_data)] = new_tiles_data
    final_ncgr = rebuild_nds_container(ncgr_data, ['RAHC', 'CHAR'], rahc)

    nds_palette = bmp_palette_to_nds(raw_palette)
    sec_nclr = parse_nds_container(nclr_data)
    ttlp_bytes = sec_nclr.get('TTLP') or sec_nclr.get('PLTT')
    if ttlp_bytes is None: raise ValueError("未找到调色板 Section")
    ttlp = bytearray(ttlp_bytes)

    # 【核心修复】同步修正注入脚本的地址偏移为 0x08 和 0x0C
    pal_size = struct.unpack_from('<I', ttlp, 0x08)[0]
    pal_off = struct.unpack_from('<I', ttlp, 0x0C)[0]
    if pal_off + pal_size > len(ttlp): pal_off = 0x10

    ttlp[pal_off : pal_off + pal_size] = nds_palette[:pal_size]
    final_nclr = rebuild_nds_container(nclr_data, ['TTLP', 'PLTT'], ttlp)

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