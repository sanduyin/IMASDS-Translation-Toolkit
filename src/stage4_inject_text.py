# src/stage4_inject_text.py
from __future__ import annotations

import os
import sys
import struct
import shutil
import argparse
from pathlib import Path
from typing import Any, cast

import pandas as pd

# 导入全局配置和工具类
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    EXTRACT_DIR, PATCHED_DIR,
    EXCEL_SCN, EXCEL_TBL, EXCEL_ARM9,
    CSV_SCN_DIR, CSV_TBL_DIR, CSV_ARM9_FILE,
    MAPPING_FILE, EMPTY_MARKERS
)
from src.utils.text_encoder import load_mapping, text_to_bytes
from src.io.csv_handler import (
    read_translation_table, is_csv_path, is_mail_file, read_mail_csv,
)
from src.utils.bbq_mail import rebuild_mail_bbq

# ===================================================================
#  第一部分: BBQ 封包重建 (适用于 SCN 剧情与 TBL 系统文本)
# ===================================================================

def rebuild_bbq_file(src_path: str | Path, dst_path: str | Path,
                     translations: dict[int, str], char_map: dict[str, int]) -> None:
    shutil.copy2(src_path, dst_path)
    
    if not translations: 
        return

    with open(dst_path, 'rb+') as f:
        f.seek(0, 2)
        if f.tell() < 32: return
        f.seek(0)
        
        if f.read(4) != b'\x2E\x42\x42\x51': 
            return

        f.seek(16)
        header_size = struct.unpack('<I', f.read(4))[0]
        num_sec = struct.unpack('<I', f.read(4))[0]
        
        f.seek(header_size)
        sec7_info = None
        for i in range(num_sec):
            entry_pos = header_size + i * 20
            f.seek(entry_pos)
            sec_id = struct.unpack('<I', f.read(4))[0]
            if sec_id == 7:
                vals = struct.unpack('<4I', f.read(16))
                sec7_info = {'entry_pos': entry_pos, 'values': vals}
                break
        
        if not sec7_info: return 

        entry_pos = sec7_info['entry_pos']
        ptr_tbl_rel = sec7_info['values'][0]
        num_str = sec7_info['values'][1]
        pool_rel = sec7_info['values'][2]
        
        ptr_tbl_abs = entry_pos + ptr_tbl_rel
        pool_abs = entry_pos + pool_rel
        
        f.seek(ptr_tbl_abs)
        old_pointers = list(struct.unpack(f'<{num_str}I', f.read(num_str * 4)))

        new_pool_data = bytearray()
        new_pointers =[]
        
        for i in range(num_str):
            new_pointers.append(len(new_pool_data)) 
            
            if i in translations:
                # text_to_bytes 已经自带 \x00
                new_pool_data.extend(text_to_bytes(translations[i], char_map))
            else:
                f.seek(pool_abs + old_pointers[i])
                raw = bytearray()
                while True:
                    b = f.read(1)
                    raw.extend(b)
                    if b == b'\x00': break
                new_pool_data.extend(raw)
        
        while len(new_pool_data) % 4 != 0: 
            new_pool_data.append(0)

        # =======================================================
        # 【核心优化：原地顶替与精准截断】
        # =======================================================
        # 1. 回写最新的指针表
        f.seek(ptr_tbl_abs)
        for p in new_pointers:
            f.write(struct.pack('<I', p))
            
        # 2. 定位到原始的日文文本池起始地址，直接覆盖！
        f.seek(pool_abs)
        f.write(new_pool_data)
        
        # 3. 截断文件：把后面多余的（或者之前残留的）旧数据统统砍掉！
        # 这一刀下去，文件彻底瘦身，再长的文本也不会导致冗余体积。
        f.truncate()

