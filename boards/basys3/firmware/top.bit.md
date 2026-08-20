# Prebuilt bitstream

`top.bit` is a ready-to-flash bitstream for the **Basys 3** board (Xilinx `xc7a35t`), so you can
run the synth **without building** (no Vivado / F4PGA toolchain needed — just `openFPGALoader`).

It is the full XLS32 engine: 32-voice polyphony, 4 multitimbral parts, resonant multimode filter,
LFO, unison, cross-osc FM/ring-mod, and the block-RAM effects (chorus / delay / reverb).

> **Stale as of 2026-08-20, and knowingly so.** Two engine fixes have landed in `core/synth.x`
> since this was built — `290d00b` (the SVF's latched DC residue) and `3aa0227` (the pulse wave's
> own duty-cycle DC) — so what you flash from here is a measurably different synth from what the
> source tree describes. Both are DC defects; neither stops it playing. `check_artefacts.py`
> reports the drift, and this note exists for anyone who flashes without running it.
>
> It is stale rather than unfixed because refreshing it needs Vivado on an x86 machine, and the
> Tiliqua fixes were verified on hardware that is on the desk. Until it is rebuilt, `core/synth.x`
> is no longer *bit-exact* across the two boards — the first time that has been deliberate.

## Flash it

```bash
# volatile (SRAM) — fast, lost on power-off:
openFPGALoader -b basys3 firmware/top.bit

# persistent (SPI flash) — survives power cycles, boots standalone:
openFPGALoader -b basys3 -f firmware/top.bit
```

For standalone boot from flash, set the Basys 3 **mode jumper JP1 to QSPI**.

See the repo README §2 "Basys 3 — flash and go" for the full walkthrough. The Tiliqua equivalent is
`boards/tiliqua/firmware/`, which ships a flash archive rather than a bare bitstream.

## What it was built from

Rebuilt **2026-08-11** for M34, the channel mode messages — Vivado 2024.2, `STAGES=48`, `WCT=48`,
through `boards/basys3/scripts/remote_build.sh` with `BACKEND=vivado`. `core/synth.x` is shared with
the Tiliqua, so CC120 / CC121 / CC123 landed on both boards at once; this is the build that carries
them here. The previous refresh (2026-08-10, `5cf8f83`) was the first since the M20 tree split to
put the shipped bitstream and `core/synth.x` back in step, replacing what was byte-for-byte the
2026-07-13 initial-release blob (`a38cffe`) — built before M22's 18×18 narrowing and before M29, and
stuck there because refreshing it needs Vivado on an x86 machine, which no hosted runner provides.

The build closed timing on the `xc7a35t` at 100 MHz:

| | |
|---|---|
| Worst setup slack | **+0.276 ns** (MET) — **0** failing endpoints |
| Slice LUTs        | 10,541 / 20,800 (50.7 %) |
| Slice registers   | 18,029 / 41,600 (43.3 %) |
| Block RAM         | 33 / 50 tiles (66.0 %) |
| DSP48E1           | 26 / 90 (28.9 %) |

The slack went *up* while the design gained a feature: M34 also deleted an "already releasing?"
guard in `apply_off` that could never change the outcome, and its 32 comparators were sitting on
this exact path. +0.012 ns before, +0.276 after.

**Then it was flashed and played**, because timing closure says the netlist meets its constraints
and nothing about what the synth sounds like, and on this project the two have come apart before.
`boards/basys3/scripts/verify.sh` flashes over JTAG and plays a chord, checking each note by FFT
against the frequency it asked for. A major 7 came back at **438 / 554 / 658 / 830 Hz** against a
nominal 440.0 / 554.4 / 659.3 / 830.6 — 4 of 4 found, every one inside the 7.8 Hz bin the 8192-point
DFT can resolve. The 2026-08-10 build, measured the same way on a longer 6-second capture
(`host/record_wav.py`), returned 439.9 / 554.2 / 659.2 / 830.6 at a peak of 30,000 of 32,768 with no
clipping.

It was also run head to head against the blob it replaces, ten flashes alternating between the two,
on a C2/C4/C6 spread wide enough to stress the octave shifts: **12 of 15 notes for July, 13 of 15
for this one** — indistinguishable, and both flaky, which is a property of the test and not of
either bitstream (see [docs/TODO.md](../../../docs/TODO.md)). Interleaving was not fussiness: run in
blocks, the same comparison had said 9 of 9 for July against 4 of 9 for the rebuild — a convincing
regression that does not exist — and a single run of each, earlier, had said exactly the opposite.

To see the drift, and to re-record provenance after a rebuild:

```bash
uv run --no-project python scripts/check_artefacts.py           # what changed since the build
uv run --no-project python scripts/check_artefacts.py --update  # after copying in a fresh build
```

The record it compares against is `scripts/artefact_hashes.json`.

## Rebuilding

This file is a copy of `build/top.bit` (gitignored build output). To regenerate it, see README §3
"Basys 3 — build, flash, verify", then:

```bash
cp build/top.bit boards/basys3/firmware/top.bit
uv run --no-project python scripts/check_artefacts.py --update basys3
```
