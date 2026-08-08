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

1. **The Python test suite has not been run since M31.** `test/harness.py` and
   `presetgen/{validate_hw,calibrate_bank,param_diff,calibrate}.py` were all edited when
   `webui/server.py` came out; `test/run_tests.py` has not been invoked since. This is the
   highest-risk item on the page — everything else here is cosmetic or already has a fallback.
   Both boards are currently free.
2. **`scripts/demo_video.sh` has never been executed** in its rewritten form (avfoundation loopback
   capture, `AUD_IDX`, `AV_OFFSET=0`). It was rewritten against the new audio path and left there.
3. **Nothing publishes the web UI.** Pages serves from the repo root, from `/docs`, or from an
   Actions workflow, and `webui/static` is none of the three. The README no longer claims the page
   "deploys cleanly to GitHub Pages" — it states the requirement instead (any static host, served
   over HTTPS or from localhost). Actually publishing it still needs either a workflow that
   deploys `webui/static` or a move.
4. **The Aligner's mid-stream re-lock has not been demonstrated in the browser.** Initial lock was
   measured live (three-note chord: peak 0.33, rms 0.080, zero sample-to-sample jumps > 0.4) and
   the JS port is byte-equivalent to `host/transport/uart.py` under test — but the 8192-byte
   re-check that exists because of the M28a rail bug has only ever been exercised in Python.

## Known debt — recorded, not scheduled

- **+369 TRELLIS_COMB from the part-select remap, still unexplained.** The estimate was ~50. The
  cells were paid and the design fits, so nothing forced the question, but a sevenfold miss on a
  design with ~515 cells free is a hole in the area model, not a rounding error.
- **28/28 MULT18X18D, no margin at all.** One route out is known and unused: a 44-entry ROM for
  `nrel * HUE_K` frees two multipliers, at the cost of the resource that is *actually* scarce.
  Worth doing only when a feature needs a multiplier and cannot have one. See
  [ARCHITECTURE_tiliqua.md → E3](../ARCHITECTURE_tiliqua.md#e3-multipliers-28-of-28).
- **Risk 3b — `sync`/`usb` fails static timing at 60 MHz and runs anyway.** Unchanged since M25.
  See [ARCHITECTURE_tiliqua.md → E4](../ARCHITECTURE_tiliqua.md#e4-the-timing-shortfall-that-runs-anyway).
- **M24's DIN/TRS MIDI passes in simulation and has never had a cable in it.** Same state as when
  it was written; the hardware exists, the test has not been done.
- **Basys 3's MIDI-DIN input (M7) and I2S DAC output (M8)** are built and timing-closed but not
  hardware-tested — parts on order.
- **No prebuilt Tiliqua bitstream ships in the repo.** Basys 3 has
  `boards/basys3/firmware/top.bit`; the Tiliqua equivalent (a flash archive plus a `manifest.json`)
  is M32.
