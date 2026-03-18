# config.py
import os
from pathlib import Path

# 项目根目录绝对路径
BASE_DIR = Path(__file__).parent.absolute()

# ================= 核心目录 =================
DATA_DIR = BASE_DIR / "game_data"
WORKSPACE_DIR = BASE_DIR / "workspace"

ORIGINAL_DIR = DATA_DIR / "0_Original"
EXTRACT_DIR = DATA_DIR / "1_Extracted"
PATCHED_DIR = DATA_DIR / "2_Patched"
BUILD_DIR = DATA_DIR / "3_Build"
REPACK_STAGING = DATA_DIR / "Repack_Staging"

for d in[DATA_DIR, ORIGINAL_DIR, EXTRACT_DIR, PATCHED_DIR, BUILD_DIR, REPACK_STAGING, WORKSPACE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ================= 纯 Python 引擎配置 =================
ROM_NAME = "THE iDOLM@STER Dearly Stars.nds"
ORIGINAL_ROM = ORIGINAL_DIR / ROM_NAME
OUTPUT_ROM = BUILD_DIR / f"{Path(ROM_NAME).stem}_CHS.nds"

# ================= 内部封包结构字典 =================
FILE_PACKS =[
    {"ezt": "F_AGL.IDX", "ezp": "F_AGL.BIN", "output": "AGL"},
    {"ezt": "F_AGLCHR.IDX", "ezp": "F_AGLCHR.BIN", "output": "AGLCHR"},
    {"ezt": "F_BG.IDX", "ezp": "F_BG.BIN", "output": "BG"},
    {"ezt": "F_BGM.IDX", "ezp": "F_BGM.BIN", "output": "BGM"},
    {"ezt": "F_G3D.IDX", "ezp": "F_G3D.BIN", "output": "G3D"},
    {"ezt": "F_OBJ.IDX", "ezp": "F_OBJ.BIN", "output": "OBJ"},
    {"ezt": "F_SCN.IDX", "ezp": "F_SCN.BIN", "output": "SCN"},
    {"ezt": "F_TBL.IDX", "ezp": "F_TBL.BIN", "output": "TBL"},
    {"ezt": "F_TEX.IDX", "ezp": "F_TEX.BIN", "output": "TEX"},
    {"ezt": "F_VOICE.IDX", "ezp": "F_VOICE.BIN", "output": "VOICE"},
]

# ================= 工作区与字库配置 =================
EXCEL_SCN = WORKSPACE_DIR / "SCN_Translation.xlsx"
EXCEL_TBL = WORKSPACE_DIR / "TBL_Translation.xlsx"
EXCEL_ARM9 = WORKSPACE_DIR / "ARM9_Overlays_Translation.xlsx"
MAPPING_FILE = WORKSPACE_DIR / "font_mapping.json"

FONT_12PX = WORKSPACE_DIR / "fusion-pixel-12px-monospaced-zh_hans.otf"
FONT_10PX = WORKSPACE_DIR / "fusion-pixel-10px-monospaced-zh_hans.otf"

ORIGINAL_LC10 = EXTRACT_DIR / "TBL" / "0000_LC10.NFTR"
ORIGINAL_LC12 = EXTRACT_DIR / "TBL" / "0001_LC12.NFTR"
PATCHED_LC10 = PATCHED_DIR / "TBL_CHS_PATCHED" / "0000_LC10.NFTR"
PATCHED_LC12 = PATCHED_DIR / "TBL_CHS_PATCHED" / "0001_LC12.NFTR"

TARGET_PACKS =["SCN", "TBL", "G3D", "BG", "TEX", "AGL"]
EMPTY_MARKERS = ['{EMPTY}', '{empty}', ' ', '　']
