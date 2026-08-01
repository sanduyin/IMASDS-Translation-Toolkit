---
name: "imasds-lyric-l10n"
description: "THE iDOLM@STER Dearly Stars NDS 歌词填字游戏汉化流水线——从 BBQ 提取汉字、charmap 重定位、美工字形制作（PNG/PSD）、AGL/GLD 扩容、ARM9 四补丁、BBQ 注入、ROM 构建。Invoke when adding/modifying Chinese characters in lyric minigame or rebuilding the patched ROM."
---

# IMASDS 歌词填字游戏汉化流水线

> 适用于《THE iDOLM@STER Dearly Stars》（NDS）的歌词填字小游戏（Lesson Voice）汉字化工作流。
> 覆盖：BBQ 汉字提取 → charmap 重定位 → **美工字形制作** → AGL/GLD 扩容 → ARM9 四补丁 → BBQ 文本注入 → ROM 构建。

---

## 0. 核心结论（一句话根因）

AGL 文件头部写的是 `frame_count = N`（N > 255，如 `0x01E9 = 489`），但 ARM9 的 AGL v3 转换链用 `LDRB`（字节加载）只取低 8 位 → 变成 `0xE9 = 233`，导致 frame 233 以后从不构建 → 对应汉字全部空白。

修复：两条 `LDRB` → `LDRH` 指令（`0x3D28C` 和 `0x3D66C`）。

---

## 1. 项目路径约定

| 路径 | 说明 |
|------|------|
| `IMASDS-Translation-Toolkit/` | 主工具链 |
| `IMASDS-Translation-Toolkit/game_data/0_Original/` | 原版 ROM 放这里 |
| `IMASDS-Translation-Toolkit/game_data/1_Extracted/` | Stage 1 解包产物 |
| `IMASDS-Translation-Toolkit/game_data/2_Patched/` | 修改后的文件（按包分子目录） |
| `IMASDS-Translation-Toolkit/game_data/3_Build/` | 最终输出 `_CHS.nds` |
| `IMASDS-Translation-Toolkit/scripts/` | 独立分析/补丁脚本 |
| `IMASDS-Translation-Toolkit/build/lesvoice_patch/` | 歌词汉化中间产物 |
| `IMASDS-Translation-Toolkit/workspace/` | 翻译表格 + 字体文件 |

### 关键文件对应

| 概念 | 文件 | 备注 |
|------|------|------|
| ARM9 主程序 | `1_Extracted/ARM9/arm9.bin` | BLZ 解压后的 |
| overlay_0006 | `1_Extracted/ARM9/overlay_0006.bin` | 歌词游戏代码 + charmap |
| y9 重叠表 | `1_Extracted/y9.bin` | overlay RAM 映射 |
| 填字 AGL | `1_Extracted/AGL/0506_*.AGL`, `0520_*.AGL` | sprite 布局（v3 格式） |
| 填字 GLD | `1_Extracted/AGL/0507_*.GLD`, `0521_*.GLD` | 像素数据 |
| 歌词 BBQ | `1_Extracted/TBL/0022_LESVOICETABLE*.BBQ` | 10 首歌的歌词文本 |
| SSOT 字符表 | `build/lesvoice_patch/ssot_chars.csv` | 字符-码位-glyph_id 对照 |

---

## 2. 完整流水线（8 步）

### Step 1 — 解包原始 ROM

```bash
cd IMASDS-Translation-Toolkit
python3 main.py unpack
```

产出：`1_Extracted/` 下所有解压后的资源（ARM9、overlays、AGL、TBL 等）。

---

### Step 2 — 从 BBQ 提取汉字 + 构建 SSOT + charmap 重定位

**脚本**：`scripts/build_charmap_patch.py`

**核心原则：字符集由 BBQ 歌词内容决定，不是固定数字。**

