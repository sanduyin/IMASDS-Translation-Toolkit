# IMASDS Translation Toolkit — Code Wiki

> 本 Wiki 面向开发者与维护人员，提供项目整体架构、模块职责、关键类/函数说明、依赖关系及运行方式的完整参考。
>
> 文档版本：v1.0 | 生成日期：2026-07-16

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [目录结构](#3-目录结构)
4. [核心流水线（5 Stage）](#4-核心流水线5-stage)
5. [工具模块（Utils）](#5-工具模块utils)
6. [配置系统](#6-配置系统)
7. [入口与交互](#7-入口与交互)
8. [依赖关系](#8-依赖关系)
9. [数据流与目录约定](#9-数据流与目录约定)
10. [关键类与数据结构](#10-关键类与数据结构)
11. [关键函数参考表](#11-关键函数参考表)
12. [外部对比与优化方向](#12-外部对比与优化方向)

---

## 1. 项目概述

**项目名称**：IMASDS Translation Toolkit / 深情之星汉化工具链  
**目标游戏**：《THE iDOLM@STER Dearly Stars》（Nintendo DS）  
**开发语言**：Python 3.10+  
**核心目标**：提供一套自动化、可交互的 NDS ROM 汉化构建流水线，涵盖解包、文本导出/注入、图像导出/回写、字库构建、ROM 打包等完整环节。

### 1.1 核心特性
- **自动解包与解压**：基于 `ndspy` 纯 Python 引擎，支持 ARM9 / Overlay 的 BLZ (Backward LZ10) 逆向解压。
- **指针提取**：针对底层代码段进行跨文件 Shift-JIS 指针搜索与 RAM 地址计算。
- **动态字库**：集成 `OpenCC` 复用原版 JIS 汉字槽位，支持 VWF（可变宽度字体）排版。
- **安全注入机制**：严格的字节级长度校验与原地内存注入，防止程序段数据溢出崩溃。
- **DSi (TWL) 增强**：支持 DSi 扩展数据嫁接、Header 完整性修复与 CRC16 重新校验。
- **背景图处理**：完整支持 NCGR / NCLR / NSCR 背景图导出为 BMP 及无损回写。

---

## 2. 整体架构

项目采用**阶段化流水线（Stage-based Pipeline）**架构，每个 Stage 职责单一，通过 `main.py` 统一调度。

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                               │
│              (CLI 参数解析 / 交互式菜单)                       │
└──────────────┬──────────────────────────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
  ┌─────────┐    ┌──────────┐
  │  CLI模式 │    │ 交互模式 │
  └────┬────┘    └────┬─────┘
       └──────────────┘
              │
    ┌─────────┼─────────┬─────────┬─────────┐
    ▼         ▼         ▼         ▼         ▼
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│Stage1│ │Stage2│ │Stage3│ │Stage4│ │Stage5│
│Unpack│ │Export│ │ Font │ │Inject│ │Build │
└──────┘ └──────┘ └──────┘ └──────┘ └──────┘
```

### 2.1 架构原则
- **单一职责**：每个 `stage*.py` 只负责一个明确的构建步骤。
- **配置驱动**：所有路径、文件名、规则均集中在 `config.py`，避免硬编码。
- **工具复用**：公共二进制 I/O、文本编码、BBQ 解析等逻辑下沉到 `src/utils/`。
- **防御式编程**：关键步骤（如字库注入、文本回写）包含字节级体积校验，失败即抛异常。

---

## 3. 目录结构

```
IMASDS-Translation-Toolkit/
├── main.py                      # 主入口：CLI + 交互式菜单
├── config.py                    # 全局配置：路径、文件名、常量
├── requirements.txt             # Python 依赖清单
├── README.md                    # 用户级使用说明
├── OPTIMIZATION_PLAN.md         # Rust 项目对比与优化计划
├── LICENSE                      # 开源协议
├── font_mapping -04.19.json     # 字库映射表（运行时生成）
│
├── game_data/                   # 游戏数据工作区（运行时创建）
│   ├── 0_Original/              # 放置原版 ROM
│   ├── 1_Extracted/             # 解包后的原始资源
│   ├── 2_Patched/               # 汉化修改后的资源
│   ├── 3_Build/                 # 最终输出的汉化 ROM
│   └── Repack_Staging/          # 封包过程中的临时文件
│
├── workspace/                   # 翻译工作区（运行时创建）
│   ├── SCN_Translation.xlsx     # SCN 剧情文本翻译表
│   ├── TBL_Translation.xlsx     # TBL 系统文本翻译表
│   ├── ARM9_Overlays_Translation.xlsx  # 程序硬编码文本翻译表
│   ├── font_mapping.json        # 动态生成的字库映射
│   └── CT2_Font_Mapping.txt     # CrystalTile2 专用码表
│
└── src/                         # 源代码目录
    ├── __init__.py
    ├── stage1_unpack.py         # Stage 1: NDS 解包 + BIN/IDX 提取
    ├── stage2_export_text.py    # Stage 2a: SCN/TBL 文本导出到 Excel
    ├── stage2_export_arm9.py    # Stage 2b: ARM9/Overlay 文本扫描导出
    ├── stage2_export_images.py  # Stage 2c: GLD 图像导出为 BMP
    ├── stage2_export_bg.py      # Stage 2d: BG (NCGR/NCLR/NSCR) 导出为 BMP
    ├── stage3_build_font.py     # Stage 3: OpenCC + NFTR 动态字库构建
    ├── stage4_inject_text.py    # Stage 4a: 文本注入 SCN/TBL/ARM9
    ├── stage4_import_images.py  # Stage 4b: BMP 回写 GLD
    ├── stage4_import_bg.py      # Stage 4c: BMP 回写 BG
    ├── stage5_build_rom.py      # Stage 5: BIN/IDX 封包 + NDS ROM 构建
    │
    └── utils/                   # 公共工具模块
        ├── __init__.py
        ├── bbq_format.py        # .BBQ 封包格式解析器
        ├── binary_io.py         # 二进制读写辅助函数 + LZ10 压缩
        └── text_encoder.py      # 文本编码器（mapping → 字节流）
```

---

## 4. 核心流水线（5 Stage）

### Stage 1 — 解包提取 (`stage1_unpack.py`)

| 职责 | 说明 |
|------|------|
| NDS ROM 解包 | 使用 `ndspy.rom.NintendoDSRom.fromFile` 遍历 FNT 文件目录树，导出所有文件到 `game_data/0_Original/Data/` |
| ARM9 / Overlay 解压 | 使用 `ndspy.codeCompression` 对 ARM9 及所有 Overlay 执行 BLZ 逆向解压 |
| Header / 映射表导出 | 切出前 512 字节为 `header.bin`，导出 `y9.bin`、`arm7.bin`、`y7.bin` 供后续计算 RAM 地址 |
| BIN/IDX 提取 | 解析游戏内部封包（EZT 索引 + EZP 数据），对每个文件执行 Ring-LZ 解压，导出到 `game_data/1_Extracted/<PackName>/` |

**关键算法**：
- `decompress_ring_lz()`: 基于 0x1000 环形缓冲区的 LZ77 变体解压，处理 flag byte 控制的原文/回指混合流。
- `extract_archive()`: 解析 EZT 头部（magic + 文件数 + entry 表），按 entry 读取 EZP 中的偏移、大小、压缩标志，支持按 name table 还原原始文件名。

---

### Stage 2 — 导出资源

#### 2a. 文本导出 (`stage2_export_text.py`)

| 职责 | 说明 |
|------|------|
| BBQ 解析 | 调用 `utils.bbq_format.parse_bbq_file()` 提取 `.bin`/`.bbq` 中的 Section 7 文本池 |
| 角色识别 | SCN 文件解析 Section 5 的 view 结构，自动识别角色名、对话1、对话2 |
| Excel 生成 | 按文件前缀分组生成多 Sheet Excel，内置条件格式（绿/蓝/红/紫）用于长度校验和文本类型高亮 |

**输出格式**：
- `Original_Text`, `Speaker`, `Translated_Text`, `File`, `Text_Offset`, `Pointer_Locs`, `Max_Bytes`, `Index`, `Type`

#### 2b. ARM9 文本导出 (`stage2_export_arm9.py`)

| 职责 | 说明 |
|------|------|
| 内存基址读取 | 从 `header.bin` 读取 ARM9 加载基址，从 `y9.bin` 读取各 Overlay 的 RAM 基址 |
| Shift-JIS 扫描 | 使用正则匹配至少 2 字节的 Shift-JIS 字符串（含半角/全角混合），以 `\x00` 结尾 |
| 智能过滤 | `strict_filter()` 过滤 SDK 路径、乱码碎片、纯 ASCII 短文本；`analyze_chars()` 统计假名/汉字/ASCII 比例 |

#### 2c. GLD 图像导出 (`stage2_export_images.py`)

| 职责 | 说明 |
|------|------|
| GLD 解析 | 读取 32 字节头部，提取像素大小、BGR555 调色板（512 字节）、像素数据 |
| BMP 生成 | 构建标准 8bpp BMP（含 BGR0 调色板），支持固定宽度（256px）和启发式穷举宽度两种策略 |
| 文件夹策略 | `TEX`/`TBL` 优先 256px 固定宽度，`BG`/`AGL` 穷举所有合法宽度（16~1024，4 整除） |

#### 2d. BG 背景图导出 (`stage2_export_bg.py`)

| 职责 | 说明 |
|------|------|
| NDS 容器解析 | `parse_nds_container()` 解析通用 NDS 分段容器（NCLR/NCGR/NSCR） |
| 调色板解析 | `parse_nclr()` 读取 TTLP/PLTT Section，提取 BGR555 → RGB888 颜色表 |
| 图块解析 | `parse_ncgr()` 读取 RAHC/CHAR Section，支持 4bpp 和 8bpp tile 解码 |
| 地图解析 | `parse_nscr()` 读取 NRCS/SCRN Section，解析 tile 索引、翻转、调色板组 |
| 图像合成 | `compose_bg_image()` 按地图拼接 tile，应用翻转和调色板偏移，生成完整 BMP |

---

### Stage 3 — 构建字库 (`stage3_build_font.py`)

| 职责 | 说明 |
|------|------|
| 字符扫描 | 遍历 SCN/TBL/ARM9 Excel 的 `Translated_Text` 列，收集所有唯一字符 |
| 白嫖映射 | 利用 `OpenCC` (jp2t → t2s) 将原版 JIS 汉字转简体，复用未占用槽位 |
| 动态分配 | 为新增汉字分配可用编码槽位，生成 `font_mapping.json`。**该文件是汉化进度的唯一存档，必须持久化且可增量更新，禁止任何破坏其编码映射的改动。** |
| CT2 码表 | 同步生成 CrystalTile2 全景码表 `CT2_Font_Mapping.txt` |
| NFTR 注入 | 解析 NFTR 的 PLGC/HDWC/PAMC 结构，按映射表渲染 1bpp 字形并原地注入，同步更新 HDWC 字宽与进距 |

**关键规格**：
- `LC12`: 12×11 像素，17 字节/字形，CJK 字宽 11，进距 12
- `LC10`: 10×9 像素，12 字节/字形，CJK 字宽 9，进距 10
- 空格宽度自动调整，支持主字体 + fallback 字体（微软雅黑/黑体/宋体）

---

### Stage 4 — 注入与回写

#### 4a. 文本注入 (`stage4_inject_text.py`)

| 职责 | 说明 |
|------|------|
| BBQ 重建 | `rebuild_bbq_file()`: 复制原文件，替换 Section 7 的指针表和字符串池，新文本按 `text_to_bytes()` 编码，原地覆盖后 `truncate()` 截断旧数据 |
| SCN/TBL 注入 | 从 Excel 读取翻译，按文件名分桶，逐文件重建 BBQ |
| ARM9 注入 | 从 Excel 读取翻译，按 `Text_Offset` 原地写入，严格校验 `len(new_bytes) <= original_len + 1`，溢出则跳过并告警 |

#### 4b. GLD 图像回写 (`stage4_import_images.py`)

| 职责 | 说明 |
|------|------|
| BMP 读取 | 读取 8bpp BMP，校验尺寸与原 GLD 完全一致 |
| 调色板转换 | BMP BGR0 → NDS BGR555（8bit 分量右移 3 位压缩到 5bit） |
| GLD 重建 | 保留原 32 字节头部，替换像素数据 + 调色板，输出到 `2_Patched/<Folder>_IMG_PATCHED/` |

#### 4c. BG 背景图回写 (`stage4_import_bg.py`)

| 职责 | 说明 |
|------|------|
| Tile 提取 | 从 BMP 按 NSCR 地图坐标提取每个 tile 的 8×8 像素，逆向应用翻转和调色板偏移 |
| NCGR 重建 | 编码 tile 数据回 RAHC/CHAR Section，重建 NDS 容器 |
| NCLR 重建 | 将 BMP 调色板转 NDS BGR555 后注入 TTLP/PLTT Section |
| 三件套输出 | 同步输出 `.ncgr`、`.nclr`、`.nscr` 到 `BG_CHS_PATCHED/` |

---

### Stage 5 — ROM 构建 (`stage5_build_rom.py`)

| 职责 | 说明 |
|------|------|
| BIN/IDX 封包 | `repack_data_archives()`: 读取原 EZT/EZP，对 `TARGET_PACKS` 列表中的包，查找 `2_Patched/` 下的修改文件，执行 `nlzss_compress()` 压缩后回写，更新索引偏移和大小标志 |
| NDS 内存构建 | 使用 `ndspy` 在内存中重建 ROM，注入修改后的数据包、ARM9、Overlay |
| Overlay 底层注入 | 越过文件树，直接通过 `y9.bin` 的 file_id 篡改 `rom.files` 底层内存块，解除 BLZ 压缩标记并更新大小字段 |
| DSi 嫁接 | 读取原版 ROM 的 TWL 扩展数据，对齐到原 NTR 边界后嫁接到新 ROM |
| Header 修复 | 重新计算并写入 CRC16（`crc16_nds()`，多项式 `0xA001`） |

---

## 5. 工具模块（Utils）

### 5.1 `utils/bbq_format.py`

**核心函数**：`parse_bbq_file(file_path, is_scn=True) -> List[Dict]`

解析 `.BBQ` 封包格式：
1. 校验头部签名 `.BBQ`（`\x2E\x42\x42\x51`）
2. 读取 header_size、n_sections
3. 遍历 Section Entry（20 字节/entry：id + 4×uint32）
4. 定位 Section 7（文本池）：读取指针表、字符串数量、池偏移
5. 无损读取所有字符串，计算 `Max_Bytes`（基于下一个指针的间隙）
6. 若 `is_scn=True` 且存在 Section 5，解析 view 结构提取角色映射
7. 返回带 `Speaker`/`Type` 标注的条目列表

### 5.2 `utils/binary_io.py`

| 函数 | 说明 |
|------|------|
| `read_uint32(data_or_file, offset)` | 通用 4 字节无符号整数读取，支持文件对象和字节数组 |
| `read_uint16(data_or_file, offset)` | 通用 2 字节无符号整数读取 |
| `read_string_bytes(f, offset)` | 从文件指定偏移读取到 `\x00` 结束的原始字节 |
| `nlzss_compress(input_bytes)` | NDS LZ10 压缩算法：基于 4096 字节滑动窗口 + 哈希加速的前向搜索，支持长度 3~18 的回指匹配 |

### 5.3 `utils/text_encoder.py`

| 函数/常量 | 说明 |
|-----------|------|
| `PROTECTED_RANGES` | 保护区定义：ASCII + 半角片假名 (`0x20~0xDF`)、全角标点 (`0x8140~0x8799`) |
| `is_protected(code)` | 判断编码是否在保护区内 |
| `load_mapping(mapping_path)` | 加载 JSON 字库映射表 |
| `text_to_bytes(text, mapping)` | 将文本按 mapping 转字节流：支持 `\n` → `0x0A`、单/双字节自动判断、缺失字符 fallback 到 `?` (`0x3F`)、末尾强制补 `0x00` |

---

## 6. 配置系统

`config.py` 是项目的唯一配置源，使用 `pathlib.Path` 管理所有路径。

### 6.1 核心目录

| 常量 | 路径 | 说明 |
|------|------|------|
| `BASE_DIR` | 项目根目录 | `Path(__file__).parent.absolute()` |
| `DATA_DIR` | `game_data/` | 游戏数据根目录 |
| `ORIGINAL_DIR` | `game_data/0_Original/` | 原版 ROM 存放处 |
| `EXTRACT_DIR` | `game_data/1_Extracted/` | 解包后的原始资源 |
| `PATCHED_DIR` | `game_data/2_Patched/` | 汉化修改后的资源 |
| `BUILD_DIR` | `game_data/3_Build/` | 最终 ROM 输出目录 |
| `REPACK_STAGING` | `game_data/Repack_Staging/` | 封包临时目录 |
| `WORKSPACE_DIR` | `workspace/` | 翻译工作区 |

### 6.2 封包结构字典 (`FILE_PACKS`)

定义游戏内部 10 个 EZT/EZP 数据包：

| EZT 索引 | EZP 数据 | 输出目录 |
|----------|----------|----------|
| `F_AGL.IDX` | `F_AGL.BIN` | `AGL` |
| `F_AGLCHR.IDX` | `F_AGLCHR.BIN` | `AGLCHR` |
| `F_BG.IDX` | `F_BG.BIN` | `BG` |
| `F_BGM.IDX` | `F_BGM.BIN` | `BGM` |
| `F_G3D.IDX` | `F_G3D.BIN` | `G3D` |
| `F_OBJ.IDX` | `F_OBJ.BIN` | `OBJ` |
| `F_SCN.IDX` | `F_SCN.BIN` | `SCN` |
| `F_TBL.IDX` | `F_TBL.BIN` | `TBL` |
| `F_TEX.IDX` | `F_TEX.BIN` | `TEX` |
| `F_VOICE.IDX` | `F_VOICE.BIN` | `VOICE` |

### 6.3 工作区文件

| 常量 | 默认路径 | 说明 |
|------|----------|------|
| `EXCEL_SCN` | `workspace/SCN_Translation.xlsx` | SCN 剧情翻译表 |
| `EXCEL_TBL` | `workspace/TBL_Translation.xlsx` | TBL 系统翻译表 |
| `EXCEL_ARM9` | `workspace/ARM9_Overlays_Translation.xlsx` | ARM9 翻译表 |
| `MAPPING_FILE` | `workspace/font_mapping.json` | 字库映射表 |
| `FONT_12PX` | `workspace/ZLabsRoundPix_12px_M_CN.ttf` | 12px 中文字体 |
| `FONT_10PX` | `workspace/fusion-pixel-10px-monospaced-zh_hans.ttf` | 10px 中文字体 |

### 6.4 构建目标 (`TARGET_PACKS`)

```python
TARGET_PACKS = ["SCN", "TBL", "G3D", "BG", "TEX", "AGL"]
```

仅对上述包执行封包替换，其他包保持原样。

---

## 7. 入口与交互

### 7.1 命令行接口 (CLI)

```bash
python main.py [command]
```

| 命令 | 触发阶段 |
|------|----------|
| `unpack` | Stage 1 |
| `export` | Stage 2a + Stage 2b |
| `export-images` | Stage 2c + Stage 2d |
| `font` | Stage 3 |
| `inject` | Stage 4a |
| `import-images` | Stage 4b + Stage 4c |
| `build` | Stage 5 |
| `all` | Stage 3 → 4a → 4b → 4c → 5 |

### 7.2 交互式菜单

运行 `python main.py` 不带参数时进入交互模式：

```
============================================================
  THE iDOLM@STER Dearly Stars 汉化工程控制台
============================================================
  [1] 解包提取 (Unpack)
  [2] 导出文本 (Export Text)
  [3] 导出图像 (Export Images)
  [4] 构建字库 (Build Font)
  [5] 注入文本 (Inject Text)
  [6] 回写图像 (Import Images)
  [7] 打包生成 (Build ROM)
  [8] 一键自动化 (Auto Build)
  [0] 退出控制台
============================================================
```

---

## 8. 依赖关系

### 8.1 Python 依赖 (`requirements.txt`)

| 包 | 版本 | 用途 |
|----|------|------|
| `pandas` | 2.1.0 | Excel 读写、数据分析 |
| `openpyxl` | 3.1.2 | Excel `.xlsx` 底层解析 |
| `xlsxwriter` | 3.1.3 | Excel 条件格式与样式写入 |
| `Pillow` | 10.0.0 | 图像渲染（字库 1bpp 生成） |
| `numpy` | <2.0 | 数值计算辅助 |

### 8.2 运行时自动安装

| 包 | 安装时机 | 用途 |
|----|----------|------|
| `ndspy` | Stage 1 首次运行时 | NDS ROM 解析、BLZ 解压 |
| `opencc` | Stage 3 首次运行时 | 简繁日汉字映射 |

### 8.3 内部模块依赖图

```
main.py
├── config.py
├── stage1_unpack.py
│   ├── config.py
│   └── utils/binary_io.py
├── stage2_export_text.py
│   ├── config.py
│   └── utils/bbq_format.py
├── stage2_export_arm9.py
│   └── config.py
├── stage2_export_images.py
│   └── config.py
├── stage2_export_bg.py
│   └── config.py
├── stage3_build_font.py
│   ├── config.py
│   ├── utils/text_encoder.py
│   └── utils/binary_io.py
├── stage4_inject_text.py
│   ├── config.py
│   └── utils/text_encoder.py
├── stage4_import_images.py
│   └── config.py
├── stage4_import_bg.py
│   ├── config.py
│   └── stage2_export_bg.py
└── stage5_build_rom.py
    ├── config.py
    └── utils/binary_io.py
```

---

## 9. 数据流与目录约定

### 9.1 完整数据流

```
[原版 ROM] ──► 0_Original/ ──► Stage 1 ──► 1_Extracted/
                                              ├── SCN/      ──► Stage 2a ──► workspace/SCN_Translation.xlsx
                                              ├── TBL/      ──► Stage 2a ──► workspace/TBL_Translation.xlsx
                                              ├── ARM9/     ──► Stage 2b ──► workspace/ARM9_Overlays_Translation.xlsx
                                              ├── AGL/      ──► Stage 2c ──► 1_Extracted_Images/AGL/
                                              ├── TEX/      ──► Stage 2c ──► 1_Extracted_Images/TEX/
                                              ├── BG/       ──► Stage 2d ──► 1_Extracted_Images/BG/
                                              └── TBL/      ──► Stage 2c ──► 1_Extracted_Images/TBL/

workspace/*.xlsx ──► 翻译 ──► Stage 4a ──► 2_Patched/SCN_CHS_PATCHED/, TBL_CHS_PATCHED/, PRG_CHS_PATCHED/
1_Extracted_Images/ ──► 修图 ──► Stage 4b/4c ──► 2_Patched/*_IMG_PATCHED/, BG_CHS_PATCHED/
workspace/*.ttf + font_mapping.json ──► Stage 3 ──► 2_Patched/TBL_CHS_PATCHED/0000_LC10.NFTR, 0001_LC12.NFTR

2_Patched/ + 0_Original/Data/ ──► Stage 5 ──► 3_Build/<ROM>_CHS.nds
```

---

## 10. 关键类与数据结构

本项目未使用复杂的 OOP 类体系，以函数式和数据驱动为主。以下是核心数据结构说明。

### 10.1 BBQ Entry（文本条目）

```python
{
    'Original_Text': str,      # 原始日文文本
    'Speaker': str,            # 角色名（SCN）或 "×"/"System/PRG"
    'Translated_Text': str,    # 翻译文本（导出时为空）
    'File': str,               # 来源文件名
    'Text_Offset': str,        # 十六进制字符串，如 "0x1234"
    'Pointer_Locs': str,       # 指针表位置（十六进制）
    'Max_Bytes': int,          # 该文本允许的最大字节长度
    'Index': int,              # 在文件中的字符串索引
    'Type': str,              # 文本类型：角色名/对话1/对话2/系统文本/程序硬编码
}
```

### 10.2 NFTR Spec（字库规格）

```python
{
    'original': Path,          # 原版 NFTR 路径
    'font_file': Path,         # 替换用 TTF 字体路径
    'output': Path,            # 输出 NFTR 路径
    'cell_width': int,         # 字形单元宽度（像素）
    'cell_height': int,        # 字形单元高度（像素）
    'font_size': int,          # 渲染字号
    'y_offset': int,           # 垂直偏移（通常为 -1）
    'glyph_bytes': int,        # 每个字形占用的字节数（1bpp）
    'cjk_glyph_w': int,        # CJK 字符渲染宽度
    'cjk_advance': int,        # CJK 字符进距（字间距）
    'space_w': int,            # 空格渲染宽度
    'space_advance': int,      # 空格进距
}
```

### 10.3 File Pack（封包定义）

```python
{
    "ezt": "F_SCN.IDX",        # 索引文件名
    "ezp": "F_SCN.BIN",        # 数据文件名
    "output": "SCN"            # 输出目录名
}
```

---

## 11. 关键函数参考表

### 11.1 Stage 1 — 解包提取

| 函数 | 文件 | 输入 | 输出 | 说明 |
|------|------|------|------|------|
| `unpack_nds_rom()` | `stage1_unpack.py` | `ORIGINAL_ROM` | `0_Original/Data/`, `1_Extracted/` | NDS 解包主函数 |
| `_dump_folder()` | `stage1_unpack.py` | `ndspy` folder 对象 | 文件系统树 | 递归导出 FNT |
| `decompress_ring_lz()` | `stage1_unpack.py` | 压缩字节流, 解压大小 | `bytearray` | Ring-LZ 解压算法 |
| `extract_archive()` | `stage1_unpack.py` | EZT 路径, EZP 路径, 输出目录 | 解压后的文件列表 | BIN/IDX 提取 |

### 11.2 Stage 2 — 导出资源

| 函数 | 文件 | 输入 | 输出 | 说明 |
|------|------|------|------|------|
| `export_bbq_directory()` | `stage2_export_text.py` | 输入目录, 输出 Excel, `is_scn` | `.xlsx` 文件 | BBQ 批量导出 |
| `create_styled_excel()` | `stage2_export_text.py` | 分组数据, 输出路径, `is_scn` | 带样式的 Excel | 条件格式与列宽设置 |
| `parse_bbq_file()` | `utils/bbq_format.py` | BBQ 文件路径, `is_scn` | `List[Dict]` | BBQ 格式解析器 |
| `scan_prg_file()` | `stage2_export_arm9.py` | bin 文件路径, 文件名, 基址 | `List[Dict]` | ARM9 Shift-JIS 扫描 |
| `strict_filter()` | `stage2_export_arm9.py` | 文本字符串 | `bool` | 智能过滤非人类文本 |
| `parse_gld_common()` | `stage2_export_images.py` | GLD 文件路径 | 像素大小, 像素数据, BMP 调色板 | GLD 公共解析 |
| `convert_gld_to_bmp()` | `stage2_export_images.py` | GLD 路径, 输出目录 | 多个 BMP（穷举宽度） | 启发式 GLD → BMP |
| `parse_nclr()` | `stage2_export_bg.py` | NCLR 字节数据 | `List[(r,g,b)]` | 调色板解析 |
| `parse_ncgr()` | `stage2_export_bg.py` | NCGR 字节数据 | tiles, bpp, 行列数 | 图块解析 |
| `parse_nscr()` | `stage2_export_bg.py` | NSCR 字节数据 | 地图条目, 尺寸 | 地图解析 |
| `compose_bg_image()` | `stage2_export_bg.py` | tiles, palette, map, 尺寸 | 像素数据, 调色板 | 背景图合成 |

### 11.3 Stage 3 — 字库构建

| 函数 | 文件 | 输入 | 输出 | 说明 |
|------|------|------|------|------|
| `build_font_mapping()` | `stage3_build_font.py` | Excel 文件集 | `Dict[char, code]` | 扫描翻译文本并分配编码 |
| `parse_nftr_pamac()` | `stage3_build_font.py` | NFTR 文件路径 | 完整字节数据, PLGC/HDWC 偏移, 编码映射 | NFTR 结构解析 |
| `render_glyph_1bpp()` | `stage3_build_font.py` | 字符, 字体, 编码, 规格 | 字形字节, 字宽, 进距 | 1bpp 字形渲染 |
| `inject_nftr()` | `stage3_build_font.py` | 规格名, 规格字典, 字符映射 | 修改后的 NFTR 文件 | 字库注入主函数 |

### 11.4 Stage 4 — 注入与回写

| 函数 | 文件 | 输入 | 输出 | 说明 |
|------|------|------|------|------|
| `rebuild_bbq_file()` | `stage4_inject_text.py` | 原路径, 目标路径, 翻译字典, 字符映射 | 修改后的 BBQ 文件 | 文本池原地重建 |
| `process_bbq_directory()` | `stage4_inject_text.py` | Excel 路径, 输入子目录, 输出子目录, 字符映射 | 修改后的文件集 | BBQ 目录批量注入 |
| `process_arm9_overlays()` | `stage4_inject_text.py` | Excel 路径, 字符映射 | 修改后的 ARM9/Overlay | 程序文本原地注入 |
| `import_bmp_to_gld()` | `stage4_import_images.py` | BMP 路径, 原 GLD 路径, 输出 GLD 路径 | 修改后的 GLD 文件 | BMP → GLD 单文件回写 |
| `batch_import_images()` | `stage4_import_images.py` | 导入文件夹列表 | 多个修改后的 GLD | 批量图像回写 |
| `import_bg_triplet()` | `stage4_import_bg.py` | BMP, NCGR, NCLR, NSCR, 输出路径 | 修改后的三件套 | BG 背景图回写 |

### 11.5 Stage 5 — ROM 构建

| 函数 | 文件 | 输入 | 输出 | 说明 |
|------|------|------|------|------|
| `repack_data_archives()` | `stage5_build_rom.py` | 无（读取全局配置） | `Repack_Staging/` 下的 EZT/EZP | BIN/IDX 封包重建 |
| `build_nds_and_restore_twl()` | `stage5_build_rom.py` | 无（读取全局配置） | `3_Build/<ROM>_CHS.nds` | NDS ROM 构建与 DSi 嫁接 |
| `crc16_nds()` | `stage5_build_rom.py` | 字节数据 | `int` | NDS 专用 CRC16 计算 |

### 11.6 工具函数

| 函数 | 文件 | 输入 | 输出 | 说明 |
|------|------|------|------|------|
| `read_uint32()` | `utils/binary_io.py` | 文件对象或字节数组, 偏移 | `int` | 通用 4 字节读取 |
| `read_uint16()` | `utils/binary_io.py` | 文件对象或字节数组, 偏移 | `int` | 通用 2 字节读取 |
| `read_string_bytes()` | `utils/binary_io.py` | 文件对象, 偏移 | `bytes` | 读取 NUL 终止字符串 |
| `nlzss_compress()` | `utils/binary_io.py` | 原始字节 | 压缩后的字节 | LZ10 压缩 |
| `text_to_bytes()` | `utils/text_encoder.py` | 文本字符串, 字符映射 | `bytearray` | 文本 → 游戏字节流 |
| `load_mapping()` | `utils/text_encoder.py` | JSON 路径 | `Dict[str, int]` | 加载字库映射 |
| `is_protected()` | `utils/text_encoder.py` | 编码整数 | `bool` | 保护区判断 |

---

## 12. 外部对比与优化方向

### 12.1 与 faraplay/dearlystars_tool (Rust) 的对比

| 维度 | 本项目 (Python) | faraplay (Rust) | 差距 |
|------|----------------|-----------------|------|
| **ROM 构建内核** | 依赖 `ndspy` + 手工 DSi 嫁接 | 自研 `ndstool`，完整 NDS/DSi 支持 | 需补齐自研内核 |
| **BLZ 压缩** | 仅解压（ndspy），无压缩 | 自研高精度 BLZ 压缩/解压 | 需实现压缩 |
| **BIN/IDX 封包** | 手工解析，封包较初级 | 自研 EZ 模块，智能排除列表 | 需完善封包引擎 |
| **BBQ 解析** | 手工字节级解析，硬编码 Section | `binrw` 声明式解析，通用性强 | 需重构为结构化解析 |
| **图像格式** | BMP 8bpp（无透明） | PNG（支持透明，自动调色板匹配） | 需升级 PNG + Sprite Format 2/3 |
| **翻译表格式** | Excel（条件格式丰富） | CSV（版本控制友好） | 需双格式支持 |
| **邮件处理** | Excel 条件格式、字节公式 | CSV 硬截断 | **本项目领先** |
| **字库构建** | OpenCC + NFTR 动态构建 | **无** | **本项目独有优势** |
| **背景图处理** | NCGR/NCLR/NSCR 完整支持 | **无** | **本项目独有优势** |
| **交互界面** | 交互式菜单 + CLI | 纯 CLI | **本项目领先** |

### 12.2 优化方向摘要

详见项目根目录的 [`OPTIMIZATION_PLAN.md`](OPTIMIZATION_PLAN.md)，主要优化路线：

1. **P0 — 消除外部依赖**：自研 NDS ROM 构建内核、BLZ 压缩、BIN/IDX 封包引擎。
2. **P1 — 数据格式升级**：Excel ↔ CSV 双格式支持，BMP → PNG 透明通道支持。
3. **P2 — BBQ 引擎重构**：从手工字节解析升级为声明式/结构化解析，支持 YAML 互转。
4. **P3 — 图像与字库增强**：GLD Sprite Format 2/3 支持、PNG 透明、背景图 4bpp 完善。
5. **P4 — 工程化改进**：类型注解、pytest 覆盖、CI/CD、ROM 字节级回归测试。

---

*本文档由自动化分析生成，涵盖截至 2026-07-16 的代码状态。*
