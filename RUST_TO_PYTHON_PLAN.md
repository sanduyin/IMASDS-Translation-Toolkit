# Rust → Python 借鉴优化工作计划表

> 基于与 `faraplay/dearlystars_tool` (Rust, v0.5.2) 的横向对比，提取可借鉴的 Rust 工程实践与算法实现，转化为 Python 项目的具体优化任务。
>
> 生成日期：2026-07-16

---

## 一、核心差异与不足速览

| 对比维度 | faraplay (Rust) | 当前项目 (Python) | 严重程度 | 行动建议 |
|----------|----------------|-------------------|----------|----------|
| **ROM 构建内核** | 自研 `ndstool` crate，完整实现 NDS/DSi Header、FNT、FAT、ARM9/ARM7、Overlay、TWL 扩展、Digest/HMAC | 依赖 `ndspy` + 手工 DSi 嫁接 + 外部 exe 调用 | **致命** | 用 Python 重写 ndstool 核心 |
| **BLZ 压缩/解压** | 自研 `blz.rs`，v0.4.1 大幅提升精度，自动处理 ARM9/Overlay | 仅 `ndspy` 解压；无压缩能力；Stage 5 不压缩 Overlay | **严重** | 移植 `blz.rs` 为 `blz.py` |
| **BIN/IDX 封包** | `ez.rs` + `lz10.rs`：智能排除列表、严格对齐、高效 LZ10 | `stage1_unpack.py` 手工解析；`stage5_build_rom.py` 封包较初级 | **严重** | 重构 EZ 引擎 |
| **BBQ 解析** | `binrw` 声明式结构体解析，通用 Section 处理，支持 YAML 互转 | `bbq_format.py` 硬编码偏移与 Section 顺序，仅处理 Section 5/7 | **严重** | 引入声明式解析框架 |
| **邮件处理** | v0.5.2 新增：30 行/32 字节硬限制，CSV 输入输出，字符串池去重 | Excel 条件格式、字节公式、换行编辑，功能更完善 | **领先** | 保持优势，兼容 CSV |
| **AGL/GLD 导入导出** | `gld.rs`：支持 Sprite Format 1/2/3、PNG 透明、自动调色板匹配、注入预览 | 仅基础 GLD → BMP 8bpp；无 Sprite Format 2/3；无透明；BMP 调色板易失真 | **严重** | 扩展 GLD 支持，升级 PNG |
| **DSi 增强** | 原生支持 arm9i/arm7i 解密、digest sector、6 组 HMAC-SHA1、modcrypt AES-CTR | `stage5_build_rom.py` 手工嫁接 TWL 数据、CRC16 修复，功能可用但非自研 | 中等 | 完善 DSi 自研实现 |
| **ARM9/Overlay 注入** | `arm9overlay.rs`：CSV 输入、按文件分桶、先清空再写入、SHIFT_JIS 编码 | Excel 输入、原地替换、自定义映射编码、严格长度校验 | 中等 | 兼容 CSV 格式，优化写入策略 |
| **翻译表格式** | CSV（轻量、diff 友好、版本控制友好） | Excel（条件格式丰富、对非技术人员友好） | 中等 | 双格式并行支持 |
| **字库构建** | **无** | OpenCC + NFTR 动态字库、VWF、1bpp 渲染、CT2 码表 | **独有优势** | 保留并增强 |
| **背景图处理** | **无** | NCGR/NCLR/NSCR 完整导出/回写 | **独有优势** | 保留并增强 |
| **交互界面** | 纯 CLI | CLI + 交互式菜单 + 一键流水线 | **独有优势** | 保留 |

---

## 二、优化工作计划表

### P0 — 消除外部依赖（核心架构升级）

| 编号 | 模块 | 任务 | 参考 Rust 源码 | 预期产出 | 验收标准 |
|------|------|------|----------------|----------|----------|
| P0-1 | `ndstool` | 用 Python 实现 NDS Header 解析与生成（0x200 字节，含 icon/title/ARM9/ARM7/FNT/FAT 偏移） | `ndstool/src/header.rs` | `src/ndstool/header.py` | 解析/生成的 Header 与 faraplay 逐字节一致 |
| P0-2 | `ndstool` | 实现 FNT（文件名表）与 FAT（文件分配表）的完整解析与重建 | `ndstool/src/fnt.rs`, `fat.rs` | `src/ndstool/fnt_fat.py` | 支持递归目录结构，重建后文件偏移正确 |
| P0-3 | `blz` | 移植 BLZ (Backward LZ10) 压缩/解压算法，精度对齐 v0.4.1 | `ndstool/src/blz.rs` | `src/utils/blz.py` | 对同一 ARM9/Overlay，压缩率 ≥ faraplay，解压后逐字节一致 |
| P0-4 | `ndstool` | 实现 ARM9/Overlay 自动解压/压缩，更新 ROM 中多处大小字段 | `ndstool/src/rom.rs` | `src/ndstool/rom_builder.py` | 重建的 ROM 可启动，Overlay 大小字段正确 |
| P0-5 | `ndstool` | 实现 DSi 增强：arm9i/arm7i 解密、digest 表重建、HMAC-SHA1、modcrypt AES-CTR | `ndstool/src/digest.rs`, `modcrypt.rs`, `key_encryption.rs` | `src/ndstool/dsi_builder.py` | DSi ROM 重建后在实机/模拟器正常启动 |
| P0-6 | `ez` | 重构 BIN/IDX 封包引擎：解析 EZT 头部、entry 表、name table；EZP 数据对齐与填充 | `dearlystars/src/ez.rs` | `src/packez/ezt_parser.py`, `ezp_pack.py` | 解包/封包与 faraplay 逐字节一致 |
| P0-7 | `lz10` | 升级 LZ10 压缩：参考 `lz10.rs` 的反向搜索、提前 break、严格校验 | `dearlystars/src/lz10.rs` | `src/utils/lz10.py` | 压缩率与 faraplay 持平，build-bin 耗时可接受 |