做什么：
1. 扫描全部 10 首 LESSONVOICE*.BBQ 的 Section 7 文本池
2. **提取去重后的所有中文汉字**（也包含日文原文中的假名/符号）
3. 保留原 charmap 中的 82 条假名/符号
4. 为新增中文汉字统一分配私有码位（`0xF040–0xF9FC`，SJIS G3 用户区）
5. 构建新 charmap 表（6 字节/条：glyph_id + key + reserved）
6. **重定位** charmap 到 overlay 文件尾部 + 0x20 BSS padding 之后
7. 补丁 y9.bin 的 overlay 6 ram_size

> 如果歌词翻译增加了新汉字，重新运行此步骤会自动更新 SSOT 和 charmap。  
> 如果通过重复歌词减少了汉字数量，SSOT 同样会自动缩小。

输出：
- `build/lesvoice_patch/ssot_chars.csv`
- `build/lesvoice_patch/charmap_new.csv`
- `build/lesvoice_patch/overlay_0006_patched.bin`
- `build/lesvoice_patch/y9_patched.bin`

> charmap 条目结构：`u16 glyph_id (LE) + u8 key[2] (BE SJIS) + u16 reserved (LE)`  
> 表按 key 升序排列，运行时用二分查找。

---

### Step 3 — 美工字形制作流程（PNG / PSD）

**这是最关键的人工环节。** GLD 中的字形像素必须由美工手动制作或精修，不能依赖程序自动生成。

#### Step 3a — 导出字形模板

**脚本**：`scripts/export_sprite_ref.py`（原版参考）或 `scripts/export_rom_gld_png.py`（完整网格）

做什么：
1. 读取原版 GLD（0507 / 0521）
2. 导出每个 sprite 为单独 PNG（按 glyph_id 命名，如 `glyph_51_一_4e00.png`）
3. 导出整张字形网格图（便于整体审阅）
4. 导出的 PNG 可直接用 Photoshop 打开编辑

输出目录建议：
```
workspace/glyph_artwork/
  ├── 0507_D_MEASURE_MOJI_MNG/
  │   ├── ref_glyph_00_あ.png      # 原版参考（不可改）
  │   ├── ref_glyph_01_い.png
  │   └── ...
  ├── 0521_D_EPANEL_MOJI_MNG/
  └── new_glyphs/                  # 新增汉字字形
      ├── glyph_081_一.png
      ├── glyph_082_上.png
      └── ...
```

> 如果美工习惯 PSD 工作流，可将 PNG 导入 Photoshop，在 PS 中制作/精修后，**导出为同名 PNG 放回 `new_glyphs/` 目录**。

#### Step 3b — 美工制作/精修字形

- 字形尺寸：**12×12 像素**（与原版假名一致）
- 颜色模式：1bit 单色或调色板索引色（Format 6，8bpp）
- 必须用 **12×12 的严格像素网格**，不能超出边界
- 风格应与原版假名保持一致（笔画粗细、留白比例）
- 保存格式：**PNG**（透明背景或纯白背景均可，注入脚本会处理）

#### Step 3c — 字形审核

用 `scripts/export_rom_gld_png.py` 生成整张网格图，在模拟器中对比显示效果：
- 字形是否清晰可读
- 是否与原版假名风格协调
- 是否有像素溢出或裁切

---

### Step 4 — GLD 注入（美工字形 → 像素数据）

**脚本**：`scripts/expand_gld_chinese.py`

做什么：
1. 读取原版 GLD（81 个 footer + 对应像素）
2. **从 `new_glyphs/` 目录读取美工制作的 PNG 字形**
3. 将 PNG 转换为 12×12 的 1bit/8bpp 像素数据
4. 追加像素数据到 pixel_data 区
5. 追加 footer 条目（pixels_offset 指向新像素）
6. 更新 header 的 `pixel_data_size` 和 `data_0c`

输入：原版 GLD + SSOT + `workspace/glyph_artwork/new_glyphs/*.png`
输出：`build/lesvoice_patch/0507_*_patched.GLD`, `0521_*_patched.GLD`

