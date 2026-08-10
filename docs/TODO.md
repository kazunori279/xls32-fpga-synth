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

1. **No demo video has been recorded with the fixed capture path.** The M32 take was published with
   67 % of its audio missing and has been deleted from YouTube; `scripts/demo_video.sh` was then
   rebuilt around `scripts/rec_audio.py`, which records through PortAudio and verifies each take
   against the board's 12.288 MHz counter (see
   [DEVELOPMENT_tiliqua.md → M32](../DEVELOPMENT_tiliqua.md#what-is-left--m32-and-the-risk-register)).
   The new path has been run end to end once, as a 10 s take with Chrome holding the device and
   playing — the condition that produced the 67 % — and the counter scored it at **0.001 %**, with
   an ffmpeg capture of the same device minutes later scoring 10.6 %. What has *not* been done is a
   full-length take: nobody has watched a 125 s recording for A/V drift, and `SCREEN_LATENCY` /
   `CAM_OFFSET` are start-up times measured on one machine, not a closed loop. The README's hero is
   back on the Basys 3 video until that take exists.
2. **The Aligner's mid-stream re-lock has not been demonstrated in the browser.** Initial lock was
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
- **The committed Basys 3 bitstream is stale, and known to be.** `boards/basys3/firmware/top.bit`
  is byte-for-byte the 2026-07-13 initial-release blob (`refactor(M20)` only moved it); `core/synth.x`
  has changed twice since (M22's 18×18 narrowing, M29), as have `rtl/top.v` and
  `rtl/build_vivado.tcl`. It plays, but it is an older engine than the one documented. Refreshing it
  needs Vivado on x86 (`boards/basys3/scripts/remote_build.sh`) — which is exactly why it drifted.
  The Tiliqua archive beside it is current.

  The drift is now *detected*, at least: `scripts/check_artefacts.py` hashes the sources that feed
  each artefact into `scripts/artefact_hashes.json` and compares on demand, catching uncommitted
  edits as well as commits. Two gaps remain in the check itself: it covers no **Tiliqua SDK**
  checkout (outside this repo, unhashable from here), and **nothing runs it automatically** — no
  hook, no CI step, so it only helps someone who thinks to run it. Once the Basys 3 bitstream is
  rebuilt it will have a real recorded provenance; today its record is an honest `null`, because
  its sources predate the M20 tree split and no truthful hash can be reconstructed for them.

  **Building on push was considered and cancelled**, not deferred: Vivado needs a licence and
  ~100 GB, so no hosted runner can produce the Basys 3 half, and a green tick covering one board of
  two claims more than it checks. `.github/workflows/pages.yml` deploys `webui/static/` and
  `docs/slides/` — it does not build, and it never will.
