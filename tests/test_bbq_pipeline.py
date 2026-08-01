from __future__ import annotations

import struct
from pathlib import Path

import pandas as pd
import pytest

from src.stage4_inject_text import rebuild_bbq_file
from src.utils.bbq_format import (
    BBQ_ENTRY_SIZE,
    BBQ_HEADER_SIZE,
    RawBbqFile,
    RawBbqSection,
    parse_raw_bbq,
    replace_type7_strings,
    serialize_raw_bbq,
)
from src.utils.lesvoice import _inject_bbq_file
from src.utils.text_encoder import TextEncodingError


def _sample_bbq() -> bytes:
    return serialize_raw_bbq(
        RawBbqFile(
            datetime=0x12345678,
            sections=(
                RawBbqSection(data_type=2, offsets=(0,), data=b"\x01\x02\x03\x04"),
                RawBbqSection(data_type=7, offsets=(0, 2), data=b"A\x00B\x00"),
            ),
            footer=(1, 2, 3, 4, 5, 6),
            trailing_data=b"TAIL",
        )
    )


def _type7_section(data: bytes) -> RawBbqSection:
    return next(section for section in parse_raw_bbq(data).sections if section.data_type == 7)


def test_type7_rebuild_preserves_footer_order_and_valid_data_size() -> None:
    original = _sample_bbq()
    rebuilt = replace_type7_strings(original, {1: b"\xF0\x40\xF0\x41"})
    parsed = parse_raw_bbq(rebuilt)
    type7 = _type7_section(rebuilt)

    assert parsed.footer == (1, 2, 3, 4, 5, 6)
    assert parsed.trailing_data == b"TAIL"
    assert type7.offsets == (0, 2)
    assert type7.data.startswith(b"A\x00\xF0\x40\xF0\x41\x00")

    type7_entry = BBQ_HEADER_SIZE + BBQ_ENTRY_SIZE
    stored_data_size = struct.unpack_from("<I", rebuilt, type7_entry + 16)[0]
    assert stored_data_size == len(type7.data)


def test_type7_rebuild_accepts_shared_string_pointers() -> None:
    source = serialize_raw_bbq(
        RawBbqFile(
            datetime=1,
            sections=(RawBbqSection(7, (0, 0), b"same\x00\x00\x00\x00"),),
            footer=(0, 0, 0, 0, 0, 0),
        )
    )

    rebuilt = replace_type7_strings(source, {1: b"changed"})
    section = _type7_section(rebuilt)

    assert section.offsets == (0, 5)
    assert section.data.startswith(b"same\x00changed\x00")


def test_generic_bbq_injector_is_all_or_nothing_for_encoding_errors(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.BBQ"
    output = tmp_path / "output.BBQ"
    source.write_bytes(_sample_bbq())

    rebuild_bbq_file(source, output, {1: "中"}, {"中": 0xF040})
    assert _type7_section(output.read_bytes()).data.startswith(b"A\x00\xF0\x40\x00")

    output.unlink()
    with pytest.raises(TextEncodingError):
        rebuild_bbq_file(source, output, {1: "未"}, {"中": 0xF040})
    assert not output.exists()


def test_lyric_injector_uses_same_lossless_bbq_serializer(tmp_path: Path) -> None:
    source = tmp_path / "0022_LESVOICETABLE_HEL.BBQ"
    source.write_bytes(_sample_bbq())
    frame = pd.DataFrame(
        [{"Index": 1, "Original_Text": "B", "Translated_Text": "中"}]
    )

    rebuilt, info = _inject_bbq_file(source, frame, {"中": 0xF040})
    parsed = parse_raw_bbq(rebuilt)

    assert info["injected"] == 1
    assert parsed.footer == (1, 2, 3, 4, 5, 6)
    assert parsed.trailing_data == b"TAIL"
    assert _type7_section(rebuilt).data.startswith(b"A\x00\xF0\x40\x00")
