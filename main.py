# main.py
import sys
import argparse
import os
from config import BASE_DIR

# 引入各个阶段的主函数
from src.stage1_unpack import main as run_stage1
from src.stage2_export_text import main as run_stage2_text
from src.stage2_export_images import main as run_stage2_images
from src.stage2_export_bg import main as run_stage2_bg       # <--- 导入 BG 导出
from src.stage2_export_arm9 import main as run_stage2_arm9
from src.stage3_build_font import main as run_stage3
from src.stage4_inject_text import main as run_stage4_text
from src.stage4_import_images import main as run_stage4_images
from src.stage4_import_bg import main as run_stage4_bg       # <--- 导入 BG 回写
from src.stage5_build_rom import main as run_stage5

def print_menu():
    print("=" * 60)
    print("  🌟 THE iDOLM@STER Dearly Stars 汉化工程控制台 🌟")
    print("=" * 60)
    print("  [1] 解包提取 (Unpack)        - 提取原始 BIN/IDX 并解压ARM9")
    print("  [2] 导出文本 (Export Text)   - 生成 SCN, TBL, ARM9 翻译表")
    print("  [3] 导出图像 (Export Images) - 导出 GLD 与 BG(NCGR) 为 BMP")
    print("  [4] 构建字库 (Build Font)    - 根据 Excel 动态生成字库")
    print("  [5] 注入文本 (Inject Text)   - 将翻译写回 SCN/TBL/ARM9")
    print("[6] 回写图像 (Import Images) - 将修改的 BMP 写回 GLD/BG")
    print("  [7] 打包生成 (Build ROM)     - 生成最终汉化版 .nds")
    print("  [8] 一键自动化 (Auto Build)  - 执行 4 -> 5 -> 6 -> 7")
    print("  [0] 退出控制台")
    print("=" * 60)

def interactive_mode():
    """交互式菜单模式"""
    while True:
        print_menu()
        choice = input("请输入你想执行的步骤序号 (0-8): ").strip()

        if choice == '1':
            run_stage1()
        elif choice == '2':
            run_stage2_text()
            run_stage2_arm9()
        elif choice == '3':
            run_stage2_images()
            run_stage2_bg()         # <--- 同时导出 BG
        elif choice == '4':
            run_stage3()
        elif choice == '5':
            run_stage4_text()
        elif choice == '6':                  
            run_stage4_images()
            run_stage4_bg()         # <--- 同时回写 BG
        elif choice == '7':                  
            run_stage5()
        elif choice == '8':                  
            print("\n🚀 启动一键自动化构建流水线...")
            run_stage3()
            run_stage4_text()
            run_stage4_images()
            run_stage4_bg()         # <--- 一键打包也包含 BG 回写
            run_stage5()
        elif choice == '0':
            print("再见！祝汉化顺利！")
            break
        else:
            print("❌ 无效的输入，请重新输入。")

        input("\n按 Enter 键返回主菜单...")

def main():
    parser = argparse.ArgumentParser(description="偶像大师深情之星 汉化构建工具")
    parser.add_argument('command', nargs='?', choices=['unpack', 'export', 'export-images', 'font', 'inject', 'import-images', 'build', 'all'])
    args = parser.parse_args()

    if args.command == 'unpack':
        run_stage1()
    elif args.command == 'export':
        run_stage2_text()
        run_stage2_arm9()
    elif args.command == 'export-images': 
        run_stage2_images()
        run_stage2_bg()
    elif args.command == 'font':
        run_stage3()
    elif args.command == 'inject':
        run_stage4_text()
    elif args.command == 'import-images':   
        run_stage4_images()
        run_stage4_bg()
    elif args.command == 'build':
        run_stage5()
    elif args.command == 'all':
        run_stage3()
        run_stage4_text()
        run_stage4_images()
        run_stage4_bg()
        run_stage5()
    else:
        interactive_mode()

if __name__ == "__main__":
    main()