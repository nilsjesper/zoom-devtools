"""Print incoming MIDI from the Zoom and the Chocolate (SINCO), with a time
stamp and the port name (ported from the Chocolate repo's tools/midimon.swift).
Ports are looked up again every second, so a device that is unplugged or
power-cycled is picked up when it comes back. Read only: sends nothing.
"""

from __future__ import annotations

import time

from .pedal import hexs

NAMES = ("ZOOM", "SINCO")


def run(seconds: float, names: tuple[str, ...] = NAMES, everything: bool = False) -> None:
    import mido

    t0 = time.monotonic()
    ports: dict[str, object] = {}

    def say(*text) -> None:
        print(f"{time.monotonic() - t0:6.2f}s", *text, flush=True)

    next_scan = 0.0
    try:
        while (now := time.monotonic()) - t0 < seconds:
            if now >= next_scan:
                live = [n for n in mido.get_input_names() if everything or any(k in n.upper() for k in names)]
                for n in live:
                    if n not in ports:
                        ports[n] = mido.open_input(n)
                        say("listening:", n)
                for n in [n for n in ports if n not in live]:
                    ports.pop(n).close()
                    say("gone:", n)
                next_scan = now + 1.0
            for n, port in ports.items():
                for msg in port.iter_pending():
                    say(n, hexs(msg.bytes()))
            time.sleep(0.002)
    finally:
        for port in ports.values():
            port.close()
    print("done")
