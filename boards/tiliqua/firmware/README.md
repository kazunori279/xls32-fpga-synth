# Prebuilt bitstream archives

Two ready-to-flash **bitstream archives** for the **Tiliqua R5** (Lattice `LFE5U-25F`), so you can
run the synth **without building** — no Amaranth, no yosys, no Docker.

| | voices | die | `clk` post-route | slot | |
|---|---|---|---|---|---|
| **`xls24-r5.tar.gz`** | 24 | 93.5 % | 56.63 MHz | **7** | **formal** — what this repo stands behind |
| `xls32-r5.tar.gz` | 32 | 98.3 % | 48.37 MHz | 6 | **experimental** — see the warning below |

Both are the same engine at 48 kHz: 4 multitimbral parts, resonant multimode filter, LFO, unison,
cross-osc FM/ring-mod, the block-RAM effects (chorus / ping-pong echo / 8-comb Freeverb), UAC2 audio
+ USB-MIDI over `usb2`, TRS MIDI in, and the 720×720p60 beam-raced visualiser on the DVI output.
They differ in one number: how many notes can sound at once before the engine steals a voice.

An archive is the format the Tiliqua bootloader wants: the bitstream plus a `manifest.json` that
tells the bootloader what to call the slot, what to print beside it, and — the part that matters —
what to program the SI5351 clock generator to. Both pin `clk0_hz: 12288000` and `clk1_hz: 39070000`,
which is why a module booted from either slot is always clocked correctly.

## Take the 24-voice one

Not because 32 does not work here — it does, and it has been the shipped build for months. Because
of what it is betting on.

