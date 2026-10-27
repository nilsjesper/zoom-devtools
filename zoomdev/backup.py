"""Patch backups: one line per patch, `<0-based index> <dump as hex>`, the
format zoomio.swift wrote, so the backups in zoom-ms-midistomp
firmware/backups/ read and compare as they are.

Dumps compare from byte 4: byte 3 is the model (0x58 or 0x61), which changes
when the same pedal moves between the MS-50G and MS-70CDR OS.
"""

from __future__ import annotations

from pathlib import Path

from .pedal import hexs

MODEL_BYTE = 3


def dumps_text(dumps: list[list[int]]) -> str:
    return "".join(f"{n} {hexs(d)}\n" for n, d in enumerate(dumps))


def read(path: str | Path) -> dict[int, bytes]:
    out = {}
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        n, _, data = line.partition(" ")
        try:
            out[int(n)] = bytes.fromhex(data)
        except ValueError as e:
            raise ValueError(f"{path}:{i}: not `<index> <hex>` ({e})") from e
    return out


def name(dump: bytes | list[int]) -> str:
    """The patch name as zoomio.swift showed it: the printable bytes of 132-144.
    The name's 7-bit groups have zero header bytes, which this drops."""
    return "".join(chr(b) for b in dump[132:145] if 0x20 <= b < 0x7F).strip() if len(dump) > 141 else ""


def differing(a: bytes, b: bytes) -> list[int]:
    """Byte offsets where two dumps differ, the model byte left out; a length
    difference counts from the end of the shorter one."""
    n = min(len(a), len(b))
    diff = [i for i in range(n) if i != MODEL_BYTE and a[i] != b[i]]
    return diff + list(range(n, max(len(a), len(b))))


def compare(want: dict[int, bytes], got: dict[int, bytes]) -> list[str]:
    """One line per patch that is missing on one side or differs; empty if all match."""
    lines = []
    for n in sorted(set(want) | set(got)):
        if n not in got:
            lines.append(f"patch {n + 1}: only in the first")
        elif n not in want:
            lines.append(f"patch {n + 1}: only in the second")
        elif diff := differing(want[n], got[n]):
            shown = ", ".join(f"{i}: {want[n][i]:02X}->{got[n][i]:02X}" if i < min(len(want[n]), len(got[n]))
                              else f"{i}: length" for i in diff[:8])
            more = f" (+{len(diff) - 8} more)" if len(diff) > 8 else ""
            lines.append(f"patch {n + 1} {name(want[n])!r}: {len(diff)} bytes differ: {shown}{more}")
    return lines
