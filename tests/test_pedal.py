"""Pedal session logic against a simulated pedal (no MIDI hardware)."""

import mido
import pytest

from zoomdev import pedal as pedal_mod
from zoomdev.cli import main


class FakeZoom:
    """Answers identity, 33 and 29 like a MIDISTOMP v1.2+ pedal, and loads a
    patch on Program Change on its channel with its PROG CH START NO."""

    def __init__(self, model=0x61, channel=0, start=0, program=7):
        self.model, self.channel, self.start, self.current = model, channel, start, program
        self.editor = False
        self.queue = []
        self.sent = []
        self.patches = [bytes([0xF0, 0x52, 0x00, model, 0x28, n] + [0] * 139 + [0xF7]) for n in range(50)]

    def receive(self, data):
        self.sent.append(data)
        if data == [0xF0, 0x7E, 0x00, 0x06, 0x01, 0xF7]:
            self.reply([0xF0, 0x7E, 0x00, 0x06, 0x02, 0x52, self.model, 0x00, 0x00, 0x00,
                        0x32, 0x2E, 0x31, 0x30, 0xF7])
        elif data[:4] == [0xF0, 0x52, 0x00, self.model]:
            cmd = data[4]
            if cmd in (0x50, 0x51):
                self.editor = cmd == 0x50
            elif cmd == 0x33:
                self.reply([0xC0, self.current])
            elif cmd == 0x29 and self.editor:
                self.reply(list(self.patches[self.current]))
        elif data[0] == 0xC0 | self.channel and 0 <= data[1] - self.start < 50:
            self.current = data[1] - self.start

    def reply(self, data):
        self.queue.append(mido.Message.from_bytes(data))


class FakeOut:
    def __init__(self, zoom):
        self.zoom = zoom

    def send(self, msg):
        self.zoom.receive(msg.bytes())

    def close(self):
        pass


class FakeIn:
    def __init__(self, zoom):
        self.zoom = zoom

    def iter_pending(self):
        while self.zoom.queue:
            yield self.zoom.queue.pop(0)

    def close(self):
        pass


@pytest.fixture
def zoom(monkeypatch):
    z = FakeZoom()
    monkeypatch.setattr(mido, "get_output_names", lambda: ["ZOOM MS Series"])
    monkeypatch.setattr(mido, "get_input_names", lambda: ["ZOOM MS Series"])
    monkeypatch.setattr(mido, "open_output", lambda name: FakeOut(z))
    monkeypatch.setattr(mido, "open_input", lambda name: FakeIn(z))
    monkeypatch.setattr(pedal_mod.time, "sleep", lambda s: None)
    return z


def test_identify_sets_model(zoom):
    zoom.model = 0x58
    zoom.patches = []
    with pedal_mod.Pedal() as p:
        assert p.identify() == 0x58
        assert p.zoom(0x33) == [0xF0, 0x52, 0x00, 0x58, 0x33, 0xF7]


def test_program_and_patch(zoom):
    with pedal_mod.Pedal() as p:
        p.identify()
        assert p.program() == 7
        assert p.patch() is None  # 29 needs editor mode
        p.editor(True)
        assert p.patch()[5] == 7


@pytest.mark.parametrize("channel,start", [(0, 0), (1, 1), (15, 0)])
def test_find_program_change(zoom, channel, start):
    zoom.channel, zoom.start = channel, start
    with pedal_mod.Pedal() as p:
        p.identify()
        assert p.find_program_change()
        assert (p.pc_channel, p.pc_offset) == (channel, start)
        assert zoom.current == 7  # back on the start patch


def test_read_all_restores_program(zoom):
    zoom.channel, zoom.start = 1, 1
    with pedal_mod.Pedal() as p:
        p.identify()
        p.editor(True)
        dumps = p.read_all()
    assert [d[5] for d in dumps] == list(range(50))
    assert zoom.current == 7


def test_read_all_without_program_change(zoom):
    zoom.channel = 16  # listens on no channel
    with pedal_mod.Pedal() as p:
        p.identify()
        with pytest.raises(RuntimeError, match="ignores Program Change"):
            p.read_all()


def test_backup_then_verify(zoom, tmp_path, capsys):
    f = tmp_path / "backup.txt"
    assert main(["backup", str(f)]) == 0
    assert len(f.read_text().splitlines()) == 50
    assert not zoom.editor  # ends with editor off
    assert main(["backup", str(f)]) == 1  # never overwrites
    assert main(["verify", str(f)]) == 0
    zoom.patches[12] = zoom.patches[12][:20] + b"\x01" + zoom.patches[12][21:]
    assert main(["verify", str(f)]) == 1
    assert "patch 13" in capsys.readouterr().out
