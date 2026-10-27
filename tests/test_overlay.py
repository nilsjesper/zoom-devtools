import os
import struct
from pathlib import Path

import pytest
from zoomms import ais, parts, patcher

from zoomdev import overlay
from zoomdev.cli import main

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT = patcher.load(ROOT / "patches/screenshot.yaml")
# A built MIDISTOMP v1.2+ Windows updater (e.g. Luca's v2.0 beta1) for the end-to-end tests.
UPDATER = os.environ.get("ZOOMDEV_UPDATER")
needs_updater = pytest.mark.skipif(not UPDATER, reason="set ZOOMDEV_UPDATER to a built MIDISTOMP updater .exe")


def image(looper=True, extra=()):
    """A small OS image: the SysEx state table, the looper's services block,
    a big MOD code section like v2.0's mixer, and a jump at the end."""
    table = bytearray(20)
    struct.pack_into("<I", table, 12, 0xC00AED88)  # state 3, stock
    sections = [ais.Section(0xC00EF2D0, bytes(table)), ais.Section(0xC00F8000, bytes(0xEC80))]
    if looper:
        block = struct.pack("<8I", overlay.SERVICES_MAGIC, 3, 0xC0636980, 0x199680, 0xC0198000, 0x68000,
                            0xC0116000, 0x82000)
        sections.append(ais.Section(overlay.SERVICES, block))
    sections += [ais.Section(a, bytes(n)) for a, n in extra]
    ops = [(ais.SECTION_LOAD, i) for i in range(len(sections))] + [(ais.JUMP_CLOSE, bytes(4))]
    return ais.AisImage(sections=sections, ops=ops)


def moved(patch, addr):
    """The patch with its section and RAM moved to start at addr."""
    delta = addr - patch["section"][0]["addr"]
    return {**patch, "section": [{**s, "addr": s["addr"] + delta} for s in patch["section"]],
            "ram": [{**r, "addr": r["addr"] + delta} for r in patch["ram"]]}


def test_image_claims_include_looper_regions():
    claims = overlay.image_claims(image())
    assert any(c.what == "looper buffer region 3" and c.lo == 0xC0116000 and c.hi == 0xC0198000 for c in claims)
    assert not any("looper" in c.what for c in overlay.image_claims(image(looper=False)))


def test_screenshot_fits_v2_layout():
    img = image()
    assert overlay.check(img, SCREENSHOT, overlay.image_claims(img)) == []


def test_old_spot_is_taken_by_the_mixer():
    # where the PR #49 prototype sat on M3b7; v2.0's mixer C code covers it
    img = image()
    problems = overlay.check(img, moved(SCREENSHOT, 0xC0100800), overlay.image_claims(img))
    assert problems and all("loaded section" in p for p in problems)


def test_looper_region_is_taken():
    img = image()
    problems = overlay.check(img, moved(SCREENSHOT, 0xC0120000), overlay.image_claims(img))
    assert any("looper buffer region 3" in p for p in problems)


def test_ram_outside_free_regions_is_refused():
    img = image()
    problems = overlay.check(img, moved(SCREENSHOT, 0xC07FF400), overlay.image_claims(img))
    assert problems == ["0xC07FFC00-0xC0800400 screenshot SNAP: copy of LCD planes A and B: "
                        "not inside RAM the OS leaves free"]


def test_upstream_section_in_region_4_is_caught():
    img = image(extra=[(0xC07F4900, 0x100)])  # inside SNAP
    problems = overlay.check(img, SCREENSHOT, overlay.image_claims(img))
    assert problems == ["0xC07F4800-0xC07F5000 screenshot SNAP: copy of LCD planes A and B: "
                        "overlaps 0xC07F4900-0xC07F4A00 loaded section"]


def test_patch_applies_to_synthetic_image():
    img = image()
    patcher.apply(img, SCREENSHOT, overlay.BUILD_ID)
    assert struct.unpack("<I", img.read(0xC00EF2DC, 4))[0] == 0xC07F4000
    assert img.sections[-1].addr == 0xC07F4000


@needs_updater
def test_build_from_updater(tmp_path):
    out = tmp_path / "dev.exe"
    assert main(["build", UPDATER, "--out", str(out)]) == 0
    before, after = Path(UPDATER).read_bytes(), out.read_bytes()
    assert len(before) == len(after)
    os_part = next(p for p in parts.find_parts(UPDATER) if p.resource_id == overlay.OS_PART)
    old = ais.parse(parts.read_part(UPDATER, os_part))
    new = ais.parse(parts.read_part(str(out), os_part))
    old.write(0xC00EF2DC, (0xC07F4000).to_bytes(4, "little"))  # the one write the patch makes
    assert [(s.addr, s.data) for s in new.sections[:-1]] == [(s.addr, s.data) for s in old.sections]
    assert new.sections[-1].addr == 0xC07F4000
    assert struct.unpack("<I", new.read(0xC00EF2DC, 4))[0] == 0xC07F4000
    assert new.entry == old.entry
    # outside the OS part only the PE checksum may change
    outside = [i for i in range(len(before)) if before[i] != after[i]
               and not os_part.offset <= i < os_part.offset + os_part.size]
    assert len(outside) <= 4


@needs_updater
def test_build_twice_is_refused(tmp_path):
    first = tmp_path / "dev.exe"
    assert main(["build", UPDATER, "--out", str(first)]) == 0
    assert main(["build", str(first), "--out", str(tmp_path / "again.exe")]) == 1  # the section is there already
