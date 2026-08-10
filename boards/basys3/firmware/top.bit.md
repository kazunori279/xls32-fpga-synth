# Prebuilt bitstream

`top.bit` is a ready-to-flash bitstream for the **Basys 3** board (Xilinx `xc7a35t`), so you can
run the synth **without building** (no Vivado / F4PGA toolchain needed — just `openFPGALoader`).

It is the full XLS32 engine: 32-voice polyphony, 4 multitimbral parts, resonant multimode filter,
LFO, unison, cross-osc FM/ring-mod, and the block-RAM effects (chorus / delay / reverb).

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

Rebuilt **2026-08-10** from the tree at `5cf8f83` — Vivado 2024.2, `STAGES=48`, `WCT=48`, through
`boards/basys3/scripts/remote_build.sh` with `BACKEND=vivado`. For the first time since the M20 tree
split, the shipped bitstream and `core/synth.x` are the same engine. What it replaced was
byte-for-byte the 2026-07-13 initial-release blob (`a38cffe`), built before M22's 18×18 narrowing
and before M29; it stayed that way because refreshing it needs Vivado on an x86 machine, which no
hosted runner can provide.

The build closed timing on the `xc7a35t` at 100 MHz:

| | |
|---|---|
| Worst setup slack | **+0.012 ns** (MET) — **0** failing endpoints |
| Slice LUTs        | 10,480 / 20,800 (50.4 %) |
| Slice registers   | 17,830 / 41,600 (42.9 %) |
| Block RAM         | 32.5 / 50 tiles (65.0 %) |
| DSP48E1           | 26 / 90 (28.9 %) |

**Then it was flashed and played**, because timing closure says the netlist meets its constraints
and nothing about what the synth sounds like, and on this project the two have come apart before.
`boards/basys3/scripts/verify.sh` flashes over JTAG and plays a chord, checking each note by FFT
against the frequency it asked for. On a 6-second capture (`host/record_wav.py`) A major 7 returned
**439.9 / 554.2 / 659.2 / 830.6 Hz** against a nominal 440.0 / 554.4 / 659.3 / 830.6 — inside
0.05 % — at a peak of 30,000 of 32,768 with no clipping.

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
