# src/stage2_export_images.py
import os
import sys
import struct
from pathlib import Path

# 导入全局配置
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
        # 1. BMP 文件头 (14 bytes)
        out.write(b'BM')
        out.write(struct.pack('<I', total_size))
        out.write(b'\x00\x00\x00\x00')
        out.write(struct.pack('<I', data_offset))

        # 2. DIB 信息头 (40 bytes)
        out.write(struct.pack('<I', 40))
        out.write(struct.pack('<i', width))
        out.write(struct.pack('<i', height))
        out.write(struct.pack('<H', 1))     # 颜色平面数 (固定为 1)
        out.write(struct.pack('<H', 8))     # 颜色深度 (8-bit)
        out.write(struct.pack('<I', 0))     # 不压缩
        out.write(struct.pack('<I', image_data_size))
        out.write(struct.pack('<I', 0))     # 水平分辨率 (忽略)
        out.write(struct.pack('<I', 0))     # 垂直分辨率 (忽略)
        out.write(struct.pack('<I', 0))     # 调色板颜色数 (默认 256)
        out.write(struct.pack('<I', 0))     # 重要颜色数 (忽略)

        # 3. 写入颜色调色板数据 (BGR0 格式)
        out.write(palette_data)

        # 4. 写入像素数据 (BMP 自下而上存储，需垂直倒序)
        padding = b'\x00' * row_padding
        for row in range(height - 1, -1, -1):
            start = row * width
            end = start + width
            out.write(pixel_data[start:end])
            out.write(padding)

def parse_gld_common(filepath):
    """
    读取 .GLD 文件的公共部分：解析头部、调色板、像素数据。
    返回 (pixel_size, raw_pixels, bmp_palette)，失败则返回 None。
    """
    with open(filepath, 'rb') as f:
        header_data = f.read(32)
        if len(header_data) < 32:
            return None

        params = struct.unpack('<8I', header_data)
        pixel_size = params[3]
        palette_offset = pixel_size + 32

        # 读取调色板
        f.seek(palette_offset)
        raw_palette = f.read(512)

        # 转换 NDS 15位色 → BMP 24位色调色板
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

        # 读取像素数据
        f.seek(32)
        raw_pixels = f.read(pixel_size)

    return pixel_size, raw_pixels, bmp_palette


def convert_gld_fixed_width(filepath, output_dir, fixed_width=256):
    """
    以固定宽度导出 .GLD 为 BMP，专用于 TEX 文件夹。
    TEX 下的 GLD 图像宽度固定为 256px，无需穷举。
    """
    result = parse_gld_common(filepath)
    if result is None:
        return

    pixel_size, raw_pixels, bmp_palette = result

    # 检查像素总量能否被固定宽度整除
    if pixel_size % fixed_width != 0:
        print(f"   ⚠️  {os.path.basename(filepath)}: 像素总量 {pixel_size} 无法被宽度 {fixed_width} 整除，跳过。")
        return

    height = pixel_size // fixed_width
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    save_path = output_dir / f"{base_name}_{fixed_width}.bmp"
    write_bmp_8bpp(save_path, fixed_width, height, raw_pixels, bmp_palette)


def convert_gld_to_bmp(filepath, output_dir):
    """
    以启发式穷举方式导出 .GLD 为 BMP，用于 BG、TBL 等宽度未知的文件夹。
    穷举 16~1024 之间所有合法宽度（4 的倍数），生成多张图供人工筛选。
    """
    result = parse_gld_common(filepath)
    if result is None:
        return

    pixel_size, raw_pixels, bmp_palette = result
    base_name = os.path.splitext(os.path.basename(filepath))[0]

    valid_widths = [w for w in range(16, 1025) if pixel_size % w == 0 and w % 4 == 0]
    if not valid_widths:
        return

    for width in valid_widths:
        height = pixel_size // width
        # 排除长宽比异常的无效结果
        if width / height > 16 or height / width > 16:
            continue
        save_path = output_dir / f"{base_name}_{width}.bmp"
        write_bmp_8bpp(save_path, width, height, raw_pixels, bmp_palette)


# ★ 文件夹处理策略配置
FOLDER_STRATEGIES = {
    # TEX：宽度固定 256px，直接导出，无需穷举
    "TEX": {"mode": "fixed", "width": 256},
    # TBL：优先尝试 256px，若像素总量无法整除则自动降级为穷举
    "TBL": {"mode": "fixed", "width": 256, "fallback": True},
    # BG：宽度未知，保留穷举模式
    "BG":  {"mode": "bruteforce"},
    "AGL":  {"mode": "bruteforce"},
}

def batch_process_images(folder_strategies):
    """批量处理指定文件夹，按各自策略导出图像"""
    for folder_name, strategy in folder_strategies.items():
        input_dir = EXTRACT_DIR / folder_name

        if not input_dir.exists():
            print(f"⏭️  跳过未找到的图像目录: {input_dir}")
            continue

        output_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / folder_name
        output_dir.mkdir(parents=True, exist_ok=True)

        files = [f for f in os.listdir(input_dir) if f.lower().endswith('.gld')]
        if not files:
            print(f"⏭️  {folder_name} 下未找到 GLD 文件，跳过。")
            continue

        mode        = strategy.get("mode", "bruteforce")
        fixed_width = strategy.get("width", 256)
        fallback    = strategy.get("fallback", False)  # ← 是否允许降级穷举

        if mode == "fixed":
            print(f"🎨 正在处理 {folder_name} 下的 {len(files)} 个 GLD 文件（优先宽度 {fixed_width}px）...")
            bruteforce_list = []  # 收集需要降级穷举的文件

            for f in files:
                filepath   = input_dir / f
                result     = parse_gld_common(filepath)
                if result is None:
                    continue
                pixel_size, raw_pixels, bmp_palette = result

                if pixel_size % fixed_width == 0:
                    # ✅ 能整除，直接用固定宽度导出
                    height    = pixel_size // fixed_width
                    base_name = os.path.splitext(f)[0]
                    save_path = output_dir / f"{base_name}_{fixed_width}.bmp"
                    write_bmp_8bpp(save_path, fixed_width, height, raw_pixels, bmp_palette)
                elif fallback:
                    # ⚠️ 不能整除，记录下来等会儿穷举
                    bruteforce_list.append(f)
                else:
                    print(f"   ⚠️  {f}: 像素总量 {pixel_size} 无法被宽度 {fixed_width} 整除，跳过。")

            print(f"   ✅ {folder_name} 固定宽度部分导出至: {output_dir}")

            # 对降级文件执行穷举
            if bruteforce_list:
                print(f"   🔄 {folder_name} 有 {len(bruteforce_list)} 个文件无法用 {fixed_width}px，降级为穷举模式...")
                for f in bruteforce_list:
                    convert_gld_to_bmp(input_dir / f, output_dir)
                print(f"   ✅ {folder_name} 穷举部分导出至: {output_dir}")
                print(f"   💡 提示：以下文件有多个版本，请手动保留显示正常的那个：")
                for f in bruteforce_list:
                    print(f"      · {f}")

        else:  # bruteforce
            print(f"🎨 正在处理 {folder_name} 下的 {len(files)} 个 GLD 文件（穷举宽度模式）...")
            for f in files:
                convert_gld_to_bmp(input_dir / f, output_dir)
            print(f"   ✅ {folder_name} 图片导出至: {output_dir}")
            print(f"   💡 提示：{folder_name} 同一图片可能有多个版本，请手动保留显示正常的那个。")


def main():
    batch_process_images(FOLDER_STRATEGIES)


if __name__ == "__main__":
    main()
