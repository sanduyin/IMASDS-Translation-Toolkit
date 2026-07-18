---
name: "soul"
description: "《偶像大师 深情之星》NDS 完整逆向工程知识库。涵盖 ARM9/Overlay 架构、BBQ 虚拟机、Native 函数、Scene 分派、剧情脚本模板、MAIL/TOUCH/Lesson 系统。在分析该游戏任何二进制文件、SCN 脚本、TBL 数据或汉化修改时调用。"
---

# 《偶像大师 深情之星》NDS 完整逆向工程知识库

## 1. 文件与内存架构

### 核心文件
- `arm9.bin`：主 ARM9 处理器二进制，基址 `0x02004000`，878,528 字节。包含 SDK、主框架、BBQ VM、资源调度、WiFi。
- `arm7.bin`：ARM7 协处理器代码（172KB），纯 ARM 模式，21 个函数。
- `y9.bin`：ARM9 Overlay 表，320 字节，10 条记录，每条 0x20 字节。
- `y7.bin`：空文件（游戏无 ARM7 Overlay）。
- `overlay_0000.bin` ~ `overlay_0009.bin`：10 个 Overlay 模块，按需加载。

### Overlay 内存槽位
- **前槽位**：Overlay 0 <-> Overlay 1，互斥，基址 `0x02116000`，范围 `0x02116000~0x02117320`。
- **后槽位**：Overlay 2~9 互斥，基址 `0x02117320`，范围 `0x02117320~0x0218B5C0`。
- 游戏可同时驻留 1 个前槽 + 1 个后槽。

### Overlay 详细规格

| ID | RAM 地址 | RAM 大小 | BSS | 压缩大小 | 模式 | 功能 |
|---|---|---|---|---|---|---|
| 0 | 0x02116000 | 0x1320 | 0x0 | 0xF58 | ARM | 运行时代码解码/自修改代码层 |
| 1 | 0x02116000 | 0x260 | 0x0 | 0x1C0 | ARM | 初始化/模式引导模块 |
| 2 | 0x02117320 | 0x6D380 | 0x6F20 | 0x4F418 | Thumb 主体 + ARM SINIT | Nintendo Wi-Fi 网络认证、下载、连接设置 |
| 3 | 0x02117320 | 0x25DE0 | 0x60 | 0x154F8 | ARM 主体 + Thumb 小岛 | Download Play / 多重引导 |
| 4 | 0x02117320 | 0x36340 | 0x37E0 | 0x1E5F0 | ARM | 能力状态、邮件 UI、脸部渲染 |
| 5 | 0x02117320 | 0x7960 | 0x20 | 0x4084 | ARM | 系统菜单、存档、主人公选择、QR 码、Wi-Fi |
| 6 | 0x02117320 | 0xCB80 | 0x20 | 0x75F8 | ARM | Vocal/Dance/Visual 三个课程小游戏 |
| 7 | 0x02117320 | 0x3520 | 0x0 | 0x20C8 | ARM | 30 秒试镜小游戏 |
| 8 | 0x02117320 | 0x7020 | 0x20 | 0x3FBC | ARM | 衣装编辑、歌曲选择界面 |
| 9 | 0x02117320 | 0x1FD00 | 0x220 | 0x115D4 | ARM | 舞台编辑、舞蹈/镜头/衣装/Call 设置、成绩数据 |

### Overlay Loader 两级映射
```
Scene ID -> 0x020784E8 -> 逻辑槽位 -> 0x020CBCF8 -> 物理 Overlay ID -> 0x02064B30 -> FS_LoadOverlay
```

逻辑槽位到物理 Overlay 映射表：
```c
static const uint32_t physical_overlay_id[9] = { 0, 5, 4, 7, 6, 9, 8, 2, 3 };
```

### Scene 到 Overlay 映射

