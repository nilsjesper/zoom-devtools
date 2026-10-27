# zoom-devtools

Tools for developing and testing [MIDISTOMP](https://github.com/matujuice/zoom-ms-midistomp)
on a Zoom MS pedal. None of this goes into release firmware. The LCD screenshot
needs code on the pedal, so it ships as a dev patch that `zoomdev build` adds to
an updater someone already built, such as a release or a beta.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

This pulls `zoomms` from zoom-ms-midistomp at the commit pinned in
`pyproject.toml`. To work against a local checkout instead:
`.venv/bin/pip install -e ~/code/personal/zoom-ms-midistomp`.

## Commands

Offline:

| command | does |
|---|---|
| `zoomdev build UPDATER.exe --out OUT.exe` | add every `patches/*.yaml` to a built MIDISTOMP Windows updater |
| `zoomdev build UPDATER.exe --mac-app ZOOM.app --out OUT.app` | the same as a Mac updater (`.zip` works too) |
| `zoomdev diff A.txt B.txt` | compare two patch backups |
| `zoomdev screenshot --from-raw lcd.bin --out lcd.png` | decode a saved LCD dump |

These talk to the pedal over USB MIDI:

| command | sends |
|---|---|
| `zoomdev screenshot --out lcd.png` | `7C` requests; needs a dev build on the pedal |
| `zoomdev backup FILE` | editor on, Program Change through all 50 patches, a dump of each, back to the start patch, editor off |
| `zoomdev verify FILE` | the same reads as `backup`, compared with FILE |
| `zoomdev read` | editor on, `33`, `07`, `16`, `2B`, `60`, `29`, editor off |
| `zoomdev send F0 52 00 61 29 F7` | one raw message; prints the replies |
| `zoomdev monitor` | nothing; prints MIDI from the Zoom and the Chocolate |

`backup`, `verify` and `read` start with an identity request (`F0 7E 00 06 01 F7`)
to learn the model byte. `backup` and `verify` change patches with Program Change, so with AUTO SAVE on,
unsaved edits on the current patch are stored when it is left. The final editor
off (`51`) discards any edits that are still unsaved. Backups use the format of
zoom-ms-midistomp `firmware/backups/`, and compare without the model byte, so
backups from the MS-50G OS (`0x58`) and the 70CDR OS (`0x61`) compare cleanly.

## Dev builds

```sh
zoomdev build "MIDISTOMP V2.0 Updater.exe" --out "build/beta1/MIDISTOMP V2.0 DEV Updater.exe"
zoomdev build "MIDISTOMP V2.0 Updater.exe" \
  --mac-app "ZOOM MS-70CDR v2.10 Updater.app" --out "build/beta1/MIDISTOMP V2.0 DEV Updater.app"
xattr -cr "build/beta1/MIDISTOMP V2.0 DEV Updater.app"
codesign --force --deep --sign - "build/beta1/MIDISTOMP V2.0 DEV Updater.app"
```

Only the main OS changes: the flash script and the boot logo are the input's.
The Mac title can be 27 characters at most (the length of Zoom's own). The
pedal still shows the input's version, so a dev build looks like the release
on screen; `zoomdev screenshot` answering is how to tell them apart.

Dev code goes in RAM no upstream code uses, and the build stops if the updater
already claims any of it. See [docs/memory.md](docs/memory.md).

## Patches

| patch | adds |
|---|---|
| `screenshot` | SysEx `7C`: the LCD's two planes, 16 pages of 128 bytes ([docs/screenshot.md](docs/screenshot.md)) |

Assembly changes need the tic6x binutils (zoom-ms-midistomp
`scripts/build-c6x-binutils.sh`):

```sh
export C6X_PREFIX=~/.local/c6x/bin/tic6x-elf-
.venv/bin/python scripts/update_hex.py
```

## Tests

```sh
.venv/bin/pytest
```

With `C6X_PREFIX` set, the tests also check each patch's hex against its
source. With `ZOOMDEV_UPDATER` pointing at a built MIDISTOMP updater .exe,
they build a dev updater from it and check that only the patched bytes changed.
Pedal commands are tested against a simulated pedal.

## History

The screenshot started as zoom-ms-midistomp PR #49. `read`, `send`, `backup`
and `monitor` replace `tools/zoomio.swift` and `tools/midimon.swift` from
zoom-ms-chocolate-midi; zoomio's `probe` and `edits` were one-off experiments
and were not ported.
