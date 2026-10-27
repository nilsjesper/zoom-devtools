# Where dev code goes in RAM

Dev patches live in RAM test region 4, `0xC07F4000`-`0xC07FFFFF` (48 KB of DDR).
The 70CDR OS never touches it (RAM test, zoom-ms-midistomp
docs/dev/ms70cdr-addresses.md), and no MIDISTOMP code uses it as of v2.0 beta1.

The rest of the free RAM is upstream's:

| range | used by (v2.0 beta1) |
|---|---|
| `0xC00F2000`-`0xC0116000` | MOD code, record 50, the MASTER screen's buffers. Grows with each release: the mixer C code ends at `0xC0106C80`, past where the screenshot first sat (`0xC0100800`) |
| `0xC0116000`-`0xC0198000` | looper buffer, region 1 |
| `0xC0198000`-`0xC0200000` | looper buffer, region 2 |
| `0xC0636980`-`0xC07D0000` | looper buffer, region 3 |
| `0xC07F4000`-`0xC0800000` | zoom-devtools |
| `0x80000000`-`0x80020000` | free (shared RAM, uncached) |

Region 4 is DDR with the same caching as the MOD code area, so code runs
there the same way.

## What `zoomdev build` checks

Before it adds a dev patch to an updater, `zoomdev build` collects what that
updater already claims:

- every section its OS loads
- the regions the looper's services block (`0xC00F3000`, magic `0x5A4C5301`)
  hands to the looper buffer

Each range the dev patch loads (`section`) or uses at run time (`ram`) must sit
inside one of the RAM test regions and overlap none of those claims; otherwise
the build stops and names the clash. Upstream buffers that are not loaded and
not in the looper table (the MASTER screen's FFT buffers, record 50) are below
`0xC0116000`, far from region 4, and not checked.

A patch's `write` entries carry the stock bytes they expect (`expect`), so if
upstream ever retargets the same hook, the build fails there too.

## Moving a patch

The screenshot code reaches stock code only through absolute addresses
(`mvkl`/`mvkh` + `b`), so changing `BASE` in its source and running
`scripts/update_hex.py` moves it; update the `ram` entries and the state table
`data` word in the YAML by hand. The tests check all three against the source.
