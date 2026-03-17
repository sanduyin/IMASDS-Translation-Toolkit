# src/stage4_import_bg.py
"""
BMP → NCGR/NCLR/NSCR 终极逆向回写工具

【黑科技原理】
不直接切割 BMP！而是读取原始的 NSCR 地图，反向在 BMP 上寻找像素，
解除镜像翻转后，精准填回 NCGR 的原始槽位中。保证体积 100% 不变！
"""

import os
import sys
import struct
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, PATCHED_DIR

# 从第二步的导出脚本借用解析工具
from src.stage2_export_bg import (
    parse_nds_container, parse_nclr, parse_ncgr, parse_nscr, find_bg_triplets
)

def read_bmp_8bpp(bmp_path):
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

def bmp_palette_to_nds(raw_palette_bgr0):
    """BMP 24位色压缩回 NDS 的 15位 BGR555 格式"""
    nds_palette = bytearray()
    for i in range(256):
        b, g, r = raw_palette_bgr0[i*4], raw_palette_bgr0[i*4+1], raw_palette_bgr0[i*4+2]
        col = (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
        nds_palette.extend(struct.pack('<H', col))
    return bytes(nds_palette)

def extract_tiles_from_bmp(pixel_data, bmp_w, entries, original_tile_count, bpp):
    """
    【核心逆向黑科技】
    通过遍历 NSCR 地图，反向在 BMP 上定位 8x8 图块，
    解除翻转和调色板偏移后，无损还原回 NCGR 阵列中。
    """
    tiles = [None] * original_tile_count
    map_w_tiles = bmp_w // 8

    for entry_idx, (tile_idx, flip_h, flip_v, pal_idx) in enumerate(entries):
        if tile_idx >= original_tile_count: continue
        # 如果这个图块已经被提取过了 (被地图复用了)，直接跳过
        if tiles[tile_idx] is not None: continue

        col, row = entry_idx % map_w_tiles, entry_idx // map_w_tiles
        tile_pixels =[]
        pal_offset = pal_idx * 16 if bpp == 4 else 0

        # 从大图中反向抠出 8x8 像素，并解除翻转
        for py in range(8):
            src_py = (7 - py) if flip_v else py
            for px in range(8):
                src_px = (7 - px) if flip_h else px
                bmp_x, bmp_y = col * 8 + src_px, row * 8 + src_py
                
                # 减去调色板偏移，还原为相对索引
                raw_idx = (pixel_data[bmp_y * bmp_w + bmp_x] - pal_offset) & 0xFF
                if bpp == 4: raw_idx &= 0x0F
                
                tile_pixels.append(raw_idx)
                
        tiles[tile_idx] = tile_pixels

    # 兜底防错：地图上没用到的废弃图块，用 0 填充以保持原体积不变
    for i in range(original_tile_count):
        if tiles[i] is None: tiles[i] = [0] * 64

    return tiles

def encode_tiles(tiles, bpp):
    """将像素阵列重新打包成二进制流"""
    raw = bytearray()
    for tile in tiles:
        if bpp == 8:
            raw.extend(tile)
        else:
            for i in range(0, 64, 2):
                raw.append((tile[i] & 0x0F) | ((tile[i+1] & 0x0F) << 4))
    return bytes(raw)

def rebuild_nds_container(original_data, section_magic_list, new_payload):
    """原样保留 NDS 容器外壳，仅精确替换指定 Section 的数据"""
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
                raise ValueError(f"体积校验失败 ({magic})！原: {sec_size - 8}，新: {len(new_payload)}")
            result.extend(original_data[offset:offset+8]) # 写入 Section 头
            result.extend(new_payload)                    # 写入新数据
        else:
            result.extend(sec_data)
        offset += sec_size
    return bytes(result)

def import_bg_triplet(bmp_path, ncgr_path, nclr_path, nscr_path, out_ncgr, out_nclr, out_nscr):
    # 1. 读取原版架构
    nclr_data, ncgr_data, nscr_data = nclr_path.read_bytes(), ncgr_path.read_bytes(), nscr_path.read_bytes()
    tiles_original, bpp, _, _ = parse_ncgr(ncgr_data)
    entries, _, _, _, _ = parse_nscr(nscr_data)
    
    # 2. 读取新修改的 BMP
    width, height, pixel_data, raw_palette = read_bmp_8bpp(bmp_path)

    # 3. 逆向抠图重建 NCGR
    new_tiles = extract_tiles_from_bmp(pixel_data, width, entries, len(tiles_original), bpp)
    new_tiles_data = encode_tiles(new_tiles, bpp)
    
    sec = parse_nds_container(ncgr_data)
    rahc = bytearray(sec.get('RAHC') or sec.get('CHAR'))
    data_off = struct.unpack_from('<I', rahc, 0x10)[0]
    rahc[data_off : data_off + len(new_tiles_data)] = new_tiles_data
    final_ncgr = rebuild_nds_container(ncgr_data, ['RAHC', 'CHAR'], rahc)

    # 4. 转换调色板重建 NCLR
    nds_palette = bmp_palette_to_nds(raw_palette)
    sec = parse_nds_container(nclr_data)
    ttlp = bytearray(sec.get('TTLP') or sec.get('PLTT'))
    pal_size, pal_off = struct.unpack_from('<I', ttlp, 0x04)[0], struct.unpack_from('<I', ttlp, 0x08)[0]
    ttlp[pal_off : pal_off + pal_size] = nds_palette[:pal_size]
    final_nclr = rebuild_nds_container(nclr_data, ['TTLP', 'PLTT'], ttlp)

    # 5. 保存回 Patch 目录 (NSCR 原样复制，因为地图结构不能改)
    out_ncgr.write_bytes(final_ncgr)
    out_nclr.write_bytes(final_nclr)
    out_nscr.write_bytes(nscr_data)

def main():
    print("=" * 50)
    print(" NDS 背景图 (BG) 无损逆向回写工具")
    print("=" * 50)
    
    img_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / "BG"
    orig_dir = EXTRACT_DIR / "BG"
    out_dir = PATCHED_DIR / "BG_CHS_PATCHED"
    
    if not img_dir.exists():
        print("❌ 未找到修改好的 BMP 目录。")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    triplets = find_bg_triplets(orig_dir)
    
    success = 0
    for ncgr, nclr, nscr, base, stem in triplets:
        bmp_path = img_dir / f"{stem}.bmp"
        if not bmp_path.exists(): continue
        
        print(f"📥 正在回写: {stem} ...")
        try:
            import_bg_triplet(
                bmp_path, ncgr, nclr, nscr,
                out_dir / ncgr.name, out_dir / nclr.name, out_dir / nscr.name
            )
            success += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            
    print(f"\n🎉 成功回写了 {success} 组背景图到 Patched 目录！")

if __name__ == "__main__":
    main()