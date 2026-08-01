#!/usr/bin/env python3
"""Safely apply the known IMASDS expanded lyric-font ARM9 patches.

This script applies two independent fixes:

1. Preserve the legacy 0x16F0 range-cache patch used by successful builds.
2. Read AGL v3 cell.frame_count as u16 in both the size and conversion passes.

The script does not choose or validate the lyric resource count. The historical
six-song build used 489 frames; the successful ten-song build uses 648 frames.
Neither number is a permanent limit. Resource consistency must be checked
separately across SSOT, charmap, AGL, GLD, and LESVOICETABLE BBQ files.

Every patch site is validated before any output is written. The operation is
idempotent and accepts unpatched, partially patched, and fully patched inputs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Iterable


@dataclass(frozen=True)
class PatchSite:
    offset: int
    original: bytes
    patched: bytes
    description: str


PATCH_SITES = (
    PatchSite(
        0x0003D28C,
        bytes.fromhex("02 50 D7 E5"),
        bytes.fromhex("B2 50 D7 E1"),
        "AGL v3 size pass: ldrb r5,[r7,#2] -> ldrh r5,[r7,#2]",
    ),
    PatchSite(
        0x0003D66C,
        bytes.fromhex("02 60 D5 E5"),
        bytes.fromhex("B2 60 D5 E1"),
        "AGL v3 conversion: ldrb r6,[r5,#2] -> ldrh r6,[r5,#2]",
    ),
    PatchSite(
        0x00062608,
        bytes.fromhex("96 04 00 E0"),
        bytes.fromhex("16 0C A0 E3"),
        "legacy range cache: mul r0,r6,r4 -> mov r0,#0x1600",
    ),
    PatchSite(
        0x0006260C,
        bytes.fromhex("10 10 80 E2"),
        bytes.fromhex("F0 10 80 E2"),
        "legacy range cache: add r1,r0,#0x10 -> add r1,r0,#0xF0",
    ),
)

KNOWN_SIZE = 878_528
KNOWN_SHA256 = {
    "14e8d4656801a47108eb9d987b19e962773dc6eb5066e039b8f14faef144c980":
        "0728 six-song diagnostic input",
    "2f3fd4b672781c999159410aa5d738452e1fd0f6e7d91f4d91a08f2c1907788b":
        "0728 six-song diagnostic input, legacy range-cache only",
    "3422eaf862fdaa79d195c4fef813269bd6144af0be07ebae2a0dcff4b2c0b1ce":
        "0728 six-song complete successful patch",
}
KNOWN_FINAL_SHA256 = (
    "3422eaf862fdaa79d195c4fef813269bd6144af0be07ebae2a0dcff4b2c0b1ce"
)


class PatchError(RuntimeError):
    """Raised when an ARM9 cannot be patched safely."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_arm9(data: bytes) -> list[tuple[PatchSite, str]]:
    """Return each patch site's state, or reject an incompatible input."""
    minimum_size = max(site.offset + len(site.original) for site in PATCH_SITES)
    if len(data) < minimum_size:
        raise PatchError(
            f"文件过短：{len(data)} 字节；至少需要 {minimum_size} 字节。"
            "输入可能不是已解压的 arm9.bin。"
        )

    states: list[tuple[PatchSite, str]] = []
    errors: list[str] = []
    for site in PATCH_SITES:
        actual = data[site.offset : site.offset + len(site.original)]
        if actual == site.original:
            state = "original"
        elif actual == site.patched:
            state = "patched"
        else:
            state = "unknown"
            errors.append(
                f"0x{site.offset:08X}: 预期 {site.original.hex(' ')} 或 "
                f"{site.patched.hex(' ')}，实际 {actual.hex(' ')}"
            )
        states.append((site, state))

    if errors:
        raise PatchError(
            "补丁点机器码不匹配，拒绝按固定偏移修改：\n  "
            + "\n  ".join(errors)
        )
    return states


