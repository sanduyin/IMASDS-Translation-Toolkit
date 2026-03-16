# src/stage2_export_text.py
import os
import sys
import re
import pandas as pd
from collections import defaultdict

# 导入全局配置和工具类
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, EXCEL_SCN, EXCEL_TBL
from src.utils.bbq_format import parse_bbq_file

def extract_sort_key(filename):
    """提取文件名中的数字前缀用于排序，例如 '0001_A.bin' 返回 1"""
    match = re.search(r'(\d+)', filename)
    return int(match.group(1)) if match else 999999

def extract_group_name(filename):
    """去除文件名中的数字前缀和后缀，提取纯字母标识用于划分 Excel Sheet 页"""
    name = os.path.splitext(filename)[0]
    name = re.sub(r'^\d+_', '', name)
    name = re.sub(r'_MES$', '', name, flags=re.IGNORECASE)
    return name

def create_styled_excel(data_groups, output_path, is_scn=False):
    """
    将提取的文本数据写入 Excel，并应用严格的条件格式和样式规则。
    """
    print(f"正在导出表格至: {output_path}")
    
    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # 定义样式格式
        fmt_green = workbook.add_format({'bg_color': '#C6EFCE', 'font_color': '#006100'})
        fmt_blue = workbook.add_format({'bg_color': '#BDD7EE', 'font_color': '#1F497D'})
        fmt_red = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})
        fmt_purple = workbook.add_format({'bg_color': '#E4D7F5'}) 
        fmt_text = workbook.add_format({'text_wrap': True, 'valign': 'top'})
        
        # 定义列顺序
        columns_order =[
            'Original_Text', 'Speaker', 'Translated_Text', 
            'File', 'Text_Offset', 'Pointer_Locs', 'Max_Bytes', 'Index', 'Type'
        ]
        
        for sheet_name, entries in data_groups.items():
            # Excel Sheet 名称最多 31 个字符
            safe_sheet_name = sheet_name[:31]
            df = pd.DataFrame(entries)[columns_order]
            df.to_excel(writer, sheet_name=safe_sheet_name, index=False)
            
            ws = writer.sheets[safe_sheet_name]
            last_row = len(df) + 1
            
            # 设置基础列宽和文本换行
            ws.set_column('A:A', 40, fmt_text)
            ws.set_column('B:B', 12)
            ws.set_column('C:C', 40, fmt_text)
            ws.set_column('D:G', 15)
            
            # SCN 文件特有的系统/选项文本高亮规则 (通过检查文件名是否包含 _MES)
            if is_scn:
                purple_range_1 = f"A2:B{last_row}"
                purple_range_2 = f"D2:I{last_row}"
                # 如果 D 列(File)不包含 "_MES"，则将其视为系统文本，标为紫色
                purple_condition = {'type': 'formula', 'criteria': '=ISERROR(SEARCH("_MES", $D2))', 'format': fmt_purple}
                ws.conditional_format(purple_range_1, purple_condition)
                ws.conditional_format(purple_range_2, purple_condition)

            # 翻译文本长度校验规则 (应用在 C 列 Translated_Text)
            # 规则1: 字节数大于 H 列(Max_Bytes) 限制，或大于 40 时标红 (溢出)
            ws.conditional_format(1, 2, last_row - 1, 2, {
                'type': 'formula', 
                'criteria': '=LENB($C2)>$G2' if not is_scn else '=LENB($C2)>40', 
                'format': fmt_red
            })
            # 规则2: 翻译长度大于原文长度但在安全范围内，标蓝
            ws.conditional_format(1, 2, last_row - 1, 2, {
                'type': 'formula', 
                'criteria': '=AND(LENB($C2)>LENB($A2), LENB($C2)<=$G2)' if not is_scn else '=AND(LENB($C2)>LENB($A2), LENB($C2)<=40)', 
                'format': fmt_blue
            })
            # 规则3: 翻译完成且长度短于或等于原文，标绿
            ws.conditional_format(1, 2, last_row - 1, 2, {
                'type': 'formula', 
                'criteria': '=AND(LENB($C2)<=$LENB($A2), $C2<>"")', 
                'format': fmt_green
            })
            
            # 冻结首行和前三列 (TBL不需要冻结前三列)
            ws.freeze_panes(1, 3 if is_scn else 1)

def export_bbq_directory(input_folder, output_excel, is_scn=False):
    """遍历指定目录读取所有 bbq/bin 文件并导出"""
    if not input_folder.exists():
        print(f"找不到文件夹，请先执行解包: {input_folder}")
        return
        
    print(f"开始解析目录: {input_folder}")
    all_files =[]
    
    for root, _, files in os.walk(input_folder):
        for file in files:
            if file.lower().endswith(('.bbq', '.bin')):
                all_files.append(os.path.join(root, file))
    
    # 按文件名前缀的数字序号严格排序
    all_files.sort(key=lambda x: extract_sort_key(os.path.basename(x)))

    grouped_data = defaultdict(list)
    for file_path in all_files:
        filename = os.path.basename(file_path)
        
        # 【核心修复】这里将 is_scn 状态传递给了底层的解析器！
        entries = parse_bbq_file(file_path, is_scn=is_scn)
        
        if entries:
            # SCN 会根据文件名拆分 Sheet，TBL 为了方便也使用同样的分类
            group_name = extract_group_name(filename) if is_scn else filename
            grouped_data[group_name].extend(entries)

    if grouped_data:
        create_styled_excel(grouped_data, output_excel, is_scn)
    else:
        print(f"未在 {input_folder} 中提取到有效文本。")

def main():
    # 1. 导出 SCN 剧情文本 (开启 is_scn=True, 会解析角色且冻结多列)
    scn_dir = EXTRACT_DIR / "SCN"
    export_bbq_directory(scn_dir, EXCEL_SCN, is_scn=True)
    
    # 2. 导出 TBL 系统文本 (开启 is_scn=False, 忽略角色名，Speaker 全填 ×)
    tbl_dir = EXTRACT_DIR / "TBL"
    export_bbq_directory(tbl_dir, EXCEL_TBL, is_scn=False)
    
    print("✅ SCN/TBL 文本导出流程执行完毕。")

if __name__ == "__main__":
    main()