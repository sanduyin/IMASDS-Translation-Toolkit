# src/stage2_export_bg.py
import os
import sys
import struct
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR

def parse_nds_container(data):
    if len(data) < 0x10: return {}
    header_size = struct.unpack_from('<H', data, 0x0C)[0]
    section_count = struct.unpack_from('<H', data, 0x0E)[0]

    sections = {}
    offset = header_size

    for _ in range(section_count):
        if offset + 8 > len(data): break
        magic = data[offset:offset+4].decode('ascii', errors='replace')
        sec_size = struct.unpack_from('<I', data, offset + 4)[0]
        sections[magic] = data[offset + 8 : offset + sec_size]
        offset += sec_size
    return sections

def parse_nclr(nclr_data):
    sections = parse_nds_container(nclr_data)
    # 兼容 TTLP 或 PLTT
    sec_data = sections.get('TTLP') or sections.get('PLTT')
    if not sec_data: raise ValueError("未找到调色板 Section")

    # 【核心修复】修正调色板的读取偏移量为 0x08 和 0x0C
    pal_size = struct.unpack_from('<I', sec_data, 0x08)[0]
    pal_offset = struct.unpack_from('<I', sec_data, 0x0C)[0]

    # 兜底防错
    if pal_offset + pal_size > len(sec_data):
        pal_offset = 0x10

    raw = sec_data[pal_offset : pal_offset + pal_size]
    colors =[]
    for i in range(len(raw) // 2):
        col = struct.unpack_from('<H', raw, i * 2)[0]
        r = (col & 0x1F) * 8
        g = ((col >> 5) & 0x1F) * 8
        b = ((col >> 10) & 0x1F) * 8
        colors.append((r, g, b))

    while len(colors) < 256:
        colors.append((0, 0, 0))

    return colors

def parse_ncgr(ncgr_data):
    sections = parse_nds_container(ncgr_data)
    rahc = sections.get('RAHC') or sections.get('CHAR')
    if not rahc: raise ValueError("未找到图块 Section")

    tile_h_count, tile_w_count = struct.unpack_from('<H', rahc, 0x00)[0], struct.unpack_from('<H', rahc, 0x02)[0]
    bpp_flag = struct.unpack_from('<I', rahc, 0x04)[0]
    tile_data_size, tile_data_offset = struct.unpack_from('<I', rahc, 0x0C)[0], struct.unpack_from('<I', rahc, 0x10)[0]

    bpp = 4 if bpp_flag == 3 else 8
    raw_tiles = rahc[tile_data_offset : tile_data_offset + tile_data_size]

    bytes_per_tile = 8 * 8 // (8 // bpp)
    tile_count = len(raw_tiles) // bytes_per_tile

    tiles =[]
    for t in range(tile_count):
        tile_raw = raw_tiles[t * bytes_per_tile : (t+1) * bytes_per_tile]
        pixels =[]
        if bpp == 8:
            pixels = list(tile_raw)
        else:
            for byte in tile_raw:
                pixels.append(byte & 0x0F)
                pixels.append((byte >> 4) & 0x0F)
        tiles.append(pixels)
    return tiles, bpp, tile_h_count, tile_w_count

def parse_nscr(nscr_data):
    sections = parse_nds_container(nscr_data)
    nrcs = sections.get('NRCS') or sections.get('SCRN')
    if not nrcs: raise ValueError("未找到地图 Section")

    map_w_px, map_h_px = struct.unpack_from('<H', nrcs, 0x00)[0], struct.unpack_from('<H', nrcs, 0x02)[0]
    map_data_size = struct.unpack_from('<I', nrcs, 0x08)[0]
    map_raw = nrcs[0x0C : 0x0C + map_data_size]

    map_w_tiles, map_h_tiles = map_w_px // 8, map_h_px // 8

    entries =[]
    for i in range(len(map_raw) // 2):
        val = struct.unpack_from('<H', map_raw, i * 2)[0]
        tile_idx, flip_h, flip_v, pal_idx = val & 0x3FF, bool(val & 0x400), bool(val & 0x800), (val >> 12) & 0xF
        entries.append((tile_idx, flip_h, flip_v, pal_idx))

    return entries, map_w_tiles, map_h_tiles, map_w_px, map_h_px

def compose_bg_image(tiles, bpp, palette_colors, map_entries, map_w_tiles, map_h_tiles):
    width, height = map_w_tiles * 8, map_h_tiles * 8
    pixels = bytearray(width * height)

    for row in range(map_h_tiles):
        for col in range(map_w_tiles):
            entry_idx = row * map_w_tiles + col
            if entry_idx >= len(map_entries): continue

            tile_idx, flip_h, flip_v, pal_idx = map_entries[entry_idx]
            if tile_idx >= len(tiles): continue

            tile_pixels = tiles[tile_idx]
            pal_offset = pal_idx * 16 if bpp == 4 else 0

            for py in range(8):
                src_py = (7 - py) if flip_v else py
                for px in range(8):
                    src_px = (7 - px) if flip_h else px
                    raw_idx = tile_pixels[src_py * 8 + src_px]
                    final_idx = (raw_idx + pal_offset) & 0xFF

                    dst_x, dst_y = col * 8 + px, row * 8 + py
                    pixels[dst_y * width + dst_x] = final_idx

    bmp_palette = bytearray()
    for (r, g, b) in palette_colors[:256]:
        bmp_palette.extend(struct.pack('BBBB', b, g, r, 0))
    while len(bmp_palette) < 1024:
        bmp_palette.extend(b'\x00\x00\x00\x00')

    return bytes(pixels), bytes(bmp_palette)

def write_bmp_8bpp(filepath, width, height, pixel_data, palette_data):
    row_padding = (4 - (width % 4)) % 4
    bmp_row_size = width + row_padding
    image_data_size = bmp_row_size * height
    total_size = 54 + 1024 + image_data_size

    with open(filepath, 'wb') as out:
        out.write(b'BM')
        out.write(struct.pack('<I', total_size))
        out.write(b'\x00\x00\x00\x00')
        out.write(struct.pack('<I', 54 + 1024))
        out.write(struct.pack('<I', 40))
        out.write(struct.pack('<i', width))
        out.write(struct.pack('<i', height))
        out.write(struct.pack('<H', 1))
        out.write(struct.pack('<H', 8))
        out.write(struct.pack('<I', 0))
        out.write(struct.pack('<I', image_data_size))
        out.write(struct.pack('<I', 0) * 4)
        out.write(palette_data)
        padding = b'\x00' * row_padding
        for row in range(height - 1, -1, -1):
            start = row * width
            out.write(pixel_data[start:start + width])
            out.write(padding)

def find_bg_triplets(input_dir):
    def strip_prefix(stem):
        parts = stem.split('_', 1)
        return parts[1] if len(parts) == 2 and parts[0].isdigit() else stem

    ncgr_files = {strip_prefix(f.stem): f for f in input_dir.glob('*.[nN][cC][gG][rR]')}
    nclr_files = {strip_prefix(f.stem): f for f in input_dir.glob('*.[nN][cC][lL][rR]')}
    nscr_files = {strip_prefix(f.stem): f for f in input_dir.glob('*.[nN][sS][cC][rR]')}

    triplets =[]
    for base, ncgr_path in sorted(ncgr_files.items()):
        if base in nclr_files and base in nscr_files:
            triplets.append((ncgr_path, nclr_files[base], nscr_files[base], base, ncgr_path.stem))
    return triplets

def main():
    input_dir, output_dir = EXTRACT_DIR / "BG", EXTRACT_DIR.parent / "1_Extracted_Images" / "BG"
    output_dir.mkdir(parents=True, exist_ok=True)
    if not input_dir.exists(): return

    triplets = find_bg_triplets(input_dir)
    print(f"🎨 正在处理 BG 下的 {len(triplets)} 组背景图...")
    for ncgr, nclr, nscr, _, stem in triplets:
        try:
            ncgr_data, nclr_data, nscr_data = ncgr.read_bytes(), nclr.read_bytes(), nscr.read_bytes()
            palette_colors = parse_nclr(nclr_data)
            tiles, bpp, _, _ = parse_ncgr(ncgr_data)
            entries, map_w, map_h, w_px, h_px = parse_nscr(nscr_data)
            pixel_data, bmp_palette = compose_bg_image(tiles, bpp, palette_colors, entries, map_w, map_h)
            write_bmp_8bpp(output_dir / f"{stem}.bmp", w_px, h_px, pixel_data, bmp_palette)
        except Exception as e: print(f"   ❌ {stem}: {e}")
    print(f"   ✅ BG 导出完毕。")

if __name__ == "__main__":
    main()