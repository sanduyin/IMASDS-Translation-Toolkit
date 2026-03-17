# src/stage4_import_images.py
"""
BMP → GLD 图像回写工具
将修改过的 BMP 文件重新写回 NDS 专用的 .GLD 格式。

【工作流程】
  ① 用 stage2_export_images.py 导出 GLD → BMP
  ② 用 Photoshop / GIMP 等工具修改 BMP（必须保持 8位索引色、画布尺寸不变）
  ③ 将修改好的 BMP 放回原导出目录：
       game_data/1_Extracted_Images/<文件夹名>/
     文件名必须保持导出时的格式，例如：
       0000_B2D_765PRO_ENTRANCE_LV3_DAYTIME_256.bmp
  ④ 运行本工具，回写后的 GLD 输出到：
       game_data/2_Patched/<文件夹名>_IMG_PATCHED/
  ⑤ 在 config.py 的 TARGET_PACKS 中加入对应模块（如 "TEX"），
     stage5_build_rom.py 打包时会自动包含这些修改。

【注意事项】
  · BMP 必须是 8位索引色（256色调色板），不支持 24/32位真彩色。
  · 图像画布尺寸（宽×高）必须与原图完全一致，不可裁剪或缩放。
  · 头部 32 字节原样保留，只替换像素数据和调色板。
"""

import os
import sys
import struct
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, PATCHED_DIR


# ============================================================
# BMP 读取
# ============================================================

def read_bmp_8bpp(bmp_path):
    """
    读取一张 8位索引色 BMP 文件。

    返回:
        width       (int)       : 图像宽度（像素）
        height      (int)       : 图像高度（像素）
        pixel_data  (bytes)     : 像素索引数组，长度 = width × height，行顺序从上到下
        raw_palette (bytes)     : BMP 调色板，BGR0 格式，共 1024 字节（256 × 4）

    异常:
        ValueError : 文件不是 BMP、不是 8bpp、或文件结构损坏时抛出
    """
    with open(bmp_path, 'rb') as f:
        # ── 文件头（14 字节）──────────────────────────────────
        magic = f.read(2)
        if magic != b'BM':
            raise ValueError(f"不是有效的 BMP 文件: {bmp_path}")

        f.read(4)                                              # 文件总大小（忽略）
        f.read(4)                                              # 保留字段
        data_offset = struct.unpack('<I', f.read(4))[0]       # 像素数据起始偏移

        # ── DIB 信息头（至少 40 字节）────────────────────────
        dib_size  = struct.unpack('<I', f.read(4))[0]
        width     = struct.unpack('<i', f.read(4))[0]
        height    = struct.unpack('<i', f.read(4))[0]
        f.read(2)                                              # 颜色平面数（忽略）
        bit_count = struct.unpack('<H', f.read(2))[0]

        if bit_count != 8:
            raise ValueError(
                f"仅支持 8位索引色 BMP，当前文件为 {bit_count}位色: {bmp_path.name}\n"
                f"    → 请在 Photoshop / GIMP 中将图像转为「索引颜色 / 256色」后再保存。"
            )

        # ── 调色板（位于 DIB 头之后，共 1024 字节）──────────
        # 调色板起始 = 文件头14字节 + DIB头dib_size字节
        palette_start = 14 + dib_size
        f.seek(palette_start)
        raw_palette = f.read(1024)   # 256色 × 4字节 (B, G, R, 保留)
        if len(raw_palette) < 1024:
            raise ValueError(
                f"调色板数据不完整（读到 {len(raw_palette)} 字节，期望 1024）: {bmp_path.name}"
            )

        # ── 像素数据（BMP 自下而上存储，需翻转）─────────────
        f.seek(data_offset)
        flip_height  = abs(height)
        row_padding  = (4 - (width % 4)) % 4
        bmp_row_size = width + row_padding

        rows = []
        for _ in range(flip_height):
            row_bytes = f.read(bmp_row_size)
            rows.append(row_bytes[:width])   # 去掉行尾对齐填充

        # height > 0 时 BMP 为自下而上，翻转为从上到下
        if height > 0:
            rows.reverse()

        pixel_data = b''.join(rows)

    return width, flip_height, pixel_data, raw_palette


# ============================================================
# 调色板格式转换：BMP BGR0 → NDS BGR555
# ============================================================