| Scene | 逻辑槽 | 物理 Overlay | 构造函数 | 功能 |
|---|---|---|---|---|
| 0,1,9 | 0 | 公共 | 0x020A981C(1) | 公共/常驻场景 |
| 2 | 1 | 5 | 0x02117320 | 系统、存档、主人公选择 |
| 3,10,11,12 | 6 | 8 | — | 衣装/歌曲/编辑子页面 |
| 4,26,27 | 2 | 4 | 0x021219BC(4) | 状态、邮件、角色显示 |
| 5 | 3 | 7 | 0x021177FC | 试镜小游戏 |
| 6 | 4 | 6 | 0x021177FC | 舞蹈课程 |
| 7 | 4 | 6 | 0x0211ACB4 | 视觉课程 |
| 8 | 4 | 6 | 0x02122070 | 声乐课程 |
| 13~22 | 5 | 9 | 多个 | 舞台/Live 编辑子场景 |
| 23 | 8 | 3 | 0x02136350 | Download Play/多重引导 |
| 24 | 7 | 2 | 0x0216EA84 | 网络认证、通信、内容下载 |
| 25 | 7 | 2 | 0x02177198 | Nintendo WFC 连接设置 |

---

## 2. BBQ 虚拟机 (VM)

### BBQ 文件格式
- 魔数：`.BBQ1.00`（8 字节）
- Header `+0x10`：节区目录偏移
- Header `+0x14`：节区数量
- 每个目录项 0x14 字节，ARM9 建立 9 个槽位对应类型 0~8。

### 节区目录项结构
```c
struct BBQSectDirEntry {
    uint32 type;            // 节区类型
    uint32 offsets_offset;  // 相对当前目录项地址
    uint32 data_count;
    uint32 data_offset;     // 相对当前目录项地址
    uint32 data_size;
};
```

### 已知节区类型
- Type 2：外部接口/表描述符
- Type 3：函数目录
- Type 5：运行时数据/定长记录
- Type 6：VM 指令流
- Type 7：字符串池（CP932 编码）

### Type 6 指令头位布局（32-bit）
```
bits  0.. 9 = opcode (0x00~0x32, 共 51 个)
bits 10..14 = value_type
bit      15 = 保留/未知标志
bits 16..31 = argument_count
```

其后跟随：
```
uint32 place;
uint32 args[argument_count];
```

### value_type 含义
- `type = 0`：无返回值调用、标记或空值
- `type = 3`：整数、布尔值和普通 32 位运算（最常用）
- `type = 10`：Type 7 字符串引用
- 类型 0~7 的 VM 栈元素大小都是 4 字节
- 类型 8~12 的大小依次为 16、32、64、128、256 字节（字符串/缓冲区）

### VM 调度器
- 地址：`0x020622C4`
- 完整 51 项跳转表，处理函数地址已确认。
- 每次执行一条命令，按 opcode `0x00..0x32` 进入跳转表。

### 已确认 Opcode

| Opcode | 名称 | 作用 |
|---|---|---|
| 0x00 | PUSH_CONST | 压入立即数或 Type 7 字符串 |
| 0x01 | LOAD_LOCAL | 读取局部变量 |
| 0x03 | LOAD_EXTERNAL | 通过回调读取全局/上下文变量 |
| 0x0B | STORE_LOCAL | 写局部变量 |
| 0x18 | EQUAL | 相等比较（字符串走字符串比较，普通类型按字节比较） |
| 0x1B | AND | 逻辑与 |
| 0x1C | OR | 逻辑或 |
| 0x27 | DUP | 复制栈顶 |
| 0x28 | PUSH_CALL_MARKER | 内部函数调用前压入特殊标记 |
| 0x29 | POP | 丢弃栈顶 |
| 0x2A | JUMP_REL | 无条件相对跳转 |
| 0x2C | JUMP_IF_ZERO_REL | 条件为 0 时相对跳转 |
| 0x2D | CALL_BBQ | 保存调用帧并跳转到另一 BBQ 函数（参数与 Type 3 描述符字段直接对应） |
| 0x2E | CALL_NATIVE | 调用游戏原生函数；args[0]=原生函数 ID，args[1]=栈参数数量 |
| 0x2F | RETURN | BBQ 函数返回；可带返回值 |
| 0x30 | YIELD_WAIT | 把解释器状态设为等待/暂停，通常用于每帧让出执行 |
| 0x31 | FUNCTION_BEGIN_MARKER | 运行时不做操作，仅作为函数开始标记 |
| 0x32 | FUNCTION_END_MARKER | 运行时不做操作，仅作为函数结束标记 |

### CALL_BBQ (0x2D)
- 保存当前代码位置、栈帧和调用帧
- 从命令参数设置被调用函数的签名及代码偏移
- 跳转到新的 Type 6 位置
- 参数与目标 Type 3 记录的字段直接对应

