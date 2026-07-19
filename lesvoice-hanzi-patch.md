# Vocal 课程填词小游戏汉化技术 Wiki

## 一、系统概述

Vocal 课程（Scene 8）是拼歌词小游戏：从选择区拖文字块填格子，拼出正确歌词。汉化目标是让游戏内显示中文字符。

**完整链路**：

```
SCN 0100_AIH_LES_PLAY.BBQ (课程流程脚本)
  └─> TBL 0022-0031 LESVOICETABLE*.BBQ (10 首歌题库: 文字块字符串)
        └─> overlay_0006.bin @0xBFF0 (SJIS → sprite_id 映射表, 原 81 条)
              └─> AGL 0521_D_EPANEL_MOJI_MNG.GLD (文字精灵图, 原 81 个)
```

改任何一环都必须保证三环一致：**BBQ 里的字符 → overlay 有该 SJIS 的映射 → GLD 有对应 sprite_id 的图**。

## 二、数据结构

### 2.1 LESVOICETABLE*.BBQ（题库）

以 0022 (HELLO!!) 为例，1,692 字节，4 个节区：

| 类型 | 条目 | 大小 | 说明 |
|------|------|------|------|
| Type 2 | 1 | 28 B | 头信息 |
| Type 5 | 1 | 896 B | 歌词数据表，112 条 × 8 B（4 个 uint16: v0 字符串索引/时间, v1-v3 参数） |
| Type 6 | 1 | 0 B | 空 |
| Type 7 | 32 | 500 B | 字符串表（SJIS/CP932），歌词文字块 + 干扰项 + 控制序列 |

每 8 条 = 1 个歌词块（2 行歌词 + 时间条 + 4 条控制序列）。**替换文本只能动 Type 7 节区**，Type 5 是二进制索引/时间，误改即坏。

### 2.2 overlay_0006.bin 映射表

- 位置：文件偏移 `0x00BFF0`（RAM `0x02123310`，overlay base `0x02117320`）
- 条目：6 字节 = `[SJIS(2B, 大端)] [padding(2B)=0] [sprite_id(2B, LE)]`
- 原始 81 条。**关键未知项：代码里遍历上限 81 是否硬编码**（见风险 R1）。

### 2.3 0521_D_EPANEL_MOJI_MNG.GLD（精灵图）

- Header 32 B：magic `\x00DLG`，偏移 8=total_size, 20=pixel_size, 24=palette_size, 28=footer_count
- 布局：`[32B header][pixel_data][palette_data][footer × 28B]`
- 像素：2bit/像素（4 色），render_width 32 对齐
- footer 条目 28 B：`<IHHHHHHHHIhh>` = pixels_offset, palette_offset, sprite_format(=2), crop_w, crop_h, render_w_id, render_h_id, crop_x, crop_y, data_14, join_x, join_y
- 原始精灵 palette_offset ∈ {0x0..0x70}，新精灵沿用 0x70 合法

## 三、现有补丁与审核结论

产物目录 `02_my_analysis/chinese_patch_192/`（81→192 精灵，+111 中文）。审核脚本 `01_my_tools/audit_192_patch.py`。

**审核结果：13 ERROR / 9 WARN，不可实机。** 核心问题：

| # | 问题 | 根因 | 后果 |
|---|------|------|------|
| P1 | 9 个高频字（愛天歌今夢花光日明）无精灵/映射，约 139 处 | `CHINESE_CHARS` 长 143 被 `[:111]` 静默截断，截掉的恰是高频字 | 显示坏字/越界 |
| P2 | `歷`(U+6B77) 不在 CP932 | 未做编码双向验证 | overlay 槽 106 空洞 + 5 个 BBQ 残留 7 处 `ぴ` + GLD 图与映射矛盾 |
| P3 | overlay 中段插入 666 B，后续内容整体位移 | `expand_overlay_map.py` 在 0xBFF0 处撑表 | 绝对地址引用失效，**极可能死机**；且未改代码里的表上限 |
| P4 | BBQ 全文件滑窗替换 | 未限定 Type 7 节区 | 可能污染 Type 5 数据区；计数 1258 vs 实测 1285 对不上 |
| P5 | 字符表 12 个重复，浪费精灵槽 | 未去重 | 有效字符仅 99/111 |
| P6 | `ぺ→台` 把节拍标记 `ぺぺぺ` 变成 `台台台` | 全局替换无白名单 | 玩法语义破坏 |

## 四、修复方向（详见 dev-plan-lesvoice.md）

1. **SSOT 字符表**：以 `KANA_TO_CHINESE` 50 个值为必选集，去重、CP932 双向验证（`歷→歴`），一份定义派生 GLD/overlay/BBQ 三处。
2. **overlay 改"追加+改指针"**：新表放末尾/空闲区，逆向定位代码中表基址与上限 81 并打代码补丁；确认 y9.bin 是否需同步。
3. **BBQ 按节区解析替换**：只动 Type 7。
4. **审核门禁**：`audit_192_patch.py` 0 ERROR 才进模拟器。

## 五、风险登记

| ID | 风险 | 状态 | 验证方式 |
|----|------|------|----------|
| R1 | 映射表遍历上限 81 硬编码在代码中 | 未验证 | ARM 反汇编定位 `0x02123310` 引用与循环常量 |
| R2 | overlay 0xBFF0 之后存在被绝对地址引用的代码/数据 | 未验证 | 反汇编扫描 `0x02123310+` 地址引用 |
| R3 | y9.bin 记录 overlay 大小/压缩标志，改尺寸需同步 | 未验证 | 解析 y9.bin overlay 表 |
| R4 | 新精灵 crop_h=16 与原 18 不一致、join_y 未沿用 | 已确认 | 显示对比截图 |

## 六、工具索引

| 脚本 | 职责 | 状态 |
|------|------|------|
| `expand_gld_to_192.py` | GLD 扩容 + 中文精灵渲染 | 结构 OK，字符表需修 |
| `expand_overlay_map.py` | overlay 映射表扩容 | **方案需推翻**（P3） |
| `patch_all_lesvoice.py` | BBQ 批量替换 | 需改节区限定（P4） |
| `make_final_patch.py` | 打包 + README | 可用 |
| `audit_192_patch.py` | 独立审核校验 | 可用，作为门禁 |
