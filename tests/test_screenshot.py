import re
import struct
import zlib
from pathlib import Path

import pytest

from zoomms import asm, patcher

from zoomdev import overlay, screenshot
from zoomdev.cli import main

ROOT = Path(__file__).resolve().parent.parent
PATCH = patcher.load(ROOT / "patches/screenshot.yaml")
SECTION = PATCH["section"][0]
SRC = (ROOT / SECTION["source"]).read_text()


def equ(name):
    return int(re.search(rf"\.equ\s+{name},\s*(\S+)", SRC).group(1), 0)


@pytest.mark.skipif(not asm.available(), reason="tic6x binutils not installed")
def test_section_matches_assembly_source():
    code = asm.assemble(ROOT / SECTION["source"], SECTION["addr"], whole_packets=True)
    assert code == bytes.fromhex("".join(SECTION["data"].split()))


def test_state_table_entry_retargeted():
    (site,) = PATCH["write"]
    assert site["addr"] == 0xC00EF2D0 + 3 * 4  # Task_MIDI SysEx state table, state 3
    assert int.from_bytes(bytes.fromhex(site["expect"]), "little") == equ("STOCK_STATE3") == 0xC00AED88
    assert int.from_bytes(bytes.fromhex(site["data"]), "little") == SECTION["addr"]  # sysex_bytes is first


def test_ram_entries_match_asm():
    assert SECTION["addr"] == equ("BASE")
    ram = {r["what"].split(":")[0]: (r["addr"], r["size"]) for r in PATCH["ram"]}
    assert ram["FLAG"] == (equ("BASE") + 0x7F8, 4)
    assert ram["SNAP"] == (equ("BASE") + 0x800, equ("PAGES") * equ("PAGE_BYTES"))


def test_layout():
    code, flag, snap = overlay.patch_claims(PATCH)
    assert code.hi <= flag.lo and flag.hi <= snap.lo  # code, then FLAG, then SNAP
    assert snap.lo % 8 == 0 and equ("LCD_RAM") % 8 == 0  # lddw/stdw copy
    assert 0xC07F4000 <= code.lo and snap.hi <= 0xC0800000  # RAM test region 4 (docs/memory.md)


def test_protocol_constants_match_asm():
    assert equ("OPCODE") == screenshot.OPCODE
    assert equ("PAGES") == screenshot.PAGES
    assert equ("PAGE_BYTES") == screenshot.PAGE_BYTES
    assert equ("LCD_RAM") == 0x11816800  # FB, docs/ms70cdr-addresses.md


def test_pack_roundtrip_and_reply_size():
    data = bytes(range(256))[:128]
    packed = screenshot.pack7(data)
    assert len(packed) == 147 and all(b < 0x80 for b in packed)
    assert screenshot.unpack7(packed) == data
    reply = [0xF0] + screenshot.request(5) + packed + [0xF7]
    assert len(reply) == 154  # the stock 0x28 patch dump is 146; the out ring holds 1023


def test_request():
    assert screenshot.request(0) == [0x52, 0x00, 0x61, 0x7C, 0x00]
    with pytest.raises(ValueError):
        screenshot.request(16)


def synthetic_planes():
    """Plane A: a border and a diagonal. Plane B: a filled box (the highlight)."""
    w, h = screenshot.WIDTH, screenshot.HEIGHT
    a = [[0] * w for _ in range(h)]
    b = [[0] * w for _ in range(h)]
    for x in range(w):
        a[0][x] = a[h - 1][x] = 1
    for y in range(h):
        a[y][0] = a[y][w - 1] = 1
        a[y][2 * y] = 1
    for y in range(10, 20):
        for x in range(30, 60):
            b[y][x] = 1

    def to_bytes(img):
        fb = bytearray(w * h // 8)
        for y in range(h):
            for x in range(w):
                if img[y][x]:
                    fb[(y // 8) * w + x] |= 1 << (y % 8)
        return bytes(fb)

    return a, b, to_bytes(a) + to_bytes(b)


def pedal_reply(planes: bytes, page: int) -> list[int]:
    """What sysex_bytes/reply send for a page, as mido gives it (no F0/F7)."""
    chunk = planes[page * 128:(page + 1) * 128]
    return [0x52, 0x00, 0x61, 0x7C, page] + screenshot.pack7(chunk)


def test_decode_synthetic_frame():
    a, b, planes = synthetic_planes()
    frame = bytearray()
    for page in range(screenshot.PAGES):
        got = screenshot.parse_reply(pedal_reply(planes, page))
        assert got is not None and got[0] == page
        frame += got[1]
    assert bytes(frame) == planes
    assert screenshot.pixels(frame, "a") == a
    assert screenshot.pixels(frame, "b") == b
    xor = screenshot.pixels(frame)
    assert xor[0][0] == 1 and xor[1][1] == 0 and xor[1][2] == 1  # border, diagonal (x = 2y)
    assert xor == [[p ^ q for p, q in zip(ra, rb)] for ra, rb in zip(a, b)]
    assert xor[15][40] == 1 and xor[15][30] == 0  # highlight; the diagonal (30, 15) inverted


def test_parse_reply_rejects_others():
    assert screenshot.parse_reply([0x52, 0x00, 0x61, 0x28, 0x00] + [0] * 147) is None  # patch dump
    assert screenshot.parse_reply([0x52, 0x00, 0x61, 0x7C, 16] + [0] * 147) is None
    assert screenshot.parse_reply([0x52, 0x00, 0x61, 0x7C, 0] + [0] * 20) is None  # short


def decode_png(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(kind + body)
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    w, h = ihdr[:2]
    raw = zlib.decompress(idat)
    stride = 1 + 3 * w
    assert len(raw) == h * stride and all(raw[i * stride] == 0 for i in range(h))
    return w, h, lambda x, y: raw[y * stride + 1 + 3 * x:y * stride + 4 + 3 * x]


def test_png():
    a, _, planes = synthetic_planes()
    w, h, px = decode_png(screenshot.png(screenshot.pixels(planes, "a"), scale=2))
    assert (w, h) == (256, 128)
    on, off = bytes((0x10, 0x10, 0x10)), bytes((0xE8, 0xEC, 0xE0))
    assert px(0, 0) == on and px(1, 1) == on  # border pixel (0, 0) scaled 2x
    assert px(4, 4) == off  # pixel (2, 2): inside, not on the diagonal row 1
    assert px(2 * 2 * 5 + 1, 2 * 5) == on  # diagonal pixel (10, 5)


def test_cli_from_raw(tmp_path):
    _, _, planes = synthetic_planes()
    raw = tmp_path / "lcd.bin"
    raw.write_bytes(planes)
    out = tmp_path / "lcd.png"
    assert main(["screenshot", "--from-raw", str(raw), "--out", str(out), "--scale", "1"]) == 0
    w, h, _ = decode_png(out.read_bytes())
    assert (w, h) == (128, 64)
