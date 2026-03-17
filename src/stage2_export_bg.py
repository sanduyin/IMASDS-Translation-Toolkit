# src/stage2_export_bg.py
"""
BG 文件夹 NCGR/NCLR/NSCR → BMP 导出工具

【格式说明】
  .NCGR (魔术字 RGCN) : 图块数据，8×8像素/块，8bpp 或 4bpp
  .NCLR (魔术字 RLCN) : 调色板，NDS BGR555 格式
  .NSCR (魔术字 RCSN) : 图块地图，记录每格用哪个块/翻转/调色板

  三个文件编号相差1，例如：
    0000_AUDITION_VOC.NCGR + 0001_AUDITION_VOC.NCLR + 0002_AUDITION_VOC.NSCR

【输出】
  game_data/1_Extracted_Images/BG/<base_name>.bmp
  例：0000_AUDITION_VOC.bmp  （以 NCGR 的 base_name 命名）
"""

import os
import sys
import struct
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR

# ============================================================
# 通用 NDS 二进制容器解析（NCGR/NCLR/NSCR 共用同一种外壳）
# ============================================================

def parse_nds_container(data):
    """
    解析 NDS 标准二进制容器格式（NCGR/NCLR/NSCR 通用）。

    容器结构：
      [0x00] 4字节  魔术字（如 RGCN），小端存储时读出来是反序的
      [0x04] 2字节  BOM (0xFFFE = 小端)
      [0x06] 2字节  版本号
      [0x08] 4字节  文件总大小
      [0x0C] 2字节  头部大小（固定 0x10）
      [0x0E] 2字节  Section 数量
      [0x10] ...    各 Section 依次排列

    每个 Section：
      [+0x00] 4字节  Section 魔术字
      [+0x04] 4字节  Section 大小（含这8字节头）
      [+0x08] ...    Section 数据

    返回：dict { section_magic_str: section_data_bytes }
    """
    if len(data) < 0x10:
        raise ValueError("文件太短，不是有效的 NDS 容器格式")

    header_size   = struct.unpack_from('<H', data, 0x0C)[0]
    section_count = struct.unpack_from('<H', data, 0x0E)[0]

    sections = {}
    offset = header_size  # 通常是 0x10

    for _ in range(section_count):
        if offset + 8 > len(data):
            break
        magic      = data[offset:offset+4].decode('ascii', errors='replace')
        sec_size   = struct.unpack_from('<I', data, offset + 4)[0]
        sec_data   = data[offset + 8 : offset + sec_size]
        sections[magic] = sec_data
        offset += sec_size

    return sections


# ============================================================
# NCLR 解析：调色板
# ============================================================

