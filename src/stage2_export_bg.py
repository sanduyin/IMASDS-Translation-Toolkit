# src/stage2_export_bg.py
from __future__ import annotations

import os
import sys
import struct
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR

def parse_nds_container(data: bytes) -> dict[str, bytes]:
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

def parse_nclr(nclr_data: bytes) -> list[tuple[int, int, int]]:
    sections = parse_nds_container(nclr_data)
    # 兼容 TTLP 或 PLTT
    sec_data = sections.get('TTLP') or sections.get('PLTT')
    if not sec_data: raise ValueError("未找到调色板 Section")

    # TTLP/PLTT body 结构（GBATEK + 实测验证）：
    #   0x00  4  hdr_size（实测值不可靠：0x3 或 0x4，不能用作偏移）
    #   0x04  4  pal_data_size（实测全为 0，不可靠）
    #   0x08  4  color_count（实测全为 512，不可靠）
    #   0x0C  4  bit_depth（实测全为 0x10，不可靠）
    #   0x10  -  palette data（RGB555，每色 2 字节，共 256 色 = 512 字节）
    # 实测所有 NCLR body 长 528 字节 = 0x10 头 + 512 字节调色板数据。
    # 旧代码误用 0x18 作为偏移，导致：
    #   1) 8bpp 索引 252-255 越界补齐为黑色（原应为白色 0x7FFF）→ "白色变黑色"
    #   2) 4bpp 子调色板边界错位 4 个颜色 → 调色板分配混乱
    pal_offset = 0x10
    pal_size = len(sec_data) - pal_offset
    raw = sec_data[pal_offset : pal_offset + pal_size]
    colors =[]
    for i in range(len(raw) // 2):
        col = struct.unpack_from('<H', raw, i * 2)[0]
        # RGB555 → RGB888：直接 <<3（NDS 硬件实际行为）
        # NDS LCD 硬件就是直接 <<3 不做高位回填，所以 31→248（不是 255）。
        # 旧代码用 (v<<3)|(v>>2) 高位回填，虽然数学上"精确"，但与硬件行为不一致，
        # 导致导出的颜色与游戏实际显示不符。
        r5 = col & 0x1F
        g5 = (col >> 5) & 0x1F
        b5 = (col >> 10) & 0x1F
        r = r5 << 3
        g = g5 << 3
        b = b5 << 3
        colors.append((r, g, b))

    while len(colors) < 256:
        colors.append((0, 0, 0))

    return colors

def parse_ncgr(ncgr_data: bytes) -> tuple[list[list[int]], int, int, int]:
    sections = parse_nds_container(ncgr_data)
    rahc = sections.get('RAHC') or sections.get('CHAR')
    if not rahc: raise ValueError("未找到图块 Section")

    tile_h_count, tile_w_count = struct.unpack_from('<H', rahc, 0x00)[0], struct.unpack_from('<H', rahc, 0x02)[0]
    bpp_flag = struct.unpack_from('<I', rahc, 0x04)[0]

    # RAHC header 字段（GBATEK + Tinke + ndspy 实测，与 NCLR 一样字段不可靠）：
    #   0x08  u32  tile_count（0 = 自动计算）
    #   0x0C  u32  保留/padding（实测全为 0，旧代码误当作 tile_data_size）
    #   0x10  u32  tile_data_size（图块数据字节数，旧代码误当作 offset）
    #   0x14  u32  header_size = data 起始偏移（通常 0x18，旧代码完全没读此字段）
    # 旧代码把 0x0C 当 size(=0)、0x10 当 offset(=14336)，导致 raw_tiles 为空 →
    # 所有 tile 像素为 0 → 渲染纯单色（color index 0）。
    tile_data_size_field = struct.unpack_from('<I', rahc, 0x10)[0]
    header_size_field = struct.unpack_from('<I', rahc, 0x14)[0]

    bpp = 4 if bpp_flag == 3 else 8

    # tile data 起始偏移 = header_size（0x14 字段），通常 0x18；异常时回退 0x18
    tile_data_offset = header_size_field if 0 < header_size_field < len(rahc) else 0x18
    # tile data 大小 = 0x10 字段；若为 0 或越界则用剩余长度
    if 0 < tile_data_size_field <= len(rahc) - tile_data_offset:
        tile_data_size = tile_data_size_field
    else:
        tile_data_size = len(rahc) - tile_data_offset

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

def parse_nscr(nscr_data: bytes) -> tuple[list[tuple[int, bool, bool, int]], int, int, int, int]:
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

def compose_bg_image(tiles: list[list[int]], bpp: int, palette_colors: list[tuple[int, int, int]],
                     map_entries: list[tuple[int, bool, bool, int]],
                     map_w_tiles: int, map_h_tiles: int) -> tuple[bytes, bytes]:
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

def write_bmp_8bpp(filepath: Path, width: int, height: int, pixel_data: bytes, palette_data: bytes) -> None:
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

def find_bg_triplets(input_dir: Path) -> list[tuple[Path, Path, Path, str, str]]:
    def strip_prefix(stem: str) -> str:
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

def main() -> None:
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