"""USB MIDI session with a Zoom MS pedal (ported from the Chocolate repo's
tools/zoomio.swift). Zoom SysEx is F0 52 00 <model> <command ...> F7; the model
byte comes from the identity reply: 0x58 on the MS-50G OS (stock, MIDISTOMP
v1.0/v1.1), 0x61 on MIDISTOMP v1.2+ (the MS-70CDR OS). The pedal ignores
SysEx with any other model byte.

Commands used here (zoom-ms-chocolate-midi docs/PROTOCOL.md):
  50 / 51   editor mode on / off (51 also discards unsaved edits)
  33        current program, answered with a Program Change C0 pp
  29        current patch, answered with a 28 patch dump (146 B on these pedals)
"""

from __future__ import annotations

import time

from zoomms.midi import find_port, parse_identity_reply

EDITOR_ON, EDITOR_OFF = 0x50, 0x51
GET_PROGRAM, GET_PATCH, PATCH_DUMP = 0x33, 0x29, 0x28
PATCHES = 50


def hexs(data) -> str:
    return " ".join(f"{b:02X}" for b in data)


class Pedal:
    """One connection: raw sends, timed reads, and the few Zoom requests above.
    Every read collects whole MIDI messages (bytes, F0..F7 for SysEx)."""

    def __init__(self, port: str | None = None):
        import mido

        out_name = port or find_port(mido.get_output_names())
        if out_name is None:
            raise RuntimeError("no Zoom MIDI port found; pass --port")
        in_name = out_name if out_name in mido.get_input_names() else find_port(mido.get_input_names())
        self.out = mido.open_output(out_name)
        self.inp = mido.open_input(in_name)
        self.port = out_name
        self.model = 0x61
        self.pc_channel, self.pc_offset = 0, 0

    def close(self) -> None:
        self.inp.close()
        self.out.close()

    def __enter__(self) -> Pedal:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- raw ----

    def send(self, data: list[int]) -> None:
        import mido

        self.out.send(mido.Message.from_bytes(list(data)))

    def take(self) -> list[list[int]]:
        """Everything received since the last take, one list per message."""
        return [msg.bytes() for msg in self.inp.iter_pending()]

    def xfer(self, data: list[int], wait: float = 0.4) -> list[list[int]]:
        """Send, wait the full time, return every message that came back."""
        self.take()
        self.send(data)
        time.sleep(wait)
        return self.take()

    def ask(self, data: list[int], want, timeout: float = 0.8) -> list[int] | None:
        """Send and return the first message `want(msg)` accepts, or None."""
        self.take()
        self.send(data)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in self.take():
                if want(msg):
                    return msg
            time.sleep(0.002)
        return None

    # ---- Zoom ----

    def zoom(self, *command: int) -> list[int]:
        return [0xF0, 0x52, 0x00, self.model, *command, 0xF7]

    def identify(self) -> int | None:
        """Ask for the identity, set self.model from it and return it (None: no reply)."""
        reply = self.ask([0xF0, 0x7E, 0x00, 0x06, 0x01, 0xF7],
                         lambda m: m[:1] == [0xF0] and m[1:2] == [0x7E] and m[3:5] == [0x06, 0x02])
        ident = reply and parse_identity_reply(reply[1:-1])
        if not ident or ident.manufacturer != 0x52:
            return None
        self.model = ident.family & 0x7F
        return self.model

    def editor(self, on: bool) -> None:
        self.send(self.zoom(EDITOR_ON if on else EDITOR_OFF))
        time.sleep(0.3)

    def program(self) -> int:
        """Current program, 0-based (-1: no answer)."""
        msg = self.ask(self.zoom(GET_PROGRAM), lambda m: len(m) == 2 and m[0] & 0xF0 == 0xC0, 0.5)
        return msg[1] if msg else -1

    def patch(self) -> list[int] | None:
        """The current patch as the pedal dumps it (F0 52 00 mm 28 ... F7)."""
        return self.ask(self.zoom(GET_PATCH),
                        lambda m: m[:2] == [0xF0, 0x52] and len(m) > 5 and m[4] == PATCH_DUMP, 1.0)

    def program_change(self, n: int, settle: float = 0.4) -> None:
        """Load patch n (0-based) with a Program Change on the found channel."""
        self.send([0xC0 | self.pc_channel, n + self.pc_offset])
        time.sleep(settle)

    def find_program_change(self) -> bool:
        """Find the channel and numbering the pedal listens to for Program Change.
        Stock: channel 1, PC 0 = patch 1. MIDISTOMP can filter to one MIDI CHANNEL
        and start at PC 1 (PROG CH START NO. 1). Goes back to the start patch."""
        start = self.program()
        for ch in range(16):
            self.send([0xC0 | ch, 1])
            time.sleep(0.4)
            p1 = self.program()
            self.send([0xC0 | ch, 2])
            time.sleep(0.4)
            p2 = self.program()
            if p1 >= 0 and p2 == p1 + 1:
                self.pc_channel, self.pc_offset = ch, 1 - p1
                self.program_change(start)
                return True
        return False

    def read_all(self, progress=None) -> list[list[int]]:
        """All patches, by stepping through them with Program Change; the start
        patch is loaded again at the end. With AUTO SAVE on, unsaved edits on the
        start patch are stored when the first Program Change leaves it."""
        start = self.program()
        if not self.find_program_change():
            raise RuntimeError("pedal ignores Program Change on every channel (PROG CH RECEIVE off?)")
        dumps = []
        try:
            for n in range(PATCHES):
                self.program_change(n)
                if self.program() != n:
                    raise RuntimeError(f"patch {n + 1} did not load")
                dump = self.patch()
                if dump is None:
                    raise RuntimeError(f"no dump for patch {n + 1}")
                dumps.append(dump)
                if progress:
                    progress(n, dump)
        finally:
            self.program_change(start)
        return dumps
