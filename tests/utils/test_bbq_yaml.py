# tests/utils/test_bbq_yaml.py
"""bbq_yaml 模块单元测试。

测试 BBQ ↔ YAML 互转的往返一致性，以及各 Type 数据结构的序列化。
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from src.utils.bbq_yaml import (
    BBQ_HEADER_MAGIC,
    BBQ_MAGIC2,
    Type2Data,
    Type3Data,
    Type5Data,
    Command,
    Type6Data,
    Type7Data,
    BbqFile,
    bbq_to_yaml,
    yaml_to_bbq,
    read_bbq,
    write_bbq,
    bbq_to_yaml_lines,
    bbq_from_yaml_lines,
)


# ----------------------------------------------------------------------
# Type2Data
# ----------------------------------------------------------------------

class TestType2Data:
    def test_to_bytes_round_trip(self) -> None:
        original = Type2Data(data=[1, 2, 3, 4, 5, 6, 7])
        encoded = original.to_bytes()
        assert len(encoded) == 28
        decoded = Type2Data.read(encoded, 28)
        assert decoded.data == [1, 2, 3, 4, 5, 6, 7]

    def test_size_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="size mismatch"):
            Type2Data.read(b"\x00" * 28, 27)

    def test_must_have_7_elements(self) -> None:
        # to_bytes 用 struct.pack('<7I', *self.data)，故 data 必须为 7 元素
        item = Type2Data(data=[1, 2, 3, 4, 5, 6, 7])
        assert len(item.data) == 7


# ----------------------------------------------------------------------
# Type3Data
# ----------------------------------------------------------------------

class TestType3Data:
    def test_round_trip_without_children(self) -> None:
        original = Type3Data(data=[10, 20, 30, 40, 50, 60, 70], children=[])
        encoded = original.to_bytes()
        # 7*4 + 4 (child_count=0) = 32 字节
        assert len(encoded) == 32
        decoded = Type3Data.read(encoded, 32)
        assert decoded.data == [10, 20, 30, 40, 50, 60, 70]
        assert decoded.children == []

    def test_round_trip_with_children(self) -> None:
        children = [[1, 2, 3, 4], [5, 6, 7, 8]]
        original = Type3Data(data=[1, 2, 3, 4, 5, 6, 7], children=children)
        encoded = original.to_bytes()
        # 28 + 4 + 2*16 = 64 字节
        assert len(encoded) == 64
        decoded = Type3Data.read(encoded, 64)
        assert decoded.data == [1, 2, 3, 4, 5, 6, 7]
        assert decoded.children == children


# ----------------------------------------------------------------------
# Type5Data
# ----------------------------------------------------------------------

class TestType5Data:
    def test_round_trip(self) -> None:
        original = Type5Data(bytes_data=bytearray(b"\x01\x02\x03\x04\x05"))
        encoded = original.to_bytes()
        decoded = Type5Data.read(encoded, 5)
        assert bytes(decoded.bytes_data) == b"\x01\x02\x03\x04\x05"

    def test_zero_size_raises(self) -> None:
        with pytest.raises(ValueError, match="size is 0"):
            Type5Data.read(b"", 0)


# ----------------------------------------------------------------------
# Command / Type6Data
# ----------------------------------------------------------------------

class TestCommand:
    def test_round_trip_no_args(self) -> None:
        cmd = Command(code1=1, code2=2, arg_count=0, place=100, args=[])
        encoded = cmd.to_bytes()
        assert len(encoded) == 8  # 1+1+2+4
        decoded, consumed = Command.read(encoded, 0)
        assert decoded.code1 == 1
        assert decoded.code2 == 2
        assert decoded.arg_count == 0
        assert decoded.place == 100
        assert decoded.args == []
        assert consumed == 8

    def test_round_trip_with_args(self) -> None:
        cmd = Command(code1=0xAA, code2=0xBB, arg_count=3, place=0x1000,
                      args=[0x11111111, 0x22222222, 0x33333333])
        encoded = cmd.to_bytes()
        # 8 + 3*4 = 20
        assert len(encoded) == 20
        decoded, consumed = Command.read(encoded, 0)
        assert decoded.code1 == 0xAA
        assert decoded.code2 == 0xBB
        assert decoded.arg_count == 3
        assert decoded.place == 0x1000
        assert decoded.args == [0x11111111, 0x22222222, 0x33333333]
        assert consumed == 20

    def test_size_property(self) -> None:
        cmd = Command(code1=0, code2=0, arg_count=2, place=0, args=[1, 2])
        # 8 + 2*4 = 16
        assert cmd.size == 16


class TestType6Data:
    def test_round_trip_multiple_commands(self) -> None:
        cmds = [
            Command(1, 2, 0, 100, []),
            Command(3, 4, 1, 200, [999]),
            Command(5, 6, 2, 300, [10, 20]),
        ]
        original = Type6Data(commands=cmds)
        encoded = original.to_bytes()
        decoded = Type6Data.read(encoded, len(encoded))
        assert len(decoded.commands) == 3
        assert decoded.commands[0].code1 == 1
        assert decoded.commands[1].args == [999]
        assert decoded.commands[2].args == [10, 20]


# ----------------------------------------------------------------------
# Type7Data
# ----------------------------------------------------------------------

class TestType7Data:
    def test_round_trip_simple_string(self) -> None:
        original = Type7Data(text="Hello")
        encoded = original.to_bytes()
        # "Hello" + NUL = 6 字节
        assert encoded == b"Hello\x00"
        decoded = Type7Data.read(encoded, len(encoded))
        assert decoded.text == "Hello"

    def test_round_trip_empty_string(self) -> None:
        original = Type7Data(text="")
        encoded = original.to_bytes()
        assert encoded == b"\x00"
        decoded = Type7Data.read(encoded, 1)
        assert decoded.text == ""

    def test_round_trip_multibyte(self) -> None:
        text = "こんにちは"
        original = Type7Data(text=text)
        encoded = original.to_bytes()
        # cp932 编码 + NUL
        assert encoded == text.encode("cp932") + b"\x00"
        decoded = Type7Data.read(encoded, len(encoded))
        assert decoded.text == text


# ----------------------------------------------------------------------
# BbqFile 完整往返
# ----------------------------------------------------------------------

class TestBbqFileRoundTrip:
    def test_minimal_bbq_with_only_type7(self) -> None:
        bbq = BbqFile(
            datetime=0x12345678,
            type7data=[Type7Data(text="Hello"), Type7Data(text="World")],
            footer=[1, 2, 3, 4, 5, 6],
        )
        encoded = bbq.to_bytes()
        decoded = BbqFile.read_bbq(encoded)
        assert decoded.datetime == 0x12345678
        assert decoded.type7data is not None
        assert len(decoded.type7data) == 2
        assert decoded.type7data[0].text == "Hello"
        assert decoded.type7data[1].text == "World"
        assert decoded.footer == [1, 2, 3, 4, 5, 6]

    def test_bbq_with_all_types(self) -> None:
        bbq = BbqFile(
            datetime=0xDEADBEEF,
            type2data=[Type2Data(data=[1, 2, 3, 4, 5, 6, 7])],
            type3data=[Type3Data(data=[10, 20, 30, 40, 50, 60, 70], children=[[1, 2, 3, 4]])],
            type5data=[Type5Data(bytes_data=bytearray(b"\xAA\xBB\xCC\xDD"))],
            type6data=[Type6Data(commands=[Command(1, 2, 0, 0, [])])],
            type7data=[Type7Data(text="Test")],
            footer=[0, 0, 0, 0, 0, 0],
        )
        encoded = bbq.to_bytes()
        decoded = BbqFile.read_bbq(encoded)
        assert decoded.datetime == 0xDEADBEEF
        assert decoded.type2data is not None
        assert decoded.type2data[0].data == [1, 2, 3, 4, 5, 6, 7]
        assert decoded.type3data is not None
        assert decoded.type3data[0].children == [[1, 2, 3, 4]]
        assert decoded.type5data is not None
        assert bytes(decoded.type5data[0].bytes_data) == b"\xAA\xBB\xCC\xDD"
        assert decoded.type6data is not None
        assert decoded.type6data[0].commands[0].code1 == 1
        assert decoded.type7data is not None
        assert decoded.type7data[0].text == "Test"
        assert decoded.footer == [0, 0, 0, 0, 0, 0]

    def test_to_bytes_starts_with_magic(self) -> None:
        bbq = BbqFile(datetime=0)
        encoded = bbq.to_bytes()
        assert encoded[:8] == BBQ_HEADER_MAGIC


# ----------------------------------------------------------------------
# YAML 往返
# ----------------------------------------------------------------------

class TestYamlRoundTrip:
    def test_yaml_lines_round_trip(self) -> None:
        bbq = BbqFile(
            datetime=0x11223344,
            type7data=[Type7Data(text="Hello"), Type7Data(text="世界")],
            footer=[10, 20, 30, 40, 50, 60],
        )
        yaml_lines = bbq_to_yaml_lines(bbq)
        restored = bbq_from_yaml_lines(yaml_lines)
        assert restored.datetime == 0x11223344
        assert restored.type7data is not None
        assert restored.type7data[0].text == "Hello"
        assert restored.type7data[1].text == "世界"
        assert restored.footer == [10, 20, 30, 40, 50, 60]

    def test_file_based_yaml_round_trip(self, tmp_path: Path) -> None:
        bbq = BbqFile(
            datetime=0xCAFEF00D,
            type2data=[Type2Data(data=[1, 2, 3, 4, 5, 6, 7])],
            type7data=[Type7Data(text="Test")],
            footer=[1, 2, 3, 4, 5, 6],
        )
        bbq_path = tmp_path / "test.bbq"
        yaml_path = tmp_path / "test.yaml"

        write_bbq(bbq, bbq_path)
        bbq_to_yaml(bbq_path, yaml_path)

        # 读回 BBQ
        read_bbq_obj = read_bbq(bbq_path)
        assert read_bbq_obj.type7data is not None
        assert read_bbq_obj.type7data[0].text == "Test"

        # YAML → BBQ
        yaml_to_bbq(yaml_path, bbq_path.with_suffix(".restored.bbq"))

        # 读取还原的 BBQ
        restored = read_bbq(bbq_path.with_suffix(".restored.bbq"))
        assert restored.datetime == 0xCAFEF00D
        assert restored.type2data is not None
        assert restored.type2data[0].data == [1, 2, 3, 4, 5, 6, 7]
        assert restored.type7data is not None
        assert restored.type7data[0].text == "Test"
        assert restored.footer == [1, 2, 3, 4, 5, 6]

    def test_byte_level_round_trip(self, tmp_path: Path) -> None:
        # to_bytes → read_bbq → to_bytes 应逐字节一致
        bbq = BbqFile(
            datetime=0xABCDEF01,
            type7data=[Type7Data(text="A"), Type7Data(text="B"), Type7Data(text="")],
            footer=[0, 0, 0, 0, 0, 0],
        )
        encoded1 = bbq.to_bytes()
        restored = BbqFile.read_bbq(encoded1)
        encoded2 = restored.to_bytes()
        assert encoded1 == encoded2
