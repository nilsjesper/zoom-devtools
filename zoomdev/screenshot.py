"""LCD screenshot over SysEx (patches/screenshot.yaml, docs/screenshot.md).

Request  F0 52 00 <model> 7C <page> F7, page 0-15.
Reply    F0 52 00 <model> 7C <page> <128 bytes in Zoom's 7-bit groups> F7.

Pages 0-7 are LCD plane A, 8-15 plane B: 8 rows of 128 column bytes each,
bit 0 on top. The screen shows A xor B (B is the blinking highlight). A
request for page 0 makes the pedal snapshot both planes, so one pass of
pages 0-15 is one frame.
"""

from __future__ import annotations

import struct
import time
import zlib

OPCODE = 0x7C
MODEL = 0x61  # every MIDISTOMP v1.2+ pedal runs the MS-70CDR OS
WIDTH, HEIGHT = 128, 64
PAGE_BYTES = 128
PAGES = 16  # 8 for plane A, 8 for plane B
PLANE_BYTES = WIDTH * HEIGHT // 8


def request(page: int, model: int = MODEL) -> list[int]:
    """SysEx body (without F0/F7, as mido wants it) asking for one page."""
    if not 0 <= page < PAGES:
        raise ValueError(f"page {page} not in 0..{PAGES - 1}")
    return [0x52, 0x00, model, OPCODE, page]


def pack7(data: bytes) -> list[int]:
    """Zoom's 7-bit groups, as the pedal's send_packed (0xC00AE060) makes them:
    for every 7 bytes a header byte whose bit (6 - j) is the top bit of byte j,
    then the 7 bytes' low bits. The last group may be short."""
    out = []
    for i in range(0, len(data), 7):
        group = data[i:i + 7]
        out.append(sum(1 << (6 - j) for j, b in enumerate(group) if b & 0x80))
        out.extend(b & 0x7F for b in group)
    return out


def unpack7(data: list[int] | bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 8):
        head, body = data[i], data[i + 1:i + 8]
        out.extend(b | (0x80 if head & (1 << (6 - j)) else 0) for j, b in enumerate(body))
    return bytes(out)


def parse_reply(body: list[int] | bytes) -> tuple[int, bytes] | None:
    """(page, 128 bytes) from a reply body (without F0/F7), or None if it is not one."""
    if len(body) < 5 or body[0] != 0x52 or body[1] != 0x00 or body[3] != OPCODE:
        return None
    page = body[4]
    data = unpack7(body[5:])
    if page >= PAGES or len(data) != PAGE_BYTES:
        return None
    return page, data


def pixels(planes: bytes, mode: str = "xor") -> list[list[int]]:
    """64 rows of 128 pixels (1 = lit) from the 2 KiB of planes A and B.
    mode: "xor" (what the screen shows), "a" or "b"."""
    if len(planes) != 2 * PLANE_BYTES:
        raise ValueError(f"need {2 * PLANE_BYTES} bytes, got {len(planes)}")
    a, b = planes[:PLANE_BYTES], planes[PLANE_BYTES:]
    fb = {"a": a, "b": b, "xor": bytes(x ^ y for x, y in zip(a, b))}[mode]
    return [[fb[(y // 8) * WIDTH + x] >> (y % 8) & 1 for x in range(WIDTH)] for y in range(HEIGHT)]


def png(rows: list[list[int]], scale: int = 4, on=(0x10, 0x10, 0x10), off=(0xE8, 0xEC, 0xE0)) -> bytes:
    """An 8-bit RGB PNG of the pixel rows, each pixel scale x scale."""
    lines = []
    for row in rows:
        line = b"".join(bytes(on if p else off) * scale for p in row)
        lines.extend([b"\0" + line] * scale)
    h, w = len(rows) * scale, len(rows[0]) * scale

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(lines), 9)) + chunk(b"IEND", b""))


def capture(port_name: str | None = None, model: int = MODEL, timeout: float = 1.0,
            tries: int = 3) -> bytes:
    """Read both planes (2 KiB) from the pedal. One request at a time: the
    pedal's MIDI out ring drops bytes when full. Reads the frame twice and
    retries (up to `tries` passes) until two passes match, so a frame caught
    mid-draw is not returned."""
    import mido

    from zoomms.midi import find_port

    port_name = port_name or find_port(mido.get_output_names())
    if port_name is None:
        raise RuntimeError("no Zoom MIDI port found; pass --port")
    in_name = port_name if port_name in mido.get_input_names() else find_port(mido.get_input_names())
    with mido.open_input(in_name) as inp, mido.open_output(port_name) as out:
        def one_pass() -> bytes:
            list(inp.iter_pending())
            frame = bytearray()
            for page in range(PAGES):
                out.send(mido.Message("sysex", data=request(page, model)))
                deadline = time.monotonic() + timeout
                got = None
                while got is None:
                    for msg in inp.iter_pending():
                        if msg.type == "sysex":
                            r = parse_reply(list(msg.data))
                            if r and r[0] == page:
                                got = r[1]
                    if got is None:
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"no reply for page {page} (is a zoomdev build with the screenshot patch flashed?)")
                        time.sleep(0.002)
                frame += got
            return bytes(frame)

        last = one_pass()
        for _ in range(tries - 1):
            again = one_pass()
            if again == last:
                return again
            last = again
        return last