> GLD footer 条目 = 32 字节；`pixels_offset` 是 32 位值，运行时按字读取，没有 16 位截断问题。

---

### Step 5 — AGL 扩容（sprite 布局）

**脚本**：`scripts/expand_agl_chinese.py`

做什么：
1. 读取原版 AGL（81 frame）
2. header `frame_count` (0x0C) 更新为 **82 + 中文去重数量**
3. cell entry `frame_count` (0x02/0x04) 同步更新
4. Table 5 第二个 u16 同步更新
5. 为新增的 frame 生成占位 cell entry

输入：原版 AGL + SSOT
输出：`build/lesvoice_patch/0506_*_patched.AGL`, `0520_*_patched.AGL`

---

### Step 6 — ARM9 四补丁（必打！）

**脚本**：`scripts/patch_arm9_rangecache_subtable.py`（补丁 1-2：range-cache）  
**脚本**：`scripts/patch_arm9_agl_framecount_u16.py`（补丁 3-4：LDRB→LDRH）  
**安全脚本**：`patch_imasds_arm9.py`（合并四补丁，带校验）

两个补丁互相独立，都打在同一个 `arm9.bin` 上。**推荐用安全脚本**，它会先校验机器码再写入。

#### 补丁 1-2：range-cache subtable 分配公式

| 项 | 值 |
|----|----|
| 文件偏移 | `0x62608` / `0x6260C` |
| RAM 地址 | `0x0206A608` / `0x0206A60C` |
| 函数 | `FUN_000625c8`（subtable 分配器） |
| 原代码 | `alloc_size = first_frame_index * 12 + 16`（动态分配，中文会越界） |
| 修复后 | 固定分配 `0x16F0`（5872 字节，足够 489 个 12 字节条目） |
| 机器码改动 | `mov r0, #0x1600` + `add r1, r0, #0xF0` |

#### 补丁 3-4：AGL v3 frame_count 8 位→16 位读取

| 用途 | 文件偏移 | RAM 地址 | 原指令 | 新指令 |
|------|---------|----------|--------|--------|
| 内存大小计算 | `0x3D28C` | `0x0204128C` | `LDRB r5,[r7,#2]` | `LDRH r5,[r7,#2]` |
| frame 转换循环 | `0x3D66C` | `0x0204166C` | `LDRB r6,[r5,#2]` | `LDRH r6,[r5,#2]` |

机器码：
```
0x3D28C: 02 50 D7 E5  →  B2 50 D7 E1
0x3D66C: 02 60 D5 E5  →  B2 60 D5 E1
```

> **注意**：两条必须一起改。只改循环不改大小计算 = 越界写内存。

#### 使用安全脚本（推荐）

```bash
# 检查当前 ARM9 状态
python3 scripts/patch_imasds_arm9.py check game_data/1_Extracted/ARM9/arm9.bin

# 应用四补丁（生成新文件，不覆盖原文件）
python3 scripts/patch_imasds_arm9.py patch \
  game_data/1_Extracted/ARM9/arm9.bin \
  game_data/2_Patched/PRG_CHS_PATCHED/arm9.bin

# 验证补丁是否完整
python3 scripts/patch_imasds_arm9.py verify game_data/2_Patched/PRG_CHS_PATCHED/arm9.bin
```

脚本特性：
- 只接受四个补丁点处于"原指令"或"目标指令"状态的文件
- 遇到其他机器码立即拒绝写入（防止误 patch）
- 支持未补丁、部分补丁、完整补丁的输入（幂等）
- 原子写入（临时文件 + replace，不会写坏文件）

---

### Step 7 — BBQ 歌词文本注入

**脚本**：`scripts/inject_bbq_chinese.py`

做什么：
1. 读取原版 LESVOICETABLE BBQ
2. 将翻译后的中文歌词按 SSOT 编码为私有码位字节流
3. 写回 BBQ 的 Section 7（文本池）
4. 校验长度不超限

输入：翻译 XLSX + SSOT
输出：`build/lesvoice_patch/0022_*_patched.BBQ` ~ `0031_*_patched.BBQ`（10 首）