### CALL_NATIVE (0x2E)
- `args[0] = 原生函数 ID`
- `args[1] = 从 VM 栈取得的参数数量`
- `value_type = 0`：不需要返回值
- `value_type = 3`：把 32 位返回值压回 VM 栈
- 因此 `2E/00` = 无返回值原生调用，`2E/0C` = 返回 32 位值的原生调用

---

## 3. 已确认 Native 函数

### BBQ / 调度 Native
| ID | ARM9 Handler | 名称/作用 |
|---|---|---|
| 0x00 | — | REPLACE_BBQ（替换当前 BBQ） |
| 0x01 | — | START_BBQ（按资源 ID 启动 BBQ 脚本） |
| 0x02 | 0x02080C5C | FINISH_BBQ_RETURN（结束当前 BBQ/返回调度器） |
| 0x0D | — | CHECK_PROGRESS_CONDITION |
| 0x0E | 0x02080150 | SET_SCENARIO_FLAG_RANGE(start_flag_id, enabled, count) |
| 0x32 | 0x0207FBE4 | 设置文本/场景参数 |
| 0x3D | — | EXECUTE_NATIVE_TASK / 等待帧数 |
| 0x59 | — | UPDATE_OR_UNLOCK_SCENARIO |
| 0x5A | — | SET_DISPATCH_STATE |
| 0x5B | — | GET_DISPATCH_STATE |
| 0x7A | 0x020818E8 | 写入全局模式字节 `+0x5E6E` |
| 0x93 | — | 物品/奖励相关 |
| 0x94 | — | 物品/奖励相关 |
| 0x9F | 0x0207F7D0 | SET_TOUCH_COUNTER（空操作/遗留调试接口） |
| 0xAF | 0x020809D4 | CHANGE_SCENE / 创建 Scene |
| 0xB0 | 0x02080A08 | WAIT_SCENE_RESULT / 等待 Scene 结果 |
| 0xBC | 0x0208057C | 异步关闭消息/人物 UI |
| 0xC3 | 0x0207B828 | 普通 MessageRecord 异步显示器 |
| 0xC4 | — | 特殊多人消息显示 |
| 0xC5 | 0x0207C30C | ADV 运行环境初始化 |
| 0xD0 | — | 显示地点标题 |
| 0xD8 | 0x0207D91C | 累加全局字段 `+0x3164` |
| 0xE0 | — | SET_TOUCH_PROMPT |
| 0xE1 | — | 进度相关 |
| 0xE3 | 0x0207C960 | EVALUATE_UNLOCK_RULE（检查数据驱动解锁规则并加入通知队列） |
| 0xF0 | — | SET_TOUCH_MODE_NORMAL |
| 0xF1 | — | SET_TOUCH_MODE_SLIDE |
| 0xF3 | 0x0207B568 | 设置人物槽显示参数/偏移 |
| 0xF7 | — | 人物显示相关 |
| 0xEA | — | 人物显示相关 |
| 0xEB | — | 人物显示相关 |
| 0xEC | — | 人物显示相关 |
| 0xFD | — | 触摸相关准备 |
| 0x100 | — | START_TOUCH_DETECTION |
| 0x102 | 0x0207EBC4 | GET_TOUCH_COORDINATE_X / 读取当前是否存在触摸输入 |
| 0x103 | 0x0207EBF8 | GET_TOUCH_COORDINATE_Y / 把原始触摸方向转换为标准方向码 |
| 0x108 | 0x0207ECC0 | 加载 TOUCH BBQ 并执行指定导出函数（异步操作） |
| 0x109 | 0x0207EE20 | 初始化触控任务与提示文本 |
| 0x10A | 0x0207EE6C | 正式启动触控判定 |
| 0x10B | 0x0207EEA0 | 结束并清理触控任务 |
| 0x10C | 0x0207EEEC | 设置触控上下文模式字段 |
| 0x10D | 0x0207EF18 | 配置触控引导/区域参数 |
| 0x10F | 0x0207EFC0 | REGISTER_TOUCH_CALLBACK（模式 1，11 个参数） |
| 0x110 | 0x0207F134 | REGISTER_TOUCH_CALLBACK（模式 0，11 个参数） |
| 0x111 | 0x0207F2A8 | 从触控事件队列取出逻辑事件 |
| 0x112 | 0x0207F2D8 | 读取指定事件的进度/计数值 |
| 0x113 | 0x0207F30C | 设置 TOUCH 函数最终返回值 |
| 0x115 | 0x0207F358 | 设置触控阈值或任务参数 |
| 0x11C | 0x0207DD90 | MAIL 专用 MessageRecord 异步显示器（六阶段状态机） |
| 0x11E | 0x0207E474 | MAIL_INIT / 初始化绑定 MAIL 的配套 MES |

