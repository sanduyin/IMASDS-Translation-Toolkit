# src/stage3_5_lyric.py
"""Stage 3.5: 歌词课汉化入口模块

定位（流水线顺序）：
  Stage 2  导出文本/图像
  Stage 3  构建字库
  Stage 3.5（本阶段）  歌词课汉化补丁
  Stage 4  注入文本 / 回写图像
  Stage 5  打包生成

本阶段做的事：
  1. export-translation  从 10 个 LESVOICETABLE BBQ 导出独立 xlsx
     （与 Stage 2 并行执行；翻译者完成后保存到 workspace/）
  2. build-patches        构建全部歌词课补丁：
     - charmap 重定位（overlay_0006 + y9.bin）
     - GLD 扩容（0507 / 0521 追加中文字形像素）
     - AGL 扩容（0506 / 0520 frame 表扩容）
     - ARM9 四补丁（0x3D28C / 0x3D66C / 0x62608 / 0x6260C）
     - BBQ 注入（10 个 LESVOICETABLE 文本注入）
     完成后部署到 2_Patched/，Stage 4 文本注入会跳过这些文件
  3. export-glyph-psd     从已扩容的 GLD 导出 PSD 供美工修正字型
     PSD 直接覆盖到 game_data/1_Extracted_Images/AGL/（美工在原位修正）
     注意：Stage 6 跳过 0506/0507/0520/0521，美工修正后需用歌词课专用回写

核心实现见 src/utils/lesvoice.py，本模块仅作薄入口。
"""
from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    EXCEL_LYRIC, LYRIC_BUILD_DIR, LYRIC_GLYPH_DIR, LYRIC_FONT_MAPPING,
    PATCHED_DIR, FONT_12PX,
)
from src.utils import lesvoice


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 3.5: 歌词课汉化（charmap + GLD/AGL 扩容 + ARM9 补丁 + BBQ 注入）"
    )
    subparsers = parser.add_subparsers(dest="command")

    # 默认子命令：full-build
    p_full = subparsers.add_parser(
        "full-build",
        help="构建全部补丁 + 部署到 2_Patched/ + 导出 PSD（默认）",
    )
    p_full.add_argument("--xlsx", type=Path, default=EXCEL_LYRIC,
                        help=f"歌词翻译 xlsx (默认: {EXCEL_LYRIC.name})")
    p_full.add_argument("--build-dir", type=Path, default=LYRIC_BUILD_DIR,
                        help=f"构建目录 (默认: {LYRIC_BUILD_DIR})")
    p_full.add_argument("--no-deploy", action="store_true",
                        help="不部署到 2_Patched/")
    p_full.add_argument("--no-psd", action="store_true",
                        help="不导出美工 PSD")

    # 仅导出翻译 xlsx
    p_exp = subparsers.add_parser(
        "export-translation",
        help="从 10 个 LESVOICETABLE BBQ 导出独立翻译 xlsx",
    )
    p_exp.add_argument("--out", type=Path, default=EXCEL_LYRIC,
                       help=f"输出 xlsx (默认: {EXCEL_LYRIC.name})")

    # 仅构建补丁
    p_build = subparsers.add_parser(
        "build-patches",
        help="构建 charmap/GLD/AGL/ARM9/BBQ 全部补丁",
    )
    p_build.add_argument("--xlsx", type=Path, default=EXCEL_LYRIC,
                         help=f"歌词翻译 xlsx (默认: {EXCEL_LYRIC.name})")
    p_build.add_argument("--build-dir", type=Path, default=LYRIC_BUILD_DIR,
                         help=f"构建目录 (默认: {LYRIC_BUILD_DIR})")
    p_build.add_argument("--deploy", action="store_true", default=True,
                         help="构建后部署到 2_Patched/ (默认开启)")
    p_build.add_argument("--no-deploy", dest="deploy", action="store_false",
                         help="不部署")

    # 仅导出 PSD
    p_psd = subparsers.add_parser(
        "export-glyph-psd",
        help="从已扩容 GLD 导出 PSD 供美工修正字型",
    )
    p_psd.add_argument("--gld-dir", type=Path, default=LYRIC_BUILD_DIR,
                       help=f"GLD patched 目录 (默认: {LYRIC_BUILD_DIR})")
    p_psd.add_argument("--out-dir", type=Path, default=LYRIC_GLYPH_DIR,
                       help=f"PSD 输出目录 (默认: {LYRIC_GLYPH_DIR})")
    p_psd.add_argument("--all", action="store_true",
                       help="导出全部 sprite (默认仅新增汉字)")

    # 美工修正后回写 PSD → 已扩容 GLD
    p_imp = subparsers.add_parser(
        "import-glyph-psd",
        help="美工修正后回写 PSD 到已扩容 GLD（Stage 6 跳过这些文件）",
    )
    p_imp.add_argument("--psd-dir", type=Path, default=LYRIC_GLYPH_DIR,
                       help=f"PSD/PNG 所在目录 (默认: {LYRIC_GLYPH_DIR})")
    p_imp.add_argument("--patched-dir", type=Path, default=PATCHED_DIR,
                       help=f"2_Patched 目录 (默认: {PATCHED_DIR})")

    # 仅 ARM9 操作
    p_arm9 = subparsers.add_parser("arm9", help="ARM9 四补丁操作")
    p_arm9.add_argument("input", type=Path, help="输入 arm9.bin")
    p_arm9.add_argument("output", type=Path, nargs="?",
                        help="输出路径 (省略则原位)")
    p_arm9.add_argument("--verify", action="store_true", help="仅验证")
    p_arm9.add_argument("--check", action="store_true", help="仅检查状态")

    args = parser.parse_args()

    # 默认行为：full-build
    if args.command is None or args.command == "full-build":
        _do_full_build(args if args.command == "full-build" else None)
        return

    if args.command == "export-translation":
        lesvoice.export_lyric_translation_xlsx(args.out)
        return

    if args.command == "build-patches":
        _do_build_patches(args.xlsx, args.build_dir, args.deploy)
        return

    if args.command == "export-glyph-psd":
        lesvoice.export_gld_psd_for_art(
            gld_patched_dir=args.gld_dir,
            out_dir=args.out_dir,
            only_new=not args.all,
        )
        return

    if args.command == "import-glyph-psd":
        lesvoice.import_lyric_glyph_psd(
            psd_dir=args.psd_dir,
            patched_dir=args.patched_dir,
        )
        return

    if args.command == "arm9":
        rc = lesvoice.patch_arm9_main(
            args.input, args.output,
            verify_only=args.verify, check_only=args.check,
        )
        if rc != 0:
            sys.exit(rc)
        return


