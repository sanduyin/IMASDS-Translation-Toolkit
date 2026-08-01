from __future__ import annotations

import sys
from pathlib import Path

import pytest

import main as toolkit_main
import src.stage3_build_font as font_stage
from config import TARGET_PACKS
from src.io.csv_handler import read_translation_table, write_mail_csv, write_text_csv
from src.ndstool.overlay import OverlayTableEntry
from src.ndstool.rom_builder import OverlayState, RomImage
from src.stage4_import_bg import extract_tiles_from_bmp
from src.utils.lesvoice import _merge_private_assignments
from src.utils.text_encoder import TextEncodingError, text_to_bytes


def test_top_level_command_is_not_reparsed_by_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["main.py", "font", "--preview"])
    seen: list[str] = []

    toolkit_main.invoke_stage(lambda: seen.extend(sys.argv))

    assert seen == ["main.py"]
    assert sys.argv == ["main.py", "font", "--preview"]


def test_csv_blank_translation_does_not_shift_following_bbq_index(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "SCN.csv"
    write_text_csv(
        csv_path,
        [
            {"Filename": "0001_TEST.BBQ", "Text": "a", "Translated_Text": "首"},
            {"Filename": "0001_TEST.BBQ", "Text": "b", "Translated_Text": ""},
            {"Filename": "0001_TEST.BBQ", "Text": "c", "Translated_Text": "末"},
            {"Filename": "0001_TEST.BBQ", "Text": "d", "Translated_Text": "{EMPTY}"},
        ],
    )

    table = read_translation_table(csv_path)
    assert table == {"0001_TEST.BBQ": {0: "首", 2: "末", 3: ""}}


def test_mail_font_scan_includes_both_translated_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scn_dir = tmp_path / "SCN"
    tbl_dir = tmp_path / "TBL"
    scn_dir.mkdir()
    tbl_dir.mkdir()
    write_text_csv(
        scn_dir / "SCN.csv",
        [{"Filename": "x.BBQ", "Text": "原", "Translated_Text": "常"}],
    )
    write_mail_csv(
        tbl_dir / "TBL_ML.csv",
        [{
            "Filename": "mail.BBQ",
            "Mail": "原邮",
            "Translated Mail": "邮",
            "Reply": "原回",
            "Translated Reply": "回",
        }],
    )
    monkeypatch.setattr(font_stage, "CSV_SCN_DIR", scn_dir)
    monkeypatch.setattr(font_stage, "CSV_TBL_DIR", tbl_dir)
    monkeypatch.setattr(font_stage, "CSV_ARM9_FILE", tmp_path / "missing.csv")

    chars: set[str] = set()
    font_stage._scan_csv_chars(chars)

    assert {"常", "邮", "回"} <= chars
    assert "原" not in chars


def test_unknown_text_character_fails_instead_of_becoming_question_mark() -> None:
    with pytest.raises(TextEncodingError):
        text_to_bytes("未", {"中": 0xF040})


def test_bg_import_preserves_unused_tiles_and_rejects_conflicting_shared_tile() -> None:
    original = [[9] * 64, [7] * 64]
    result = extract_tiles_from_bmp(
        bytes([1] * 64),
        8,
        8,
        [(0, False, False, 0)],
        1,
        1,
        original,
        8,
    )
    assert result[0] == [1] * 64
    assert result[1] == [7] * 64

    conflicting_pixels = b"".join(bytes([0] * 8 + [1] * 8) for _ in range(8))
    with pytest.raises(ValueError, match="共享 tile 0"):
        extract_tiles_from_bmp(
            conflicting_pixels,
            16,
            8,
            [(0, False, False, 0), (0, False, False, 0)],
            2,
            1,
            original,
            8,
        )


def test_obj_pack_is_in_final_repack_allowlist() -> None:
    assert "OBJ" in TARGET_PACKS


def test_lyric_glyph_ids_and_private_codes_only_append() -> None:
    previous = [
        {
            "char": "中",
            "unicode": ord("中"),
            "encoding": "private",
            "key": 0xF040,
            "key_hex": "0xF040",
            "glyph_id": 0x51,
        },
        {
            "char": "文",
            "unicode": ord("文"),
            "encoding": "private",
            "key": 0xF041,
            "key_hex": "0xF041",
            "glyph_id": 0x52,
        },
    ]

    merged = _merge_private_assignments(["中", "新"], previous)

    # Removed text does not reclaim an artist-facing glyph; newly encountered
    # text is appended after the locked prefix.
    assert [(item["char"], item["key"], item["glyph_id"]) for item in merged] == [
        ("中", 0xF040, 0x51),
        ("文", 0xF041, 0x52),
        ("新", 0xF042, 0x53),
    ]


def test_overlay_id_collision_requires_processor() -> None:
    rom = object.__new__(RomImage)
    rom.arm9_overlays = [
        OverlayState(OverlayTableEntry(id=1), b"arm9", file_id=10)
    ]
    rom.arm7_overlays = [
        OverlayState(OverlayTableEntry(id=1), b"arm7", file_id=11)
    ]
    rom._overlays_dirty = {}

    with pytest.raises(ValueError, match="同时存在"):
        rom.get_overlay(1)
    assert rom.get_overlay(1, "arm9") == b"arm9"
    assert rom.get_overlay(1, "arm7") == b"arm7"

    rom.set_overlay(1, b"changed", "arm7")
    assert rom.get_overlay(1, "arm7") == b"changed"
    assert rom._overlays_dirty[("arm7", 1)] is True
