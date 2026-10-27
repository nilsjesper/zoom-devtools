# LCD screenshot over SysEx

`patches/screenshot.yaml` (source `asm/screenshot/screenshot.S`) answers a new
SysEx request with the LCD memory; `zoomdev screenshot` asks for it and writes a
PNG. MS-70CDR 2.10 OS, so every MIDISTOMP v1.2+ pedal.

Ran on an MS-50G on 2026-10-23 at `0xC0100800`, on top of M3B7 (zoom-ms-midistomp
PR #49, closed: dev builds only, so it lives here). Moved to `0xC07F4000` for
v2.0, where the mixer code covers the old spot; the move changes only the three
`mvkl`/`mvkh` pairs that load FLAG and SNAP. Not yet run at the new address.

## Protocol

```
request  F0 52 00 <model> 7C <page> F7                                   page 0-15
reply    F0 52 00 <model> 7C <page> <128 bytes, Zoom 7-bit groups> F7    154 bytes
```

- `<model>` is the pedal's SysEx id (B14+212, `0x61` on the 70CDR OS); the
  stock state machine only reaches our code when it matches.
- Opcode `0x7C`: the stock command switch (`0xC00ADD04`) handles 00-09, 0E,
  16, 28, 29, 2B, 31-33, 50, 51 and 60, and drops anything else.
- 7-bit groups are the stock ones (`send_packed`, `0xC00AE060`, same as the
  `0x28` patch dump): a header byte whose bit `6 - j` is the top bit of data
  byte `j`, then 7 low-7-bit bytes; the last group of a page has 2 bytes.
- Page `p` is bytes `128p`..`128p + 127` of the 2 KiB at `0x11816800`: pages
  0-7 are plane A, 8-15 plane B.
- A request for page 0 first copies the whole 2 KiB to SNAP (`0xC07F4800`) with
  interrupts off (256 `lddw`/`stdw`, about 2,000 cycles); every page is sent
  from that copy. One pass of pages 0-15 is therefore one frame.
- No editor mode (`0x50`) needed, unlike `0x29`.

## LCD memory

- `0x11816800` plane A and `0x11816C00` plane B, 1 KiB each: 128 x 64, 8 rows
  of 128 column bytes, bit 0 on top. `0xC00B54AC` clears both planes
  (`0x11816800 + 1024 d` and `0x11816C00 + 1024 d`, `d` = 0 on these pedals); the
  AIS section at `0x11817000` starts right after them.
- The LCD send loop (`0xC00B59E0`-`0xC00B5A34`) sends `A xor B` for each byte,
  or A alone in a blink phase (`[B11] & 512` set). So B is the blinking
  highlight and the screen normally shows `A xor B`; that is the PNG default
  (`--plane a|b` writes one plane).
- 0x6800 is built by 49 `mvk`/`mvkh` sites, 0x6C00 by 10.

## Hook

Task_MIDI (70CDR) parses SysEx with a state byte (B14+227) and a jump table of
five states at `0xC00EF2D0` (`ldw *+b5[b4], b1 ; bnop b1` at `0xC00AED5C`):

| state | stock | job |
|---|---|---|
| 0 | `0xC00AEE06` | `52` (Zoom) or `7E` (universal) |
| 1 | `0xC00AEDD8` | device byte |
| 2 | `0xC00AEDAC` | model byte vs B14+212 |
| 3 | `0xC00AED88` | Zoom command bytes: `0xC00ADD04(byte, index)`, index at B14+232 |
| 4 | `0xC00AED64` | universal bytes: `0xC00AD694` |

`F7` calls the executor `0xC00AE6A4`, which runs the action code a command
left in B14+208 and clears it.

The patch writes one data word: table entry 3 (`0xC00EF2DC`, stock
`0xC00AED88`) points at `sysex_bytes` (`BASE`, `0xC07F4000`). It is entered by a
branch with A6 = byte and B3 = index (B14+232). Index 0 with byte `7C` marks the
message as ours (FLAG, `0xC07F47F8`) and sets the index to 1; index 1 is the
page, which it answers at once; later bytes are ignored. Every other message
goes on to the stock state 3 with A6 and B3 untouched. Exits branch to the loop
end `0xC00AEEE0`, as the stock states do (midi-clock's hook there still runs).
The `F7` that follows runs the executor with action 0, which does nothing, as
for any unknown command. The table is only referenced at `0xC00AED54`; the
midi-settings patch already retargets switch tables in the same section.

## Send path

The stock `0x29` reply (`0xC00AE634`) is copied:

- `Semaphore_pend(B14+804, -1)` (`0xC00C7EE0`), the MIDI out lock, held for the
  whole message so nothing interleaves; `Semaphore_post` (`0xC00D6B00`) after.
- `send_byte(b)` (`0xC00AE044`) -> `0xC00BF488(port 0, &b, 1)` ->
  `0xC00C2804`, which puts the bytes into the USB MIDI out ring
  (`0xC00C27D0`/`0xC00C2794`: 1024-byte ring, so 1023 usable) and wakes the USB
  side. A full ring drops the rest silently; nobody checks the return value.
- `send_packed(buf, len, 0)` (`0xC00AE060`) packs and sends through
  `send_byte`.

A whole frame unchunked would be 2 x 1,171 packed bytes, past the ring, so the
reply is one 154-byte page (the stock patch dump is 146 bytes) and the host
waits for each reply before the next request. The reply runs in Task_MIDI,
where the stock replies run, with a shallower stack.

## Code and RAM

`BASE` = `0xC07F4000`: code (480 bytes, loaded as an AIS section), `BASE + 0x7F8`
flag word, `BASE + 0x800` to `BASE + 0xFFF` snapshot (plain RAM, not loaded).
This is RAM test region 4; docs/memory.md has why, and the checks `zoomdev build`
runs before it adds the patch.

## Tearing

The copy runs with interrupts off, so no task runs during it. Task_MIDI can
still preempt Task_UpdateUI in the middle of drawing a screen (relative task
priorities not checked), so a snapshot can hold a half-drawn frame.
`zoomdev screenshot` reads the frame twice and retries, up to 3 passes, until
two passes match. Copying under `Semaphore_LCDUpdate` or from the draw hook
(`0xC00BB0EC`) would avoid that, at the cost of touching the UI task; not
needed unless the retries show it.

## Risks and open questions

- If a reply loses bytes (USB side slower than assumed), `parse_reply` rejects
  the short page and the host times out on it; it does not draw garbage.
- Zoom's editors never send `7C` to this model as far as known; any message
  with that command now gets a reply instead of being dropped.
- The flag word and snapshot are uninitialized RAM; the flag is written on
  every Zoom command byte before it is read, and a page other than 0 before any
  page 0 returns whatever is in the snapshot.
- The relative priority of Task_MIDI and Task_UpdateUI is unknown (see
  Tearing).

## Build and use

```sh
zoomdev build "MIDISTOMP V2.0 Updater.exe" --out "MIDISTOMP V2.0 DEV Updater.exe"
zoomdev screenshot --out lcd.png --raw lcd.bin
zoomdev screenshot --from-raw lcd.bin --plane a --out a.png
```