### 关键 ARM9 函数地址
| 地址 | 名称/作用 |
|---|---|
| 0x02061D68 | BBQ_InitFromMemory |
| 0x02061E30 | BBQ_Load/Validate |
| 0x020621A8 | BBQ_FindType3Entry |
| 0x02062288 | BBQ_GetType7Item |
| 0x020622C4 | BBQ_ExecuteOneCommand |
| 0x02063FA4 | BBQ_GetType5BytePtr |
| 0x02063FD0 | BBQ_GetType6Base |
| 0x02064B30 | Game_LoadLogicalOverlay（游戏自己的 Overlay 管理器） |
| 0x020168A4 | FS_LoadOverlayInfo |
| 0x02016A3C | FS_LoadOverlayImage |
| 0x02016BB4 | FS_StartOverlay |
| 0x02016DEC | FS_LoadOverlay |
| 0x02016E3C | FS_UnloadOverlay |
| 0x02074D24 | 通知入队（类型 3、规则 ID） |
| 0x020784E8 | Scene 到逻辑槽位映射 |
| 0x020CBCF8 | 逻辑槽位到物理 Overlay 映射 |

---

## 4. BBQ 文件类型与架构

### BBQ 双文件配对模式
- **逻辑文件**（含指令和函数）+ **文本文件**（仅字符串，文件名带 `_MES`）
- MES 文件通过 `Native 0x11E` 自动绑定：主 BBQ 编号 + 1 = MES 编号

### BBQ 文件分类

| 类型 | 特征 | 例子 |
|---|---|---|
| MESSAGE | 纯文本，0 指令 0 函数 | — |
| MAIN | 章节主流程/总调度器 | 0060_GAMEMAIN, 0070_AIH_MAIN |
| ROUTE_CHAPTER_CONTROLLER |  Rank 章节控制器，32 函数模板 | 0071_AIH_F01_MAIN, 0075, 0083 |
| ADV_SCENARIO | 固定或分支剧情演出脚本 | 0066_TOTAL_GEND_MAIN01, 0809_ERI_GEND_MAIN01 |
| MESSAGE_DATABASE | 纯消息包，Type 6=0 | *_MES.BBQ |
| AUDITION | 试镜系统控制器 | 0084_AIH_AUD_SYS |
| LESSON_CONTROLLER | 课程开始/结果调度 | 0086_AIH_LES_BEGIN, 0100_AIH_LES_PLAY, 0102_AIH_LES_RESULT |
| MAIL_REACTION_CONTROLLER | 邮件反应脚本，6 函数标准架构 | 1171_AIH_E01_MAIL01 |
| MAIL_REACTION_MESSAGE_DATABASE | 邮件反应配套 MES | 1172_AIH_E01_MAIL01_MES |
| TOUCH_FUNCTION_LIBRARY | 触控互动函数库，无公共入口 0x1001 | 1248, 1253, 1262 |
| WEEKLY_SYSTEM_CONTROLLER | 每周循环系统 | 0116_AIH_MTG_GREE, 0122_AIH_MTG_SYA, 0124_AIH_SUN_TALK |
| ENDING | 结局 | 0064_ENDING |
| GEND | 培养系统 | 0065_EDITPART |
| TABLE | 纯数据配置表，无执行代码 | TBL 文件 |

### 标准 ADV 剧情 BBQ 编译模板
- Type 6 前 `0x17EC` 字节（6,124 字节）是共享的 14 个公共辅助函数（签名 `0x10000~0x1000D`）。
- 真正需要分析的是 `0x1000E` 之后的控制器、剧情主体和分支状态函数。
- 公共入口签名统一为 `0x1001`。
- 标准入口结构：
```c
function PublicEntry() {
    CallLocalFunction(0x1000E);  // 状态控制器
    Native_02();                  // 结束当前 BBQ
    return 0;
}
```

