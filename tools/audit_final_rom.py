#!/usr/bin/env python3
"""Audit an IMASDS final ROM without extracting or writing game content.

The report contains only structure, sizes, hashes, patch states, and errors.
Python 3.10+; DSi crypto verification uses the toolkit's pycryptodome dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from src.ndstool.dsi_builder import verify_dsi_integrity
    DSI_CRYPTO_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # report a precise audit error instead of crashing at import time
    verify_dsi_integrity = None  # type: ignore[assignment]
    DSI_CRYPTO_IMPORT_ERROR = exc


ARM9_COMPRESSED_END_PTR_OFFSET = 0x0FC4
Y9_ENTRY_SIZE = 0x20
MAX_REASONABLE_BLZ_EXTEND = 0x02000000

PATCH_SITES = (
    (0x3D28C, bytes.fromhex("0250D7E5"), bytes.fromhex("B250D7E1"), "agl_v3_size_u16"),
    (0x3D66C, bytes.fromhex("0260D5E5"), bytes.fromhex("B260D5E1"), "agl_v3_loop_u16"),
    (0x62608, bytes.fromhex("960400E0"), bytes.fromhex("160CA0E3"), "legacy_range_cache_hi"),
    (0x6260C, bytes.fromhex("101080E2"), bytes.fromhex("F01080E2"), "legacy_range_cache_lo"),
)


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    context: dict[str, Any]


class Result:
    def __init__(self, rom_path: Path, rom_data: bytes) -> None:
        self.metadata: dict[str, Any] = {
            "rom_path": str(rom_path),
            "rom_size": len(rom_data),
            "rom_sha256": hashlib.sha256(rom_data).hexdigest(),
        }
        self.issues: list[Issue] = []

    def add(self, severity: str, code: str, message: str, **context: Any) -> None:
        self.issues.append(Issue(severity, code, message, context))

    @property
    def error_count(self) -> int:
        return sum(i.severity == "ERROR" for i in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(i.severity == "WARNING" for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata,
            "issues": [asdict(i) for i in self.issues],
            "summary": {
                "errors": self.error_count,
                "warnings": self.warning_count,
                "status": "FAIL" if self.error_count else "PASS_WITH_WARNINGS" if self.warning_count else "PASS",
            },
        }


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def crc16_nds(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc >> 1) ^ 0xA001) if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def in_range(data: bytes, offset: int, size: int) -> bool:
    return 0 <= offset <= len(data) and 0 <= size <= len(data) - offset


def parse_blz_footer(data: bytes) -> dict[str, int] | None:
    if len(data) < 8:
        return None
    compressed_and_header_size = data[-8] | (data[-7] << 8) | (data[-6] << 16)
    header_size = data[-5]
    extend_size = u32(data, len(data) - 4)
    if not (8 <= header_size <= 11):
        return None
    if not (header_size <= compressed_and_header_size <= len(data)):
        return None
    if extend_size > MAX_REASONABLE_BLZ_EXTEND:
        return None
    return {
        "compressed_and_header_size": compressed_and_header_size,
        "header_size": header_size,
        "extend_size": extend_size,
    }


def blz_decompress(data: bytes) -> bytes:
    footer = parse_blz_footer(data)
    if footer is None:
        raise ValueError("no valid BLZ footer")

    in_length = len(data)
    compressed_and_header_size = footer["compressed_and_header_size"]
    header_size = footer["header_size"]
    extend_size = footer["extend_size"]
    compressed_index = compressed_and_header_size - header_size
    decompressed_index = compressed_and_header_size + extend_size
    out = bytearray(data)
    out.extend(b"\x00" * extend_size)
    start = in_length - compressed_and_header_size

    while True:
        compressed_index -= 1
        if compressed_index < 0:
            raise ValueError("BLZ flag index underflow")
        flags = out[start + compressed_index]
        for _ in range(8):
            if flags & 0x80:
                compressed_index -= 2
                if compressed_index < 0:
                    raise ValueError("BLZ reference token underflow")
                ref0 = out[start + compressed_index]
                ref1 = out[start + compressed_index + 1]
                length = (ref1 >> 4) + 3
                displacement = ((ref1 & 0x0F) << 8) + ref0 + 3
                decompressed_index -= length
                if decompressed_index < 0:
                    raise ValueError("BLZ output index underflow")
                dst = start + decompressed_index
                src = dst + displacement
                if src + length > len(out):
                    raise ValueError("BLZ reference exceeds output")
                for i in range(length - 1, -1, -1):
                    out[dst + i] = out[src + i]
            else:
                compressed_index -= 1
                decompressed_index -= 1
                if compressed_index < 0 or decompressed_index < 0:
                    raise ValueError("BLZ literal index underflow")
                out[start + decompressed_index] = out[start + compressed_index]

            flags = (flags << 1) & 0xFF
            if decompressed_index == 0:
                return bytes(out)
            if decompressed_index < 0:
                raise ValueError("BLZ output index skipped zero")


def classify_patch_sites(arm9: bytes, result: Result, allow_unpatched: bool) -> dict[str, str]:
    states: dict[str, str] = {}
    for offset, original, target, name in PATCH_SITES:
        if not in_range(arm9, offset, 4):
            states[name] = "out_of_range"
            result.add("ERROR", "ARM9_PATCH_RANGE", f"ARM9 is too short for patch site {name}", offset=hex(offset))
            continue
        found = arm9[offset : offset + 4]
        if found == target:
            states[name] = "patched"
        elif found == original:
            states[name] = "unpatched"
            severity = "INFO" if allow_unpatched else "ERROR"
            result.add(severity, "ARM9_PATCH_MISSING", f"Required ARM9 patch is missing: {name}", offset=hex(offset))
        else:
            states[name] = "unknown"
            result.add(
                "ERROR",
                "ARM9_PATCH_UNKNOWN",
                f"Unknown machine code at ARM9 patch site {name}",
                offset=hex(offset),
                found=found.hex(),
                expected_original=original.hex(),
                expected_target=target.hex(),
            )
    return states


def audit_header(rom: bytes, result: Result) -> dict[str, int]:
    if len(rom) < 0x4000:
        result.add("ERROR", "ROM_TOO_SHORT", "ROM is shorter than the 0x4000-byte header area")
        return {}

    fields = {
        "unit_code": rom[0x12],
        "arm9_rom_offset": u32(rom, 0x20),
        "arm9_entry_address": u32(rom, 0x24),
        "arm9_ram_address": u32(rom, 0x28),
        "arm9_size": u32(rom, 0x2C),
        "arm7_rom_offset": u32(rom, 0x30),
        "arm7_size": u32(rom, 0x3C),
        "fnt_offset": u32(rom, 0x40),
        "fnt_size": u32(rom, 0x44),
        "fat_offset": u32(rom, 0x48),
        "fat_size": u32(rom, 0x4C),
        "arm9_overlay_offset": u32(rom, 0x50),
        "arm9_overlay_size": u32(rom, 0x54),
        "application_end_offset": u32(rom, 0x80),
        "rom_header_size": u32(rom, 0x84),
    }
    result.metadata["title"] = rom[0:12].rstrip(b"\0").decode("ascii", errors="replace")
    result.metadata["game_code"] = rom[12:16].decode("ascii", errors="replace")
    result.metadata["header"] = {k: hex(v) if k != "unit_code" else v for k, v in fields.items()}

    stored_logo_crc = u16(rom, 0x15C)
    actual_logo_crc = crc16_nds(rom[0x0C0:0x15C])
    if stored_logo_crc != actual_logo_crc:
        result.add("ERROR", "LOGO_CRC", "Nintendo logo CRC mismatch", stored=hex(stored_logo_crc), actual=hex(actual_logo_crc))

    stored_header_crc = u16(rom, 0x15E)
    actual_header_crc = crc16_nds(rom[0:0x15E])
    if stored_header_crc != actual_header_crc:
        result.add("ERROR", "HEADER_CRC", "NDS header CRC mismatch", stored=hex(stored_header_crc), actual=hex(actual_header_crc))

    for name in ("arm9", "arm7", "fnt", "fat", "arm9_overlay"):
        off = fields[f"{name}_rom_offset"] if name in ("arm9", "arm7") else fields[f"{name}_offset"]
        size = fields[f"{name}_size"]
        if size and not in_range(rom, off, size):
            result.add("ERROR", "HEADER_RANGE", f"{name} range is outside ROM", offset=hex(off), size=hex(size))

    if fields["application_end_offset"] > len(rom):
        result.add(
            "ERROR",
            "APPLICATION_END",
            "Header application_end_offset is beyond the ROM file",
            application_end=hex(fields["application_end_offset"]),
            rom_size=hex(len(rom)),
        )
    return fields


def audit_arm9(rom: bytes, fields: dict[str, int], result: Result, allow_unpatched: bool) -> None:
    if not fields:
        return
    offset = fields["arm9_rom_offset"]
    size = fields["arm9_size"]
    if not in_range(rom, offset, size):
        return
    physical = rom[offset : offset + size]
    arm9_meta: dict[str, Any] = {
        "physical_size": size,
        "physical_sha256": sha256(physical),
        "blz_footer": parse_blz_footer(physical),
    }

    footer = parse_blz_footer(physical)
    if footer is not None:
        try:
            decompressed = blz_decompress(physical)
        except Exception as exc:  # malformed input should be reported, not crash audit
            result.add("ERROR", "ARM9_BLZ", "ARM9 BLZ decompression failed", error=str(exc))
            result.metadata["arm9"] = arm9_meta
            return
        arm9_meta["compression"] = "BLZ"
        arm9_meta["decompressed_size"] = len(decompressed)
        arm9_meta["decompressed_sha256"] = sha256(decompressed)
        if not in_range(physical, ARM9_COMPRESSED_END_PTR_OFFSET, 4):
            result.add("ERROR", "ARM9_END_PTR_RANGE", "Compressed ARM9 is too short to contain 0x0FC4")
        else:
            stored_end = u32(physical, ARM9_COMPRESSED_END_PTR_OFFSET)
            expected_end = fields["arm9_ram_address"] + size
            arm9_meta["compressed_end_pointer"] = hex(stored_end)
            arm9_meta["expected_compressed_end_pointer"] = hex(expected_end)
            if stored_end != expected_end:
                result.add(
                    "ERROR",
                    "ARM9_END_PTR",
                    "ARM9 0x0FC4 does not equal RAM base plus physical compressed size",
                    stored=hex(stored_end),
                    expected=hex(expected_end),
                    physical_size=hex(size),
                )
    else:
        decompressed = physical
        arm9_meta["compression"] = "uncompressed"
        arm9_meta["decompressed_size"] = len(decompressed)
        arm9_meta["decompressed_sha256"] = sha256(decompressed)
        if in_range(physical, ARM9_COMPRESSED_END_PTR_OFFSET, 4):
            stored_end = u32(physical, ARM9_COMPRESSED_END_PTR_OFFSET)
            arm9_meta["compressed_end_pointer"] = hex(stored_end)
            if stored_end != 0:
                result.add(
                    "ERROR",
                    "ARM9_UNCOMPRESSED_PTR",
                    "ARM9 has no BLZ footer but 0x0FC4 is non-zero",
                    stored=hex(stored_end),
                )
        result.add("WARNING", "ARM9_UNCOMPRESSED", "Final ROM ARM9 is not BLZ-compressed")

    arm9_meta["patch_states"] = classify_patch_sites(decompressed, result, allow_unpatched)
    result.metadata["arm9"] = arm9_meta

    if offset == 0x4000 and in_range(rom, offset, 0x4000):
        stored_crc = u16(rom, 0x6C)
        actual_crc = crc16_nds(rom[offset : offset + 0x4000])
        arm9_meta["secure_area_crc_stored"] = hex(stored_crc)
        arm9_meta["secure_area_crc_actual"] = hex(actual_crc)
        if stored_crc != actual_crc:
            result.add("ERROR", "SECURE_AREA_CRC", "Secure-area CRC mismatch", stored=hex(stored_crc), actual=hex(actual_crc))


def audit_overlays(rom: bytes, fields: dict[str, int], result: Result) -> None:
    if not fields:
        return
    y9_offset = fields["arm9_overlay_offset"]
    y9_size = fields["arm9_overlay_size"]
    fat_offset = fields["fat_offset"]
    fat_size = fields["fat_size"]
    if not in_range(rom, y9_offset, y9_size) or not in_range(rom, fat_offset, fat_size):
        return
    if y9_size % Y9_ENTRY_SIZE:
        result.add("ERROR", "Y9_SIZE", "ARM9 overlay table size is not a multiple of 0x20", size=hex(y9_size))
        return
    if fat_size % 8:
        result.add("ERROR", "FAT_SIZE", "FAT size is not a multiple of 8", size=hex(fat_size))
        return

    fat_count = fat_size // 8
    overlay_meta: list[dict[str, Any]] = []
    physical_ranges: list[tuple[int, int, int]] = []
    seen_ids: set[int] = set()

    for entry_offset in range(y9_offset, y9_offset + y9_size, Y9_ENTRY_SIZE):
        overlay_id, ram, ram_size, bss, sinit, sinit_end, file_id, size_flag = struct.unpack_from("<8I", rom, entry_offset)
        flags = size_flag >> 24
        saved_size = size_flag & 0x00FFFFFF
        item: dict[str, Any] = {
            "id": overlay_id,
            "ram_address": hex(ram),
            "ram_size": ram_size,
            "bss_size": bss,
            "static_init_start": hex(sinit),
            "static_init_end": hex(sinit_end),
            "file_id": file_id,
            "flag_byte": hex(flags),
            "saved_size": saved_size,
        }
        overlay_meta.append(item)

        if overlay_id in seen_ids:
            result.add("ERROR", "Y9_DUPLICATE_ID", "Duplicate overlay ID", overlay_id=overlay_id)
        seen_ids.add(overlay_id)
        if file_id >= fat_count:
            result.add("ERROR", "Y9_FILE_ID", "Overlay file ID is outside FAT", overlay_id=overlay_id, file_id=file_id, fat_count=fat_count)
            continue

        fat_entry = fat_offset + file_id * 8
        top, bottom = struct.unpack_from("<II", rom, fat_entry)
        physical_size = bottom - top if bottom >= top else -1
        item["fat_top"] = hex(top)
        item["fat_bottom"] = hex(bottom)
        item["fat_size"] = physical_size
        if physical_size < 0 or not in_range(rom, top, max(physical_size, 0)):
            result.add("ERROR", "OVERLAY_FAT_RANGE", "Overlay FAT range is invalid", overlay_id=overlay_id, top=hex(top), bottom=hex(bottom))
            continue
        physical_ranges.append((top, bottom, overlay_id))
        payload = rom[top:bottom]
        item["physical_sha256"] = sha256(payload)

        is_compressed = bool(flags & 1)
        if flags not in (2, 3):
            result.add("ERROR", "Y9_FLAG", "Unexpected overlay flag byte", overlay_id=overlay_id, flag=hex(flags))
        if saved_size != physical_size:
            result.add(
                "ERROR",
                "Y9_FAT_SIZE",
                "Y9 saved size does not equal FAT physical size",
                overlay_id=overlay_id,
                y9_saved=hex(saved_size),
                fat_size=hex(physical_size),
            )

        if is_compressed:
            footer = parse_blz_footer(payload)
            item["blz_footer"] = footer
            if footer is None:
                result.add("ERROR", "OVERLAY_BLZ_FOOTER", "Compressed overlay has no valid BLZ footer", overlay_id=overlay_id)
                continue
            try:
                decompressed = blz_decompress(payload)
            except Exception as exc:
                result.add("ERROR", "OVERLAY_BLZ", "Overlay BLZ decompression failed", overlay_id=overlay_id, error=str(exc))
                continue
        else:
            decompressed = payload
            result.add("WARNING", "OVERLAY_UNCOMPRESSED", "Overlay is stored uncompressed", overlay_id=overlay_id)

        item["decompressed_size"] = len(decompressed)
        item["decompressed_sha256"] = sha256(decompressed)
        if len(decompressed) != ram_size:
            result.add(
                "ERROR",
                "OVERLAY_RAM_SIZE",
                "Overlay decompressed size does not equal Y9 ram_size",
                overlay_id=overlay_id,
                decompressed_size=hex(len(decompressed)),
                ram_size=hex(ram_size),
            )

        if sinit or sinit_end:
            if not (ram <= sinit <= sinit_end <= ram + ram_size):
                result.add(
                    "ERROR",
                    "OVERLAY_SINIT_RANGE",
                    "Overlay static initializer range is outside decompressed RAM image",
                    overlay_id=overlay_id,
                    ram_start=hex(ram),
                    ram_end=hex(ram + ram_size),
                    sinit=hex(sinit),
                    sinit_end=hex(sinit_end),
                )
        if ram + ram_size + bss > 0x02400000:
            result.add("ERROR", "OVERLAY_RAM_LIMIT", "Overlay extends past main RAM", overlay_id=overlay_id, end=hex(ram + ram_size + bss))

    physical_ranges.sort()
    for (top_a, bottom_a, id_a), (top_b, bottom_b, id_b) in zip(physical_ranges, physical_ranges[1:]):
        if bottom_a > top_b:
            result.add("ERROR", "OVERLAY_FAT_OVERLAP", "Overlay FAT ranges overlap", first=id_a, second=id_b, first_bottom=hex(bottom_a), second_top=hex(top_b))
    result.metadata["arm9_overlays"] = overlay_meta


def audit_dsi_layout(rom: bytes, fields: dict[str, int], result: Result) -> None:
    if not fields or fields["unit_code"] not in (2, 3):
        return
    if len(rom) < 0x1000:
        result.add("ERROR", "DSI_HEADER", "DSi title is missing the extended header")
        return

    dsi = {
        "dsi_flags": rom[0x1C],
        "arm9i_offset": u32(rom, 0x1C0),
        "arm9i_size": u32(rom, 0x1CC),
        "arm7i_offset": u32(rom, 0x1D0),
        "arm7i_size": u32(rom, 0x1DC),
        "digest_ntr_start": u32(rom, 0x1E0),
        "digest_ntr_size": u32(rom, 0x1E4),
        "digest_twl_start": u32(rom, 0x1E8),
        "digest_twl_size": u32(rom, 0x1EC),
        "sector_hashtable_start": u32(rom, 0x1F0),
        "sector_hashtable_size": u32(rom, 0x1F4),
        "block_hashtable_start": u32(rom, 0x1F8),
        "block_hashtable_size": u32(rom, 0x1FC),
        "digest_sector_size": u32(rom, 0x200),
        "digest_block_sectorcount": u32(rom, 0x204),
        "total_rom_size": u32(rom, 0x210),
        "modcrypt1_start": u32(rom, 0x220),
        "modcrypt1_size": u32(rom, 0x224),
        "modcrypt2_start": u32(rom, 0x228),
        "modcrypt2_size": u32(rom, 0x22C),
    }
    result.metadata["dsi"] = {key: hex(value) for key, value in dsi.items()}

    ranges = (
        ("arm9i", dsi["arm9i_offset"], dsi["arm9i_size"]),
        ("arm7i", dsi["arm7i_offset"], dsi["arm7i_size"]),
        ("digest_ntr", dsi["digest_ntr_start"], dsi["digest_ntr_size"]),
        ("digest_twl", dsi["digest_twl_start"], dsi["digest_twl_size"]),
        ("sector_hashtable", dsi["sector_hashtable_start"], dsi["sector_hashtable_size"]),
        ("block_hashtable", dsi["block_hashtable_start"], dsi["block_hashtable_size"]),
        ("modcrypt1", dsi["modcrypt1_start"], dsi["modcrypt1_size"]),
        ("modcrypt2", dsi["modcrypt2_start"], dsi["modcrypt2_size"]),
    )
    dsi_ranges_ok = True
    for name, offset, size in ranges:
        if size and not in_range(rom, offset, size):
            dsi_ranges_ok = False
            result.add("ERROR", "DSI_RANGE", f"DSi {name} range is outside ROM", offset=hex(offset), size=hex(size))

    if dsi["arm9i_offset"] and fields["application_end_offset"] > dsi["arm9i_offset"]:
        result.add(
            "ERROR",
            "NTR_TWL_OVERLAP",
            "NTR application end overlaps ARM9i/TWL region",
            application_end=hex(fields["application_end_offset"]),
            arm9i_offset=hex(dsi["arm9i_offset"]),
        )
    if dsi["total_rom_size"] > len(rom):
        dsi_ranges_ok = False
        result.add("ERROR", "DSI_TOTAL_SIZE", "DSi total_rom_size is beyond file", declared=hex(dsi["total_rom_size"]), actual=hex(len(rom)))
    if dsi["digest_sector_size"] == 0 or dsi["digest_block_sectorcount"] == 0:
        dsi_ranges_ok = False
        result.add("ERROR", "DSI_DIGEST_GEOMETRY", "DSi digest sector/block geometry contains zero")

    if dsi["dsi_flags"] & 0x02:
        if dsi["modcrypt1_size"] and dsi["modcrypt1_start"] != dsi["arm9i_offset"]:
            result.add(
                "ERROR",
                "DSI_MODCRYPT1_OFFSET",
                "Pico Loader requires modcrypt1 to start at ARM9i",
                modcrypt=hex(dsi["modcrypt1_start"]),
                arm9i=hex(dsi["arm9i_offset"]),
            )
        if dsi["modcrypt2_size"] and dsi["modcrypt2_start"] != dsi["arm7i_offset"]:
            result.add(
                "ERROR",
                "DSI_MODCRYPT2_OFFSET",
                "Pico Loader requires modcrypt2 to start at ARM7i",
                modcrypt=hex(dsi["modcrypt2_start"]),
                arm7i=hex(dsi["arm7i_offset"]),
            )

    if not dsi_ranges_ok:
        return
    if verify_dsi_integrity is None:
        result.add(
            "ERROR",
            "DSI_CRYPTO_DEPENDENCY",
            "DSi crypto audit could not load the toolkit dependency",
            error=str(DSI_CRYPTO_IMPORT_ERROR),
        )
        return

    try:
        crypto = verify_dsi_integrity(rom)
    except Exception as exc:
        result.add(
            "ERROR",
            "DSI_CRYPTO_AUDIT",
            "DSi digest/HMAC/modcrypt verification failed to execute",
            error=str(exc),
        )
        return

    result.metadata["dsi"]["crypto"] = crypto
    if not crypto["secure_area_final_decrypted"]:
        result.add("ERROR", "DSI_SECURE_STATE", "Final Secure Area is not in the expected decrypted state")
    if not crypto["sector_hashtable_ok"]:
        result.add("ERROR", "DSI_SECTOR_DIGEST", "DSi sector digest table does not match ROM content")
    if not crypto["block_hashtable_ok"]:
        result.add("ERROR", "DSI_BLOCK_DIGEST", "DSi block digest table does not match sector digest table")
    for name, ok in crypto["hmac_ok"].items():
        if not ok:
            result.add("ERROR", "DSI_HMAC", f"DSi HMAC mismatch: {name}", field=name)


def audit_rom(path: Path, allow_unpatched: bool = False) -> Result:
    rom = path.read_bytes()
    result = Result(path, rom)
    fields = audit_header(rom, result)
    audit_arm9(rom, fields, result, allow_unpatched)
    audit_overlays(rom, fields, result)
    audit_dsi_layout(rom, fields, result)
    return result


def print_human(result: Result) -> None:
    data = result.to_dict()
    print(f"ROM: {data['metadata']['rom_path']}")
    print(f"Size: {data['metadata']['rom_size']} bytes")
    print(f"SHA-256: {data['metadata']['rom_sha256']}")
    for issue in data["issues"]:
        suffix = f" | {json.dumps(issue['context'], ensure_ascii=False, sort_keys=True)}" if issue["context"] else ""
        print(f"[{issue['severity']}] {issue['code']}: {issue['message']}{suffix}")
    summary = data["summary"]
    print(f"SUMMARY: {summary['status']} errors={summary['errors']} warnings={summary['warnings']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit IMASDS final ROM boot metadata without extracting game data")
    parser.add_argument("rom", type=Path, help="final .nds file")
    parser.add_argument("--json", dest="json_path", type=Path, help="also write a JSON report")
    parser.add_argument("--allow-unpatched", action="store_true", help="treat original ARM9 patch bytes as informational (for baseline ROM A)")
    args = parser.parse_args()

    if not args.rom.is_file():
        print(f"ERROR: file not found: {args.rom}", file=sys.stderr)
        return 2
    try:
        result = audit_rom(args.rom, allow_unpatched=args.allow_unpatched)
    except Exception as exc:
        print(f"ERROR: audit crashed: {exc}", file=sys.stderr)
        return 2

    print_human(result)
    if args.json_path:
        args.json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON: {args.json_path}")
    return 1 if result.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