---

### Step 8 — 部署补丁文件 + 构建 ROM

部署到 `2_Patched/` 对应子目录：

```bash
# ARM9（已打好四补丁）
cp arm9_patched.bin 2_Patched/PRG_CHS_PATCHED/arm9.bin

# overlay_0006
cp overlay_0006_patched.bin 2_Patched/PRG_CHS_PATCHED/overlay_0006.bin

# AGL/GLD
cp 0506_*_patched.AGL  2_Patched/AGL_CHS_PATCHED/0506_D_MEASURE_MOJI_MNG.AGL
cp 0507_*_patched.GLD  2_Patched/AGL_CHS_PATCHED/0507_D_MEASURE_MOJI_MNG.GLD
cp 0520_*_patched.AGL  2_Patched/AGL_CHS_PATCHED/0520_D_EPANEL_MOJI_MNG.AGL
cp 0521_*_patched.GLD  2_Patched/AGL_CHS_PATCHED/0521_D_EPANEL_MOJI_MNG.GLD

# BBQ
cp 002*_patched.BBQ  2_Patched/TBL_CHS_PATCHED/
```

然后构建 ROM：

```bash
python3 main.py build
```

输出：`game_data/3_Build/THE iDOLM@STER Dearly Stars_CHS.nds`

---

## 3. 关键数据结构速查

### AGL v3 Header

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0x00 | 4 | magic | `"RCN\x00"`？ |
| 0x0C | 2 | frame_count | 总 frame 数（**16 位，原代码误按 8 位读**） |
| ... | ... | ... | ... |

### GLD Header

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0x00 | 4 | magic | `"SPSX"` |
| 0x0C | 4 | data_0c | 与像素区大小相关 |
| ... | ... | ... | ... |
| footer 区 | 32B/entry | footer table | `pixels_offset` 是 32 位 |

### Charmap 条目（6 字节）

```
+0  u16  glyph_id    (little-endian)
+2  u8   key[0]      (SJIS high byte)
+3  u8   key[1]      (SJIS low byte)
+4  u16  reserved    (little-endian, 通常 0)
```

### Overlay 6 y9 表项（32 字节）

| 偏移 | 字段 | 说明 |
|------|------|------|
| +0 | overlay_id | 6 |
| +4 | ram_address | `0x0211D13C` |
| +8 | ram_size | 需更新为新大小（含追加的 charmap） |
| +12 | bss_size | 保持 0x20 |
| +24 | file_id + flag | 低 24 位 file_id，高 8 位压缩标志 |
| +28 | size_flag | 高 8 位 = 0x02 未压缩 / 0x03 BLZ |

---

## 4. 常见问题排查

### Q: 有一部分汉字显示空白方框

**检查顺序**：
1. GLD 中对应 glyph_id 的像素数据是否非空 → `scripts/verify_indices_81_488.py`
2. AGL frame_count 是否真的是 `82 + 中文数量`（检查 0x0C 处的 u16）
3. ARM9 的四条补丁是否全部打上了 → 用 `patch_imasds_arm9.py verify`
4. range-cache subtable 分配补丁是否打上了（`0x62608` 和 `0x6260C`）
5. charmap 二分查找是否能命中 → 检查表是否按 key 升序

### Q: 进游戏直接白屏

常见原因：
- ARM9 0x0FC4 没清零（BLZ 解压标志残留）
- Secure Area CRC 不对
- Header CRC 不对
- overlay 压缩标志高字节写错（应为 0x02 表示未压缩）

构建脚本 `stage5_build_rom.py` 已自动处理以上全部。

### Q: 只有左边候选字有问题 / 只有下方填字区有问题

- 左侧候选 → `0506/0507` 组（MEASURE）
- 下方填字 → `0520/0521` 组（EPANEL）

两组是独立的 AGL/GLD，分别检查。

### Q: 怎么验证 ARM9 补丁有没有打上