### P1 — 数据格式与图像升级

| 编号 | 模块 | 任务 | 参考 Rust 源码 | 预期产出 | 验收标准 |
|------|------|------|----------------|----------|----------|
| P1-1 | `gld` | 支持 PNG 导入导出（替代 BMP），保留索引色并支持透明通道 | `dearlystars/src/gld.rs` (png crate) | `src/stage2_export_images.py` 升级 | PNG 导出/回写后游戏内显示正常 |
| P1-2 | `gld` | 扩展 Sprite Format 2 和 3 的解析与重建 | `dearlystars/src/gld.rs` | `src/utils/gld_format.py` | Format 2/3 的 GLD 不崩溃，注入正确 |
| P1-3 | `gld` | 实现调色板匹配预览：注入前生成调色板适配后的预览图 | `dearlystars/src/main.rs` (`-p injected_preview`) | `src/utils/palette_preview.py` | 预览图与游戏内实际显示色差 < 5% |
| P1-4 | `csv` | 支持 CSV 作为翻译表输入输出，兼容 faraplay 的列格式；**必须附带旧 xlsx 迁移脚本** | `dearlystars/src/csv.rs` | `src/io/csv_handler.py` + `scripts/migrate_xlsx_to_csv.py` | `python main.py export --format=csv` 可用；旧 xlsx 可一键迁移 |
| P1-5 | `csv` | 实现完整 RFC 4180 CSV 解析：支持转义引号、换行字段 | `dearlystars/src/csv.rs` | `src/io/csv_handler.py` | 复杂文本（含逗号/换行）正确读写 |
| P1-6 | `yaml` | BBQ ↔ YAML 互转：完整保留 Type2/3/5/6/7 数据，支持人工编辑 | `dearlystars/src/bbq.rs` (YamlConvert) | `src/utils/bbq_yaml.py` | 往返测试（BBQ→YAML→BBQ）逐字节一致 |

### P2 — BBQ 与文本引擎重构

| 编号 | 模块 | 任务 | 参考 Rust 源码 | 预期产出 | 验收标准 |
|------|------|------|----------------|----------|----------|
| P2-1 | `bbq` | 使用 `dataclasses` + `struct` 定义声明式 BBQ 头部与 Section Entry | `dearlystars/src/bbq.rs` (binrw) | `src/utils/bbq_struct.py` | 所有 BBQ 文件正确解析，不硬编码偏移 |
| P2-2 | `bbq` | 通用 Section 解析：自动识别 data_type，不依赖固定 Section 顺序 | `dearlystars/src/bbq.rs` (v0.5.1 改进) | `src/utils/bbq_format.py` 重构 | F_TBL 等文件解析不报错 |
| P2-3 | `bbq` | 支持 Type3Data（含 children）的完整解析与重建 | `dearlystars/src/bbq.rs` | `src/utils/bbq_type3.py` | Type3 数据不丢失 |
| P2-4 | `bbq` | 支持 Type6（Command）完整解析：code1/code2/arg_count/place/args | `dearlystars/src/bbq.rs` | `src/utils/bbq_type6.py` | 命令数据正确读写 |
| P2-5 | `bbq` | 邮件 BBQ 专用处理：30 行/32 字节硬限制模式、空回信安全处理 | `dearlystars/src/bbq.rs` (mail 相关) | `src/utils/bbq_mail.py` | 邮件注入与 faraplay 输出一致 |
| P2-6 | `inject` | ARM9/Overlay 注入：支持 CSV 输入、先清空再写入、按文件分桶 | `dearlystars/src/arm9overlay.rs` | `src/stage4_inject_text.py` 升级 | 注入输出与 faraplay 逐字节一致 |

### P3 — 字库稳定性与背景图修复（保留独有优势，禁止破坏性改动）

**核心约束**：`font_mapping.json` 是汉化进度的唯一存档，任何格式升级（CSV/YAML/PNG）都**必须保证该文件逻辑不变**，否则已完成的翻译字符将全部失效。

