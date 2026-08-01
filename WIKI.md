# 偶像大师DS汉化逆向工程 — 知识库 Wiki

> **本文档定位**：自包含的可调取知识库，汇总偶像大师DS填歌词游戏汉化逆向工程的
> 所有分析结论。在另一台电脑上**无需访问源代码**即可理解全部结论。
>
> **置信度标注**：
> - `[确证]` = 反编译/实测确认
> - `[强推测]` = 数据规律推导
> - `[待验证]` = 需实机验证
>
> **最后更新**：2026-07-25

---

## 目录

1. [项目概览](#1-项目概览)
2. [技术结论速查表](#2-技术结论速查表)
3. [地址映射表](#3-地址映射表)
4. [补丁点清单](#4-补丁点清单)
5. [函数地址索引](#5-函数地址索引)
6. [文件格式规范](#6-文件格式规范)
7. [实验状态](#7-实验状态)
8. [常见问题](#8-常见问题)
9. [文档索引](#9-文档索引)
10. [变更历史](#10-变更历史)

---

## 1. 项目概览

| 项目 | 内容 |
|------|------|
| **项目目标** | 偶像大师DS (THE iDOLM@STER DearlyStars) 填歌词游戏汉化 |
| **分析对象** | ARM9 主程序 (0x02008B9C-0x020D8428) + overlay_0006.bin (0x02117320-0x02123EA0) |
| **填歌词游戏入口** | 事件 ID 3100（分配 16564 B，最可能为完整小游戏入口）`[强推测]` |
| **游戏布局** | 2×12 字符格（12 字一行，共两行），全假名填字 `[确证]` |
| **歌词文件** | F_TBL/0022-0031_LESVOICETABLE*.BBQ（10 首歌）`[确证]` |
| **字形资源** | AGL/GLD 图片配对（0506/0507 左侧候选字、0520/0521 下侧题目字）`[确证]` |
| **当前进度** | 逆向分析完成，待实机验证 |
| **汉化组进度** | 已翻译 6/10 首歌词，约 200 个简体汉字 `[确证]` |
| **分析工具** | Ghidra 12.1.2 + ghidra-mcp 插件 (HTTP API @ localhost:8089) |
| **Ghidra 项目** | `DS_ARM9_Full.gpr`，加载基址 0x02117320 |
| **处理器** | ARM:LE:32:v5t（ARM946E-S / ARMv5TE，含 Thumb） |

### 1.1 数据流总览 [确证]

```
BBQ 歌词文件 (F_TBL/00xx_LESVOICETABLE*.BBQ)
  │  存储歌词文本（原 SJIS，汉化改为私有码位 0xF040-0xF9FC）
  ▼
overlay_0006.anim_text_fx_update (0x0211f13c)
  │  逐字符读取歌词（每行 12 字，共 2 行，固定 2 字节步长）
  │  调用 char_map_equal_range 查找 charmap 表
  ▼
charmap 表 (0x021232FC, 82条 → 重定位到 0x02123EC0)
  │  key(2字节) → glyph_id (uint16, 16位 LDRSH)
  │  通过 PTR_DAT_0211ff2c / PTR_DAT_0211ff30 间接引用
  ▼
glyph_id (uint16)
  │  作为 AGL/GLD sprite 索引
  │  左侧候选字和下侧题目字共用同一 charmap 表
  ▼
ARM9 资源加载系统 (FUN_0206e664, param_4==5)
  │  按 resource_id 加载 AGL/GLD 文件（配对：AGL id + GLD id = id+1）
  │  13项运行时表缓存资源句柄（8字节/条目）
  ▼
AGL/GLD 字形 sprite (0507_D_MEASURE_MOJI_MNG / 0521_D_EPANEL_MOJI_MNG)
  │  AGL frame[i].glyph_id = 0x1000 + i  ↔  GLD footer[i] (1:1 映射)
  │  渲染到屏幕
  ▼
玩家看到的歌词字符
```

### 1.2 两条独立字形渲染路径 [确证]

| 路径 | 用途 | 字形来源 | 编码 |
|------|------|---------|------|
| **路径 A** | 填歌词游戏 | AGL/GLD 图片（0506/0507/0520/0521） | SJIS/私有码位 |
| **路径 B** | 剧情对话 | ARM9 标准字库 LC12.nrtf | SJIS |

**两条路径完全独立**，汉化策略需分别推进。本 Wiki 主要关注路径 A（填歌词游戏）。

---

## 2. 技术结论速查表

### 2.1 charmap 表 [确证]

| 项目 | 值 |
|------|-----|
| 表起始地址（原） | RAM 0x021232FC / 文件偏移 0xBFDC |
| 表结束地址（原） | RAM 0x021234E8 / 文件偏移 0xC1C8 |
| 表大小 | 492 字节（0x1EC） |
| 条目数 | 82 |
| 条目大小 | 6 字节 |
| 排序方式 | 按 key 字节序列无符号升序（与 memcmp 一致） |
| 引用方式 | 通过 PTR_DAT_0211ff2c/ff30 间接引用，二分查找 |
| 字面池指针位置 | 文件偏移 0x8C0C / 0x8C10 |
| 补丁点数 | 仅 2 处（第 3 处 0xB9D0 是地址巧合，保持不动） |
| 字符范围 | 平假名（ぁ-ん）+ 全角空格 + 长音记号「ー」+ 减号「−」 |
| glyph_id 范围 | 0x0000-0x0050（81 个唯一值，0x2E 重复用于「ー」和「−」） |
| reserved 字段 | 全部为 0x0000 |
| 重定位目标 | RAM 0x02123EC0 / 文件偏移 0xCBA0 |

### 2.2 AGL/GLD 格式 [确证]

| 项目 | AGL | GLD |
|------|-----|-----|
| 魔数 | `\0LGA` (0x41474C00 LE) | `\0DLG` (0x474C4400 LE) |
| 头部大小 | 40 字节 (0x28) | 32 字节 |
| Type ID | 0xC | 0xD |
| ARM9 解析函数 | FUN_02042660 (dispatcher) + FUN_02042440 (v3 parser) | FUN_0204e41c |
| 字形数 | frame_count (header 0x0C) | footer_entry_count (header 0x1C) |
| glyph_id 字段 | frame entry 偏移 0x16 (vals[11]) | — |
| glyph_id 规律 | `0x1000 + frame_index` | — |
| 配对关系 | AGL id + 1 = GLD id | |
| 映射关系 | AGL frame_count == GLD footer_entry_count，按索引 1:1 映射 | |
| 0506/0507 (MEASURE) | 81 frame, 全 Format 2 (2bpp/4色), 18×18 | 7420 字节 |
| 0520/0521 (EPANEL) | 81 frame, 74×Format2 + 7×Format6 (8bpp/8色/alpha), 24×24 | 16444 字节 |
| 字节一致重打包 | ✅ 全部 4 个样本 PASS | |
| 汉化是否改 AGL | 若字形数 ≤ 81：不改；新增字形需扩展 frame_count | |

### 2.3 BBQ 格式 [确证]

| 项目 | 值 |
|------|-----|
| 魔数 | `.BBQ` (0x2E424251 LE) |
| 版本字符串 | `1.00` |
| 头部大小 | 24 字节 |
| Section 表偏移 | 0x18 (header_size) |
| Section 条目大小 | 20 字节 |
| Section 数量 | 4（10 首歌全部一致：`[2, 5, 6, 7]`） |
| Type ID | 0（不解析，游戏代码自处理） |
| ARM9 解析函数 | FUN_0205DD68（magic 校验 + Section 表遍历） |
| Section 2 | 全局参数表（28 字节，含难度/节拍）`[强推测]` |
| Section 5 | 时序事件表（16 字节/条目，文本索引 + 时间参数） |
| Section 6 | 空 Section（预留） |
| Section 7 | 歌词文本（指针表 + 文本池） |
| 文本编码 | SJIS，0x00 终止，无 BOM，无长度前缀 |
| 文本读取 | 固定 2 字节步长，不做 lead byte 判定 |
| 控制码 | **无任何控制码**（无 LF/CR/TAB/转义） |
| 字符集 | 82 个唯一字符（平假名 + 全角空格 + 长音记号） |
| 时序绑定 | **文本长度与时序无绑定关系**（替换歌词不会破坏时序） |

### 2.3.1 BBQ 题目-答案-候选字映射机制 [确证] (V4 新增)

**Section 5 事件分类**（10 首歌 600 条事件 100% 分类，0 条 UNKNOWN）：

| 事件类型 | 占比 | f0/f1/f2 含义 | 用途 |
|---------|------|--------------|------|
| LYRIC_DISPLAY | 42.7% | f0=文本索引, f1/f2/f3=时序 | 歌词显示 |
| CANDIDATE_SETUP | 32.3% | f0=答案文本索引, f1=候选池1, f2=候选池2 | 候选字设置 |
| CONTROL | 25.0% | f0 ≥ 0x10000 (packed u16) | 控制事件 |

**CANDIDATE_SETUP 的两种子模式**：

| 模式 | 次数 | f3 | 答案来源 | c1/c2 | 用途 |
|------|------|-----|---------|-------|------|
| 主模式 | 150 | hi=3, lo=2 | text[4] (4字 gibberish) | c1≠c2 | 填字小游戏 |
| 次模式 | 44 | hi=0, lo=6/14/30 | 实际歌词 | c1=c2 | 关键歌词高亮 |

**关键发现**：
- 10 首歌的 text[4/5/6] 都是固定长度的 gibberish 假名串（4/8/10 字），跨歌曲结构完全一致
- 主模式答案=4 字、候选池=14 唯一字（来自 c1+c2=18 字去重），9/10 首歌答案字全在池中
- 候选池布局与 overlay_0006 的 2×12 字符格 + 左侧 MEASURE_MOJI 完美对应

**对汉化的影响** [确证]：
- **主模式 gibberish 无需翻译**（无语义，直接保留原样）
- **次模式歌词随 LYRIC_DISPLAY 同步翻译**（共用同一文本池，无额外补丁点）
- **Section 5 事件本身无需修改**（f0 是文本索引，只要新文本能被正确编码即可）
- **不要替换 text[4/5/6]**（它们是候选字池，替换会破坏候选字逻辑）

### 2.3.2 AGL/GLD 像素编排 [确证] (V4 新增)

| 项目 | 值 |
|------|-----|
| 像素排列 | **LINEAR（行主序）**，非 8×8 tile |
| 像素字节偏移 | `pixels_offset + (y * render_width + x) * bit_depth / 8` |
| 2bpp 打包 | LE 顺序（低 bit 对应低 x 坐标），每字节 4 像素 |
| 4bpp 打包 | LE 顺序，每字节 2 像素 |
| 8bpp 打包 | 1 字节 1 像素 |
| render_width (Type2) | `8 << render_width_id` |
| render_height (Type2) | `8 << render_height_id` |
| crop_x/crop_y | sprite 在 render 画布中的裁剪起点（样本均为 0） |
| Format 6 位排列 | **低 3 位 = color_idx，高 5 位 = alpha_bank**（0-31） |
| 调色板格式 | BGR555：bit15=不透明，bit0-4=R, bit5-9=G, bit10-14=B |
| 压缩 | **无压缩**（pixel_data 和 palette_data 均未压缩） |
| 像素对齐 | pixels_offset 对齐到 16 字节边界 |
| 字节一致重打包 | ✅ utils/gld_format.py 的 extract/inject_sprite_rgba 验证正确 |

### 2.4 编码方案 [确证]

| 项目 | 值 |
|------|-----|
| 编码方案 | 私有码位 0xF040-0xF9FC（1880 码位） |
| 原因 | CP932 无法编码简体中文（如「说」「这」「车」「门」「爱」「语」） |
| 分配规则 | `lead = 0xF0 + n // 188; trail = 0x40 + n % 188; if trail >= 0x7F: trail += 1` |
| 终止符约束 | lead/trail 都不能为 0x00（与 0x00 终止符冲突） |
| 必须验证项 | (1) BBQ 是否做 lead byte 判定；(2) 是否与 0xF0-0xF9 控制码冲突 |
| 兼容性 | ARM9 文本读取采用固定 2 字节步长，支持任意双字节码位 |
| utils 改造 | `utils/bbq_format.py` 解码逻辑需改为按码表解码（先查 charmap，再 fallback） |

### 2.5 重定位方案 [确证]

| 项目 | 值 |
|------|-----|
| 推荐方案 | 方案 A：修改双指针 + 扩展表（补丁点 2 处 + 新表数据） |
| 新表放置 | 文件尾部 0xCBA0 起（先 0x20 字节零填充覆盖原 BSS，再放新表） |
| 新表 RAM 起始 | 0x02123EC0 = RAM_BASE 0x02117320 + 文件偏移 0xCBA0 |
| BSS 处理 | 原 BSS 段 32 字节（0x02123EA0-0x02123EBF）已并入文件零填充 |
| y9.bin 修改 | overlay_0006 条目偏移 0xC0 + 0x08（RAM Size）+ 0xCC（BSS Size） |
| 关键约束 | RAM Size 必须等于新的文件大小（NitroSDK 按此复制） |
| 共存 overlay | overlay 2-9 互斥加载（同一 RAM 区域 0x02117320），扩容不冲突 |
| Code Cave | 仅 363 字节，分散，**不可用于扩展表**，仅能放跳转桩 |

### 2.6 资源加载系统 [确证]

| 项目 | 值 |
|------|-----|
| resource_id 格式 | bit15-12: dir_id (0-9)；bit11-0: file_id (0-4095) |
| 掩码 | 0x0FFF（来自 DAT_020658a8） |
| 目录数 | 10 |
| AGL/GLD 配对 | GLD id = AGL id + 1（param_4==5 专用路径） |
| 缓存机制 | AGL/GLD: 0x40 个 0x28 字节槽；普通: 0x10 字节槽按类型分组 |
| 状态流转 | 1 (加载中) → 2 (就绪) → 3 (已交付) |

---

## 3. 地址映射表

### 3.1 overlay_0006.bin 关键地址 [确证]

| 用途 | RAM 地址 | 文件偏移 | 说明 |
|------|---------|---------|------|
| overlay 加载基址 | 0x02117320 | 0x0000 | y9.bin RAM Address |
| overlay 文件结束 | 0x02123EA0 | 0xCB80 | 文件大小 52096 字节 |
| .text 段 | 0x02117320-0x02122D7F | 0x0000-0xBA5F | 47712 字节，189 个函数 |
| .rodata 段 | 0x02122D80-0x0212351F | 0xBA60-0xC1FF | 1792 字节 |
| .data 段 | 0x02123520-0x02123E9F | 0xC200-0xCB7F | 2560 字节（含 charmap 表） |
| .bss 段 | 0x02123EA0-0x02123EBF | N/A | 32 字节（y9 BSS Size=0x20） |
| **charmap 表数据** | **0x021232FC** | **0xBFDC** | 492 字节，82 条 × 6 字节 |
| charmap 表结束（半开） | 0x021234E8 | 0xC1C8 | 指向最后条目之后 |
| **字面池指针 1（表起始）** | **0x0211ff2c** | **0x8C0C** | 4 字节，原值 = 0x021232FC，**补丁点 1** |
| **字面池指针 2（表结束）** | **0x0211ff30** | **0x8C10** | 4 字节，原值 = 0x021234E8，**补丁点 2** |
| 13项 UI sprite 表 resource_id 数组 | 0x02123288 | 0xBF68 | 13 个 u16 (26 字节) |
| 13项 UI sprite 表指针 (PTR_DAT_0211ec08) | 0x0211ec08 | 0x78E8 | 指向 0x02123288，**保持不动** |
| dialog_seq 指针表（第 3 处引用） | 0x02122CF0 | 0xB9D0 | **保持不动**（地址巧合，dialog_seq 仍读 0x021234E8） |
| textctx_init（overlay 入口） | 0x02117320 | 0x0000 | 场景 ID 2/0x14/事件 3100 触发 |
| anim_text_fx_create | 0x0211e280 | 0x6F60 | 构建 13 项运行时表 |
| anim_text_fx_update | 0x0211f13c | 0x7E1C | charmap 唯一调用者（2×12 字符格） |
| char_map_equal_range | 0x0211ff3c | 0x8C1C | 二分查找（泛型模板） |
| char_map_upper_bound | 0x0212002c | 0x8D0C | upper_bound 模板 |
| char_map_lower_bound | 0x021200ac | 0x8D8C | lower_bound 模板 |
| glyph_entry_lower_bound | 0x0211ec50 | 0x7F30 | 13项表二分查找（8字节步长） |
| glyph_entry_sort | 0x0211ecb0 | 0x7F90 | 13项表排序（内省排序） |

### 3.2 重定位后地址（实验 3 产物）[确证]

| 用途 | RAM 地址 | 文件偏移 | 说明 |
|------|---------|---------|------|
| BSS 填充区 | 0x02123EA0-0x02123EBF | 0xCB80-0xCB9F | 32 字节零填充 |
| **新 charmap 表起始** | **0x02123EC0** | **0xCBA0** | 重定位后 |
| **新 charmap 表结束** | **0x021240AC** | **0xCD8C** | 半开区间 |
| 新 overlay 文件大小 | — | 0xCD8C (52620 字节) | 增量 524 字节 |

### 3.3 y9.bin 中 overlay 6 条目 [确证]

y9.bin 中 overlay_0006 条目偏移 = 6 × 0x20 = **0xC0**：

| 字段 | 偏移 | 原值 | 实验 3 新值 |
|------|------|------|-----------|
| Overlay ID | 0xC0 + 0x00 | 6 | 不变 |
| RAM Address | 0xC0 + 0x04 | 0x02117320 | 不变 |
| **RAM Size** | **0xC0 + 0x08** | **0xCB80** | **0xCD8C** |
| **BSS Size** | **0xC0 + 0x0C** | **0x20** | **0x00**（或保留 0x20） |
| Static Init Start | 0xC0 + 0x10 | 0 | 不变 |
| Static Init End | 0xC0 + 0x14 | 0 | 不变 |
| File ID | 0xC0 + 0x18 | 6 | 不变 |
| Flags/Compressed | 0xC0 + 0x1C | 0x030075F8 | 不变（务必保持未压缩） |

### 3.4 共存 overlay（共享 RAM 0x02117320）[确证]

| Overlay | RAM 大小 | 说明 |
|---------|---------|------|
| 2 | 447360 (0x6D300) | Wi-Fi 通信 |
| 3 | 155104 (0x25D80) | |
| 4 | 222016 (0x36380) | |
| 5 | 31072 (0x7980) | |
| **6** | **52096 (0xCB80)** | **本报告对象** |
| 7 | 13600 (0x3520) | 最小共存 overlay |
| 8 | 28704 (0x7020) | |
| 9 | 130304 (0x1FD00) | |

**关键约束**：Overlay 2-9 **互斥加载**，扩容不冲突。

### 3.5 ROM 文件系统目录 [确证]

| 目录类型 ID | 目录名 | 文件扩展名 |
|-----------|--------|-----------|
| 0 | F_TBL | .NFTR 字体 / .BBQ 歌词表 |
| 1 | F_SCN | .BBQ 场景脚本 |
| 2 | F_BG | .NCGR/.NCLR/.NSCR 背景 |
| 3 | F_OBJ | .NCGR/.NCLR/.NANR/.NCER 2D 对象 |
| 4 | F_AGL | .AGL/.GLD 图片（配对） |
| 5 | F_AGLCHR | AGL 角色文件 |
| 6 | F_G3D | .NSBMD 等 3D 模型 |
| 7 | F_BGM | .S14/.SSS 音频 |
| 8 | F_VOICE | .IDX/.BIN 语音 |
| 9 | F_TEX | .GLD 纹理 |

### 3.6 13 项 UI sprite resource_id 完整映射 [确证]

13 个 resource_id（u16，LE）位于文件偏移 0xBF68 / RAM 0x02123288：

| index | resource_id | dir | file_id | 文件名 | 用途 |
|-------|-------------|-----|---------|--------|------|
| 0 | 0x41F5 | 4 | 501 | 0501_D_MEASURE_MNG.GLD | 测量管理器 |
| 1 | 0x41F7 | 4 | 503 | 0503_D_MEASURESPACE_MNG.GLD | 测量空间 |
| 2 | 0x41F9 | 4 | 505 | 0505_D_MEASURENOPUT_MNG.GLD | 测量未放置 |
| **3** | **0x41FB** | **4** | **507** | **0507_D_MEASURE_MOJI_MNG.GLD** | **左侧候选字文字** |
| 4 | 0x41FD | 4 | 509 | 0509_D_BATSU_MNG.GLD | 叉号 |
| 5 | 0x41FF | 4 | 511 | 0511_D_WAKU_MNG.GLD | 框架 |
| 6 | 0x4201 | 4 | 513 | 0513_D_STARS_MNG.GLD | 星星 |
| 7 | 0x4203 | 4 | 515 | 0515_D_BASE_MNG.GLD | 基础 |
| 8 | 0x4205 | 4 | 517 | 0517_D_ERABUKUN_MNG.GLD | 选择君 |
| 9 | 0x4207 | 4 | 519 | 0519_D_EPANEL_MNG.GLD | 面板 |
| **10** | **0x4209** | **4** | **521** | **0521_D_EPANEL_MOJI_MNG.GLD** | **下侧题目文字** |
| 11 | 0x420B | 4 | 523 | 0523_D_COUNTDOWN_MNG.GLD | 倒计时 |
| 12 | 0x420D | 4 | 525 | 0525_D_ATTENTION.GLD | 注意提示 |

> 文件名前缀是**十进制**序号（0501-0525），resource_id 低12位是**十六进制** file_id（0x1F5-0x20D）。

---

## 4. 补丁点清单

### 4.1 charmap 表重定位补丁（实验 3/4/6/7 通用）[确证]

| 补丁点 | 文件 | 偏移 | 大小 | 原值 | 实验 3 新值 | 用途 |
|--------|------|------|------|------|-----------|------|
| 1 | overlay_0006.bin | 0x8C0C | 4 字节 | 0x021232FC | 0x02123EC0 | 表起始指针 |
| 2 | overlay_0006.bin | 0x8C10 | 4 字节 | 0x021234E8 | 0x021240AC | 表结束指针 |
| 3 | y9.bin | 0xC8 | 4 字节 | 0xCB80 | 0xCD8C | RAM Size = 新文件大小 |
| 4 | y9.bin | 0xCC | 4 字节 | 0x20 | 0x00 | BSS Size（或保留 0x20） |

> **实验 6/7 中新表大小会变**：
> - 补丁点 2 新值 = 0x02123EC0 + 新表字节数
> - 补丁点 3 新值 = 0xCBA0 + 新表字节数

### 4.2 实验 4 额外补丁 [确证]

| 补丁点 | 文件 | 偏移 | 大小 | 原值 | 新值 | 用途 |
|--------|------|------|------|------|------|------|
| 5 | overlay_0006.bin | 0xCBBE | 2 字节 | 0x0001 | 0x0100 | 改 index 5「い」glyph_id 为 0x0100 验证位宽 |

### 4.3 实验 6/7 字形资源补丁

| 修改对象 | 修改内容 |
|---------|---------|
| 0506_D_MEASURE_MOJI_MNG.AGL | 扩展 frame_count，追加新 frame entry |
| 0507_D_MEASURE_MOJI_MNG.GLD | 扩展 footer_entry_count，追加新 footer + 像素数据 |
| 0520_D_EPANEL_MOJI_MNG.AGL | 同 0506 |
| 0521_D_EPANEL_MOJI_MNG.GLD | 同 0507 |
| 0022_LESVOICETABLEHEL.BBQ（或其他歌曲） | 替换 Section 7 文本池内容（中文编码为私有码位） |

### 4.4 不需修改的项（地址巧合）[确证]

| 位置 | 当前值 | 指向 | 不修改原因 |
|------|--------|------|----------|
| overlay_0006.bin 0xB9D0 | 0x021234E8 | dialog_seq 数据起始 | 0x021234E8 既是 charmap 表结束，也是 dialog_seq 数据起始（地址巧合，两系统读不同数据） |
| overlay_0006.bin 0x78E8 (PTR_DAT_0211ec08) | 0x02123288 | 13项 UI sprite 表 | 高段 glyph_id（0x41F5-0x420D），与字符映射独立 |

---

## 5. 函数地址索引

### 5.1 overlay_0006.bin 内关键函数（189 个，按子系统分组）[确证]

#### textctx 类族（文本上下文）

| 函数 | RAM 地址 | 文件偏移 | 说明 |
|------|---------|---------|------|
| textctx_init | 0x02117320 | 0x0000 | 初始化，分配 16×0x288 字节字符槽，**overlay 入口** |
| textctx_deinit | 0x0211739C | 0x007C | 析构 |
| textctx_state_update | 0x02117530 | 0x0210 | 状态机逐帧更新 |
| textctx_hit_check | 0x02118BAC | 0x388C | 命中判定：3 级区间，加分 5/4/3 |
| textctx_set_speed_level | 0x021181A0 | 0xE80 | 速度等级设置（0-5） |
| textctx_adjust_speed | 0x0211825C | 0xF3C | 速度调整，阈值 9/0x11/0x19 |

#### text_window 打字机

| 函数 | RAM 地址 | 文件偏移 | 说明 |
|------|---------|---------|------|
| text_window_ctor | 0x0211A170 | 0x2E50 | 构造 |
| text_window_begin_print | 0x0211A404 | 0x30E4 | 配置会话，字符数钳制 0x3C=60 |
| text_window_advance_char | 0x0211A630 | 0x3310 | 每 2 帧推进 1 字符 |
| text_window_apply_speed_change | 0x0211A750 | 0x3430 | 速度调整，钳制 0-0x18，SE 0x3A/0x3B |

#### anim_text_window 动画文本（填歌词游戏核心）

| 函数 | RAM 地址 | 文件偏移 | 说明 |
|------|---------|---------|------|
| anim_text_fx_create | 0x0211E280 | 0x6F60 | 创建特效，加载 **13 个**字形资源 |
| anim_text_fx_update | 0x0211F13C | 0x7E1C | **11 态状态机，2×12 字符格（填歌词题目和填空处）** |
| char_map_equal_range | 0x0211FF3C | 0x8C1C | charmap 等值区间查找（泛型模板） |
| char_map_upper_bound | 0x0212002C | 0x8D0C | charmap 上界查找 |
| char_map_lower_bound | 0x021200AC | 0x8D8C | charmap 下界查找 |
| glyph_entry_lower_bound | 0x0211EC50 | 0x7F30 | 13项表二分查找（8字节步长） |
| glyph_entry_sort | 0x0211ECB0 | 0x7F90 | 内省排序（快排+选择+三数取中） |
| shuffle_u32_array | 0x0211E18C | 0x6E6C | Fisher-Yates 洗牌 |

#### dialog_seq 对话序列

| 函数 | RAM 地址 | 文件偏移 | 说明 |
|------|---------|---------|------|
| dialog_seq_create | 0x02122108 | 0xADE8 | 创建 6 页文本序列 |
| dialog_seq_update | 0x02122670 | 0xB350 | 10 态状态机，含存档写回 +0x11488-0x11490 |
| dialog_seq_destroy | 0x02122D6C | 0xBA4C | 销毁 |

#### 其他子系统

| 函数 | RAM 地址 | 文件偏移 | 说明 |
|------|---------|---------|------|
| minigame_main_ctor | 0x0211BAD4 | 0x47B4 | 触摸小游戏构造 |
| minigame_update | 0x0211BE28 | 0x4B08 | 5 态状态机 |
| rank_judge_view_evaluate_rank | 0x0211AA20 | 0x3700 | 等级计算 0-4，4 个 u16 阈值 |
| glyph_trail_draw | 0x0211DD88 | 0x6068 | 5 点采样渐隐绘制 |
| resource_load_progress_update | 0x0211E038 | 0x6318 | 百分比→0-5 等级 |

### 5.2 ARM9.bin 内资源加载系统 [确证]

| 函数 | 地址 | 功能 |
|------|------|------|
| FUN_0206e664 | 0x0206e664 | 资源缓存管理器入口（被 10 处调用） |
| FUN_0206be58 | 0x0206be58 | 资源加载分支（AGL/GLD vs 普通，param_4==5 为 AGL/GLD） |
| FUN_0206e3f8 | 0x0206e3f8 | AGL/GLD 缓存查找（0x40 个 0x28 字节槽） |
| FUN_0206e448 | 0x0206e448 | AGL/GLD 资源创建（加载 id 和 id+1） |
| FUN_02072fc0 | 0x02072fc0 | 文件加载主函数（查找+大小+格式识别） |
| FUN_02065b5c | 0x02065b5c | 文件查找/加载（缓存或新读） |
| FUN_02065854 | 0x02065854 | resource_id 解析（dir_id + file_id） |
| FUN_02065dfc | 0x02065dfc | 目录条目查找（8 个 0x24 字节槽，state==3） |
| FUN_02066654 | 0x02066654 | 数据加载/复制（memcpy） |
| FUN_0205458c | 0x0205458c | 格式识别器（magic → type） |
| FUN_020547ac | 0x020547ac | 格式分发器（type → 解析函数） |
| FUN_02042660 | 0x02042660 | AGL dispatcher（type 0xC） |
| FUN_02042440 | 0x02042440 | AGL v3 parser |
| FUN_0204e41c | 0x0204e41c | GLD parser（type 0xD） |
| FUN_0205DD68 | 0x0205DD68 | BBQ 解析器（magic 校验 + Section 表遍历） |
| FUN_02080238 | 0x02080238 | BBQ 构造器 |
| FUN_0205DC08 | 0x0205DC08 | BBQ 上下文初始化 |

### 5.3 ARM9 关键全局变量 [确证]

| 全局变量 | 地址 | 值 | 含义 |
|---------|------|-----|------|
| DAT_020657b8 | 0x020657b8 | → 0x020CBDE4 | 指向目录数量字段 |
| DAT_020657bc | 0x020657bc | → 0x020CBE3C | 指向索引表（目录名指针数组） |
| DAT_020658a4 | 0x020658a4 | → 0x021005b4 | 资源管理器全局状态结构 |
| DAT_020658a8 | 0x020658a8 | 0x00000FFF | 文件 ID 掩码（低 12 位） |
| 0x020CBDE4 | — | 0x0000000A | 目录数量 = 10 |
| DAT_0005de2c | 0x0205DE2C | ".BBQ" | BBQ magic 常量 |

### 5.4 overlay_0006 触发场景 [确证/强推测]

| 触发源 | 场景/事件 ID | 分配大小 | 推测用途 | 置信度 |
|--------|-------------|---------|---------|--------|
| FUN_02077fc8 case 2 | 场景 2 | 508B | 轻量 textctx（如简单文本展示） | `[待验证]` |
| FUN_02077fc8 case 0x14 | 场景 20 | 484B | 轻量 textctx（另一入口） | `[待验证]` |
| FUN_02096cb4 | **事件 3100** | **16564B** | **完整填歌词游戏（所有子系统）** | `[强推测]` |

> **主测试路径**：事件 ID 3100（分配大小最大，最可能是完整填歌词游戏）。

---

## 6. 文件格式规范

### 6.1 AGL v3 头部结构（40 字节 = 0x28）[确证]

| 偏移 | 大小 | 字段 | 0506 值 | 0520 值 | 说明 |
|------|------|------|---------|---------|------|
| 0x00 | 4 | magic | `\0LGA` | `\0LGA` | 魔数 = 0x41474C00 LE |
| 0x04 | 2 | version | 3 | 3 | 版本 (1/2/3) |
| 0x06 | 2 | segment_count | 2 | 2 | 段数 (≠0 时 table8 不存在) |
| 0x08 | 4 | total_size | 0x10934 | 0x10930 | 逻辑总大小 (≠ 文件大小) |
| 0x0A | 1 | cell_count | 1 | 1 | cell 表条目数 (u8) |
| 0x0C | 2 | frame_count | 81 (0x51) | 81 (0x51) | 帧数 = GLD footer_entry_count |
| 0x0E | 2 | table9_count | 0 | 0 | Table 9 条目数 (24B/entry) |
| 0x10 | 4 | reserved | 0 | 0 | 保留 |
| 0x12 | 2 | table3_count | 0 | 0 | Table 3 条目数 (16B/entry) |
| 0x14 | 2 | table4_count | 1 | 1 | Table 4 条目数 (8B/entry, cell size) |
| 0x16 | 2 | table5_count | 1 | 1 | Table 5 条目数 (4B/entry) |
| 0x18 | 2 | table6_count | 4 | 3 | Table 6 条目数 (4B/entry, sub-size) |
| 0x1C | 2 | table11_count | 4 | 4 | Table 11 条目数 (1B/entry) |
| 0x20 | 2 | table10_count | 0 | 0 | Table 10 条目数 (8B/entry) |
| 0x22 | 2 | table12_count | 0 | 0 | Table 12 条目数 |
| 0x24 | 2 | table7_count | 0 | 0 | Table 7 条目数 (8B/entry) |
| 0x26 | 2 | field_26 | 0 | 0 | segment_count≠0 时=0；==0 时=table8_count |

**关键规则**：当 `segment_count != 0` 时，偏移 0x26 处的值强制解析为 0（table8 不存在）。

### 6.2 AGL 文件结构 [确证]

```
[0x28 字节 Header]
[cell_table: cell_count × 16B]        + align4
[frame_table: frame_count × 44B]      + align4
[table3: table3_count × 16B]          + align4   (通常 0)
[table4: table4_count × 8B]           + align4   (cell size)
[table5: table5_count × 4B]           + align4
[table6: table6_count × 4B]           + align4   (sub-size)
[table7: table7_count × 8B]           + align4
[table8: table8_count × 12B]          (NO align — 仅 segment_count==0 时存在)
[table9: table9_count × 24B]          + align4
[table10: table10_count × 8B]         + align4
[table11: table11_count × 1B]         + align4
[table12: table12_count × ?B]
```

> 每个表后有 4 字节对齐填充，**table8 除外**（无对齐，来自 FUN_02042440 反编译确认）。

### 6.3 AGL Frame Entry（44 字节 = 22 × u16）[确证]

| 偏移 | vals[] | 字段 | Frame 0 值 | 说明 |
|------|--------|------|-----------|------|
| 0x00 | [0] | cell_id | 0 | 引用的 cell 索引 |
| 0x02 | [1] | field_02 | 1 | (通常 = cell_id+1) |
| 0x04 | [2,3] | field_04 | 0 | u32 (0506=0, 0520=0/1) |
| 0x08 | [4] | field_08 | 0 | |
| 0x0A | [5] | field_0a | 2 | |
| 0x0C | [6] | field_0c | 0x7FFF | 常见哨兵值 |
| 0x0E | [7] | field_0e | 0x7FFF | |
| 0x10 | [8] | field_10 | 0x7FFF | |
| 0x12 | [9] | field_12 | 0x001F | |
| 0x14 | [10] | field_14 | 0 | |
| **0x16** | **[11]** | **glyph_id** | **0x1000** | **字形 ID = 0x1000 + frame_index** |
| 0x18 | [12] | field_18 | 0 | |
| 0x1A | [13] | field_1a | 0 | |
| 0x1C | [14] | field_1c | 0 | |
| 0x1E | [15] | field_1e | 0 | |
| 0x20 | [16] | field_20 | 0 | |
| 0x22 | [17] | field_22 | 0xFF00 | 标志位模式 |
| 0x24 | [18] | next_frame_id | 0 | = frame_index (顺序链) |
| 0x26 | [19] | field_26 | 1 | (= 1 + frame_index) |
| 0x28 | [20] | field_28 | 0 | |
| 0x2A | [21] | field_2a | 3 | |

**glyph_id 规律**：Frame 0 → 0x1000，Frame 80 → 0x1050（范围 0x1000-0x1050 = 4096-4176，共 81 个字形）

### 6.4 GLD 头部结构（32 字节）[确证]

| 偏移 | 大小 | 字段 | 0507 值 | 0521 值 | 说明 |
|------|------|------|---------|---------|------|
| 0x00 | 4 | magic | `\0DLG` | `\0DLG` | 魔数 = 0x474C4400 LE |
| 0x04 | 2 | data_04 | 2 | 2 | 必须为 2 |
| 0x06 | 2 | data_06 | 2 | 2 | 1=Type1(24B) / 2=Type2(28B) |
| 0x08 | 4 | total_size | 7420 | 16444 | 文件总大小 |
| 0x0C | 4 | data_0c | 5104 | 14016 | (= pixel_data_size in samples) |
| 0x10 | 4 | data_10 | 0 | 0 | 通常为 0 |
| 0x14 | 4 | pixel_data_size | 5104 (0x13F0) | 14016 (0x36C0) | 像素数据大小 |
| 0x18 | 4 | palette_data_size | 16 (0x10) | 128 (0x80) | 调色板数据大小 |
| 0x1C | 4 | footer_entry_count | 81 (0x51) | 81 (0x51) | Footer 条目数 |

### 6.5 GLD 文件结构 [确证]

```
[32 字节 Header]
[pixel_data_size 字节像素数据]
[palette_data_size 字节调色板数据]
[footer_entry_count × 28B (Type2) 或 24B (Type1) Footer Entries]
```

### 6.6 GLD Footer Entry（Type2 = 28 字节）[确证]

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0x00 | 4 | pixels_offset | 像素在 pixel_data 中的字节偏移 |
| 0x02 | 2 | palette_offset | 调色板在 palette_data 中的字节偏移 |
| 0x04 | 2 | sprite_format | 格式 ID (bit15=deleted) |
| 0x06 | 2 | crop_width | 裁剪宽度 |
| 0x08 | 2 | crop_height | 裁剪高度 |
| 0x0A | 2 | render_width_id | Type2: render_width = 8 << render_width_id |
| 0x0C | 2 | render_height_id | 渲染高度 ID |
| 0x0E | 2 | crop_x | 裁剪 X 偏移 |
| 0x10 | 2 | crop_y | 裁剪 Y 偏移 |
| 0x14 | 4 | data_14 | (仅 Type2) |
| 0x18 | 2 | join_x | (i16) |
| 0x1A | 2 | join_y | (i16) |

> Type1 (24B) 无 data_14 字段，其余相同。

### 6.7 Sprite Format [确证]

| Format | 位深 | 调色板色数 | 像素编码 | 透明机制 |
|--------|------|-----------|---------|---------|
| 1 | 8bpp | 32 | [color:5][alpha:3] | alpha 8 级 (3bit) |
| 2 | 2bpp | 4 | 纯索引 | 调色板 bit15 |
| 3 | 4bpp | 16 | 纯索引 | 调色板 bit15 |
| 4 | 8bpp | 256 | 纯索引 | 调色板 bit15 |
| 6 | 8bpp | 8 | [color:3][alpha:5] | alpha 32 级 (5bit) |

- bit15 (0x8000) = deleted，导出时跳过
- BGR555 调色板：bit15=1 不透明，bit15=0 透明
- 0507: 全部 81 个 sprite 为 Format 2 (2bpp, 4色)
- 0521: 74 个 Format 2 + 7 个 Format 6 (8bpp, 8色, alpha 5bit)

### 6.8 BBQ 头部结构（24 字节）[确证]

| 偏移 | 大小 | 字段 | 类型 | 说明 |
|------|------|------|------|------|
| 0x00 | 4 | magic | char[4] | `.BBQ` (0x2E 0x42 0x42 0x51) |
| 0x04 | 4 | version | char[4] | `1.00` (0x31 0x2E 0x30 0x30) |
| 0x08 | 4 | hash/checksum | u32 | 文件哈希，值域 0x4A5C7EB4-0x4A5C7EB6 |
| 0x0C | 4 | flag/version | u32 | 始终为 0x00000001 |
| 0x10 | 4 | header_size | u32 | Section表偏移 = 24 (0x18) |
| 0x14 | 2 | n_sections | u16 | Section数量 = 4 |
| 0x16 | 2 | padding | u16 | 填充0 |

### 6.9 BBQ Section 表条目（20 字节）[确证]

| 偏移 | 大小 | 字段 | 类型 | 说明 |
|------|------|------|------|------|
| 0x00 | 2 | sect_id | u16 | Section ID |
| 0x02 | 2 | padding | u16 | 填充0 |
| 0x04 | 4 | v0 | u32 | 数据偏移1 (相对section条目) |
| 0x08 | 4 | v1 | u32 | 数量/标志 |
| 0x0C | 4 | v2 | u32 | 数据偏移2 (相对section条目) |
| 0x10 | 4 | v3 | u32 | 数据大小 |

### 6.10 BBQ Section 用途 [确证/强推测]

| Section ID | 用途 | 置信度 |
|-----------|------|--------|
| 2 | 全局参数表 (28字节，含难度/节拍等) | `[强推测]` |
| 5 | 时序事件表 (16字节/条目，文本索引+时间) | `[确证]` |
| 6 | 空 Section (v3=0，预留) | `[确证]` |
| 7 | 歌词文本 (指针表+文本池) | `[确证]` |

### 6.11 charmap 条目结构（6 字节）[确证]

```c
// 6 字节/条目，需 2 字节对齐
// 注意：key 声明为 uint8_t[2] 而非 uint16_t，避免反编译器显示反字节序值
typedef struct {
    uint16_t glyph_id;    // +0: 字形 ID（LE），索引到 AGL/GLD sprite
    uint8_t  key[2];      // +2: 字符码（2字节，memcmp 直接比较，大端 SJIS）
    uint16_t reserved;    // +4: 保留字段（实测全为 0，新表可安全填 0）
} CharmapEntry;           // sizeof = 6
```

**字节序说明** `[确证]`：ARM 小端架构，但 SJIS 字符码以大端存储（高字节在前），
与字符串字节序列一致，便于 `memcmp(2)` 直接比较。
在 Ghidra 中声明结构体时，key 字段应使用 `uint8_t[2]` 而非 `uint16_t`。

### 6.12 13项运行时表条目结构（8 字节）[确证]

```c
typedef struct {
    uint16_t resource_id;      // +0: resource_id (LE)，作为查找 key
    uint8_t  pad[2];           // +2: 间隙
    uint32_t resource_handle;  // +4: func_0206e664 返回的资源句柄
} GlyphEntry;                  // sizeof = 8
```

### 6.13 charmap 表前 5 条与后 5 条 [确证]

```
idx        ram  glyph     key char  reserved
  0  0x21232FC 0x002E  0x815B    ー    0x0000
  1  0x2123302 0x002E  0x817C    −    0x0000
  2  0x2123308 0x0048  0x829F    ぁ    0x0000
  3  0x212330E 0x0000  0x82A0    あ    0x0000
  4  0x2123314 0x0049  0x82A1    ぃ    0x0000
...
 77  0x21234CA 0x0029  0x82EA    れ    0x0000
 78  0x21234D0 0x002A  0x82EB    ろ    0x0000
 79  0x21234D6 0x002B  0x82ED    わ    0x0000
 80  0x21234DC 0x002C  0x82F0    を    0x0000
 81  0x21234E2 0x002D  0x82F1    ん    0x0000
```

> 完整 82 条数据见 `charmap_original.csv`（权威数据）。

---

## 7. 实验状态

| 实验 | 状态 | 产物 | 成功标准 |
|------|------|------|---------|
| 1 全二进制扫描 | ✅ 完成 | 确认 2 处补丁点 + 1 处地址巧合 | 需额外补丁点则失败 |
| 2 charmap dump | ✅ 完成 | `charmap_original.csv` | 82 条升序校验 PASS |
| 3 原样搬迁 | 🟢 构建完成 | `build/exp3/` | **待实机验证**：游戏行为完全不变 |
| 4 glyph_id 位宽 | 🟢 构建完成 | `build/exp4/` | **待实机验证**：确认 8 位截断或 16 位 |
| 5 左侧候选字来源 | ✅ 完成 | — | 不需额外补丁点 |
| 6 单字端到端 | ⏳ 待做 | — | 新增"说"字正确显示 |
| 7 整首歌替换 | ⏳ 待做 | — | 整首歌正确显示中文 |

### 7.1 实验 3 关键参数 [确证]

| 参数 | 值 |
|------|-----|
| 原 overlay 文件大小 | 0xCB80 (52096 字节) |
| 新 overlay 文件大小 | 0xCD8C (52620 字节)，增量 524 字节 |
| BSS 填充位置 | 文件偏移 0xCB80-0xCB9F（32 字节零填充） |
| 新 charmap 表文件偏移 | 0xCBA0 |
| 新表 RAM 起始 | 0x02123EC0 |
| 新表 RAM 结束 | 0x021240AC |
| 补丁点 1（表起始指针） | 文件偏移 0x8C0C，值改为 0x02123EC0 |
| 补丁点 2（表结束指针） | 文件偏移 0x8C10，值改为 0x021240AC |
| y9 RAM Size 偏移 | 0xC8，值改为 0xCD8C |
| y9 BSS Size 偏移 | 0xCC，值改为 0x00 |

### 7.2 实验 4 关键参数 [确证]

| 参数 | 值 |
|------|-----|
| 修改条目 | index 5（い, key=0x82A2） |
| 原 glyph_id | 0x0001 |
| 新 glyph_id | 0x0100 |
| 修改位置 | 文件偏移 0xCBBE（新表内 index 5 的 glyph_id 字段） |

### 7.3 实验 4 预期结果分析 [确证]

实机观察「い」字（原 glyph_id=0x0001，已改为 0x0100）：

| 情况 | 表现 | 含义 | 后续行动 |
|------|------|------|---------|
| A | 显示「い」（原字）| glyph_id 是 16 位，0x0100 未被截断；但 0x0100 可能对应另一个已存在的字形 | ✅ 确认 16 位，可进入实验 6 |
| B | 显示「ぁ」（字形 0）| glyph_id 被截断为 8 位（0x0100 → 0x00） | ⚠️ 字符集必须压到 ≤ 255 |
| C | 空白/乱码 | glyph_id 是 16 位，但 0x0100 对应的字形不存在 | ✅ 确认 16 位，可进入实验 6 |

---

## 8. 常见问题

### Q1: CP932 为什么不行？

**A**: CP932 (Shift-JIS) 无法编码简体中文常用字，如「说」「这」「车」「门」「爱」「语」等会触发 `UnicodeEncodeError`。但本链路不需要合法 SJIS：

- ARM9 文本读取采用固定 2 字节步长（`pcVar2 += 2`），**不做 lead byte 判定** `[确证]`
- charmap 表查找用 `memcmp(entry+2, cur2bytes, 2)` 二分查找，key 只是 2 字节不透明标识 `[确证]`
- 终止判断是 `*p == 0`（单字节 0），所以 lead/trail 都不能为 0x00

正确做法是定义游戏私有双字节码（0xF040-0xF9FC，1880 码位）。同时 `utils/bbq_format.py` 的 cp932 解码必须改为**按码表解码**（先查自定义 charmap，再 fallback）。`[确证]`

### Q2: glyph_id 是 8 位还是 16 位？

**A**: **16 位**。`[确证]`

反汇编证据（`anim_text_fx_update` 0x0211f13c 的 0x0211f424 处）：
```asm
0211f424: ldrshne r3,[r1,#0x0]   ; LDRSH = Load Register Signed Halfword (16位)
```

- 不是 LDRB（8位），而是 LDRSH（16位有符号半字）
- glyph_id 可达 0x7FFF（有符号）或 0xFFFF（无符号）
- 现有 glyph_id 最大值 0x0050 (80)，新增中文字形 ID 从 **0x51** 开始分配
- 实验 4 待实机确认下游 AGL/GLD 解析是否真的接受 16 位

### Q3: 左侧候选字需要额外补丁吗？

**A**: **不需要**。`[确证]`

13项运行时表（PTR_DAT_0211ec08 → 0x02123288）是 **UI sprite 框架表**，不是字符映射表：
- 13 个 key 值范围：0x41F5-0x420D（高段 UI glyph_id，全奇数步长 2）
- 与字符映射（0x00-0x50）完全独立

`anim_text_fx_update` case 1 中的候选字队列**全部来自 char_map_equal_range**（同一个 charmap 表 PTR_DAT_0211ff2c/ff30 → 0x021232FC）。

只需 patch 0x8C0C/0x8C10 两处指针重定位 charmap 表，即可同时覆盖：
- 下侧 EPANEL 题目字（2×12 面板）
- 左侧 MEASURE 候选字队列

### Q4: BBQ 时序会破坏吗？

**A**: **不会**。`[确证]`

证据（0022 数据）：
- 文本数 32，Section 5 条目数 56
- 时序值（f1/f2/f3）不与对应文本的字符数成比例
- 文本"いま　めざしてく"（8字符）对应 f1=58
- 文本"わたしだけのすとーりー"（11字符）对应 f1=114
- 字符数比 8:11 ≈ 0.73，但 f1 比 58:114 ≈ 0.51，不匹配

时序值更可能是**帧数/时间戳**，与文本内容无关。替换歌词文本为不同长度的中文，**不会**影响时序和判定。

### Q5: AGL/GLD 文件汉化时需要修改 AGL 吗？

**A**: **视情况**。`[确证]`

- **若中文字形数 ≤ 81**：AGL frame_count 不变，AGL 原样保留，只替换 GLD 像素数据
- **若需新增字形**：需同时扩展 AGL frame_count 和 GLD footer_entry_count，并在 AGL frame 表末尾追加新 frame entry（修改 glyph_id = 0x1000 + new_frame_index）

### Q6: overlay_0006 扩容会与其他 overlay 冲突吗？

**A**: **不会**。`[确证]`

- Overlay 2-9 **互斥加载**（同一 RAM 区域 0x02117320，同一时刻只有一个 overlay）
- NDS 加载器按各自 overlay 的 RAM Size 分配，互不干扰
- 扩容只需修改 y9.bin 中 overlay_0006 的 RAM Size 字段
- 但需注意 ROM 文件系统可能需要重新分配簇

### Q7: 实验 3 失败的可能原因？

**A**: 见下表：

| 情况 | 可能原因 | 排查方法 |
|------|---------|---------|
| 游戏启动即崩溃 | FAT 表未同步更新 | 重新解包打包后的 ROM，校验 overlay SHA256 |
| 文字消失/乱码 | 指针修改不正确 | Python 读取 0x8C0C/0x8C10 校验指针值 |
| 进入填歌词后崩溃 | BSS Size 改 0 导致变量未清零 | 把 BSS Size 改回 0x20 |
| 上述均无效 | heap/arena 起点受 overlay 末端动态计算 | 用模拟器调试检查 RAM 0x02123EC0 区域 |

### Q8: 私有码位与 BBQ 控制码冲突吗？

**A**: **不冲突**。`[确证]`

通过 `analyze_control_codes()` 扫描所有 10 首歌的全部文本：
- 无 LF (0x0A)、CR (0x0D)、TAB (0x09)
- 无其他控制字符 (< 0x20)
- 无非 SJIS 字节
- 文本完全由 SJIS 双字节字符组成，以 0x00 终止

BBQ 文本**无任何控制码**，私有码位 0xF040-0xF9FC 不会冲突。但仍需实验 6 单字端到端验证确认。

---

## 9. 文档索引

### 9.1 核心报告文件

| 文档 | 内容摘要 | 字数 |
|------|---------|------|
| `report_v3_charmap_relocation.md` | **charmap 表重定位方案权威文档**（V3 修订版）。包含评审意见响应、地址常量定位、charmap 完整 dump、重定位方案、glyph_id 位宽确认、13项运行时表分析、overlay_0006 加载触发场景、完整汉化路径总结 | ~33KB |
| `report_agl_gld_format.md` | **AGL/GLD 图像封包格式逆向分析报告**。包含 AGL v3 头部结构、12 张表排列、Frame Entry 字段、GLD 头部、Footer Entry、Sprite Format、AGL↔GLD 映射关系、字节一致重打包验证 | ~15KB |
| `report_bbq_format.md` | **BBQ 歌词文件格式逆向分析报告**。包含头部结构、Section 表、Section 7 文本存储、Section 5 时序结构、10 首歌一致性、控制码分析、汉化影响分析 | ~20KB |
| `report_bbq_question_answer.md` | **BBQ 题目-答案-候选字映射关系逆向分析报告**（V4 新增）。包含 Section 5 事件分类（LYRIC_DISPLAY/CANDIDATE_SETUP/CONTROL）、两种子模式、10 首歌跨曲对比、汉化影响分析 | ~22KB |
| `report_agl_gld_pixel_layout.md` | **AGL/GLD 像素数据编排方式逆向分析报告**（V4 新增）。包含 LINEAR vs tile 验证、crop/render 关系、Format 6 位排列、调色板格式、压缩状态、字形制作指导 | ~25KB |
| `report_resource_loading.md` | **ARM9 资源加载系统完整分析**。包含完整调用链、目录索引表、resource_id 解析、AGL/GLD 配对加载、格式识别器、overlay_0006 加载点分析（事件 ID 3100） | ~25KB |
| `report_overlay_0006_v2.md` | **overlay_0006 详细逆向分析（v2 修订版）**。包含可复现性信息、模块概览、内存布局与补丁空间、字符编码链路专题、文本容量约束总表、结构体布局、189 函数表 | ~61KB |
| `report_overlay_0006_minigame.md` | overlay_0006 填歌词游戏专题（v1，已被 v2 取代） | — |
| `report_overlay_0002_wifi.md` | ARM9 Wi-Fi overlay（overlay_0002）分析 | — |
| `report_ARM9.md` | ARM9 主程序（4609 函数）分析 | — |
| `docs/realdevice_validation_guide.md` | **实机验证指导方案**（自包含主入口）。包含环境准备、文件清单、实验 3/4/6/7 完整步骤、故障排查指南 | ~51KB |

### 9.2 数据文件

| 文件 | 内容 |
|------|------|
| `charmap_original.csv` | **原 82 条 charmap 完整 dump（权威数据）**。字段：index, file_off, ram, glyph_id, glyph_id_dec, key_hex, key_bytes, char, reserved |
| `1_Extracted/y9.bin` | overlay 表（含 overlay_0006 条目） |
| `1_Extracted/ARM9/overlay_0006.bin` | 原 overlay_0006（52096 字节） |
| `1_Extracted/AGL/0506-0521_*.AGL/GLD` | 4 个样本 AGL/GLD 文件 |
| `1_Extracted/TBL/0022_LESVOICETABLEHEL.BBQ` | 歌词文件样本 |

### 9.3 工具脚本

| 脚本 | 用途 |
|------|------|
| `exp1_scan_charmap_refs.py` | 实验1：全二进制扫描 charmap 引用 |
| `exp2_dump_charmap.py` | 实验2：dump charmap 表到 CSV |
| `exp3_relocate_charmap_nochange.py` | 实验3：原样搬迁重定位脚本 |
| `exp4_glyph_id_bitwidth.py` | 实验4：glyph_id 位宽验证脚本 |
| `exp5_bbq_question_answer_analysis.py` | 实验5：BBQ 题目-答案-候选字映射分析（V4 新增） |
| `exp6_agl_gld_pixel_layout.py` | 实验6：AGL/GLD 像素编排验证（V4 新增） |
| `analyze_agl_gld.py` | AGL/GLD 样本分析脚本 |
| `utils/gld_format.py` | GLD 图像封包格式解析工具 |
| `utils/bbq_format.py` | BBQ 歌词文件格式解析工具（需改解码逻辑） |
| `utils/binary_io.py` | 二进制读写工具 |
| `utils/lz10.py` / `utils/blz.py` | LZ10 / BLZ 压缩算法 |
| `build/bbq_dump/parse_bbq_full.py` | BBQ 完整解析工具 |
| `build/bbq_dump/analyze_sections.py` | BBQ Section 深度分析 |

### 9.4 构建产物

| 路径 | 内容 |
|------|------|
| `build/exp3/overlay_0006_relocated.bin` | 实验3 产物（52620 字节） |
| `build/exp3/y9_relocated.bin` | 实验3 y9 产物（320 字节） |
| `build/exp3/verify_report.txt` | 实验3 构建校验报告 |
| `build/exp4/overlay_0006_glyphid_test.bin` | 实验4 产物（52620 字节） |
| `build/exp4/y9_relocated.bin` | 实验4 y9 产物（复用实验3） |
| `build/exp4/verify_report.txt` | 实验4 构建校验报告 |
| `build/agl_gld_dump/*.csv` | AGL/GLD 样本解析 CSV（0506/0507/0520/0521） |
| `build/agl_gld_dump/pixel_layout_analysis.csv` | AGL/GLD 像素编排分析（V4 新增） |
| `build/agl_gld_dump/png_*/sprite_*.png` | 像素编排验证 PNG（linear vs tile 对比，V4 新增） |
| `build/bbq_dump/*.csv` | BBQ 样本解析 CSV（0022） |
| `build/bbq_dump/question_answer_map.csv` | BBQ 题目-答案-候选字映射（V4 新增） |
| `build/bbq_dump/sec5_event_trace.csv` | BBQ Section 5 事件轨迹（V4 新增，600 条） |
| `build/bbq_dump/text_usage_stats.csv` | BBQ 文本使用统计（V4 新增） |

---

## 10. 变更历史

### 10.1 关键发现时间线

| 日期 | 事件 | 置信度变化 |
|------|------|-----------|
| 2026-07-25 | AGL/GLD glyph_id 偏移修正：从 0x14/0x22 修正为 **0x16 (vals[11])**，值 = `0x1000 + frame_index` | `[确证]` (修正) |
| 2026-07-25 | 确认 AGL/GLD 文件实际使用 `\0LGA`/`\0DLG` 头（type 0xC/0xD），会被 ARM9 解析；非 "AGL\0"/"GLD\0"（type 0） | `[确证]` (修正初稿错误) |
| 2026-07-25 | 实验 5 完成：确认左侧候选字与下侧题目字共用同一 charmap 表，不需额外补丁点 | `[确证]` |
| 2026-07-25 | 实验 1 完成：全二进制扫描确认仅 2 处补丁点（0x8C0C/0x8C10），第 3 处 0xB9D0 是地址巧合 | `[确证]` |
| 2026-07-25 | 实验 2 完成：dump 全 82 条 charmap 到 CSV，修正 v3 附录 A 错误（第13条是「が」非「き」，第77条是「れ」非「む」） | `[确证]` |
| 2026-07-25 | glyph_id 位宽确认：LDRSH 指令（16位），非 LDRB（8位） | `[确证]` |
| 2026-07-25 | BBQ 格式逆向完成：确认文本长度与时序无绑定关系，无控制码，82 个唯一字符 | `[确证]` |
| 2026-07-25 | AGL/GLD 格式逆向完成：4 个样本字节一致重打包 PASS | `[确证]` |
| 2026-07-25 | overlay_0006 加载点确认：事件 ID 3100 触发完整填歌词游戏（16564 B） | `[强推测]` |
| 2026-07-25 | 两条字形渲染路径确认：路径 A（AGL/GLD）+ 路径 B（LC12.nrtf），汉化策略独立 | `[确证]` |
| 2026-07-25 | V3 charmap 重定位方案修订：删除错误附录 A、改用私有码位、修正 overlay 扩容细节、降级 P1/P2 状态 | `[确证]` |
| 2026-07-25 | V4 BBQ 题目-答案-候选字映射机制完成：Section 5 事件 100% 分类（LYRIC_DISPLAY/CANDIDATE_SETUP/CONTROL），发现主模式使用 text[4/5/6] gibberish 候选池，次模式与歌词高亮相关 | `[确证]` |
| 2026-07-25 | V4 AGL/GLD 像素编排完成：确认 LINEAR 行主序排列（非 tile）、Format 6 是低 3 位 color + 高 5 位 alpha、无压缩、utils 工具验证正确 | `[确证]` |

### 10.2 报告版本演进

| 版本 | 主要修正 |
|------|---------|
| overlay_0006 v1 → v2 | 删除假地址、确认 thunk 数 4 个、纠正类层次为推测、新增字符编码链路/容量约束/结构体定义 |
| v2 → v2.1 | 确认两条字形渲染路径、识别 2×12 字符格即填歌词游戏、补充歌词文件清单、修订汉化方案 |
| charmap 报告 V3 | 删除错误附录 A、改用私有码位（0xF040-0xF9FC）、修正表规模（282-532 条非 2100）、写死 BSS 处理、补充 5 项实验结果 |
| BBQ 报告 V4 | 新增题目-答案-候选字映射机制（Section 5 事件分类、两种子模式、跨歌曲一致性） |
| AGL/GLD 报告 V4 | 新增像素编排（LINEAR 行主序）、Format 6 位排列（低3位color+高5位alpha）、无压缩确认 |

### 10.3 待验证项汇总

| 项目 | 置信度 | 验证方式 |
|------|--------|---------|
| 实验 3 重定位机制 | 待实机验证 | 实机跑实验 3 |
| 实验 4 glyph_id 位宽 | 待实机验证 | 实机跑实验 4 |
| 事件 ID 3100 = 完整填歌词游戏 | `[强推测]` | 实机触发验证 |
| Section 2 u32[6] 含义（BPM/难度） | `[待验证]` | 断点调试 |
| Section 5 f1/f2/f3 时间单位 | `[待验证]` | 断点调试（疑为帧数 60fps） |
| CANDIDATE_SETUP 主模式 f3 含义（hi=3, lo=2） | `[待验证]` | 反编译上层调用或断点 |
| field_8 校验值算法 | `[待验证]` | 修改文件后是否需更新 |
| AGL cell 表条目数上限 | `[待验证]` | 扩展测试 |
| 纹理最大边长 / VRAM 预算 | `[待验证]` | 扩展测试 |
| ARM9 heap/arena 起点是否受 overlay 末端影响 | `[待验证]` | 实机调试 |
| 实验 6b 单字端到端（含候选字池修改） | `[待验证]` | 实机跑实验 6 + 验证候选字路径 |
| 0521 部分sprite actual_height 公式 | `[待验证]` | 不影响汉化（crop 区域 ≤ render 范围即可） |

---

## 附录 A：置信度标注说明

| 标注 | 含义 | 依据类型 |
|------|------|---------|
| `[确证]` | 反编译/实测确认 | Ghidra 反编译、内存读取、字节级验证、脚本扫描 |
| `[强推测]` | 数据规律推导 | 多个数据点规律性、命名约定、调用关系 |
| `[待验证]` | 需实机验证 | 需 NDS 实机或高精度模拟器断点调试 |

## 附录 B：关键 magic 常量表 [确证]

| Magic (小端 uint32) | 字符串 | Type | 处理函数 | 含义 |
|---------------------|--------|------|---------|------|
| 0x41474C00 | `\0LGA` | 0xC | FUN_02042660 | AGL（角色/精灵解析） |
| 0x474C4400 | `\0DLG` | 0xD | FUN_0204e41c | GLD（复杂格式解析） |
| 0x2E424251 | `.BBQ` | 0 | 不解析（游戏代码自处理） | BBQ 封包 |
| 0x4D504D56 | `VMPM` | 0x11 | func_0x0213ca70 (overlay) | MVMP |
| 0x30444D42 | `BMD0` | 0xE | — | BMD0 |
| 0x4E454E52 | `NENR` | 0x2 | FUN_0202e1ac | Nitro Engine Resource |
| 0x4E434752 | `NCGR` | 0x5 | thunk_FUN_0202ded4 | Nitro Character Graphic |
| 0x4E465452 | `NFTR` | 0x9 | FUN_02032d24 | Nitro Font Resource |

## 附录 C：技术问答速查（10 题）

详见 `report_agl_gld_format.md` §4，简表如下：

| # | 问题 | 答案 |
|---|------|------|
| a | AGL/GLD 的魔数分别是什么？ | AGL: `\0LGA` (0x41474C00 LE)；GLD: `\0DLG` (0x474C4400 LE) |
| b | AGL v3 头部多大？各表如何排列？ | 头部 40 字节 (0x28)；12 张表按固定顺序排列，每张表后 align4 填充（table8 例外） |
| c | glyph_id 在 Frame Entry 的哪个偏移？ | 偏移 **0x16** (vals[11])，u16，值 = `0x1000 + frame_index` |
| d | AGL 和 GLD 如何对应？ | 文件编号相差 1；frame_count == footer_entry_count，按索引 1:1 映射 |
| e | Sprite Format 2 和 6 有什么区别？ | Format 2: 2bpp/4色/调色板 bit15 透明；Format 6: 8bpp/8色/alpha 5bit |
| f | 调色板如何组织？ | BGR555 连续存放，每 2 字节一颜色；bit15=1 不透明 |
| g | 像素数据如何对齐？ | 所有 pixels_offset 对齐到 16 字节边界 |
| h | 字节一致重打包是否验证通过？ | 全部 4 个样本 PASS ✓ |
| i | 汉化需要修改 AGL 吗？ | 字形数 ≤ 81：不改；新增字形需扩展 frame_count |
| j | 汉化字形替换的完整流程？ | 解析→导出 PNG→制作中文字形→注入→写回（AGL 无需修改） |

---

**Wiki 结束**

> 本 Wiki 基于已完成的逆向分析结论编写，所有结论均有反编译或实证依据。
> 如发现 Wiki 与实际行为不符，请优先信任实机表现，并反馈以更新逆向分析报告。
>
> 主入口文档：`docs/realdevice_validation_guide.md`
> 项目规范：`AGENTS.md`