### MAIL 类型 BBQ 标准架构（6 函数）
1. 入口函数
2. 初始化函数（`0x10000`，Native `0x11E` 绑定 MES）
3. 重置/清理函数（`0x10001`，Native `0xBC`）
4. 主循环/控制器函数（`0x10002`）
5. 邮件显示/反应函数（`0x10003`，Native `0x11C`）
6. 收尾函数（`0x10004`，状态 99->100）

状态机仅包含：状态 0（显示）和状态 99（退出）。
MAIL 类型 BBQ 完全不使用 `Native 0xC3`（普通对话系统）。

### TOUCH 类型 BBQ 特征
- **没有公共入口 `0x1001`**
- 由外部剧情通过 `Native 0x108` 动态加载并调用
- 签名 `0x10000~0x1000D` 是 14 个公共 ADV 显示辅助函数
- 真正被调用的是较小的导出签名（如 `0, 1, 2, 0x46`）
- 执行模板：Prepare -> Native_109 -> Native_10C -> RegisterRules -> Native_10A -> 轮询 Native_111 -> Native_113 写结果 -> Native_10B 清理

---

## 5. 启动流程与调度层级

### 根调度器
```
0060_GAMEMAIN（根调度器）
  state 1 -> Native 0x01(0x103E) -> 0061_MAKERLOGO
  state 2 -> Native 0x01(0x103F) -> 0062_GAMETITLE
  state 5 -> Native 0x01(0x1041) -> 0064_ENDING
```

### 主人公选择
```
0060 根据主人公值分派三条个人路线：
  主人公 0 -> 资源 0x1047 -> 0070_AIH_MAIN（日高爱）
  主人公 1 -> 资源 0x10xx -> 水谷绘理路线
  主人公 2 -> 资源 0x10xx -> 秋月凉路线
```

### 资源编号换算公式
```
SCN 文件编号 = BBQ resource ID - 0x1001
```

### 日高爱路线层级
```
0060_GAMEMAIN
  └─ 0070_AIH_MAIN（13 章总调度器）
     ├─ 0071_AIH_F01_MAIN (F Rank)
     ├─ 0072 ...
     ├─ 0073 ...
     ├─ 0074 ...
     ├─ 0075_AIH_D01_MAIN (D Rank)
     ├─ ...
     └─ 0083_AIH_A01_MAIN (A Rank)
        └─ 各章节实际剧情/消息文件（如 SCN 0143, 0245, 0431）
```

### 0070_AIH_MAIN 章节分派
| route_cursor | 资源 ID | SCN 文件 |
|---|---|---|
| 1 | 0x1048 | 0071 |
| 2 | 0x1049 | 0072 |
| ... | ... | ... |
| 13 | 0x1054 | 0083 |

### 进度更新配对表（0070 函数 742）
| 章节 | 条件 A | SCN A | 条件 B | SCN B |
|---|---|---|---|---|
| 1 | 0 | 0103 | 2 | 0102 |
| 2 | 3 | 0106 | 4 | 0105 |
| 3 | 0 | 0109 | 5 | 0108 |
| 4 | 5 | 0112 | 5 | 0111 |
| 5 | 6 | 0115 | 7 | 0114 |
| 6 | 7 | 0118 | 7 | 0117 |
| 7 | 0 | 0121 | 8 | 0120 |
| 8 | 9 | 0124 | 9 | 0123 |
| 9 | 10 | 0127 | 11 | 0126 |
| 10 | 0 | 0130 | 11 | 0129 |
| 11 | 12 | 0133 | 13 | 0132 |
| 12 | 0 | 0136 | 0 | 0135 |
| 13 | 0 | 0139 | 0 | 0138 |

---

## 6. 系统流程详解