def process_bbq_directory(translation_path: str | Path, input_subfolder: str,
                          output_subfolder: str, char_map: dict[str, int]) -> None:
    """
    注入 BBQ 文本。支持 xlsx（单文件）和 csv（目录）两种格式。

    Args:
        translation_path: xlsx 文件路径 或 csv 目录路径
        input_subfolder:  源子目录名（如 "SCN" / "TBL"）
        output_subfolder: 输出子目录名
        char_map:         字符映射表
    """
    # 检测格式：如果是目录或 .csv 文件，用 CSV 读取；否则用 xlsx
    is_csv_mode = (isinstance(translation_path, (str, os.PathLike))
                   and (os.path.isdir(translation_path) or is_csv_path(translation_path)))

    # mail_db 仅在 CSV 模式下填充（5 列 ML 格式）；xlsx 模式邮件走旧路径
    # {filename: (final_mail, final_reply, mail_translated, reply_translated)}
    mail_db: dict[str, tuple[str, str, bool, bool]] = {}

    if is_csv_mode:
        # CSV 模式：translation_path 是目录，遍历所有 .csv 文件
        csv_dir = translation_path if os.path.isdir(translation_path) else os.path.dirname(translation_path)
        if not os.path.exists(csv_dir):
            print(f"⏭️  跳过 {input_subfolder}: 找不到 CSV 目录 {csv_dir}")
            return

        print(f"\n📂 开始注入 {input_subfolder} 文本 (CSV 模式)...")
        # ML CSV 单独读取（5 列格式，不能用 read_translation_table，否则会把
        # Mail 列当作 Text 列、把整封邮件当成 1 条 Type7 字符串注入，破坏邮件结构）
        basename = os.path.basename(os.path.normpath(csv_dir))
        ml_csv_name = f"{basename}_ML.csv"
        ml_csv_path = os.path.join(csv_dir, ml_csv_name)
        if os.path.exists(ml_csv_path):
            mail_db = read_mail_csv(ml_csv_path)
            print(f"  📧 读取邮件 CSV: {ml_csv_name} ({len(mail_db)} 个邮件)")

        trans_db: dict[str, dict[int, str]] = {}
        import glob
        for csv_path in sorted(glob.glob(os.path.join(csv_dir, "*.csv"))):
            # 跳过 ML CSV（已单独读取）
            if os.path.basename(csv_path) == ml_csv_name:
                continue
            rows = cast(dict[str, dict[int, str]], read_translation_table(csv_path, is_arm9=False))
            for fname, idx_map in rows.items():
                if fname not in trans_db:
                    trans_db[fname] = {}
                trans_db[fname].update(idx_map)
    else:
        # xlsx 模式
        from pathlib import Path
        excel_path = Path(translation_path) if not isinstance(translation_path, Path) else translation_path
        if not excel_path.exists():
            print(f"⏭️  跳过 {input_subfolder}: 找不到翻译表格 {excel_path.name}")
            return

        print(f"\n📂 开始注入 {input_subfolder} 文本 (Excel 模式)...")
        trans_db = cast(dict[str, dict[int, str]], read_translation_table(excel_path, is_arm9=False))

    src_dir = EXTRACT_DIR / input_subfolder
    dst_dir = PATCHED_DIR / output_subfolder
    dst_dir.mkdir(parents=True, exist_ok=True)
    
    success_count = 0
    for root, _, files in os.walk(src_dir):
        for file in files:
            if file.lower().endswith(('.bin', '.bbq')):
                src_path = os.path.join(root, file)
                dst_path = os.path.join(dst_dir, file)

                try:
                    # 邮件分支（仅 CSV 模式且 ML CSV 中有该文件）：
                    if is_csv_mode and is_mail_file(file) and file in mail_db:
                        mail1, mail2, mail_translated, reply_translated = mail_db[file]
                        if not mail_translated and not reply_translated:
                            # 未翻译：直接复制原文件，保留原始 Shift-JIS 字节
                            # （避免 char_map 重映射改变未翻译文本的字节）
                            shutil.copy2(src_path, dst_path)
                        else:
                            # 有翻译：用 rebuild_mail_bbq 重建 Type2/5/6/7
                            # 传 char_map 用 text_to_bytes 编码，支持中文译文
                            rebuild_mail_bbq(src_path, dst_path, mail1, mail2, char_map)
                        success_count += 1
                    else:
                        # 普通文本分支
                        translations = trans_db.get(file, {})
                        rebuild_bbq_file(src_path, dst_path, translations, char_map)
                        if translations: success_count += 1
                except Exception as e:
                    print(f"  ❌ 错误 {file}: {e}")
                    shutil.copy2(src_path, dst_path)

    print(f"  ✅ 完成！共成功修改 {success_count} 个 {input_subfolder} 文件。")

