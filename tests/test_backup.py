from zoomdev import backup
from zoomdev.cli import main


def dump(model=0x61, name="Bass Oct", fill=0x00):
    """146 bytes shaped like a patch dump, with the name's ASCII at 132-139."""
    d = bytearray([0xF0, 0x52, 0x00, model, 0x28] + [fill] * 140 + [0xF7])
    d[132:132 + len(name)] = name.encode()
    return bytes(d)


def test_text_round_trip(tmp_path):
    dumps = [list(dump(name=f"P{n}")) for n in range(3)]
    f = tmp_path / "b.txt"
    f.write_text(backup.dumps_text(dumps))
    assert f.read_text().splitlines()[1].startswith("1 F0 52 00 61 28 ")
    assert backup.read(f) == {n: bytes(d) for n, d in enumerate(dumps)}


def test_name():
    assert backup.name(dump(name="Bass Oct")) == "Bass Oct"
    assert backup.name(dump()[:100]) == ""


def test_model_byte_ignored():
    assert backup.differing(dump(model=0x58), dump(model=0x61)) == []
    assert backup.compare({0: dump(model=0x58)}, {0: dump(model=0x61)}) == []


def test_compare_reports_bytes_and_missing():
    a = {0: dump(), 1: dump(), 2: dump()}
    changed = bytearray(dump())
    changed[20] = 0x7F
    b = {0: dump(), 1: bytes(changed), 3: dump()}
    lines = backup.compare(a, b)
    assert lines == ["patch 2 'Bass Oct': 1 bytes differ: 20: 00->7F",
                     "patch 3: only in the first",
                     "patch 4: only in the second"]


def test_compare_length():
    assert backup.differing(dump(), dump()[:-1]) == [145]


def test_cli_diff(tmp_path, capsys):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text(backup.dumps_text([list(dump(model=0x58))]))
    b.write_text(backup.dumps_text([list(dump(model=0x61))]))
    assert main(["diff", str(a), str(b)]) == 0
    b.write_text(backup.dumps_text([list(dump(fill=1))]))
    assert main(["diff", str(a), str(b)]) == 1
    assert "patch 1" in capsys.readouterr().out