### 完整 Lesson 流程
```
0086 LES_BEGIN
  ├─ 显示"课程练习室"
  ├─ 判断 Vocal(1)/Dance(2)/Visual(3)
  ├─ 判断积极/普通/低干劲
  ├─ 读取 0087 开场语音
  └─ 分派 0088~0099 前辈事件
       ├─ 0088/0089：千早
       ├─ 0090/0091：弥生
       ├─ 0092/0093：律子
       ├─ 0094/0095：梓
       ├─ 0096/0097：真
       └─ 0098/0099：亚美
            ↓
          0100 LES_PLAY
            ├─ 首次教程
            ├─ Scene 6 Dance / Scene 7 Visual / Scene 8 Vocal (Overlay 6)
            ↓
          0101 LES_PLAY_MES
            ├─ 开场反应
            ├─ 爱自评或前辈四档评价
            └─ 结束问候
            ↓
          0102 LES_RESULT
            ├─ 计算正式成绩
            └─ 按前辈分派结果事件
                 ├─ 0104/0105 千早
                 ├─ 0106/0107 弥生
                 ├─ 0108/0109 律子
                 ├─ 0110/0111 梓
                 ├─ 0112/0113 真
                 └─ 0114/0115 亚美
                      ├─ 四档正式评价
                      ├─ 指导成长/毕业事件
                      └─ 最终面板奖励
```

### 试镜流程
```
0071~0083 章节控制器
  ↓ 需要进行试镜
0084_AIH_AUD_SYS
  ├─ 显示首次教程
  ├─ 准备爱语音和参数
  ├─ ChangeScene(5) -> Overlay 7（30 秒试镜小游戏）
  ├─ WaitSceneResult()
  └─ 合格/不合格/取消分支
       ├─ 0085 爱短语音/反应
       └─ 审查员合格发表
```

### 每周循环流程
```
每周开始：
  0116/0117 事务所早晨问候（三人 Tension 互动）
  -> 0122/0123 社长 Meeting（流行、Rank 建议、Audition 安排、合格率分析）
  -> 本周 Lesson/营业/Audition

每周结束/星期日：
  -> 0124/0125 星期日自由活动
       ├─ 外出看绘理/凉舞台
       └─ 留在家中按 40%/60%/80% 概率抽取粉丝礼物
```

### 邮件系统完整流程
```
TBL 邮件正文
  │
  ├─ 邮件 ID、发件人、标题、正文、回复选项
  └─ 邮件槽位/内容 ID
       │
       ▼
0016_MAILTABLE（86 项索引映射）
       │
       ├─────────────────────────────┐
       ▼                             ▼
0017_MAILSCNTABLE              0018_MAILSYSTABLE
37 条路线剧情邮件              46 条系统/教程邮件
trigger_key + reaction_scn     trigger_key=0xFFFF, reaction_scn=0
       │                             │
       ▼                             ▼
Overlay 4 启动 MAIL SCN        直接显示/归档
       │
       ▼
1171~1243 奇数 SCN（反应脚本）
       │
       ▼
自动绑定下一份偶数 MES
       │
       ▼
主角内心反应、flag 写入、解锁检查
```

### 0017 MAILSCNTABLE 记录结构（20 字节）
```c
struct MailMetadataRecord {
    uint16 reserved_00;           // 始终为 0
    uint16 trigger_key;           // +0x02，剧情事件键
    uint16 reserved_04;           // 始终为 0
    uint16 reaction_scn_resource; // +0x06，SCN 资源 ID
    uint32 subject_string_index;  // +0x08，Type 7 标题索引
    uint32 sender_string_index;   // +0x0C，Type 7 发件人索引
    uint32 mail_content_id;       // +0x10，全局邮件内容 ID
};
```

---

## 7. TBL 数据表结构

### TBL 型 BBQ 通用格式
- Header `+0x10`：目录偏移 = `0x18`
- section_count = 4
- 节区：Type 2（表描述符）、Type 5（定长记录）、Type 6（空）、Type 7（字符串池）

### Type 2 描述符（28 字节）
```c
struct TBLDescriptor {
    uint32 unknown = 0;
    uint32 signature = 0x00010000;
    uint32 unknown = 0;
    uint32 data_block_count = 1;
    uint32 record_size;
    uint32 record_count;
    uint32 unknown = 0;
};
```
验证：`record_size * record_count == Type5.data_size`

### 已知 TBL 文件
| 文件 | record_size | record_count | data_size | 内容 |
|---|---|---|---|---|
| 0002_BGMTABLE | 16 | 72 | 1152 | BGM 表 |
| 0003_SETABLE | 8 | 86 | 688 | SE 表 |
| 0005_SONGTABLE | 140 | 13 | 1820 | 歌曲表 |
| 0016_MAILTABLE | 4 | 86 | 344 | 邮件索引映射 |
| 0017_MAILSCNTABLE | 20 | 37 | 740 | 路线邮件元数据 |
| 0018_MAILSYSTABLE | 20 | 46 | 920 | 系统邮件元数据 |

