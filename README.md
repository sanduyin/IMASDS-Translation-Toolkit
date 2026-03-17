# IMASDS Translation Toolkit / 深情之星汉化工具链

[English](#english) | [中文](#中文)

---

<a id="english"></a>
## English

An automated, Python-based ROM hacking and localization toolchain for *THE iDOLM@STER Dearly Stars* (Nintendo DS).

### Features
*   **Automatic unpacking and decompression**: `ndstool` and `ndspy` are used to decompress ARM9 and Overlay files using BLZ (Backward LZ10) reverse compression.
*   **Pointer extraction**: Performs cross-file SHIFT-JIS pointer searches on low-level code segments.
*   **Dynamic Character Library**: Integrates `OpenCC` to reuse the original JIS character slots.
*   **Secure Injection Mechanism**: Strict byte-level length verification and in-place memory injection prevent program segment data overflow crashes.

### Usage
1. Install Python 3.10+ and run `pip install -r requirements.txt`.
2. Place your legally dumped ROM named `THE iDOLM@STER Dearly Stars.nds` in `game_data/0_Original/`.
3. Provide your own font and place it in the `workspace/` directory, updating the filenames in `config.py`.
4. Run `python main.py` to open the interactive console.

### Acknowledgments
This project stands on the shoulders of giants. Special thanks to:
*   [ndstool](https://github.com/Relys/ndstool) by DarkFader / Relys for the foundational NDS file system tool.
*   [ndspy](https://github.com/RoadrunnerWMC/ndspy) by RoadrunnerWMC for the excellent BLZ decompression engine.
*   [OpenCC](https://github.com/BYVoid/OpenCC) by BYVoid for the flawless CJK mapping capabilities.

### Disclaimer
This project is for educational and technical research purposes only. The repository **DOES NOT** contain any copyrighted game ROMs, assets, or proprietary code from the original game. Users must legally obtain their own copy of the game. The authors are not responsible for any copyright infringement caused by the end users.


<a id="中文"></a>
## 中文

专为《偶像大师 深情之星》打造的基于 Python 的逆向工程与汉化构建工具链。

### 核心特性
*   **自动解包与解压**：由`ndstool` 与 `ndspy`解除 ARM9 及 Overlay 文件的 BLZ (Backward LZ10) 逆向压缩。
*   **指针提取**：针对底层程序代码段进行跨文件 SHIFT-JIS 指针搜索。
*   **动态字库**：集成 `OpenCC`复用原版 JIS 汉字槽位。
*   **安全注入机制**：严格的字节级长度校验与原地内存注入，杜绝程序段数据溢出崩溃。

### 使用指南
1. 安装 Python 3.10+，执行 `pip install -r requirements.txt` 安装依赖。
2. 将您合法提取的原版 ROM 命名为 `THE iDOLM@STER Dearly Stars.nds`，并放置于 `game_data/0_Original/` 目录。
3. 请自行准备所想要修改替换的字体文件，放置于 `workspace/` 目录下，并在 `config.py` 中修改对应名称。
4. 运行 `python main.py` 唤出交互式构建控制台。


### 致谢
本项目的成功离不开开源社区的伟大贡献，特此鸣谢：
*   [ndstool](https://github.com/Relys/ndstool) (DarkFader / Relys) 提供 NDS 文件系统基础工具。
*   [ndspy](https://github.com/RoadrunnerWMC/ndspy) (RoadrunnerWMC) 提供完善的底层解压缩引擎。
*   [OpenCC](https://github.com/BYVoid/OpenCC) (BYVoid) 提供完美的简繁日汉字映射技术。

### 免责声明
本项目仅供编程学习与技术研究使用。代码库中 **不包含** 任何受版权保护的游戏 ROM、美术资源或专有代码。使用者必须合法拥有原版游戏拷贝。作者不对最终用户使用本工具所导致的任何版权纠纷负责。