def patch_arm9_bytes(data: bytes) -> tuple[bytes, list[tuple[PatchSite, str]]]:
    """Patch a validated ARM9 and return (patched_bytes, original_states)."""
    states = inspect_arm9(data)
    result = bytearray(data)

    for site, state in states:
        if state == "original":
            result[site.offset : site.offset + len(site.patched)] = site.patched

    changed = {
        index
        for index, (before, after) in enumerate(zip(data, result))
        if before != after
    }
    allowed = {
        site.offset + index
        for site in PATCH_SITES
        for index in range(len(site.original))
    }
    unexpected = changed - allowed
    if unexpected:
        raise PatchError(
            "内部校验失败：补丁点之外出现修改："
            + ", ".join(f"0x{offset:X}" for offset in sorted(unexpected))
        )

    patched = bytes(result)
    final_states = inspect_arm9(patched)
    if not all(state == "patched" for _site, state in final_states):
        raise PatchError("内部校验失败：写入后仍有补丁点未完成。")

    input_hash = sha256(data)
    output_hash = sha256(patched)
    if input_hash in KNOWN_SHA256 and output_hash != KNOWN_FINAL_SHA256:
        raise PatchError(
            "已知 0728 六首歌诊断输入的输出哈希不匹配："
            f"期望 {KNOWN_FINAL_SHA256}，实际 {output_hash}"
        )

    return patched, states


def summarize(path: Path, data: bytes, states: Iterable[tuple[PatchSite, str]]) -> None:
    input_hash = sha256(data)
    known = KNOWN_SHA256.get(input_hash, "未登记版本（按补丁点机器码验证）")
    print(f"文件: {path}")
    print(f"大小: {len(data)} 字节"
          + ("（与 0728 ARM9 一致）" if len(data) == KNOWN_SIZE else ""))
    print(f"SHA-256: {input_hash}")
    print(f"版本: {known}")
    for site, state in states:
        label = "已补丁" if state == "patched" else "待补丁"
        print(f"  [{label}] 0x{site.offset:08X}  {site.description}")


def write_atomically(destination: Path, data: bytes, source: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            source_mode = stat.S_IMODE(source.stat().st_mode)
            temporary.chmod(source_mode)
        except OSError:
            pass
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def command_check(source: Path) -> int:
    data = source.read_bytes()
    states = inspect_arm9(data)
    summarize(source, data, states)
    if all(state == "patched" for _site, state in states):
        print("状态: 四个补丁点均已完成。")
    elif all(state == "original" for _site, state in states):
        print("状态: 尚未应用补丁，可以安全处理。")
    else:
        print("状态: 部分补丁，可以安全补齐。")
    return 0


def command_patch(source: Path, destination: Path) -> int:
    data = source.read_bytes()
    patched, original_states = patch_arm9_bytes(data)
    summarize(source, data, original_states)
    write_atomically(destination, patched, source)
    print(f"输出: {destination}")
    print(f"输出 SHA-256: {sha256(patched)}")
    print("结果: 四个补丁点均已完成，并已通过回读校验。")
    return 0


def command_verify(source: Path) -> int:
    data = source.read_bytes()
    states = inspect_arm9(data)
    summarize(source, data, states)
    missing = [site for site, state in states if state != "patched"]
    if missing:
        print("验证失败: 仍有补丁点未完成。", file=sys.stderr)
        return 1
    print("验证成功: ARM9 已包含已知歌词字库补丁；资源帧数需另行校验。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查或应用 IMASDS 可变歌词字库所需的已知 ARM9 补丁。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="只检查，不写入")
    check_parser.add_argument("input_arm9", type=Path)

    patch_parser = subparsers.add_parser("patch", help="验证并应用补丁")
    patch_parser.add_argument("input_arm9", type=Path)
    patch_parser.add_argument("output_arm9", type=Path, nargs="?")
    patch_parser.add_argument(
        "--in-place",
        action="store_true",
        help="明确原位修改输入文件（使用临时文件原子替换）",
    )

    verify_parser = subparsers.add_parser("verify", help="要求四处均已补丁")
    verify_parser.add_argument("input_arm9", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "check":
            return command_check(args.input_arm9)
        if args.command == "verify":
            return command_verify(args.input_arm9)

        if args.in_place and args.output_arm9 is not None:
            parser.error("--in-place 与 output_arm9 不能同时使用")
        if not args.in_place and args.output_arm9 is None:
            parser.error("请提供 output_arm9，或明确使用 --in-place")
        destination = args.input_arm9 if args.in_place else args.output_arm9
        return command_patch(args.input_arm9, destination)
    except FileNotFoundError as error:
        print(f"文件不存在: {error.filename}", file=sys.stderr)
        return 2
    except (OSError, PatchError) as error:
        print(f"拒绝处理: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