---

## 8. MessageRecord 结构

标准 16 字节消息记录：
```c
struct MessageRecord {
    uint8  speaker_code;       // 说话角色/窗口表现代码
    int8   display_style;      // 名牌或消息布局样式（0=普通, 1=内心独白/旁白）
    uint16 voice_id;           // 语音编号；0 表示无语音
    uint32 speaker_string;     // Type 7 说话人字符串索引
    uint32 line1_string;       // 第一行/第一段正文索引
    uint32 line2_string;       // 第二行/第二段正文索引，0 表示没有
};
```

---

## 9. 课程小游戏玩法确认

| Lesson | Scene | Overlay | 核心玩法 |
|---|---|---|---|
| Vocal | 8 | 6 | 选择、拖放文字块，拼成正确歌词（歌词文字块） |
| Dance | 6 | 6 | 按颜色和节奏点击流动踏板（节奏踏板） |
| Visual | 7 | 6 | 点击与目标一致的感情面板（感情面板） |

课程类型编号：1=Vocal, 2=Dance, 3=Visual

---

## 10. 汉化与修改关键知识

### 中文字符显示修改点
1. **GLD 精灵图文件**：替换日文字形为中文，使用 2bit/pixel 格式（index 0=透明, 1=黑色, 2-3=灰色抗锯齿），render_width=32 对齐。
2. **Overlay 映射表**：位于 RAM `0x02123310`（文件偏移 `0x00BFF0`），6 字节条目，更新为中文 SJIS 代码。
3. **LESVOTICETABLE 文本**：替换歌词中的假名为中文，使用 CP932 编码。

### Vocal 课程判定机制
- 判定基于**字符串索引比较**而非文本内容比较。
- 无效字符串会导致索引计算异常，但判定全部正确。

### 重建 ROM 注意事项
- Overlay 0 是运行时代码解码层，会生成 16 字节密钥并原地解密 ARM 代码。
- Y9 的 `RAM size` 必须保持解压后大小，不能填压缩大小。
- 压缩标志字段：压缩写入用 `0x03000000 | compressed_size`；解压态写入用 `0x02000000` 并让 FAT 文件大小等于解压大小。
- 任何 Y9 压缩标志、RAM size、FAT File ID 或 BLZ 数据错误，第一次进入需要后槽 Overlay 的 Scene 时就会失败（白屏）。

---

## 11. 分析经验与常见陷阱

1. **必须区分 CALL_BBQ (0x2D) 和 Native 0x00/0x01**：前者是 BBQ 内部函数调用，后者是跨文件资源调度。
2. **必须区分 value_type**：`value_type=3` 是普通整数，`value_type=10` 才是 Type 7 字符串引用。不能只看数值是否落在 Type 7 索引范围内。
3. **Type 7 索引 0 是空字符串**：非空字符串从索引 1 开始。其他 AI 报告常把六条非空字符串编号成 0~5，导致 PUSH_STRING 解析偏移一位。
4. **AND (0x1B) 不是 LESS_THAN，OR (0x1C) 不是 GREATER_EQUAL**：这两个逻辑运算在条件判断中至关重要，误标会直接改变程序含义。
5. **Native 0x30 是 YIELD_WAIT 不是循环标记**：它把解释器状态设为等待状态。
6. **Native 0x01 是按资源 ID 启动 BBQ，不是 SET_STATE**：传给它的常量与 BBQ 文件编号精确对应（0x103D=0060, 0x103E=0061, 等）。
7. **MAIL 类型 BBQ 没有公共入口 0x1001 的调用链**：它由上层系统调用，返回后控制权回到调用者。
8. **TOUCH BBQ 没有公共入口 0x1001**：它们是被 `Native 0x108` 动态加载的函数库。
9. **前 0x17EC 字节是 ADV 模板**：批量分析时若哈希一致，可直接标记为 `ADV_TEMPLATE_V1`，不必重复人工分析 14 个通用函数。
10. **TBL 文件是纯数据配置表**：无 VM 代码，节区类型少（通常只有 Type 2/5/6/7），节区目录偏移在 0x10（SCN 脚本在 0x20）。