| 编号 | 模块 | 任务 | 说明 | 预期产出 | 验收标准 |
|------|------|------|------|----------|----------|
| P3-1 | `font` | `font_mapping.json` 兼容性锁定 | 在任何新格式（CSV/YAML） workflow 中，Stage 3 的字符扫描逻辑必须以 `font_mapping.json` 为基准增量更新，禁止全量重生成导致编码漂移 | `src/stage3_build_font.py` 增加 `--incremental` 模式 | 旧 `font_mapping.json` 在新 workflow 下可无缝复用 |
| P3-2 | `font` | 映射表备份与回滚机制 | 每次 `build_font` 前自动备份旧 mapping，提供 `rollback` 命令 | `src/utils/mapping_backup.py` | 误操作后可一键恢复上一版 mapping |
| P3-3 | `bg` | 4bpp Tile 编码修复 | 修复 `stage4_import_bg.py` 中 4bpp 回写的已知边界问题（tile 索引越界/错位） | `src/stage4_import_bg.py` 修复 | 4bpp 背景图回写无错位，与 8bpp 同等稳定 |

### P4 — 工程化与测试

| 编号 | 模块 | 任务 | 说明 | 预期产出 | 验收标准 |
|------|------|------|------|----------|----------|
| P4-1 | `typing` | 类型注解全覆盖 | Python 3.10+ 类型注解，`mypy` 静态检查 | 全源码通过 `mypy --strict` | 零类型错误 |
| P4-2 | `pytest` | 单元测试覆盖 | utils 模块 ≥ 80% 覆盖率，Stage 核心函数有回归测试 | `tests/` 目录扩充 | CI 通过 |
| P4-3 | `ci` | GitHub Actions | mypy、pytest、ROM 构建验证 | `.github/workflows/ci.yml` | 每次 PR 自动验证 |
| P4-4 | `bench` | 性能基准 | 记录各 Stage 耗时，追踪性能回归 | `benchmarks/` | 构建时间可量化 |

---

## 三、实施优先级与时间线

```
周次    1-2       3-4       5-6       7-8       9-10      11-12     13-14     15-16
       ├─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┤
P0     │P0-1/2   │P0-3     │P0-4     │P0-5     │P0-6     │P0-7     │集成测试  │         │
       │Header   │BLZ      │ROM重建  │DSi      │EZT/EZP  │LZ10     │         │         │
       ├─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┤
P1     │         │P1-4/5   │P1-1     │P1-2     │P1-3     │P1-6     │         │         │
       │         │CSV      │PNG      │Sprite   │预览     │YAML     │         │         │
       ├─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┤
P2     │         │         │P2-1/2   │P2-3/4   │P2-5     │P2-6     │         │         │
       │         │         │BBQ结构  │Type3/6  │邮件     │ARM9 CSV │         │         │
       ├─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┤
P3     │         │         │         │         │P3-1/2   │P3-3     │         │         │
       │         │         │         │         │mapping  │4bpp修复  │         │         │
       ├─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┤
P4     │持续      │持续      │P4-1     │P4-2     │P4-3     │P4-4     │         │         │
       │         │         │类型注解 │pytest   │CI       │基准     │         │         │
       └─────────┴─────────┴─────────┴─────────┴─────────┴─────────┴─────────┴─────────┘
```

### 关键里程碑

| 里程碑 | 时间 | 目标 |
|--------|------|------|
| **M1** | 第 4 周末 | 自研 NDS ROM 构建内核完成，可独立 `extract-nds` / `build-nds` |
| **M2** | 第 6 周末 | BLZ 压缩与 EZ 封包引擎完成，不再依赖外部 exe |
| **M3** |第 10 周末 | PNG + Sprite Format + CSV/YAML 支持完成，工作流现代化 |
| **M4** |第 16 周末 | 所有功能与 faraplay v0.5.2 逐字节等价，工程化达标 |

---

## 四、需保留的 Python 独有优势

以下功能 faraplay 不具备，优化过程中必须保留：

1. **`font_mapping.json` 进度存档** — 字符替换的唯一真相源，任何 workflow 升级必须保证其格式与增量更新逻辑不变
2. **OpenCC + NFTR 动态字库构建** — 汉化核心基础设施
3. **邮件换行编辑与字节公式** — Excel 条件格式、行高控制
4. **NCGR/NCLR/NSCR 背景图处理** — 完整背景替换能力
5. **交互式构建控制台** — 对非技术人员友好的菜单界面
6. **5-Stage 一键自动化流水线** — `Auto Build` 命令

---

## 五、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 自研 ndstool 的 DSi HMAC/modcrypt 实现错误 | 高 | 与 faraplay 输出逐字节对比；实机测试 |
| BLZ 压缩率不足导致 ARM9/Overlay 溢出 | 高 | 大量样本测试，对比 ndspy/faraplay 压缩率 |
| BBQ 通用解析改变后兼容性下降 | 中 | 保留旧解析器作为 fallback，灰度切换 |
| PNG 调色板转换与原版差异 | 中 | 注入前强制生成预览图，人工确认 |
| Python 性能瓶颈（build-bin 过慢）| 中 | 关键路径用 `numpy`/`struct` 优化，必要时 Cython |

---

> 注：更详细的横向对比分析、Phase 细分任务、工时预估请参阅 [`OPTIMIZATION_PLAN.md`](OPTIMIZATION_PLAN.md)。