def parse_nclr(nclr_data):
    """
    解析 NCLR 文件，返回 BGR555 调色板列表（每项为 (r,g,b) 三元组，0-255范围）。

    TTLP Section 结构：
      [0x00] 2字节  位深 (0x0003=4bpp, 0x0004=8bpp)
      [0x02] 2字节  保留
      [0x04] 4字节  调色板数据大小
      [0x08] 4字节  调色板数据偏移（相对 Section 数据起始）
      [调色板数据]  每色 2字节，BGR555 小端
    """
    sections = parse_nds_container(nclr_data)

    if 'TTLP' not in sections:
        raise ValueError("NCLR 文件中未找到 TTLP Section")

    ttlp = sections['TTLP']
    pal_size   = struct.unpack_from('<I', ttlp, 0x04)[0]
    pal_offset = struct.unpack_from('<I', ttlp, 0x08)[0]

    # 读取调色板原始数据
    raw = ttlp[pal_offset : pal_offset + pal_size]
    colors = []
    for i in range(len(raw) // 2):
        col = struct.unpack_from('<H', raw, i * 2)[0]
        r = (col & 0x1F) * 8
        g = ((col >> 5) & 0x1F) * 8
        b = ((col >> 10) & 0x1F) * 8
        colors.append((r, g, b))

    # 补齐到 256 色
    while len(colors) < 256:
        colors.append((0, 0, 0))

    return colors


# ============================================================
# NCGR 解析：图块数据
# ============================================================

def parse_ncgr(ncgr_data):
    """
    解析 NCGR 文件，返回 (tiles, bpp, tile_count)。

    RAHC Section 结构：
      [0x00] 2字节  图块高度（单位：图块数，即像素高/8）
      [0x02] 2字节  图块宽度（单位：图块数，即像素宽/8）
      [0x04] 4字节  位深 (0x03=4bpp, 0x04=8bpp)
      [0x08] 4字节  保留
      [0x0C] 4字节  图块数据大小
      [0x10] 4字节  图块数据偏移（相对 Section 数据起始）
      [图块数据]    每个图块 8×8 像素

    返回：
      tiles      : list of bytes，每项是一个 8×8 图块的像素索引（长度64）
      bpp        : 4 或 8
      tile_h     : 图块行数（可能为 0xFFFF 表示未知，需从数据量推算）
      tile_w     : 图块列数
    """
    sections = parse_nds_container(ncgr_data)

    if 'RAHC' not in sections:
        raise ValueError("NCGR 文件中未找到 RAHC Section")

    rahc = sections['RAHC']

    tile_h_count = struct.unpack_from('<H', rahc, 0x00)[0]
    tile_w_count = struct.unpack_from('<H', rahc, 0x02)[0]
    bpp_flag     = struct.unpack_from('<I', rahc, 0x04)[0]
    tile_data_size   = struct.unpack_from('<I', rahc, 0x0C)[0]
    tile_data_offset = struct.unpack_from('<I', rahc, 0x10)[0]

    bpp = 4 if bpp_flag == 3 else 8

    raw_tiles = rahc[tile_data_offset : tile_data_offset + tile_data_size]

    # 解码图块：每个图块 8×8 像素
    bytes_per_tile = 8 * 8 // (8 // bpp)  # 4bpp=32字节, 8bpp=64字节
    tile_count = len(raw_tiles) // bytes_per_tile

    tiles = []
    for t in range(tile_count):
        tile_raw = raw_tiles[t * bytes_per_tile : (t+1) * bytes_per_tile]
        pixels = []
        if bpp == 8:
            pixels = list(tile_raw)
        else:  # 4bpp：每字节存两个像素，低4位在前
            for byte in tile_raw:
                pixels.append(byte & 0x0F)
                pixels.append((byte >> 4) & 0x0F)
        tiles.append(pixels)

    return tiles, bpp, tile_h_count, tile_w_count


# ============================================================
# NSCR 解析：图块地图
# ============================================================

def parse_nscr(nscr_data):
    """
    解析 NSCR 文件，返回 (map_entries, map_w_tiles, map_h_tiles)。

    NRCS Section 结构：
      [0x00] 2字节  地图宽度（像素）
      [0x02] 2字节  地图高度（像素）
      [0x04] 2字节  保留
      [0x06] 2字节  保留
      [0x08] 4字节  地图数据大小
      [0x0C] ...    地图数据，每格 2字节

    每格 2字节（小端）：
      bit  0-9  : 图块索引
      bit 10    : 水平翻转
      bit 11    : 垂直翻转
      bit 12-15 : 调色板编号（4bpp时有效，8bpp时通常为0）
    """
    sections = parse_nds_container(nscr_data)

    if 'NRCS' not in sections:
        raise ValueError("NSCR 文件中未找到 NRCS Section")

    nrcs = sections['NRCS']

    map_w_px = struct.unpack_from('<H', nrcs, 0x00)[0]
    map_h_px = struct.unpack_from('<H', nrcs, 0x02)[0]
    map_data_size = struct.unpack_from('<I', nrcs, 0x08)[0]
    map_raw = nrcs[0x0C : 0x0C + map_data_size]

    map_w_tiles = map_w_px // 8
    map_h_tiles = map_h_px // 8

    entries = []
    for i in range(len(map_raw) // 2):
        val      = struct.unpack_from('<H', map_raw, i * 2)[0]
        tile_idx = val & 0x3FF
        flip_h   = bool(val & 0x400)
        flip_v   = bool(val & 0x800)
        pal_idx  = (val >> 12) & 0xF
        entries.append((tile_idx, flip_h, flip_v, pal_idx))

    return entries, map_w_tiles, map_h_tiles, map_w_px, map_h_px


# ============================================================
# 合成最终图像
# ============================================================

def compose_bg_image(tiles, bpp, palette_colors, map_entries, map_w_tiles, map_h_tiles):
    """
    将图块、调色板、地图合成为完整的像素数组（8bpp 索引色）。

    返回：(pixel_data_bytes, final_palette_bgr0_bytes)
      pixel_data_bytes     : 宽×高 字节，每字节一个调色板索引
      final_palette_bgr0_bytes : 1024字节，BMP 用的 BGR0 调色板
    """
    width  = map_w_tiles * 8
    height = map_h_tiles * 8
    pixels = bytearray(width * height)

    for row in range(map_h_tiles):
        for col in range(map_w_tiles):
            entry_idx = row * map_w_tiles + col
            if entry_idx >= len(map_entries):
                continue

            tile_idx, flip_h, flip_v, pal_idx = map_entries[entry_idx]

            if tile_idx >= len(tiles):
                continue  # 越界图块，留黑

            tile_pixels = tiles[tile_idx]  # 64个像素索引

            # 4bpp 时，调色板偏移 = pal_idx * 16
            pal_offset = pal_idx * 16 if bpp == 4 else 0

            for py in range(8):
                src_py = (7 - py) if flip_v else py
                for px in range(8):
                    src_px = (7 - px) if flip_h else px
                    raw_idx = tile_pixels[src_py * 8 + src_px]
                    final_idx = (raw_idx + pal_offset) & 0xFF

                    dst_x = col * 8 + px
                    dst_y = row * 8 + py
                    pixels[dst_y * width + dst_x] = final_idx

    # 构建 BMP BGR0 调色板
    bmp_palette = bytearray()
    for (r, g, b) in palette_colors[:256]:
                bmp_palette.extend(struct.pack('BBBB', b, g, r, 0))
    # 补齐到 256 色 × 4 字节
    while len(bmp_palette) < 1024:
        bmp_palette.extend(b'\x00\x00\x00\x00')

    return bytes(pixels), bytes(bmp_palette)


# ============================================================
# BMP 写入（复用 stage2_export_images.py 的逻辑）
# ============================================================

def write_bmp_8bpp(filepath, width, height, pixel_data, palette_data):
    row_padding    = (4 - (width % 4)) % 4
    bmp_row_size   = width + row_padding
    image_data_size = bmp_row_size * height
    header_size    = 54
    palette_size   = 1024
    total_size     = header_size + palette_size + image_data_size
    data_offset    = header_size + palette_size

    with open(filepath, 'wb') as out:
        out.write(b'BM')
        out.write(struct.pack('<I', total_size))
        out.write(b'\x00\x00\x00\x00')
        out.write(struct.pack('<I', data_offset))
        out.write(struct.pack('<I', 40))
        out.write(struct.pack('<i', width))
        out.write(struct.pack('<i', height))
        out.write(struct.pack('<H', 1))
        out.write(struct.pack('<H', 8))
        out.write(struct.pack('<I', 0))
        out.write(struct.pack('<I', image_data_size))
        out.write(struct.pack('<I', 0))
        out.write(struct.pack('<I', 0))
        out.write(struct.pack('<I', 0))
        out.write(struct.pack('<I', 0))
        out.write(palette_data)
        padding = b'\x00' * row_padding
        for row in range(height - 1, -1, -1):
            start = row * width
            out.write(pixel_data[start:start + width])
            out.write(padding)


# ============================================================
# 三文件配对逻辑
# ============================================================

def find_bg_triplets(input_dir):
    """
    扫描 BG 目录，将 NCGR/NCLR/NSCR 按 base_name 配对。

    命名规律：同一张背景图的三个文件 base_name 相同，
    编号连续（如 0000/0001/0002），扩展名不同。

    返回：list of (ncgr_path, nclr_path, nscr_path, base_name)
    """
    ncgr_files = {f.stem: f for f in input_dir.glob('*.NCGR')}
    ncgr_files.update({f.stem: f for f in input_dir.glob('*.ncgr')})
    nclr_files = {f.stem: f for f in input_dir.glob('*.NCLR')}
    nclr_files.update({f.stem: f for f in input_dir.glob('*.nclr')})
    nscr_files = {f.stem: f for f in input_dir.glob('*.NSCR')}
    nscr_files.update({f.stem: f for f in input_dir.glob('*.nscr')})

    # 按 base_name（去掉编号前缀后的部分）配对
    # 例：0000_AUDITION_VOC → AUDITION_VOC
    # 策略：直接用 NCGR 的 stem 去找同名的 NCLR 和 NSCR
    # 但编号不同，所以改用"去掉4位编号后的名称"来匹配
    def strip_prefix(stem):
        """去掉 '0000_' 这样的数字前缀，返回后面的 base"""
        parts = stem.split('_', 1)
        if len(parts) == 2 and parts[0].isdigit():
            return parts[1]
        return stem

    # 建立 base → stem 的映射
    ncgr_by_base = {strip_prefix(s): s for s in ncgr_files}
    nclr_by_base = {strip_prefix(s): s for s in nclr_files}
    nscr_by_base = {strip_prefix(s): s for s in nscr_files}

    triplets = []
    for base, ncgr_stem in sorted(ncgr_by_base.items()):
        if base in nclr_by_base and base in nscr_by_base:
            triplets.append((
                ncgr_files[ncgr_stem],
                nclr_files[nclr_by_base[base]],
                nscr_files[nscr_by_base[base]],
                base,
                ncgr_stem,   # 用 NCGR 的完整 stem 作为输出文件名
            ))
        else:
            missing = []
            if base not in nclr_by_base: missing.append('NCLR')
            if base not in nscr_by_base: missing.append('NSCR')
            print(f"   ⚠️  {ncgr_stem}: 缺少配对文件 {missing}，跳过。")

    return triplets


# ============================================================
# 单组导出
# ============================================================

def export_bg_triplet(ncgr_path, nclr_path, nscr_path, output_path):
    """将一组 NCGR+NCLR+NSCR 合成并导出为 BMP。"""
    ncgr_data = ncgr_path.read_bytes()
    nclr_data = nclr_path.read_bytes()
    nscr_data = nscr_path.read_bytes()

    palette_colors              = parse_nclr(nclr_data)
    tiles, bpp, _, _            = parse_ncgr(ncgr_data)
    entries, map_w, map_h, w_px, h_px = parse_nscr(nscr_data)

    pixel_data, bmp_palette = compose_bg_image(
        tiles, bpp, palette_colors, entries, map_w, map_h
    )

    write_bmp_8bpp(output_path, w_px, h_px, pixel_data, bmp_palette)


# ============================================================
# 批量导出入口
# ============================================================

def main():
    input_dir  = EXTRACT_DIR / "BG"
    output_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / "BG"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        print(f"❌ BG 目录不存在: {input_dir}")
        return

    triplets = find_bg_triplets(input_dir)
    if not triplets:
        print("❌ 未找到任何可配对的 NCGR/NCLR/NSCR 文件组。")
        return

    print(f"🎨 正在处理 BG 下的 {len(triplets)} 组背景图...")
    ok = 0
    err = 0
    for ncgr_path, nclr_path, nscr_path, base, ncgr_stem in triplets:
        output_path = output_dir / f"{ncgr_stem}.bmp"
        try:
            export_bg_triplet(ncgr_path, nclr_path, nscr_path, output_path)
            print(f"   ✅ {ncgr_stem}.bmp")
            ok += 1
        except Exception as e:
            print(f"   ❌ {ncgr_stem}: {e}")
            err += 1

    print(f"\n   📊 BG 导出完毕：成功 {ok} | 错误 {err}")
    print(f"   输出目录: {output_dir}")

if __name__ == "__main__":
    main()