def bmp_palette_to_nds(raw_palette_bgr0):
    """
    将 BMP 的 BGR0 调色板（1024 字节）转换为 NDS BGR555 格式（512 字节）。

    NDS 颜色格式（小端 16bit）：
        bit  0- 4 : R（5位）
        bit  5- 9 : G（5位）
        bit 10-14 : B（5位）
        bit    15 : Alpha（通常为 0）

    BMP 调色板每色 4 字节顺序：B, G, R, 0
    转换方式：8bit 分量右移 3 位压缩到 5bit。
    """
    nds_palette = bytearray()
    for i in range(256):
        b = raw_palette_bgr0[i * 4 + 0]
        g = raw_palette_bgr0[i * 4 + 1]
        r = raw_palette_bgr0[i * 4 + 2]
        col = (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
        nds_palette.extend(struct.pack('<H', col))
    return bytes(nds_palette)


# ============================================================
# 单文件回写：BMP → GLD
# ============================================================

def import_bmp_to_gld(bmp_path, original_gld_path, output_gld_path):
    """
    将修改后的 BMP 写回 GLD 文件。

    流程：
      1. 读取原始 GLD，取出 32 字节头部（原样保留）并记录原始像素大小
      2. 读取 BMP，获取像素数据和调色板
      3. 校验像素总量是否与原 GLD 完全一致
      4. 将 BMP 调色板转换为 NDS BGR555 格式
      5. 按 [32字节头部 | 像素数据 | NDS调色板(512字节)] 顺序写出新 GLD

    参数:
        bmp_path          (Path) : 修改后的 BMP 文件
        original_gld_path (Path) : 原始 GLD 文件（用于读取头部和校验像素大小）
        output_gld_path   (Path) : 输出的新 GLD 文件路径
    """
    # ── 读取原始 GLD 头部 ─────────────────────────────────────
    with open(original_gld_path, 'rb') as f:
        original_header = f.read(32)
        if len(original_header) < 32:
            raise ValueError(f"原始 GLD 头部不完整: {original_gld_path.name}")
        original_pixel_size = struct.unpack('<8I', original_header)[3]

    # ── 读取 BMP ──────────────────────────────────────────────
    width, height, pixel_data, raw_palette_bgr0 = read_bmp_8bpp(bmp_path)

    # ── 校验像素总量 ──────────────────────────────────────────
    new_pixel_size = len(pixel_data)
    if new_pixel_size != original_pixel_size:
        raise ValueError(
            f"像素总量不匹配！\n"
            f"    原始 GLD : {original_pixel_size} 字节\n"
            f"    新  BMP  : {new_pixel_size} 字节（{width}×{height}）\n"
            f"    → 请确保图像尺寸与原图完全一致，不要改变画布大小。"
        )

    # ── 转换调色板并写出 ──────────────────────────────────────
    nds_palette = bmp_palette_to_nds(raw_palette_bgr0)

    output_gld_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_gld_path, 'wb') as f:
        f.write(original_header)   # 32 字节头部，原样保留
        f.write(pixel_data)        # 像素索引数据
        f.write(nds_palette)       # NDS BGR555 调色板（512 字节）

    print(f"   ✅ 已写回: {output_gld_path.name}  ({width}×{height})")


# ============================================================
# 批量处理
# ============================================================

# 只需列出你实际修改了图片的文件夹
IMPORT_FOLDERS = ["TEX", "TBL", "BG"]

def batch_import_images(import_folders):
    """
    批量扫描导出图像目录，将修改过的 BMP 写回对应的 GLD。

    目录约定（与其他 stage 保持一致）：
      BMP 来源 : game_data/1_Extracted_Images/<folder>/
      原始 GLD : game_data/1_Extracted/<folder>/
      输出 GLD : game_data/2_Patched/<folder>_IMG_PATCHED/
    """
    total_ok    = 0
    total_skip  = 0
    total_error = 0

    for folder_name in import_folders:
        bmp_dir      = EXTRACT_DIR.parent / "1_Extracted_Images" / folder_name
        original_dir = EXTRACT_DIR / folder_name
        output_dir   = PATCHED_DIR / f"{folder_name}_IMG_PATCHED"

        if not bmp_dir.exists():
            print(f"⏭️  跳过：BMP 目录不存在: {bmp_dir}")
            continue
        if not original_dir.exists():
            print(f"⏭️  跳过：原始 GLD 目录不存在: {original_dir}")
            continue

        bmp_files = sorted(f for f in os.listdir(bmp_dir) if f.lower().endswith('.bmp'))
        if not bmp_files:
            print(f"⏭️  {folder_name}: 没有找到任何 BMP 文件，跳过。")
            continue

        print(f"\n📥 正在处理 {folder_name}，共 {len(bmp_files)} 个 BMP 文件...")

        for bmp_filename in bmp_files:
            bmp_path = bmp_dir / bmp_filename

            # ── 从 BMP 文件名还原原始 GLD 文件名 ────────────────
            # 导出命名规则：{base_name}_{width}.bmp
            # 例：0000_B2D_765PRO_ENTRANCE_LV3_DAYTIME_256.bmp
            #   → 原始 GLD：0000_B2D_765PRO_ENTRANCE_LV3_DAYTIME.GLD
            stem  = Path(bmp_filename).stem       # 去掉 .bmp 后缀
            parts = stem.rsplit('_', 1)           # 从右切掉最后一段（宽度数字）

            if len(parts) != 2 or not parts[1].isdigit():
                print(f"   ⚠️  跳过（文件名格式不符，无法识别宽度后缀）: {bmp_filename}")
                total_skip += 1
                continue

            gld_base = parts[0]   # 例：0000_B2D_765PRO_ENTRANCE_LV3_DAYTIME

            # GLD 扩展名可能大写或小写，都尝试
            gld_path = None
            for ext in ['.GLD', '.gld']:
                candidate = original_dir / (gld_base + ext)
                if candidate.exists():
                    gld_path = candidate
                    break

            if gld_path is None:
                print(f"   ⚠️  跳过（找不到对应的原始 GLD）: {gld_base}.GLD")
                total_skip += 1
                continue

            output_gld_path = output_dir / gld_path.name

            try:
                import_bmp_to_gld(bmp_path, gld_path, output_gld_path)
                total_ok += 1
            except ValueError as e:
                print(f"   ❌ 错误 [{bmp_filename}]: {e}")

def main():
    batch_import_images(IMPORT_FOLDERS)
    print(f"\n🎉 图像回写完成！")
    print(f"   输出目录: {PATCHED_DIR}/<文件夹名>_IMG_PATCHED/")
    print(f"   请确认 config.py 的 TARGET_PACKS 已包含对应模块（如 \"TEX\"），")
    print(f"   否则 stage5_build_rom.py 打包时不会包含这些修改。")

if __name__ == "__main__":
    main()