# ===================================================================
#  第二部分: ARM9 与 Overlay 程序文件原地注入
# ===================================================================

def process_arm9_overlays(translation_path: str | Path, char_map: dict[str, int]) -> None:
    """
    注入 ARM9/Overlay 文本。支持 xlsx 和 csv 两种格式。

    Args:
        translation_path: xlsx 文件路径 或 csv 文件路径
        char_map:         字符映射表
    """
    from pathlib import Path

    # 检测格式
    if isinstance(translation_path, (str, os.PathLike)) and is_csv_path(translation_path):
        # CSV 模式
        translation_path = Path(translation_path) if not isinstance(translation_path, Path) else translation_path
        if not translation_path.exists():
            print(f"⏭️  跳过 ARM9: 找不到 CSV 文件 {translation_path}")
            return

        print(f"\n💻 开始注入 ARM9 & Overlays 程序代码段文本 (CSV 模式)...")
        rows = cast(list[dict[str, Any]], read_translation_table(translation_path, is_arm9=True))
        # 按 File 分组
        grouped: dict[str, Any] = {}
        for row in rows:
            fname = row.get("File", "").strip()
            if not fname:
                continue
            grouped.setdefault(fname, []).append(row)
    else:
        # xlsx 模式
        excel_path = Path(translation_path) if not isinstance(translation_path, Path) else translation_path
        if not excel_path.exists():
            print(f"⏭️  跳过 ARM9: 找不到翻译表格 {excel_path.name}")
            return

        print(f"\n💻 开始注入 ARM9 & Overlays 程序代码段文本 (Excel 模式)...")
        df = pd.read_excel(excel_path)
        df = df.dropna(subset=['Translated_Text', 'Text_Offset'])
        grouped = {str(k): g for k, g in df.groupby('File')}

    dst_dir = PATCHED_DIR / "PRG_CHS_PATCHED"
    dst_dir.mkdir(parents=True, exist_ok=True)

    src_arm9_dir = EXTRACT_DIR / "ARM9"
    if not src_arm9_dir.exists():
        print("  ⚠️ 警告: 未找到 Extracted/ARM9 目录。")
        return

    for fname in os.listdir(src_arm9_dir):
        name_lower = fname.lower()
        if name_lower == 'arm9.bin' or (name_lower.startswith('overlay') and name_lower.endswith('.bin')):
            shutil.copy2(src_arm9_dir / fname, dst_dir / fname)

    total_success = 0
    total_overflow = 0

    for filename, group_items in grouped.items():
        dst_path = dst_dir / filename
        if not dst_path.exists(): continue

        # 统一转换为可迭代的 dict 行
        rows_list: list[Any]
        if isinstance(group_items, pd.DataFrame):
            row_iter = group_items.iterrows()
            rows_list = [row for _, row in row_iter]
        else:
            rows_list = group_items

        with open(dst_path, 'rb+') as f:
            for row in rows_list:
                try:
                    offset_str = str(row.get('Text_Offset', ''))
                    offset = int(offset_str, 16)

                    # P2-6: 使用 CSV/xlsx 的 Max_Bytes 列作为可用空间（与 faraplay 一致）
                    # Max_Bytes = 原文字符串长度（不含 NUL 终止符），由 stage2_export_arm9 导出
                    max_bytes_raw = row.get('Max_Bytes', '')
                    try:
                        max_bytes = int(max_bytes_raw)
                    except (ValueError, TypeError):
                        # Max_Bytes 缺失或无效（如旧 xlsx），回退到扫描二进制获取原文字符串长度
                        f.seek(offset)
                        scan_len = 0
                        while True:
                            b = f.read(1)
                            if b == b'\x00' or b == b'':
                                break
                            scan_len += 1
                        max_bytes = scan_len

                    if max_bytes <= 0:
                        continue

                    trans_text = str(row.get('Translated_Text', ''))
                    if not trans_text.strip():
                        continue

                    # text_to_bytes 返回的字节流已含 NUL 终止符
                    new_bytes = text_to_bytes(trans_text, char_map)

                    # 溢出检查：译文+NUL 必须容纳在 ROM 中原文所占空间内
                    # Max_Bytes = 原文字符串长度（不含 NUL 终止符）
                    # ROM 中原文占 max_bytes + 1 字节（含 NUL），即可用空间 = max_bytes + 1
                    # text_to_bytes 返回值已含 NUL，故判断 len(new_bytes) > max_bytes + 1
                    # 旧代码用 len(new_bytes) > max_bytes，导致译文与原文等长时误报溢出
                    # （例如 "作詞：？？？"→"作词：？？？" 原文 12B+NUL，译文也 12B+NUL，
                    #   旧判断 13 > 12 = True 错误跳过，正确判断 13 > 13 = False 允许注入）
                    if len(new_bytes) > max_bytes + 1:
                        print(f"  ⚠️[溢出跳过] {filename} @ {offset_str}")
                        print(f"     原文: {row.get('Original_Text', '')}")
                        print(f"     译文: {trans_text}")
                        print(f"     ❌ 译文所需 {len(new_bytes)} 字节 > 可用空间 {max_bytes + 1} 字节\n")
                        total_overflow += 1
                        continue

                    # 先清空再写入（与 faraplay arm9overlay.rs 一致）：
                    # 1. 写入 max_bytes+1 个 0（含 NUL 位置），确保旧数据完全清除
                    # 2. 再写入译文字节（含 NUL），剩余位置保持为 0
                    f.seek(offset)
                    f.write(b'\x00' * (max_bytes + 1))
                    f.seek(offset)
                    f.write(new_bytes)

                    total_success += 1

                except Exception as e:
                    print(f"  [错误] 注入偏移 {row.get('Text_Offset')} 时失败: {e}")

    print(f"  ✅ ARM9 注入完成。成功: {total_success} 条。")
    if total_overflow > 0:
        print(f"  🚨 注意: 仍有 {total_overflow} 条文本由于确实过长被跳过。")