The `clk` domain (`sync` and `usb` are one net, fixed at 60 MHz by ULPI) has never closed timing on
this design. At 32 voices the design fills 98.3 % of the die and closes at **48.37 MHz**, which is a
bet that the silicon is **24 % faster than nextpnr models it**. That bet pays on this desk. It did
not pay on one of the vendor's two modules, which is
[issue #3](https://github.com/kazunori279/xls32-fpga-synth/issues/3) and the reason any of this
exists. At 24 voices the same design is 93.5 % and closes at **56.63 MHz** — the same bet at **6 %**.

Eight fewer notes of polyphony for a quarter of the risk. If a module refuses to enumerate,
drops audio intermittently, or gets worse as it warms up, try slot 7 before you try anything else.

The 24-voice build is graded **99.8/100 (A+)** on the module — 174 pass / 1 warn / 0 fail over the
175-case suite, frame gaps 0.00 % across 175 captures, audio clock 12.288 MHz. The one warn is
`filter_sweep`, [issue #7](https://github.com/kazunori279/xls32-fpga-synth/issues/7), which both
builds share.

The visualiser draws **6 × 4** here and 8 × 4 on the 32-voice build: one tile per voice either way,
with the grid derived from the voice count rather than fixed at 32. Until M37 it was fixed, and the
bottom row of this build never lit ([#40](https://github.com/kazunori279/xls32-fpga-synth/issues/40)).

## Flash it

Either way round writes an archive to a slot; the archive does not care which. **Slot 7 for
24 voices and slot 6 for 32** is what this repo's docs and tooling assume, and keeping them apart
means you can A/B the two without reflashing.

The 24-voice build is also **in the flasher's own Community list as `XLS24`**, merged upstream on
2026-08-22, so flashing it needs no file at all. That entry is `xls24-9976c4e-r5.tar.gz` — M37's
archive at 54.30 MHz — and it is deliberately one netlist behind the `.tar.gz` here: it is the copy
the module's maker tested across several of his own Tiliquas before merging, and it is the only
build with evidence from more than one die. Upload the file below to flash what is committed here.

```bash
# A) No toolchain — open https://apfaudio.github.io/tiliqua-webflash/ in Chrome, pick the module
#    over WebUSB, then either choose XLS24 from the Community list (24 voices only, 54.30 MHz) or
#    upload one of these archives. Choose the slot.

# B) With the vendor SDK checked out:
cd ~/Documents/GitHub/tiliqua/gateware
pdm flash archive <this-repo>/boards/tiliqua/firmware/xls24-r5.tar.gz --slot 7
```

Catch the bootloader's five-second countdown and pick the slot from the menu once; every cold boot
from then on loads it directly. See the repo README §2 "Tiliqua — flash and go" for the full
walkthrough, including what you should see and hear when it comes up.

## What they were built from

The manifest's `tag` field names the commit. A trailing `-` means the tree was dirty at build time
and the archive should not be trusted as a release.

The commit alone does not tell you whether an archive is still *current*, so the sources that feed
each one are hashed into `scripts/artefact_hashes.json`:

```bash
uv run --no-project python scripts/check_artefacts.py tiliqua      # both
uv run --no-project python scripts/check_artefacts.py tiliqua-24   # just the formal one
```

That catches uncommitted edits too, which the `tag` cannot.

The other half of the bitstream is the vendor's — luna, luna-soc and the `tiliqua` gateware, linked
in from `$TILIQUA_SDK`. That lives outside this repo, so it is recorded by **commit** rather than by
hash, and the check reports it changing since the build as staleness like any other source. One
caveat, which the output states rather than papers over: with no checkout on the machine (CI, or a
fresh clone) it can only say it could not verify. Both archives now carry the SDK commit they were
built against; the 32-voice one predated that field until M40.

Neither archive is waived. The 32-voice one was, for three milestones: M37's four changes —
`voices.py`, the tile index, the panel strings and the router options — were rebuilt against at 24
voices only, because at this occupancy re-running the 32-voice build means a fresh seed sweep and
the flashed bitstream was not wrong, only routed with the old options and announcing itself with
the old wording. M41 ran the sweep and the waiver is gone
([#46](https://github.com/kazunori279/xls32-fpga-synth/issues/46)).

The 24-voice archive was rebuilt in the same milestone, for the record rather than for the
bitstream: M39 touched `build.sh` and `top.py`, which is enough to report it stale even though
`XLS32_SPLIT_CLOCKS` is off by default and neither edit reaches the netlist. It came back at
**22,722 cells and 56.63 MHz** — the shipped numbers to the cell and the megahertz — and graded the
same 99.8/100 (A+), 174/1/0. A rebuild that reproduces its own numbers exactly is the cheapest
evidence there is that the seed pin and the build script still mean what they say.

One thing the record has to work harder at since there are two archives. They are built from *the
same committed sources*: the voice count is not in the tree, it is rewritten into a throwaway copy
of `core/synth.x` by `spike/voices_variant.py` at build time. So `voices` is recorded as a build
parameter alongside `STAGES` and `WCT`, and without it the check would happily report a 24-voice
archive as matching a 32-voice source set. The board cannot help here either — nothing in the
bitstream reports its own voice count, so the USB stamp says which *commit* is flashed and not which
of the two builds. The panel's SETTINGS ▸ Firmware block is worded to admit that.

## Rebuilding

Each file is a copy of the archive the build drops beside `top.bit` (both gitignored). See README §3
"Tiliqua — build, flash, verify", then:

```bash
bash boards/tiliqua/build.sh                  # 24 voices, the default
VOICES=32 bash boards/tiliqua/build.sh        # the experimental one

cp build/tiliqua/build/xls24-r5/xls24-*-r5.tar.gz boards/tiliqua/firmware/xls24-r5.tar.gz
uv run --no-project python scripts/check_artefacts.py --update tiliqua-24
uv run --no-project python scripts/build_firmware_json.py
```

Run `--update` only when the archive you just copied in was built from the tree as it stands — the
record is a provenance claim, and a false one is worse than none. Build on a **clean tree**: a dirty
one appends `-dirty` to the stamp, and because the stamp is a string descriptor baked into a ROM,
those six characters change the netlist's width and re-draw the seed lottery `build.sh` pins a seed
against. That is not theoretical — M37 measured a dirty build at 22,745 cells and the clean one at
22,985, and the seed that won the first was the *worst* of the second.

`build.sh` pins **seed 7** for 24 voices and **seed 10** for 32. Those are not preferences. At this
occupancy the router either converges or runs away depending on the seed, the winners do not
transfer between netlists — seed 7 wins the 24-voice draw at 56.63 MHz and read 50.90, near the
bottom, on the netlist one commit earlier — and any netlist change costs a fresh sweep. Only seven
of the thirteen seeds tried on the 32-voice netlist converged at all; the other six had to be killed
by hand. `seed_sweep.sh` re-places an existing `top.json` under several seeds at once, which skips
the whole front of the build. The long version is in `build.sh`.

## History

The 32-voice archive was the only one until M36. What changed is not the engine but a measurement:
`voices_variant.py` had never rewritten one of the voice-count sites, so every reduced-voice build
anyone had ever made had its voice ring silently optimised away by yosys — smaller, faster on paper,
and completely silent. "24 voices is measured and dead" had been in the architecture notes for four
milestones on the strength of it
([#35](https://github.com/kazunori279/xls32-fpga-synth/issues/35),
[#36](https://github.com/kazunori279/xls32-fpga-synth/issues/36)).

Both archives carry the two engine DC fixes introduced in `290d00b` and `3aa0227`: the SVF no longer
latches a residue when a voice's envelope dies, so All Sound Off gives *exactly* zero rather than a
few hundred counts per part that has ever played; and the pulse wave subtracts its own duty-cycle
DC, which stops a loud pulse passage clipping on one rail only. That second fix is why a 78 %-duty
stack of 16 voices peaks at +1.04 / −1.10 where the pre-`3aa0227` archive peaked at +0.07 / −1.88
with its positive half eaten by the clamp. Both fixes are `core/synth.x` changes and so belong to
the Basys 3 too. That board carried a blob predating them for two weeks; it was rebuilt in M37 and
is back in step ([#41](https://github.com/kazunori279/xls32-fpga-synth/issues/41)).
