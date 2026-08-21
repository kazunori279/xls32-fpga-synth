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

Rebuilt **2026-08-22** for M37 (`2fcb2b9`) — Vivado 2024.2, `STAGES=48`, `WCT=48`, through
`boards/basys3/scripts/remote_build.sh` with `BACKEND=vivado`. It exists to clear the staleness the
previous copy carried in this file for two weeks: `290d00b` (the SVF's latched DC residue) and
`3aa0227` (the pulse wave's own duty-cycle DC) had landed in the shared `core/synth.x` and been
verified on the Tiliqua, leaving the two boards deliberately not bit-exact. They are back in step.
Note that `c837e59` also went by in that window, so the sustain pedal on CC64 that the 2026-08-11
blob answered to is gone from this one; CC120 / CC121 / CC123 stay.

The rebuild did not close timing on the first attempt, which is why `2fcb2b9` is an RTL commit and
not just a copy. See the `ardy` comment in `rtl/top.v` and the two exceptions it licenses in
`rtl/build_vivado.tcl`; the short version is that lengthening the engine's combinational
backpressure chain turned a handshake that had always been sloppy into a real 10 ns path, on the
clock-enable pin of nearly every register in the engine. Second attempt, on the `xc7a35t` at
100 MHz:

| | 2026-08-11 (M34) | this build |
|---|---|---|
| Worst setup slack | +0.276 ns (MET) — 0 failing | **+1.322 ns** (MET) — **0** failing |
| Slice LUTs        | 10,541 / 20,800 (50.7 %) | 10,568 / 20,800 (50.8 %) |
| Slice registers   | 18,029 / 41,600 (43.3 %) | 17,978 / 41,600 (43.2 %) |
| Block RAM         | 33 / 50 tiles (66.0 %)   | 33 / 50 tiles (66.0 %) |
| DSP48E1           | 26 / 90 (28.9 %)         | 26 / 90 (28.9 %) |

A full nanosecond more slack than M34 is not one AND term being clever. The run gained two timing
exceptions as well, and which of the three bought the margin was not isolated — one build each was
not worth the VM time. The likely candidate is the `/6` exception on `rwetL_p`/`rwetR_p`: Vivado had
absorbed `revwetL_reg` into the DSP as its B input register, so the name-matched constraint went
with the cell, and the reverb wet multiply spent the first attempt reporting the worst path in the
design (−0.565 ns) against a requirement six times too tight. That path was almost certainly also
what M34 measured its +0.276 against.

**Then it was flashed and played**, because timing closure says the netlist meets its constraints
and nothing about what the synth sounds like, and on this project the two have come apart before.
`boards/basys3/scripts/verify.sh` flashes over JTAG and plays a chord, checking each note by FFT
against the frequency it asked for. A major 7 came back at **438 / 554 / 658 / 830 Hz** against a
nominal 440.0 / 554.4 / 659.3 / 830.6 — 4 of 4 found, every one inside the 7.8 Hz bin the 8192-point
DFT can resolve, and the frame phase held over all 40 windows of the 80,163-sample capture. Those
are the same four integers the 2026-08-11 build returned, which is not a copy-paste: the peaks are
reported to the nearest bin, and two builds of the same oscillator land in the same bin unless
something is badly wrong. The 2026-08-10 build, measured the same way on a longer 6-second capture
(`host/record_wav.py`), returned 439.9 / 554.2 / 659.2 / 830.6 at a peak of 30,000 of 32,768 with no
clipping.

To see the drift, and to re-record provenance after a rebuild:

```bash
uv run --no-project python scripts/check_artefacts.py           # what changed since the build
uv run --no-project python scripts/check_artefacts.py --update  # after copying in a fresh build
```

The record it compares against is `scripts/artefact_hashes.json`.

### Earlier copies

The two previous refreshes were 2026-08-11 for M34 (the channel mode messages) and 2026-08-10
(`5cf8f83`), the first since the M20 tree split to put the shipped bitstream and `core/synth.x` back
in step. Before that it was byte-for-byte the 2026-07-13 initial-release blob (`a38cffe`) — built
before M22's 18×18 narrowing and before M29, and stuck there because refreshing it needs Vivado on
an x86 machine, which no hosted runner provides.

The 2026-08-10 rebuild was run head to head against that July blob, ten flashes alternating between
the two, on a C2/C4/C6 spread wide enough to stress the octave shifts: **12 of 15 notes for July,
13 of 15 for the rebuild** — indistinguishable, and both flaky, which is a property of the test and
not of either bitstream (see [docs/TODO.md](../../../docs/TODO.md)). Interleaving was not fussiness:
run in blocks, the same comparison had said 9 of 9 for July against 4 of 9 for the rebuild — a
convincing regression that does not exist — and a single run of each, earlier, had said exactly the
opposite.

## Rebuilding

This file is a copy of `build/top.bit` (gitignored build output). To regenerate it, see README §3
"Basys 3 — build, flash, verify", then:

```bash
cp build/top.bit boards/basys3/firmware/top.bit
uv run --no-project python scripts/check_artefacts.py --update basys3
```