# ===================================================================
#  主流程入口
# ===================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="文本注入与数据重建引擎")
    parser.add_argument("--format", choices=["xlsx", "csv"], default="xlsx",
                        help="翻译表格式：xlsx（默认）或 csv（faraplay 兼容窄列）")
    args = parser.parse_args()

    fmt = args.format
    print("=" * 50)
    print(f" 文本注入与数据重建引擎 (格式: {fmt})")
    print("=" * 50)

    char_map = load_mapping(MAPPING_FILE)
    if not char_map:
        print("❌ 找不到 font_mapping.json，请先执行 Stage 3 字库生成脚本。")
        return

    # 根据格式选择翻译表路径
    if fmt == "csv":
        scn_path = CSV_SCN_DIR
        tbl_path = CSV_TBL_DIR
        arm9_path = CSV_ARM9_FILE
    else:
        scn_path = EXCEL_SCN
        tbl_path = EXCEL_TBL
        arm9_path = EXCEL_ARM9

    process_bbq_directory(scn_path, "SCN", "SCN_CHS_PATCHED", char_map)
    process_bbq_directory(tbl_path, "TBL", "TBL_CHS_PATCHED", char_map)
    process_arm9_overlays(arm9_path, char_map)

    print("\n🎉 Stage 4 注入阶段全部完成！")

if __name__ == "__main__":
    main()