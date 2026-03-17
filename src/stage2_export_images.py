# src/stage2_export_images.py
import os
import sys
import struct
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR

def write_bmp_8bpp(filepath, width, height, pixel_data, palette_data):
    """
    构建并写入标准的 8位索引颜色 BMP 图像文件。
    """
    row_padding = (4 - (width % 4)) % 4
    bmp_row_size = width + row_padding
    image_data_size = bmp_row_size * height
    
    header_size = 54
    palette_size = 1024  # 256 种颜色，每种颜色 4 字节 (BGRA)
    total_size = header_size + palette_size + image_data_size
    data_offset = header_size + palette_size

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
            end = start + width
            out.write(pixel_data[start:end])
            out.write(padding)

def parse_gld_common(filepath):
    """
    读取 .GLD 文件的公共部分：解析头部、调色板、像素数据。
    """
    with open(filepath, 'rb') as f:
        header_data = f.read(32)
        if len(header_data) < 32:
            return None

        params = struct.unpack('<8I', header_data)
        pixel_size = params[3]
        palette_offset = pixel_size + 32

        f.seek(palette_offset)
        raw_palette = f.read(512)

        bmp_palette = bytearray()
        for i in range(256):
            if i * 2 + 1 < len(raw_palette):
                col = struct.unpack('<H', raw_palette[i*2 : i*2+2])[0]
                r = (col & 0x1F) * 8
                g = ((col >> 5) & 0x1F) * 8
                b = ((col >> 10) & 0x1F) * 8
                bmp_palette.extend(struct.pack('BBBB', b, g, r, 0))
            else:
                bmp_palette.extend(b'\x00\x00\x00\x00')

        f.seek(32)
        raw_pixels = f.read(pixel_size)

    return pixel_size, raw_pixels, bmp_palette

def convert_gld_fixed_width(filepath, output_dir, fixed_width=256):
    """以固定宽度导出 .GLD"""
    result = parse_gld_common(filepath)
    if result is None: return

    pixel_size, raw_pixels, bmp_palette = result
    if pixel_size % fixed_width != 0:
        print(f"   ⚠️  {os.path.basename(filepath)}: 像素总量 {pixel_size} 无法被宽度 {fixed_width} 整除，跳过。")
        return

    height = pixel_size // fixed_width
    
    # 防线1：固定高度不能为0
    if height == 0: return

    base_name = os.path.splitext(os.path.basename(filepath))[0]
    save_path = output_dir / f"{base_name}_{fixed_width}.bmp"
    write_bmp_8bpp(save_path, fixed_width, height, raw_pixels, bmp_palette)

def convert_gld_to_bmp(filepath, output_dir):
    """以启发式穷举方式导出 .GLD"""
    result = parse_gld_common(filepath)
    if result is None: return

    pixel_size, raw_pixels, bmp_palette = result
    base_name = os.path.splitext(os.path.basename(filepath))[0]

    valid_widths =[w for w in range(16, 1025) if pixel_size % w == 0 and w % 4 == 0]
    if not valid_widths: return

    for width in valid_widths:
        height = pixel_size // width
        
        # 【核心修复防线】：如果猜的宽度比图片像素总量还大，算出来高度为0，直接跳过！
        if height == 0 or width == 0:
            continue

        # 排除长宽比异常的无效结果
        if width / height > 16 or height / width > 16:
            continue
            
        save_path = output_dir / f"{base_name}_{width}.bmp"
        write_bmp_8bpp(save_path, width, height, raw_pixels, bmp_palette)

FOLDER_STRATEGIES = {
    "TEX": {"mode": "fixed", "width": 256},
    "TBL": {"mode": "fixed", "width": 256, "fallback": True},
    "BG":  {"mode": "bruteforce"},
    "AGL": {"mode": "bruteforce"},
}

def batch_process_images(folder_strategies):
    for folder_name, strategy in folder_strategies.items():
        input_dir = EXTRACT_DIR / folder_name

        if not input_dir.exists():
            print(f"⏭️  跳过未找到的图像目录: {input_dir}")
            continue

        output_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / folder_name
        output_dir.mkdir(parents=True, exist_ok=True)

        files =[f for f in os.listdir(input_dir) if f.lower().endswith('.gld')]
        if not files:
            print(f"⏭️  {folder_name} 下未找到 GLD 文件，跳过。")
            continue

        mode        = strategy.get("mode", "bruteforce")
        fixed_width = strategy.get("width", 256)
        fallback    = strategy.get("fallback", False)

        if mode == "fixed":
            print(f"🎨 正在处理 {folder_name} 下的 {len(files)} 个 GLD 文件（优先宽度 {fixed_width}px）...")
            bruteforce_list =[]

            for f in files:
                filepath = input_dir / f
                result = parse_gld_common(filepath)
                if result is None: continue
                pixel_size, raw_pixels, bmp_palette = result

                if pixel_size % fixed_width == 0:
                    height = pixel_size // fixed_width
                    if height == 0: continue
                    base_name = os.path.splitext(f)[0]
                    save_path = output_dir / f"{base_name}_{fixed_width}.bmp"
                    write_bmp_8bpp(save_path, fixed_width, height, raw_pixels, bmp_palette)
                elif fallback:
                    bruteforce_list.append(f)
                else:
                    print(f"   ⚠️  {f}: 像素总量 {pixel_size} 无法被宽度 {fixed_width} 整除，跳过。")

            print(f"   ✅ {folder_name} 固定宽度部分导出至: {output_dir}")

            if bruteforce_list:
                print(f"   🔄 {folder_name} 有 {len(bruteforce_list)} 个文件无法用 {fixed_width}px，降级为穷举模式...")
                for f in bruteforce_list:
                    convert_gld_to_bmp(input_dir / f, output_dir)
                print(f"   ✅ {folder_name} 穷举部分导出至: {output_dir}")
                print(f"   💡 提示：以下文件有多个版本，请手动保留显示正常的那个：")
                for f in bruteforce_list: print(f"      · {f}")

        else:
            print(f"🎨 正在处理 {folder_name} 下的 {len(files)} 个 GLD 文件（穷举宽度模式）...")
            for f in files:
                convert_gld_to_bmp(input_dir / f, output_dir)
            print(f"   ✅ {folder_name} 图片导出至: {output_dir}")

def main():
    batch_process_images(FOLDER_STRATEGIES)

if __name__ == "__main__":
    main()