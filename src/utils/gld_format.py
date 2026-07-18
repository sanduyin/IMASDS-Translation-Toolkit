# src/utils/gld_format.py
"""
GLD 图像封包格式的完整解析与重建引擎。

参考实现：reference/dearlystars_tool/dearlystars/src/gld.rs

## GLD 文件结构

    [32 字节 Header]
    [pixel_data_size 字节像素数据]
    [palette_data_size 字节调色板数据]
    [footer_entry_count × (24 或 28) 字节 Footer Entries]

## Header (32 字节)

    0x00  magic[4]            魔数 = b'\\x00DLG'
    0x04  data_04: u16        必须为 2
    0x06  data_06: u16        1=Type1 footer(24B) / 2=Type2 footer(28B)
    0x08  total_size: u32     文件总大小
    0x0C  data_0c: u32        （在 Dearly Stars 中恰好等于 pixel_data_size）
    0x10  data_10: u32        通常为 0
    0x14  pixel_data_size: u32  像素数据大小
    0x18  palette_data_size: u32 调色板数据大小
    0x1C  footer_entry_count: u32 Footer 条目数

## Footer Entry

每个 footer entry 描述一个 sprite：
    pixels_offset: u32    像素在 pixel_data 中的字节偏移
    palette_offset: u16   调色板在 palette_data 中的字节偏移
    sprite_format: u16    Sprite Format (1/2/3/4/6, bit15=deleted)
    crop_width: u16       裁剪宽度
    crop_height: u16      裁剪高度
    render_width_id: u16  渲染宽度 ID (Type2: render_width = 8 << render_width_id)
    render_height_id: u16 渲染高度 ID
    crop_x: u16           裁剪 X 偏移
    crop_y: u16           裁剪 Y 偏移
    [Type1: join_x: i16, join_y: i16]
    [Type2: data_14: u32, join_x: i16, join_y: i16]

## Sprite Format

    1: 8bpp, 32 色,  像素 = [color:5][alpha:3], alpha 8 级
    2: 2bpp, 4 色,   纯索引, 透明通过调色板 bit15
    3: 4bpp, 16 色,  纯索引, 透明通过调色板 bit15
    4: 8bpp, 256 色, 纯索引, 透明通过调色板 bit15
    6: 8bpp, 8 色,   像素 = [color:3][alpha:5], alpha 32 级

    bit15 (0x8000) = deleted, 导出时跳过
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# GLD 魔数
GLD_MAGIC = b'\x00DLG'

# Footer 类型
FOOTER_TYPE_1 = 1  # 24 字节/entry
FOOTER_TYPE_2 = 2  # 28 字节/entry

# Sprite Format
SPRITE_FORMAT_1 = 1  # 8bpp, 32色, alpha 3bit
SPRITE_FORMAT_2 = 2  # 2bpp, 4色
SPRITE_FORMAT_3 = 3  # 4bpp, 16色
SPRITE_FORMAT_4 = 4  # 8bpp, 256色
SPRITE_FORMAT_6 = 6  # 8bpp, 8色, alpha 5bit

# deleted 标志
SPRITE_DELETED = 0x8000

# 各 format 的最大调色板色数
MAX_PALETTE_SIZE = {
    1: 32,
    2: 4,
    3: 16,
    4: 256,
    6: 8,
}

# 各 format 的位深度（bits per pixel）
BIT_DEPTH = {
    1: 8,
    2: 2,
    3: 4,
    4: 8,
    6: 8,
}

# 各 format 每字节像素数
PIXELS_PER_BYTE = {
    1: 1,
    2: 4,
    3: 2,
    4: 1,
    6: 1,
}

# 调色板颜色距离最大值（透明惩罚 + 3 通道各 31）
MAX_COLOR_DISTANCE = 100 + 31 * 3


# ----------------------------------------------------------------------
# BGR555 颜色转换
# ----------------------------------------------------------------------

def bgr555_to_rgb888(x: int) -> tuple[int, int, int]:
    """BGR555 → RGB888，含高位回填（无损转换）"""
    r5 = x & 0x1F
    g5 = (x >> 5) & 0x1F
    b5 = (x >> 10) & 0x1F
    # 5bit → 8bit：左移 3 位 + 高 2 位回填
    r = (r5 << 3) | (r5 >> 2)
    g = (g5 << 3) | (g5 >> 2)
    b = (b5 << 3) | (b5 >> 2)
    return (r, g, b)


def bgr555_is_transparent(x: int) -> bool:
    """BGR555 的 bit15 = 透明标志"""
    return ((x >> 15) & 1) != 0


def rgb888_to_bgr555(r: int, g: int, b: int, transparent: bool = False) -> int:
    """RGB888 → BGR555（有损截断）"""
    val = (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
    if transparent:
        val |= 0x8000
    return val


# ----------------------------------------------------------------------
# 像素打包/解包（2bpp / 4bpp）
# ----------------------------------------------------------------------

def unpack_2bits_le(data: bytes) -> list[int]:
    """解包 2bpp 数据（LE 顺序：低位在前）"""
    result = []
    for byte in data:
        result.extend([byte & 0x3, (byte >> 2) & 0x3, (byte >> 4) & 0x3, (byte >> 6) & 0x3])
    return result


def unpack_4bits_le(data: bytes) -> list[int]:
    """解包 4bpp 数据（LE 顺序：低位在前）"""
    result = []
    for byte in data:
        result.extend([byte & 0xF, (byte >> 4) & 0xF])
    return result


def pack_2bits_le(data: list[int], last_byte: int = 0) -> bytes:
    """打包 2bpp 数据（LE 顺序），不足 4 像素的最后一字节用 last_byte 填充"""
    result = bytearray()
    n = len(data)
    for i in range(0, n, 4):
        chunk = data[i:i + 4]
        # 不足的部分用 last_byte 的对应位填充
        b0 = chunk[0] if len(chunk) > 0 else (last_byte & 0x3)
        b1 = chunk[1] if len(chunk) > 1 else ((last_byte >> 2) & 0x3)
        b2 = chunk[2] if len(chunk) > 2 else ((last_byte >> 4) & 0x3)
        b3 = chunk[3] if len(chunk) > 3 else ((last_byte >> 6) & 0x3)
        result.append((b0 & 0x3) | ((b1 & 0x3) << 2) | ((b2 & 0x3) << 4) | ((b3 & 0x3) << 6))
    return bytes(result)


def pack_4bits_le(data: list[int], last_byte: int = 0) -> bytes:
    """打包 4bpp 数据（LE 顺序），不足 2 像素的最后一字节用 last_byte 填充"""
    result = bytearray()
    n = len(data)
    for i in range(0, n, 2):
        b0 = data[i] if i < n else (last_byte & 0xF)
        b1 = data[i + 1] if i + 1 < n else ((last_byte >> 4) & 0xF)
        result.append((b0 & 0xF) | ((b1 & 0xF) << 4))
    return bytes(result)


# ----------------------------------------------------------------------
# 调色板匹配
# ----------------------------------------------------------------------

def color_distance(r: int, g: int, b: int, is_transparent: bool, palette_color: int) -> int:
    """计算 RGB 颜色与调色板颜色的距离（曼哈顿距离 + 透明惩罚）"""
    delta_r = (r >> 3) - (palette_color & 0x1F)
    delta_g = (g >> 3) - ((palette_color >> 5) & 0x1F)
    delta_b = (b >> 3) - ((palette_color >> 10) & 0x1F)
    palette_transparent = bgr555_is_transparent(palette_color)
    delta_a = 100 if (is_transparent != palette_transparent) else 0
    return abs(delta_r) + abs(delta_g) + abs(delta_b) + delta_a


def get_color_index(r: int, g: int, b: int, is_transparent: bool, palette: list[int]) -> int:
    """在调色板中找距离最近的颜色索引"""
    min_dist = MAX_COLOR_DISTANCE
    min_idx = 0
    for idx, pc in enumerate(palette):
        dist = color_distance(r, g, b, is_transparent, pc)
        if dist == 0:
            return idx
        if dist < min_dist:
            min_dist = dist
            min_idx = idx
    return min_idx


# ----------------------------------------------------------------------
# 数据类
# ----------------------------------------------------------------------

@dataclass
class GldHeader:
    magic: bytes
    data_04: int
    data_06: int
    total_size: int
    data_0c: int
    data_10: int
    pixel_data_size: int
    palette_data_size: int
    footer_entry_count: int

    @classmethod
    def from_bytes(cls, data: bytes) -> 'GldHeader':
        magic = data[:4]
        data_04, data_06 = struct.unpack_from('<HH', data, 4)
        total_size, data_0c, data_10, pixel_data_size, palette_data_size, footer_entry_count = \
            struct.unpack_from('<6I', data, 8)
        return cls(magic, data_04, data_06, total_size, data_0c, data_10,
                   pixel_data_size, palette_data_size, footer_entry_count)

    def to_bytes(self) -> bytes:
        return struct.pack('<4sHH6I', self.magic, self.data_04, self.data_06,
                           self.total_size, self.data_0c, self.data_10,
                           self.pixel_data_size, self.palette_data_size,
                           self.footer_entry_count)


@dataclass
class GldFooterEntry:
    pixels_offset: int
    palette_offset: int
    sprite_format: int
    crop_width: int
    crop_height: int
    render_width_id: int
    render_height_id: int
    crop_x: int
    crop_y: int
    data_14: int = 0  # 仅 Type2
    join_x: int = 0
    join_y: int = 0

    @property
    def is_deleted(self) -> bool:
        return (self.sprite_format & SPRITE_DELETED) != 0

    @property
    def format_id(self) -> int:
        """去掉标志位的纯 format ID"""
        return self.sprite_format & 0xFF

    @property
    def max_palette_size(self) -> int:
        return MAX_PALETTE_SIZE.get(self.format_id, 256)


@dataclass
class GldFile:
    file_stem: str
    header: GldHeader
    pixel_data: bytearray
    palette_data: bytearray
    footer_entries: list[GldFooterEntry]

    # -- 调色板 --

    def get_palettes(self) -> dict[int, list[int]]:
        """
        按 palette_offset 分组提取调色板。

        返回 {palette_offset: [u16 color, ...]}。
        调色板边界由 footer entries 的 palette_offset 决定：
            将所有不重复的 palette_offset 排序，相邻两个 offset 之间是一个调色板，
            最后一个到 palette_data_size 之间是最后一个调色板。
        """
        # 收集未删除 sprite 的 palette_offset
        offsets = sorted(set(
            e.palette_offset for e in self.footer_entries
            if not e.is_deleted
        ))
        if not offsets:
            return {}

        # 添加结束边界
        offsets_with_end = offsets + [self.header.palette_data_size]

        palettes: dict[int, list[int]] = {}
        for i in range(len(offsets)):
            start = offsets_with_end[i]
            end = offsets_with_end[i + 1]
            raw = self.palette_data[start:end]
            # 每 2 字节一个 u16 颜色
            colors = [struct.unpack_from('<H', raw, j)[0]
                      for j in range(0, len(raw) - 1, 2)]
            palettes[offsets[i]] = colors
        return palettes

    def get_palette(self, entry: GldFooterEntry) -> list[int]:
        """获取某个 sprite 的调色板（截断到 max_palette_size）"""
        all_palettes = self.get_palettes()
        if entry.palette_offset not in all_palettes:
            return []
        pal = all_palettes[entry.palette_offset]
        return pal[:entry.max_palette_size]

    # -- render_width --

    def render_width(self, entry: GldFooterEntry) -> int:
        """计算 sprite 的渲染宽度"""
        max_x = entry.crop_x + entry.crop_width
        if self.header.data_06 == FOOTER_TYPE_1:
            # Type1: 对齐到下一个 2 的幂
            if max_x <= 0:
                return 1
            return 1 << (max_x - 1).bit_length()
        else:
            # Type2: 8 << render_width_id
            return 8 << entry.render_width_id

    # -- sprite 提取 --

    def extract_sprite_rgba(self, index: int) -> tuple[int, int, bytes]:
        """
        提取 sprite 为 RGBA 数据。

        Returns:
            (width, height, rgba_bytes)
            width = crop_width, height = crop_height
            rgba_bytes 长度 = width * height * 4
        """
        entry = self.footer_entries[index]
        if entry.is_deleted:
            # 已删除的 sprite 返回全透明
            w, h = entry.crop_width, entry.crop_height
            return (w, h, bytes(w * h * 4))

        fmt = entry.format_id
        bit_depth = BIT_DEPTH.get(fmt, 8)
        ppb = PIXELS_PER_BYTE.get(fmt, 1)  # pixels per byte

        palette = self.get_palette(entry)

        max_y = entry.crop_y + entry.crop_height
        render_width = self.render_width(entry)
        render_width_bytes = render_width * bit_depth // 8
        render_byte_count = render_width_bytes * max_y

        # 从 pixel_data 中提取该 sprite 的渲染区域
        start = entry.pixels_offset
        end = start + render_byte_count
        entry_pixel_data = bytes(self.pixel_data[start:end])

        # 解包为像素索引数组
        if bit_depth == 8:
            pixels = list(entry_pixel_data)
        elif bit_depth == 4:
            pixels = unpack_4bits_le(entry_pixel_data)
        elif bit_depth == 2:
            pixels = unpack_2bits_le(entry_pixel_data)
        else:
            pixels = list(entry_pixel_data)

        # 按 crop 区域提取并转换为 RGBA
        rgba = bytearray()
        for y in range(entry.crop_y, entry.crop_y + entry.crop_height):
            row_start = y * render_width + entry.crop_x
            row_end = row_start + entry.crop_width
            for x in range(row_start, row_end):
                if x < len(pixels):
                    pixel_val = pixels[x]
                else:
                    pixel_val = 0

                rgba.extend(self._pixel_to_rgba(pixel_val, fmt, palette))

        return (entry.crop_width, entry.crop_height, bytes(rgba))

    def _pixel_to_rgba(self, pixel_val: int, fmt: int, palette: list[int]) -> bytes:
        """将单个像素值转换为 RGBA 字节"""
        if fmt == SPRITE_FORMAT_1:
            # [color:5][alpha:3]
            color_idx = pixel_val & 0x1F
            alpha_bank = (pixel_val >> 5) & 0x7
            alpha = (alpha_bank << 5) | (alpha_bank << 2) | (alpha_bank >> 1)
            if color_idx < len(palette):
                r, g, b = bgr555_to_rgb888(palette[color_idx])
            else:
                r, g, b = 0, 0, 0
            return bytes([r, g, b, alpha])

        elif fmt in (2, 3, 4):
            # 纯索引，透明通过调色板 bit15
            if pixel_val < len(palette):
                pc = palette[pixel_val]
                r, g, b = bgr555_to_rgb888(pc)
                alpha = 0 if bgr555_is_transparent(pc) else 0xFF
            else:
                r, g, b, alpha = 0, 0, 0, 0
            return bytes([r, g, b, alpha])

        elif fmt == SPRITE_FORMAT_6:
            # [color:3][alpha:5]
            color_idx = pixel_val & 0x07
            alpha_bank = (pixel_val >> 3) & 0x1F
            alpha = (alpha_bank << 3) | (alpha_bank >> 2)
            if color_idx < len(palette):
                r, g, b = bgr555_to_rgb888(palette[color_idx])
            else:
                r, g, b = 0, 0, 0
            return bytes([r, g, b, alpha])

        else:
            return bytes([0, 0, 0, 0])

    # -- sprite 注入 --

    def inject_sprite_rgba(self, index: int, rgba_data: bytes,
                           width: int, height: int) -> None:
        """
        从 RGBA 数据注入 sprite。

        Args:
            index: sprite 索引
            rgba_data: RGBA 字节流（长度 = width * height * 4）
            width: PNG 宽度（必须 = crop_width）
            height: PNG 高度（必须 = crop_height）
        """
        entry = self.footer_entries[index]
        if entry.is_deleted:
            return

        if width != entry.crop_width or height != entry.crop_height:
            raise ValueError(
                f"PNG 尺寸不匹配：期望 {entry.crop_width}x{entry.crop_height}，"
                f"实际 {width}x{height}"
            )

        fmt = entry.format_id
        ppb = PIXELS_PER_BYTE.get(fmt, 1)
        palette = self.get_palette(entry)

        # 将 RGBA 转换为像素值数组
        pixel_values: list[int] = []
        for i in range(0, len(rgba_data), 4):
            r, g, b, a = rgba_data[i], rgba_data[i + 1], rgba_data[i + 2], rgba_data[i + 3]
            pixel_values.append(self._rgba_to_pixel(r, g, b, a, fmt, palette))

        # 写回 pixel_data
        render_width = self.render_width(entry)
        bit_depth = BIT_DEPTH.get(fmt, 8)
        render_width_bytes = render_width * bit_depth // 8

        for y in range(entry.crop_height):
            src_start = y * entry.crop_width

            # 目标行起始字节：pixels_offset + (crop_y + y) * render_width_bytes
            dest_row_start = entry.pixels_offset + (entry.crop_y + y) * render_width_bytes

            if ppb == 1:
                # 8bpp (Format 1/4/6)：1 像素 = 1 字节，crop_x 直接就是字节偏移
                dest_byte_start = dest_row_start + entry.crop_x
                self.pixel_data[dest_byte_start:dest_byte_start + entry.crop_width] = \
                    bytes(pixel_values[src_start:src_start + entry.crop_width])
            else:
                # 2bpp/4bpp (Format 2/3)：1 字节含多个像素。
                # crop_x 可能不是 ppb 的倍数（半字节偏移），必须 read-modify-write：
                #   1. 读取整行字节 → 解包为像素数组
                #   2. 修改 crop_x ~ crop_x+crop_width 范围内的像素
                #   3. 重新打包写回整行
                # 这样头部（crop_x 之前）和尾部（crop_x+crop_width 之后）的不完整
                # nibble 都会被原样保留，无需特殊处理 last_byte。
                # render_width 一定是 ppb 的倍数（审核已验证），整行可完整打包。
                row_byte_end = dest_row_start + render_width_bytes
                row_bytes = bytes(self.pixel_data[dest_row_start:row_byte_end])

                if bit_depth == 4:
                    row_pixels = unpack_4bits_le(row_bytes)
                else:  # bit_depth == 2
                    row_pixels = unpack_2bits_le(row_bytes)

                # 确保 row_pixels 长度 = render_width（ppb 倍数）
                while len(row_pixels) < render_width:
                    row_pixels.append(0)
                if len(row_pixels) > render_width:
                    row_pixels = row_pixels[:render_width]

                # 仅修改 crop 区域内的像素，其余像素保留旧值
                for i in range(entry.crop_width):
                    tx = entry.crop_x + i
                    if 0 <= tx < len(row_pixels):
                        row_pixels[tx] = pixel_values[src_start + i]

                # 重新打包写回（整行完整，last_byte=0 即可）
                if bit_depth == 4:
                    packed = pack_4bits_le(row_pixels, 0)
                else:  # bit_depth == 2
                    packed = pack_2bits_le(row_pixels, 0)

                self.pixel_data[dest_row_start:dest_row_start + len(packed)] = packed

    def _rgba_to_pixel(self, r: int, g: int, b: int, a: int,
                       fmt: int, palette: list[int]) -> int:
        """将 RGBA 值转换为 GLD 像素值"""
        is_transparent = a < 128  # alpha < 128 视为透明（仅对 format 2/3/4 有效）

        if fmt == SPRITE_FORMAT_1:
            # [color:5][alpha:3]
            # Format 1 的 alpha 编码在像素值中（3bit），与调色板透明标志无关。
            # 调色板匹配时必须忽略 bit15，否则会错误匹配到透明色。
            palette_masked = [c & 0x7FFF for c in palette]
            color_idx = get_color_index(r, g, b, False, palette_masked)
            # alpha: 0-255 → 0-7 (3bit)，直接量化（不走透明阈值）
            alpha_bank = (a >> 5) & 0x7
            return (color_idx & 0x1F) | (alpha_bank << 5)

        elif fmt in (2, 3, 4):
            # 纯索引，透明通过调色板 bit15
            return get_color_index(r, g, b, is_transparent, palette)

        elif fmt == SPRITE_FORMAT_6:
            # [color:3][alpha:5]
            # Format 6 的 alpha 编码在像素值中（5bit），与调色板透明标志无关。
            palette_masked = [c & 0x7FFF for c in palette]
            color_idx = get_color_index(r, g, b, False, palette_masked)
            # alpha: 0-255 → 0-31 (5bit)，直接量化
            alpha_bank = (a >> 3) & 0x1F
            return (color_idx & 0x07) | (alpha_bank << 3)

        else:
            return 0

    # -- 重建 GLD --

    def to_bytes(self) -> bytes:
        """将 GLD 序列化为字节流"""
        result = bytearray()
        result.extend(self.header.to_bytes())
        result.extend(self.pixel_data)
        result.extend(self.palette_data)

        # 写入 footer entries
        is_type2 = (self.header.data_06 == FOOTER_TYPE_2)
        for entry in self.footer_entries:
            if is_type2:
                # Type2: 28 bytes = I(4) + 8×H(16) + I(4) + 2×h(4)
                result.extend(struct.pack('<IHHHHHHHHIhh',
                    entry.pixels_offset, entry.palette_offset, entry.sprite_format,
                    entry.crop_width, entry.crop_height,
                    entry.render_width_id, entry.render_height_id,
                    entry.crop_x, entry.crop_y,
                    entry.data_14, entry.join_x, entry.join_y))
            else:
                # Type1: 24 bytes = I(4) + 8×H(16) + 2×h(4)
                result.extend(struct.pack('<IHHHHHHHHhh',
                    entry.pixels_offset, entry.palette_offset, entry.sprite_format,
                    entry.crop_width, entry.crop_height,
                    entry.render_width_id, entry.render_height_id,
                    entry.crop_x, entry.crop_y,
                    entry.join_x, entry.join_y))

        # 更新 header 中的 total_size
        struct.pack_into('<I', result, 8, len(result))
        return bytes(result)


# ----------------------------------------------------------------------
# 解析/写入
# ----------------------------------------------------------------------

def parse_gld(path: Path | str) -> GldFile:
    """解析 GLD 文件"""
    path = Path(path)
    with open(path, 'rb') as f:
        data = f.read()

    if len(data) < 32:
        raise ValueError(f"{path.name} 太小（{len(data)} 字节），不是有效的 GLD 文件")

    header = GldHeader.from_bytes(data[:32])

    if header.magic != GLD_MAGIC:
        raise ValueError(f"{path.name} 魔数错误: {header.magic!r}")
    if header.data_04 != 2:
        raise ValueError(f"{path.name} header.data_04 = {header.data_04}（期望 2）")

    offset = 32
    pixel_data = bytearray(data[offset:offset + header.pixel_data_size])
    offset += header.pixel_data_size

    palette_data = bytearray(data[offset:offset + header.palette_data_size])
    offset += header.palette_data_size

    footer_entries: list[GldFooterEntry] = []
    is_type2 = (header.data_06 == FOOTER_TYPE_2)
    entry_size = 28 if is_type2 else 24

    for i in range(header.footer_entry_count):
        entry_data = data[offset:offset + entry_size]
        if len(entry_data) < entry_size:
            break

        (pixels_offset, palette_offset, sprite_format,
         crop_width, crop_height, render_width_id, render_height_id,
         crop_x, crop_y) = struct.unpack_from('<IHHHHHHHH', entry_data, 0)

        if is_type2:
            data_14, join_x, join_y = struct.unpack_from('<Ihh', entry_data, 20)
        else:
            data_14 = 0
            join_x, join_y = struct.unpack_from('<hh', entry_data, 20)

        footer_entries.append(GldFooterEntry(
            pixels_offset, palette_offset, sprite_format,
            crop_width, crop_height, render_width_id, render_height_id,
            crop_x, crop_y, data_14, join_x, join_y
        ))
        offset += entry_size

    return GldFile(
        file_stem=path.stem,
        header=header,
        pixel_data=pixel_data,
        palette_data=palette_data,
        footer_entries=footer_entries,
    )


def write_gld(gld: GldFile, path: Path | str) -> None:
    """写入 GLD 文件"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(gld.to_bytes())
