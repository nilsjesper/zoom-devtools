"""Reassemble every `section` that names a `source` and rewrite its hex in place.

    C6X_PREFIX=~/.local/c6x/bin/tic6x-elf- python scripts/update_hex.py patches/*.yaml

Needs the tic6x binutils (zoomms: scripts/build-c6x-binutils.sh). Comments and
the rest of the file are kept; only the `data: >-` block after each `source:`
line changes. tests/ check the result still matches the source.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml
from zoomms import asm

ROOT = Path(__file__).resolve().parent.parent


def hex_block(code: bytes, indent: str) -> str:
    h = code.hex()
    return "\n".join(indent + h[i:i + 64] for i in range(0, len(h), 64))


def update(path: Path) -> bool:
    text = path.read_text()
    changed = False
    for sec in yaml.safe_load(text).get("section", []):
        if "source" not in sec:
            continue
        code = asm.assemble(ROOT / sec["source"], sec["addr"], whole_packets=True)
        # the `source:` line, then `data: >-` and its indented hex lines
        m = re.search(rf"(source: {re.escape(sec['source'])}\n(\s*)data: >-\n)((?:\2  \S+\n)+)", text)
        if m is None:
            sys.exit(f"{path}: no `data: >-` block right after `source: {sec['source']}`")
        new = hex_block(code, m.group(2) + "  ") + "\n"
        if m.group(3) != new:
            text = text[:m.start(3)] + new + text[m.end(3):]
            changed = True
            print(f"{path.name}: 0x{sec['addr']:08X} {len(code)} B from {sec['source']}")
    if changed:
        path.write_text(text)
    return changed


if __name__ == "__main__":
    if not asm.available():
        sys.exit(f"no assembler at {asm.PREFIX}as; set C6X_PREFIX")
    for p in sys.argv[1:] or sorted((ROOT / "patches").glob("*.yaml")):
        if not update(Path(p)):
            print(f"{Path(p).name}: up to date")