def _do_full_build(args) -> None:
    """执行默认全流程：构建补丁 + 部署 + 导出 PSD"""
    # 默认参数（用户未传子命令时）
    xlsx = EXCEL_LYRIC
    build_dir = LYRIC_BUILD_DIR
    deploy = True
    export_psd = True
    if args is not None:
        xlsx = args.xlsx
        build_dir = args.build_dir
        deploy = not args.no_deploy
        export_psd = not args.no_psd

    # 1. 构建全部补丁（charmap + GLD + AGL + ARM9 + BBQ）
    _do_build_patches(xlsx, build_dir, deploy=False)

    # 2. 部署到 2_Patched/
    if deploy:
        lesvoice.deploy_lyric_patches_to_patched(
            build_dir=build_dir, patched_dir=PATCHED_DIR
        )

    # 3. 导出 PSD 供美工修正字型
    if export_psd:
        lesvoice.export_gld_psd_for_art(
            gld_patched_dir=build_dir,
            out_dir=LYRIC_GLYPH_DIR,
            only_new=True,
        )

    print("\n" + "=" * 70)
    print("✅ Stage 3.5 歌词课汉化全部完成")
    print("=" * 70)
    if deploy:
        print("  补丁已部署到 2_Patched/:")
        print("    - PRG_CHS_PATCHED/  (arm9.bin / overlay_0006.bin / y9.bin)")
        print("    - AGL_CHS_PATCHED/  (0506/0507/0520/0521 扩容文件)")
        print("    - TBL_CHS_PATCHED/  (10 个 LESVOICETABLE BBQ)")
    if export_psd:
        print(f"  美工 PSD 已导出到: {LYRIC_GLYPH_DIR}")
        print("  美工修正后将 PSD+PNG 放回此目录，再执行 Stage 6 回写")
    print("\n  接下来执行 Stage 4 (注入文本) 和 Stage 6 (回写图像)，")
    print("  Stage 4/6 已配置跳过歌词课资产，不会覆盖本阶段补丁。")


def _do_build_patches(xlsx: Path, build_dir: Path, deploy: bool) -> None:
    """构建全部歌词课补丁（charmap + GLD + AGL + ARM9 + BBQ）

    Args:
        xlsx:        歌词翻译 xlsx
        build_dir:   构建目录
        deploy:      是否部署到 2_Patched/
    """
    # 1. charmap 重定位 + y9 补丁
    result = lesvoice.build_charmap_patch(
        lyric_xlsx=xlsx, build_dir=build_dir,
    )
    new_frames = result['new_frame_count'] - lesvoice.AGL_OLD_FRAME_COUNT

    # 2. GLD 扩容（0507 / 0521）
    lesvoice.expand_gld_chinese(
        ssot_csv=build_dir / "ssot_chars.csv",
        build_dir=build_dir,
        font_path=FONT_12PX,
    )

    # 3. AGL 扩容（0506 / 0520）
    lesvoice.expand_agl_chinese(new_frames=new_frames, build_dir=build_dir)

    # 4. ARM9 四补丁
    from config import EXTRACT_DIR
    arm9_src = EXTRACT_DIR / "ARM9" / lesvoice.ARM9_FILENAME
    arm9_dst = build_dir / "arm9_patched.bin"
    lesvoice.patch_arm9(arm9_src, arm9_dst)

    # 5. BBQ 注入（10 个 LESVOICETABLE）
    lesvoice.inject_bbq_chinese(
        lyric_xlsx=xlsx,
        font_mapping_path=build_dir / "font_mapping_lesvoice.json",
        build_dir=build_dir,
    )

    if deploy:
        lesvoice.deploy_lyric_patches_to_patched(
            build_dir=build_dir, patched_dir=PATCHED_DIR
        )

    print("\n✅ 歌词课补丁全部构建完成"
          + ("（已部署到 2_Patched/）" if deploy else ""))


if __name__ == "__main__":
    main()
