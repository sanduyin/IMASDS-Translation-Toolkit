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
    
    NDS 的图像通常缺乏宽度信息，因此通过外部穷举计算出长宽后，
    利用此函数将其封装为 Windows 能够直接预览的 BMP 文件。
    """
    # BMP 文件规范要求每一行的字节数必须是 4 的倍数，需要计算填充量
    row_padding = (4 - (width % 4)) % 4
    bmp_row_size = width + row_padding
    image_data_size = bmp_row_size * height
    
    header_size = 54
    palette_size = 1024 # 256 种颜色，每种颜色 4 字节 (BGRA)
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

        # 4. 写入像素数据
        # 注意：BMP 的像素存储顺序是自下而上的 (Bottom-Up)，因此需要垂直倒序写入
        padding = b'\x00' * row_padding
        for row in range(height - 1, -1, -1):
            start = row * width
            end = start + width
            out.write(pixel_data[start:end])
            out.write(padding)

def convert_gld_to_bmp(filepath, output_dir):
    """
    解析 NDS 专用的 .gld 图像文件。
    
    因为 .gld 头文件只包含数据大小和调色板偏移，未包含明确的宽度和高度，
    所以这里采用启发式算法，穷举可能的宽度(16 到 1024 之间)，生成多张图片供人工筛选。
    """
    filename = os.path.basename(filepath)
    base_name = os.path.splitext(filename)[0]
    
    with open(filepath, 'rb') as f:
        # 读取并解析文件头 (32 bytes)
        header_data = f.read(32)
        if len(header_data) < 32:
            return

        params = struct.unpack('<8I', header_data)
        # params[3] 记录了像素数据块的总大小，这同时也是调色板数据相对头部的偏移量
        pixel_size = params[3]
        palette_offset = params[3] + 32
        
        # 跳转并读取调色板数据 (固定读取 256 个颜色的空间，即 512 字节)
        f.seek(palette_offset)
        raw_palette = f.read(512)
        
        # 转换 NDS 15位色调色板为标准的 24位色 BMP 调色板
        bmp_palette = bytearray()
        for i in range(256):
            if i * 2 + 1 < len(raw_palette):
                # NDS 颜色格式：1位 Alpha，5位 B，5位 G，5位 R
                col = struct.unpack('<H', raw_palette[i*2 : i*2+2])[0]
                r = (col & 0x1F) * 8
                g = ((col >> 5) & 0x1F) * 8
                b = ((col >> 10) & 0x1F) * 8
                # BMP 要求的顺序是 B, G, R, 0(Reserved)
                bmp_palette.extend(struct.pack('BBBB', b, g, r, 0))
            else:
                bmp_palette.extend(b'\x00\x00\x00\x00')

        # 读取所有的像素索引数据
        f.seek(32)
        raw_pixels = f.read(pixel_size)

        # 启发式穷举寻找合适的宽度
        # NDS 的图像宽度通常为 4 的倍数，且宽度最小一般为 16 像素
        valid_widths =[]
        for w in range(16, 1025):
            if pixel_size % w == 0 and w % 4 == 0:
                valid_widths.append(w)

        if not valid_widths:
            return

        # 为每一个可能合法的宽度生成一张位图
        for width in valid_widths:
            height = pixel_size // width
            
            # 排除长宽比异常的无效结果 (如 1024x2 的细长条)
            if width / height > 16 or height / width > 16:
                continue

            save_name = f"{base_name}_{width}.bmp"
            save_path = output_dir / save_name
            write_bmp_8bpp(save_path, width, height, raw_pixels, bmp_palette)

def batch_process_images(target_folders):
    """批量处理指定列表中的所有图像文件夹"""
    for folder_name in target_folders:
        input_dir = EXTRACT_DIR / folder_name
        
        if not input_dir.exists():
            print(f"⏭️  跳过未找到的图像目录: {input_dir}")
            continue
            
        # 设置在解压目录外层的专用导出目录，避免污染原解包文件
        output_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / folder_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        files =[f for f in os.listdir(input_dir) if f.lower().endswith('.gld')]
        if not files:
            continue
            
        print(f"🎨 正在处理 {folder_name} 下的 {len(files)} 个 GLD 图像文件...")
        for f in files:
            convert_gld_to_bmp(input_dir / f, output_dir)
            
        print(f"   ✅ {folder_name} 图片导出至: {output_dir}")

def main():
    # 可以根据需要在这里添加其他可能含有 .gld 的文件夹，如 "BG", "OBJ" 等
    target_folders = ["TEX", "TBL", "BG"]
    batch_process_images(target_folders)
    print("提示: 对于同一张图片导出的多个版本 (如 _128, _256)，请手动保留显示正常的那个并删除其它废弃图片。")

if __name__ == "__main__":
    main()