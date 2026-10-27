"""zoomdev command line.

Offline (never touch a pedal): build, diff, screenshot --from-raw.
Talk to the pedal over USB MIDI: screenshot, backup, verify, read, send, monitor
(monitor only listens).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCHES = ROOT / "patches"


def cmd_build(args) -> int:
    from . import overlay

    out = Path(args.out)
    mac = out.suffix.lower() in (".app", ".zip")
    if mac and not args.mac_app:
        print("error: a .app or .zip output needs --mac-app (Zoom's MS-70CDR v2.10 Mac updater)", file=sys.stderr)
        return 1
    if out.exists():
        print(f"error: {out} already exists", file=sys.stderr)
        return 1
    patches = args.patch or sorted(PATCHES.glob("*.yaml"))
    try:
        exe, log = overlay.apply(args.updater, patches)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for line in log:
        print(line)
    if not mac:
        out.write_bytes(exe)
        print(f"wrote {out}")
        return 0
    from zoomms import cli as zoomms_cli

    with tempfile.TemporaryDirectory() as tmp:
        tmp_exe = Path(tmp) / f"{out.stem}.exe"
        tmp_exe.write_bytes(exe)
        return zoomms_cli.main(["build-mac", args.mac_app, "--from-exe", str(tmp_exe), "--out", str(out)])


def cmd_screenshot(args) -> int:
    from . import screenshot

    if args.from_raw:
        planes = Path(args.from_raw).read_bytes()
    else:
        planes = screenshot.capture(args.port, model=int(args.model, 0))
    if args.raw:
        Path(args.raw).write_bytes(planes)
        print(f"wrote {args.raw} ({len(planes)} B: plane A, then plane B)")
    Path(args.out).write_bytes(screenshot.png(screenshot.pixels(planes, args.plane), args.scale))
    print(f"wrote {args.out}")
    return 0


def _connect(args):
    from .pedal import Pedal

    pedal = Pedal(args.port)
    model = pedal.identify()
    if model is None:
        pedal.close()
        raise RuntimeError(f"{pedal.port}: no identity reply")
    print(f"{pedal.port}: model {model:02X}")
    return pedal


def _read_all(args) -> list[list[int]]:
    from .backup import name

    with _connect(args) as pedal:
        pedal.editor(True)
        try:
            print("current program:", pedal.program())
            dumps = pedal.read_all(lambda n, d: print(n, len(d), "bytes", name(d)))
            print("restored program:", pedal.program())
        finally:
            pedal.editor(False)
    return dumps


def cmd_backup(args) -> int:
    from . import backup

    out = Path(args.file)
    if out.exists():
        print(f"error: {out} already exists", file=sys.stderr)
        return 1
    out.write_text(backup.dumps_text(_read_all(args)))
    print(f"wrote {out}")
    return 0


def cmd_verify(args) -> int:
    from . import backup

    want = backup.read(args.file)
    got = {n: bytes(d) for n, d in enumerate(_read_all(args))}
    problems = backup.compare(want, got)
    for line in problems:
        print(line)
    print(f"{len(got) - len(problems)} of {len(got)} patches match {args.file}" if problems
          else f"all {len(got)} patches match {args.file}")
    return 1 if problems else 0


def cmd_diff(args) -> int:
    from . import backup

    a, b = backup.read(args.a), backup.read(args.b)
    problems = backup.compare(a, b)
    for line in problems:
        print(line)
    if not problems:
        print(f"all {len(a)} patches match")
    return 1 if problems else 0


def cmd_read(args) -> int:
    from .pedal import hexs

    with _connect(args) as pedal:
        pedal.editor(True)
        try:
            print("program:", pedal.program())
            for c in (0x07, 0x16, 0x2B, 0x60):
                print(f"cmd {c:02X}:", " | ".join(hexs(m) for m in pedal.xfer(pedal.zoom(c))) or "(none)")
            dump = pedal.patch()
            print("patch:", hexs(dump) if dump else "(none)")
        finally:
            pedal.editor(False)
    return 0


def cmd_send(args) -> int:
    from .pedal import Pedal, hexs

    try:
        data = [int(b, 16) for b in args.bytes]
    except ValueError:
        print("error: bytes are hex, e.g. send F0 52 00 61 29 F7", file=sys.stderr)
        return 1
    with Pedal(args.port) as pedal:
        print("sent: ", hexs(data))
        replies = pedal.xfer(data, args.wait)
        print("reply:", "\n       ".join(hexs(m) for m in replies) or "(none)")
    return 0


def cmd_monitor(args) -> int:
    from . import monitor

    monitor.run(args.seconds, everything=args.all)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zoomdev", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build", help="add the dev patches to a built MIDISTOMP updater (offline)")
    p.add_argument("updater", help="a built MIDISTOMP Windows updater .exe (e.g. a release or beta)")
    p.add_argument("--out", required=True, help=".exe, or .app/.zip for a Mac updater (needs --mac-app)")
    p.add_argument("--patch", action="append", help="dev patch file (default: every patches/*.yaml)")
    p.add_argument("--mac-app", help="Zoom's MS-70CDR v2.10 Mac updater app, for a Mac output")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("screenshot", help="read the LCD over SysEx and write a PNG (needs a zoomdev build)")
    p.add_argument("--port")
    p.add_argument("--out", default="screenshot.png")
    p.add_argument("--scale", type=int, default=4, help="pixels per LCD pixel (default 4)")
    p.add_argument("--plane", choices=["xor", "a", "b"], default="xor",
                   help="xor: what the screen shows (default); a, b: one LCD plane")
    p.add_argument("--raw", help="also save the 2 KiB dump (plane A, then B)")
    p.add_argument("--from-raw", help="decode a saved dump instead of asking the pedal")
    p.add_argument("--model", default="0x61", help="SysEx model id (default 0x61, the MS-70CDR OS)")
    p.set_defaults(func=cmd_screenshot)

    for name, func, text in (
            ("backup", cmd_backup, "save all 50 patches to a new file (steps through them with Program Change)"),
            ("verify", cmd_verify, "read all 50 patches and compare them with a backup file")):
        p = sub.add_parser(name, help=text, description=text + ". Ends with editor off (51), which discards "
                           "unsaved edits; with AUTO SAVE on, leaving the current patch stores them first.")
        p.add_argument("file")
        p.add_argument("--port")
        p.set_defaults(func=func)

    p = sub.add_parser("diff", help="compare two backup files (offline; the model byte is ignored)")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("read", help="current program, replies to 07 16 2B 60, current patch (ends with editor off)")
    p.add_argument("--port")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("send", help="send one raw MIDI message as is and print what comes back")
    p.add_argument("bytes", nargs="+", help="hex bytes, e.g. F0 52 00 61 29 F7")
    p.add_argument("--wait", type=float, default=0.8, help="seconds to collect replies (default 0.8)")
    p.add_argument("--port")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("monitor", help="print incoming MIDI from the Zoom and the Chocolate (listens only)")
    p.add_argument("--seconds", type=float, default=120)
    p.add_argument("--all", action="store_true", help="every MIDI input, not just ZOOM and SINCO")
    p.set_defaults(func=cmd_monitor)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, TimeoutError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