```bash
# 推荐用安全脚本
python3 scripts/patch_imasds_arm9.py verify arm9.bin

# 或手动检查
xxd -s 0x3D28C -l 4 arm9.bin   # 应为 b2 50 d7 e1 (LDRH)
xxd -s 0x3D66C -l 4 arm9.bin   # 应为 b2 60 d5 e1 (LDRH)
xxd -s 0x62608 -l 4 arm9.bin   # 应为 16 0c a0 e3 (MOV)
xxd -s 0x6260C -l 4 arm9.bin   # 应为 f0 10 80 e2 (ADD)
```

### Q: 新增了歌词汉字怎么办？

重新按顺序执行：
1. `build_charmap_patch.py`（Step 2，自动提取新汉字）
2. 美工制作新字形 PNG（Step 3）
3. `expand_gld_chinese.py`（Step 4，注入新字形）
4. `expand_agl_chinese.py`（Step 5，frame_count 自动更新）
5. ARM9 补丁不变（Step 6，代码补丁不依赖数据量）
6. `inject_bbq_chinese.py`（Step 7，新歌词编码）
7. 重新构建 ROM（Step 8）

### Q: 减少了歌词汉字怎么办？

同上，只是 SSOT 会自动缩小，frame_count 会自动减小。AGL/GLD 会重新生成。

---

## 5. 相关脚本索引

| 脚本 | 用途 | 阶段 |
|------|------|------|
| `build_charmap_patch.py` | SSOT + charmap 重定位 + y9 补丁 | Step 2 |
| `export_sprite_ref.py` | 导出原版 sprite PNG 参考 | Step 3a |
| `export_rom_gld_png.py` | 导出完整字形网格图 | Step 3a / 审核 |
| `expand_gld_chinese.py` | GLD 注入美工字形 | Step 4 |
| `expand_agl_chinese.py` | AGL 扩容 | Step 5 |
| `patch_imasds_arm9.py` | **安全四补丁脚本**（check/patch/verify） | Step 6 |
| `patch_arm9_rangecache_subtable.py` | range-cache 补丁（独立） | Step 6 |
| `patch_arm9_agl_framecount_u16.py` | LDRB→LDRH 补丁（独立） | Step 6 |
| `inject_bbq_chinese.py` | BBQ 歌词文本注入 | Step 7 |
| `verify_indices_81_488.py` | 验证 GLD 字模完整性 | 诊断 |
| `diag_blank_root_cause.py` | 空白字根因诊断 | 诊断 |
| `verify_rom_full.py` | ROM 构建后全量校验 | 验证 |

---

## 6. 必须保持的约束（来自 patch-imasds-lyric-font）

- **同时修改** AGL v3 的大小计算和 frame 转换循环；只改后一处会造成数组越界。
- **保留** range-cache 的 `0x16F0` 固定分配。它覆盖索引 0–488，最后一项结束于 `0x16EC`，仍有 4 字节余量；不要无依据改成 `0x1700`。
- **不修改** Overlay 0006 的 `AND #0xFF` 来解决此问题；该值不是 glyph ID。
- **不把** GLD `pixels_offset` 改成 16 位；运行时按 32 位读取。
- **不在** skill 中保存或分发 ARM9 成品二进制。始终对用户自己的解包文件应用补丁。

---

## 7. 已知 ARM9 哈希参考

| 状态 | SHA-256 |
|---|---|
| 原始 ARM9 | `14e8d4656801a47108eb9d987b19e962773dc6eb5066e039b8f14faef144c980` |
| 仅 range-cache | `2f3fd4b672781c999159410aa5d738452e1fd0f6e7d91f4d91a08f2c1907788b` |
| 完整四补丁 | `3422eaf862fdaa79d195c4fef813269bd6144af0be07ebae2a0dcff4b2c0b1ce` |

如果 ARM9 还含有文本或其他合法修改，整文件哈希会不同。此时只能在四个指令点全部匹配已知原/目标机器码时应用补丁，不能强行按偏移写入。
