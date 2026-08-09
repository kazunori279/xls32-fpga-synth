# Open TODOs

What the last few milestones left behind, split by whether it is *unproven* or *known and
accepted*. Milestone work proper lives in the roadmap tables in
[DEVELOPMENT.md](../DEVELOPMENT.md) and [DEVELOPMENT_tiliqua.md](../DEVELOPMENT_tiliqua.md); this is
the residue that does not deserve a milestone number.

*(This file replaced §7 of the old `docs/TILIQUA_PORT.md`, whose §7.1 — "the docs need one
refurbishment, not a list of patches" — was itself carried out by the rewrite that produced
[README.md](../README.md), [ARCHITECTURE_tiliqua.md](../ARCHITECTURE_tiliqua.md) and
[DEVELOPMENT_tiliqua.md](../DEVELOPMENT_tiliqua.md). The port document's remaining content lives in
those three; git history keeps the original.)*

## Unverified — things believed to work that have not been watched working

1. **`scripts/demo_video.sh` has never been executed** in its rewritten form (avfoundation loopback
   capture, `AUD_IDX`, `AV_OFFSET=0`). It was rewritten against the new audio path and left there.
2. **Nothing publishes the web UI.** Pages serves from the repo root, from `/docs`, or from an
   Actions workflow, and `webui/static` is none of the three. The README no longer claims the page
   "deploys cleanly to GitHub Pages" — it states the requirement instead (any static host, served
   over HTTPS or from localhost). Actually publishing it still needs either a workflow that
   deploys `webui/static` or a move.
3. **The Aligner's mid-stream re-lock has not been demonstrated in the browser.** Initial lock was
   measured live (three-note chord: peak 0.33, rms 0.080, zero sample-to-sample jumps > 0.4) and
   the JS port is byte-equivalent to `host/transport/uart.py` under test — but the 8192-byte
   re-check that exists because of the M28a rail bug has only ever been exercised in Python.

## Known debt — recorded, not scheduled

- **A residual DC offset on Tiliqua that is not the reverb tank's.** Fixing the comb dead band
  (see [ARCHITECTURE_tiliqua.md → C3](../ARCHITECTURE_tiliqua.md#c3-the-freeverb-tank-at-half-length))
  took `stress_fx_tail`'s late-window level from +206 to +82.3, but not to zero, and
  `stress_silence_recovery` still settles at exactly **+115** — one unique sample value across the
  whole window, so a DC rail rather than a decaying tail. That case runs with `revwet == 0`, which
  puts the tank out of the path entirely: something *else* is holding a constant. Basys 3 returns
  tail RMS 0 for the same case, so it is Tiliqua-side. Candidates not eliminated: the `>> 1` floors
  in the echo and chorus mixers, and the engine → AK4619 / UAC2 output path. At ~−49 dBFS it fails
  no checker, which is exactly why it is written down here rather than fixed in passing.
- **`filter_sweep` WARNs on Tiliqua at 80.7** against 86 on Basys 3 — the same DSLX filter,
  the same sweep, a consistently worse score. Deferred rather than diagnosed; nothing yet rules out
  the 48 kHz coefficient set as the difference.
- **+369 TRELLIS_COMB from the part-select remap, still unexplained.** The estimate was ~50. The
  cells were paid and the design fits, so nothing forced the question, but a sevenfold miss on a
  design with ~515 cells free is a hole in the area model, not a rounding error.
- **28/28 MULT18X18D, no margin at all.** One route out is known and unused: a 44-entry ROM for
  `nrel * HUE_K` frees two multipliers, at the cost of the resource that is *actually* scarce.
  Worth doing only when a feature needs a multiplier and cannot have one. See
  [ARCHITECTURE_tiliqua.md → E3](../ARCHITECTURE_tiliqua.md#e3-multipliers-28-of-28).
- **Risk 3b — `sync`/`usb` fails static timing at 60 MHz and runs anyway.** Open since M25, but two
  of the things it used to say are no longer true. The shortfall is now **39.92 MHz**, not the
  42.51 MHz quoted through M31 (the comb magnitude-truncation fix cost 2.59 MHz), and the failing
  path is **not inside luna** — rebuilding M31's netlist unmodified puts it in `fx`, so the
  characterisation had already gone stale before this change. Both figures fail the 60 MHz
  constraint by a wide margin and both run clean; that is the part that has not changed. See
  [ARCHITECTURE_tiliqua.md → E4](../ARCHITECTURE_tiliqua.md#e4-the-timing-shortfall-that-runs-anyway).
- **M24's DIN/TRS MIDI passes in simulation and has never had a cable in it.** Same state as when
  it was written; the hardware exists, the test has not been done.
- **Basys 3's MIDI-DIN input (M7) and I2S DAC output (M8)** are built and timing-closed but not
  hardware-tested — parts on order.
- **Nothing detects that a committed bitstream has gone stale.** M32 shipped prebuilt artefacts for
  both boards — `boards/basys3/firmware/top.bit` and `boards/tiliqua/firmware/xls32-r5.tar.gz` —
  and both are refreshed by hand, so the repo can hold a bitstream that no longer matches the
  sources beside it and nothing will say so. The idea on the shelf is a checked-in hash of the
  sources each artefact was built from, compared on demand; it catches the drift without building
  anything. **Building on push was considered and cancelled**, not deferred: Vivado needs a licence
  and ~100 GB, so no hosted runner can produce the Basys 3 half, and a green tick covering one
  board of two claims more than it checks.
