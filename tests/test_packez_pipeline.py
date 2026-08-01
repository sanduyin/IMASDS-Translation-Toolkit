from __future__ import annotations

from pathlib import Path

from src.packez.ezp_pack import build_pack, extract_pack
from src.packez.ezt_parser import EZT_HEADER_SIZE, read_idx
from src.stage1_unpack import extract_archive
from src.stage5_build_rom import repack_archive


def _make_pack(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    (source / "DIR").mkdir(parents=True)
    (source / ".index").write_text(
        "0001\nDIR_BEGIN\n    inside.BIN\nDIR_END\noutside.DAT\n",
        encoding="utf-8",
    )
    (source / "DIR" / "0001_inside.BIN").write_bytes(b"first-entry")
    (source / "0003_outside.DAT").write_bytes(b"outside" * 128)

    ezt = tmp_path / "F_TEST.IDX"
    ezp = tmp_path / "F_TEST.BIN"
    build_pack(source, ezt, ezp)
    return ezt, ezp


def test_packez_entries_start_immediately_after_16_byte_header(tmp_path: Path) -> None:
    ezt, ezp = _make_pack(tmp_path)
    raw_idx = ezt.read_bytes()
    header, entries = read_idx(raw_idx)

    assert header.entry_count == 4
    assert raw_idx[EZT_HEADER_SIZE : EZT_HEADER_SIZE + 12] == entries[0].to_bytes()

    extracted = tmp_path / "extracted"
    extract_pack(ezt, ezp, extracted)
    assert (extracted / "DIR" / "0001_inside.BIN").read_bytes() == b"first-entry"
    assert (extracted / "0003_outside.DAT").read_bytes() == b"outside" * 128


def test_repack_preserves_entry_numbers_and_only_replaces_known_index(
    tmp_path: Path,
) -> None:
    original_ezt, original_ezp = _make_pack(tmp_path)
    patch_dir = tmp_path / "patch"
    patch_dir.mkdir()
    replacement = b"translated-image" * 64
    # Existing translation work uses the historical number (raw entry - 1)
    # because entry 0000 is the archive's outer DIR_BEGIN marker.
    (patch_dir / "0002_outside.DAT").write_bytes(replacement)

    output_ezt = tmp_path / "rebuilt.IDX"
    output_ezp = tmp_path / "rebuilt.BIN"
    count = repack_archive(
        original_ezt,
        original_ezp,
        [patch_dir],
        output_ezt,
        output_ezp,
        tmp_path / "repack-work",
    )

    assert count == 1
    original_header, original_entries = read_idx(original_ezt.read_bytes())
    rebuilt_header, rebuilt_entries = read_idx(output_ezt.read_bytes())
    assert rebuilt_header.something1 == original_header.something1
    assert len(rebuilt_entries) == len(original_entries) == 4

    extracted = tmp_path / "verified"
    extract_pack(output_ezt, output_ezp, extracted)
    assert (extracted / "DIR" / "0001_inside.BIN").read_bytes() == b"first-entry"
    assert (extracted / "0003_outside.DAT").read_bytes() == replacement


def test_stage1_keeps_existing_user_facing_numbers(tmp_path: Path) -> None:
    original_ezt, original_ezp = _make_pack(tmp_path)
    extracted = tmp_path / "stage1-output"

    extract_archive(original_ezt, original_ezp, extracted)

    assert (extracted / "0000_inside.BIN").read_bytes() == b"first-entry"
    assert (extracted / "0002_outside.DAT").read_bytes() == b"outside" * 128
    assert not (extracted / "0001_inside.BIN").exists()


def test_repack_rejects_patch_number_not_present_in_original(tmp_path: Path) -> None:
    original_ezt, original_ezp = _make_pack(tmp_path)
    patch_dir = tmp_path / "unknown-patch"
    patch_dir.mkdir()
    (patch_dir / "9999_unknown.DAT").write_bytes(b"bad")

    try:
        repack_archive(
            original_ezt,
            original_ezp,
            [patch_dir],
            tmp_path / "rebuilt.IDX",
            tmp_path / "rebuilt.BIN",
            tmp_path / "repack-work",
        )
    except RuntimeError as exc:
        assert "9999" in str(exc)
    else:
        raise AssertionError("unknown PackEZ index was accepted")
