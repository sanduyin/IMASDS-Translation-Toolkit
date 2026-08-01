# src/ndstool/header.py
"""
NDS ROM 基础 Header（0x180 字节）解析与生成。

参考实现：dearlystars_tool (Rust) ndstool/header.rs

字段布局严格对齐 Rust 的 ``DsHeader`` 结构体。DSi ROM 从绝对偏移
``0x180`` 起紧接 ``DsiExtraFields``（0xE80 字节），两者共同组成
0x1000 字节的 TWL Header；因此绝不能把 0x180~0x1FF 当成 NTR 保留区。

DSi 增强字段见 :mod:`dsi_builder`。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, asdict
from typing import Any

from .crc import crc16_header, crc16_logo, verify_header_crc

HEADER_SIZE = 0x180
"""``DsHeader`` 的精确长度；DSi 扩展从 ROM 绝对偏移 0x180 开始。"""

TITLE_LEN = 0x0C
GAMECODE_LEN = 0x04
MAKERCODE_LEN = 0x02
RESERVED_A_LEN = 0x07
RESERVED_B_LEN = 0x2C
LOGO_LEN = 0x9C
SECURE_DISABLE_LEN = 0x08
DEBUG_ZERO_LEN = 0x10


@dataclass
class NDSHeader:
    """
    NDS ROM 基础 Header（0x180 字节）。

    字段命名与偏移严格对齐 faraplay/ndstool 的 DsHeader 结构体。
    所有数值字段使用 int，字节数组字段使用 bytes（不可变）或 bytearray（可变）。
    """

    # 0x000 - 0x01F：基础标识区
    title: bytes = b"\x00" * TITLE_LEN                  # 0x000  Game Title (12B, ASCII, null-padded)
    gamecode: bytes = b"\x00" * GAMECODE_LEN            # 0x00C  Game Code (4B, ASCII)
    makercode: bytes = b"\x00" * MAKERCODE_LEN          # 0x010  Maker Code (2B, ASCII)
    unitcode: int = 0                                   # 0x012  Unit Code: 0=NDS, 2=NDS+DSi, 3=DSi
    devicetype: int = 0                                 # 0x013  Device Type (0=normal)
    devicecap: int = 0                                  # 0x014  Device Capacity (1<<n Mbit)
    reserved_a: bytes = b"\x00" * RESERVED_A_LEN        # 0x015  Reserved (7B)
    dsi_flags: int = 0                                  # 0x01C  DSi Flags
    nds_region: int = 0                                 # 0x01D  NDS Region
    romversion: int = 0                                 # 0x01E  ROM Version
    autostart: int = 0                                  # 0x01F  Autostart

    # 0x020 - 0x03F：ARM9/ARM7 二进制描述
    arm9_rom_offset: int = 0                            # 0x020  ARM9 ROM Offset
    arm9_entry_address: int = 0                         # 0x024  ARM9 Entry Address
    arm9_ram_address: int = 0                           # 0x028  ARM9 RAM Address
    arm9_size: int = 0                                  # 0x02C  ARM9 Size
    arm7_rom_offset: int = 0                            # 0x030  ARM7 ROM Offset
    arm7_entry_address: int = 0                         # 0x034  ARM7 Entry Address
    arm7_ram_address: int = 0                           # 0x038  ARM7 RAM Address
    arm7_size: int = 0                                  # 0x03C  ARM7 Size

    # 0x040 - 0x05F：FNT/FAT/Overlay 表描述
    fnt_offset: int = 0                                 # 0x040  FNT Offset
    fnt_size: int = 0                                   # 0x044  FNT Size
    fat_offset: int = 0                                 # 0x048  FAT Offset
    fat_size: int = 0                                   # 0x04C  FAT Size
    arm9_overlay_offset: int = 0                        # 0x050  ARM9 Overlay Table Offset
    arm9_overlay_size: int = 0                          # 0x054  ARM9 Overlay Table Size
    arm7_overlay_offset: int = 0                        # 0x058  ARM7 Overlay Table Offset
    arm7_overlay_size: int = 0                          # 0x05C  ARM7 Overlay Table Size

    # 0x060 - 0x07F：ROM 控制、Banner、Secure Area
    rom_control_info1: int = 0                          # 0x060  Port 40001A0h normal cmd (0x00416657)
    rom_control_info2: int = 0                          # 0x064  Port 40001A0h KEY1 cmd (0x081808F8)
    banner_offset: int = 0                              # 0x068  Icon/Title Banner Offset
    secure_area_crc: int = 0                            # 0x06C  Secure Area CRC16
    rom_control_info3: int = 0                          # 0x06E  Port 40001A0h setting (0x0D7E)
    arm9_autoload_hook_ram_address: int = 0             # 0x070  ARM9 Autoload Hook RAM Address
    arm7_autoload_hook_ram_address: int = 0             # 0x074  ARM7 Autoload Hook RAM Address
    secure_area_disable: bytes = b"\x00" * SECURE_DISABLE_LEN  # 0x078  Secure Area Disable (8B)

    # 0x080 - 0x093：ROM 大小与参数表
    application_end_offset: int = 0                     # 0x080  Application End Offset (NTR ROM Size)
    rom_header_size: int = 0                            # 0x084  ROM Header Size (0x4000)
    arm9_parameters_table_offset: int = 0               # 0x088  ARM9 Parameters Table Offset
    arm7_parameters_table_offset: int = 0               # 0x08C  ARM7 Parameters Table Offset
    dsi_ntr_rom_region_end: int = 0                     # 0x090  DSi NTR ROM Region End
    dsi_twl_rom_region_start: int = 0                   # 0x092  DSi TWL ROM Region Start
    reserved_b: bytes = b"\x00" * RESERVED_B_LEN        # 0x094  Reserved (0x2C bytes)

    # 0x0C0 - 0x15F：Nintendo Logo + CRC
    logo: bytes = b"\x00" * LOGO_LEN                    # 0x0C0  Nintendo Logo (156B)
    logo_crc: int = 0                                   # 0x15C  Logo CRC16
    header_crc: int = 0                                 # 0x15E  Header CRC16

    # 0x160 - 0x17F：Debug 区
    debug_rom_offset: int = 0                           # 0x160  Debug ROM Offset
    debug_size: int = 0                                 # 0x164  Debug Size
    debug_ram_address: int = 0                          # 0x168  Debug RAM Address
    offset_0x16c: int = 0                               # 0x16C  Unknown (offset 0x16C)
    zero: bytes = b"\x00" * DEBUG_ZERO_LEN              # 0x170  Zero padding (16B)

    def __post_init__(self) -> None:
        """构造后校验所有字节数组字段的长度。"""
        self._validate_bytes("title", TITLE_LEN)
        self._validate_bytes("gamecode", GAMECODE_LEN)
        self._validate_bytes("makercode", MAKERCODE_LEN)
        self._validate_bytes("reserved_a", RESERVED_A_LEN)
        self._validate_bytes("secure_area_disable", SECURE_DISABLE_LEN)
        self._validate_bytes("reserved_b", RESERVED_B_LEN)
        self._validate_bytes("logo", LOGO_LEN)
        self._validate_bytes("zero", DEBUG_ZERO_LEN)

    def _validate_bytes(self, name: str, expected_len: int) -> None:
        v = getattr(self, name)
        if not isinstance(v, (bytes, bytearray)):
            raise TypeError(f"{name} 必须是 bytes/bytearray，实际为 {type(v).__name__}")
        if len(v) != expected_len:
            raise ValueError(f"{name} 长度错误：期望 {expected_len}，实际 {len(v)}")

    # ------------------------------------------------------------------
    # 序列化 / 反序列化
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, data: bytes | bytearray) -> "NDSHeader":
        """从字节数组解析 Header。要求 len(data) >= HEADER_SIZE。"""
        if len(data) < HEADER_SIZE:
            raise ValueError(
                f"Header 数据过短：{len(data)} < 0x{HEADER_SIZE:X}"
            )
        # 仅消费基础 Header；0x180 起属于 DSiExtraFields。
        d = bytes(data[:HEADER_SIZE])
        return cls(
            title=d[0x000:0x00C],
            gamecode=d[0x00C:0x010],
            makercode=d[0x010:0x012],
            unitcode=d[0x012],
            devicetype=d[0x013],
            devicecap=d[0x014],
            reserved_a=d[0x015:0x01C],
            dsi_flags=d[0x01C],
            nds_region=d[0x01D],
            romversion=d[0x01E],
            autostart=d[0x01F],
            arm9_rom_offset=struct.unpack_from("<I", d, 0x020)[0],
            arm9_entry_address=struct.unpack_from("<I", d, 0x024)[0],
            arm9_ram_address=struct.unpack_from("<I", d, 0x028)[0],
            arm9_size=struct.unpack_from("<I", d, 0x02C)[0],
            arm7_rom_offset=struct.unpack_from("<I", d, 0x030)[0],
            arm7_entry_address=struct.unpack_from("<I", d, 0x034)[0],
            arm7_ram_address=struct.unpack_from("<I", d, 0x038)[0],
            arm7_size=struct.unpack_from("<I", d, 0x03C)[0],
            fnt_offset=struct.unpack_from("<I", d, 0x040)[0],
            fnt_size=struct.unpack_from("<I", d, 0x044)[0],
            fat_offset=struct.unpack_from("<I", d, 0x048)[0],
            fat_size=struct.unpack_from("<I", d, 0x04C)[0],
            arm9_overlay_offset=struct.unpack_from("<I", d, 0x050)[0],
            arm9_overlay_size=struct.unpack_from("<I", d, 0x054)[0],
            arm7_overlay_offset=struct.unpack_from("<I", d, 0x058)[0],
            arm7_overlay_size=struct.unpack_from("<I", d, 0x05C)[0],
            rom_control_info1=struct.unpack_from("<I", d, 0x060)[0],
            rom_control_info2=struct.unpack_from("<I", d, 0x064)[0],
            banner_offset=struct.unpack_from("<I", d, 0x068)[0],
            secure_area_crc=struct.unpack_from("<H", d, 0x06C)[0],
            rom_control_info3=struct.unpack_from("<H", d, 0x06E)[0],
            arm9_autoload_hook_ram_address=struct.unpack_from("<I", d, 0x070)[0],
            arm7_autoload_hook_ram_address=struct.unpack_from("<I", d, 0x074)[0],
            secure_area_disable=d[0x078:0x080],
            application_end_offset=struct.unpack_from("<I", d, 0x080)[0],
            rom_header_size=struct.unpack_from("<I", d, 0x084)[0],
            arm9_parameters_table_offset=struct.unpack_from("<I", d, 0x088)[0],
            arm7_parameters_table_offset=struct.unpack_from("<I", d, 0x08C)[0],
            dsi_ntr_rom_region_end=struct.unpack_from("<H", d, 0x090)[0],
            dsi_twl_rom_region_start=struct.unpack_from("<H", d, 0x092)[0],
            reserved_b=d[0x094:0x0C0],
            logo=d[0x0C0:0x15C],
            logo_crc=struct.unpack_from("<H", d, 0x15C)[0],
            header_crc=struct.unpack_from("<H", d, 0x15E)[0],
            debug_rom_offset=struct.unpack_from("<I", d, 0x160)[0],
            debug_size=struct.unpack_from("<I", d, 0x164)[0],
            debug_ram_address=struct.unpack_from("<I", d, 0x168)[0],
            offset_0x16c=struct.unpack_from("<I", d, 0x16C)[0],
            zero=d[0x170:0x180],
        )

    def build(self, update_crc: bool = True) -> bytes:
        """
        将基础 Header 序列化为 0x180 字节 bytes。

        Args:
            update_crc: 是否在序列化前重新计算 logo_crc (0x15C) 与 header_crc (0x15E)。
                        重建 ROM 时应置 True；做精确镜像时置 False 以保留原值。

        注意：update_crc=True 会**修改实例本身**的 logo_crc / header_crc 字段
        （2.2 修复标注的副作用）。如需保留旧 CRC，调用前请自行拷贝实例或置 False。
        """
        if update_crc:
            # 临时构造一份不带 CRC 的镜像用于计算
            self.logo_crc = crc16_logo(self.logo)
            # header_crc 计算覆盖 0x000~0x15D，故 CRC 字段本身（0x15E）不参与
            tmp = self._build_no_crc()
            self.header_crc = crc16_header(tmp[0x000:0x15E])

        return self._build_no_crc()

    def _build_no_crc(self) -> bytes:
        """内部：序列化 0x180 字节，但不重新计算 CRC。"""
        out = bytearray(HEADER_SIZE)
        out[0x000:0x00C] = self.title
        out[0x00C:0x010] = self.gamecode
        out[0x010:0x012] = self.makercode
        out[0x012] = self.unitcode & 0xFF
        out[0x013] = self.devicetype & 0xFF
        out[0x014] = self.devicecap & 0xFF
        out[0x015:0x01C] = self.reserved_a
        out[0x01C] = self.dsi_flags & 0xFF
        out[0x01D] = self.nds_region & 0xFF
        out[0x01E] = self.romversion & 0xFF
        out[0x01F] = self.autostart & 0xFF
        struct.pack_into("<I", out, 0x020, self.arm9_rom_offset & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x024, self.arm9_entry_address & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x028, self.arm9_ram_address & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x02C, self.arm9_size & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x030, self.arm7_rom_offset & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x034, self.arm7_entry_address & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x038, self.arm7_ram_address & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x03C, self.arm7_size & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x040, self.fnt_offset & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x044, self.fnt_size & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x048, self.fat_offset & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x04C, self.fat_size & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x050, self.arm9_overlay_offset & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x054, self.arm9_overlay_size & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x058, self.arm7_overlay_offset & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x05C, self.arm7_overlay_size & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x060, self.rom_control_info1 & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x064, self.rom_control_info2 & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x068, self.banner_offset & 0xFFFFFFFF)
        struct.pack_into("<H", out, 0x06C, self.secure_area_crc & 0xFFFF)
        struct.pack_into("<H", out, 0x06E, self.rom_control_info3 & 0xFFFF)
        struct.pack_into("<I", out, 0x070, self.arm9_autoload_hook_ram_address & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x074, self.arm7_autoload_hook_ram_address & 0xFFFFFFFF)
        out[0x078:0x080] = self.secure_area_disable
        struct.pack_into("<I", out, 0x080, self.application_end_offset & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x084, self.rom_header_size & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x088, self.arm9_parameters_table_offset & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x08C, self.arm7_parameters_table_offset & 0xFFFFFFFF)
        struct.pack_into("<H", out, 0x090, self.dsi_ntr_rom_region_end & 0xFFFF)
        struct.pack_into("<H", out, 0x092, self.dsi_twl_rom_region_start & 0xFFFF)
        out[0x094:0x0C0] = self.reserved_b
        out[0x0C0:0x15C] = self.logo
        struct.pack_into("<H", out, 0x15C, self.logo_crc & 0xFFFF)
        struct.pack_into("<H", out, 0x15E, self.header_crc & 0xFFFF)
        struct.pack_into("<I", out, 0x160, self.debug_rom_offset & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x164, self.debug_size & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x168, self.debug_ram_address & 0xFFFFFFFF)
        struct.pack_into("<I", out, 0x16C, self.offset_0x16c & 0xFFFFFFFF)
        out[0x170:0x180] = self.zero
        return bytes(out)

    # ------------------------------------------------------------------
    # 便捷属性
    # ------------------------------------------------------------------

    @property
    def title_str(self) -> str:
        """Game Title 解码为 ASCII（去除末尾 \x00）。"""
        return self.title.rstrip(b"\x00").decode("ascii", errors="replace")

    @title_str.setter
    def title_str(self, value: str) -> None:
        b = value.encode("ascii", errors="replace")[:TITLE_LEN]
        self.title = b + b"\x00" * (TITLE_LEN - len(b))

    @property
    def gamecode_str(self) -> str:
        return self.gamecode.decode("ascii", errors="replace")

    @gamecode_str.setter
    def gamecode_str(self, value: str) -> None:
        b = value.encode("ascii", errors="replace")[:GAMECODE_LEN]
        self.gamecode = b + b"\x00" * (GAMECODE_LEN - len(b))

    @property
    def makercode_str(self) -> str:
        return self.makercode.decode("ascii", errors="replace")

    @makercode_str.setter
    def makercode_str(self, value: str) -> None:
        b = value.encode("ascii", errors="replace")[:MAKERCODE_LEN]
        self.makercode = b + b"\x00" * (MAKERCODE_LEN - len(b))

    @property
    def is_dsi(self) -> bool:
        """unitcode & 0x02 表示 DSi 增强。"""
        return (self.unitcode & 0x02) != 0

    @property
    def ntr_rom_size(self) -> int:
        """NTR 区域 ROM 大小（application_end_offset）。"""
        return self.application_end_offset

    def verify_crc(self) -> tuple[bool, bool]:
        """验证 Logo CRC 与 Header CRC。返回 (logo_ok, header_ok)。"""
        return verify_header_crc(self._build_no_crc())

    def to_dict(self) -> dict[str, Any]:
        """转为可读字典（字节数组转十六进制字符串）。"""

        def conv(v: Any) -> Any:
            if isinstance(v, (bytes, bytearray)):
                return v.hex()
            return v

        return {k: conv(v) for k, v in asdict(self).items()}


# ----------------------------------------------------------------------
# 模块级便捷函数
# ----------------------------------------------------------------------

def parse_header(data: bytes | bytearray) -> NDSHeader:
    """便捷函数：从字节数组解析 NDS Header。"""
    return NDSHeader.parse(data)


def build_header(header: NDSHeader, update_crc: bool = True) -> bytes:
    """便捷函数：将 NDSHeader 序列化为 0x180 字节 bytes。"""
    return header.build(update_crc=update_crc)
