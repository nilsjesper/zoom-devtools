"""Add dev patches (patches/*.yaml) to an updater someone already built.

The input is a finished MIDISTOMP Windows updater, e.g. Luca's v2.0 beta1
.exe, not Zoom's stock one: the dev build is then the exact firmware testers
get plus our patches, and we never need to know which upstream patches went
into it. Only the main OS (BIN/129) changes; the flash script, BIN/133 and
everything else are kept as they are.

Before applying, every range a dev patch loads (`section`) or uses at run time
(`ram`) is checked against what the updater already has: its loaded sections
and the big RAM regions the looper's services block hands out. A clash is an
error, not a warning: upstream grows into free RAM between releases, and a
silent overlap would corrupt the pedal's memory instead of failing here.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from zoomms import ais, flash, parts, patcher

BUILD_ID = "ms70cdr-2.10"  # every MIDISTOMP v1.2+ updater
OS_PART = 129

# RAM the 70CDR OS leaves alone (RAM test regions 1-5, zoomms
# docs/dev/ms70cdr-addresses.md, confirmed on a pedal). Dev patches must sit
# inside one of these.
FREE_REGIONS = [
    (0xC00F3100, 0xC0198000),
    (0xC0198000, 0xC0200000),
    (0xC0636980, 0xC07D0000),
    (0xC07F4000, 0xC0800000),
    (0x80000000, 0x80020000),
]

# The looper's services block (zoomms asm/looper/block.S): magic, region count,
# then (base, bytes) for each region its buffer may take.
SERVICES = 0xC00F3000
SERVICES_MAGIC = 0x5A4C5301


@dataclass
class Claim:
    lo: int
    hi: int
    what: str

    def __str__(self) -> str:
        return f"0x{self.lo:08X}-0x{self.hi:08X} {self.what}"


def section_bytes(sec: dict) -> int:
    return len("".join(str(sec["data"]).split())) // 2


def patch_claims(patch: dict) -> list[Claim]:
    """What a dev patch loads or uses, for BUILD_ID."""
    out = [Claim(s["addr"], s["addr"] + section_bytes(s), f"{patch['name']} section")
           for s in patch.get("section", []) if s["model"] == BUILD_ID]
    out += [Claim(r["addr"], r["addr"] + r["size"], f"{patch['name']} {r['what']}") for r in patch.get("ram", [])]
    return out


def image_claims(img: ais.AisImage) -> list[Claim]:
    """What the updater's OS already loads, plus the looper's buffer regions."""
    out = [Claim(s.addr, s.addr + len(s.data), "loaded section") for s in img.sections]
    try:
        magic, n = struct.unpack("<II", img.read(SERVICES, 8))
    except ais.AisError:  # no section at SERVICES: a build without the looper
        return out
    if magic == SERVICES_MAGIC and 0 < n <= 8:
        for i in range(n):
            base, size = struct.unpack("<II", img.read(SERVICES + 8 + 8 * i, 8))
            out.append(Claim(base, base + size, f"looper buffer region {i + 1}"))
    return out


def check(img: ais.AisImage, patch: dict, taken: list[Claim]) -> list[str]:
    """Problems with putting `patch` into `img`; empty if it fits."""
    problems = []
    for c in patch_claims(patch):
        if not any(lo <= c.lo and c.hi <= hi for lo, hi in FREE_REGIONS):
            problems.append(f"{c}: not inside RAM the OS leaves free")
        for t in taken:
            if c.lo < t.hi and t.lo < c.hi:
                problems.append(f"{c}: overlaps {t}")
    return problems


def apply(exe_path: str | Path, patch_paths: list[str | Path]) -> tuple[bytes, list[str]]:
    """(new updater .exe bytes, log lines). Raises ValueError on any clash or patch error."""
    found = parts.find_parts(str(exe_path))
    os_part = next((p for p in found if p.resource_id == OS_PART), None)
    if os_part is None:
        raise ValueError(f"{exe_path}: no BIN/{OS_PART}; not a Zoom updater .exe")
    original = parts.read_part(str(exe_path), os_part)
    img = ais.parse(original)
    taken = image_claims(img)
    log = []
    for path in patch_paths:
        patch = patcher.load(path)
        problems = check(img, patch, taken)
        if problems:
            raise ValueError(f"{patch['name']} does not fit this updater:\n  " + "\n  ".join(problems))
        try:
            lines = patcher.apply(img, patch, BUILD_ID)
        except patcher.PatchError as e:
            raise ValueError(str(e)) from e
        log += [f"{patch['name']}: {line}" for line in lines]
        taken += patch_claims(patch)
    new_os = ais.build_part(img, original)
    # skip_boot=False: the input's flash script is already the one it should be.
    return flash.build_updater(str(exe_path), new_os, skip_boot=False), log
