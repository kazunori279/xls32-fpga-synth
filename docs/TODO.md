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

## Queued behind the next rebuild

Three open items are each individually cheap and each blocked by the same thing, so they are
collected here rather than left to be rediscovered separately. **Whichever of them finally forces a
re-sweep should carry the other two.**

| item | change | why it cannot land alone |
|---|---|---|
| [#39](https://github.com/kazunori279/xls32-fpga-synth/issues/39) | adopt `--router2-tmg-ripup` | new P&R options, so a new netlist and a new seed lottery |
| [#43](https://github.com/kazunori279/xls32-fpga-synth/issues/43) | put the real voice count in the manifest `brief` | needs `export VOICES` in `build.sh`; the manifest is baked into the archive |
| [#40](https://github.com/kazunori279/xls32-fpga-synth/issues/40) | make the visualiser grid follow the voice count | `N_VOICE`/`COLS`/`ROWS` are literals in the gateware |

Two costs are shared, and they are what make the batching worth it rather than merely tidy. A
rebuild **re-rolls the seed lottery** — seed rankings do not transfer between netlists, so the
24-voice build's 55.48 MHz on seed 4 is not a number that survives a re-sweep, it is a number that
has to be won again. And it **invalidates the board verification**: the shipped 24-voice bitstream
scored 99.8/100 over 175 cases, which is most of a board-day to re-earn, per bitstream.

There is a third, quieter cost that only #39 and #43 pay: both edit `boards/tiliqua/build.sh`, and
`scripts/check_artefacts.py` hashes `.sh` **raw** — its comment-stripping normaliser covers Python
but deliberately excludes `.sh`, `.tcl` and `.xdc`. So either edit marks both shipped archives
`stale` the instant it lands, before anything has actually been rebuilt.

## Unverified — things believed to work that have not been watched working

1. ~~**The re-recorded demo video has not been watched end to end.**~~ **Done — watched and
   accepted 2026-08-10.** Kept here for the trail, because two rounds of this were passed by
   measurement and failed by a listener, and the next take will be tempted by the same shortcut.
   What is left of it is at the bottom of the item. The M32 take was published with
   67 % of its audio missing and has been deleted from YouTube; `scripts/demo_video.sh` was then
   rebuilt around `scripts/rec_audio.py`, which records through PortAudio and verifies each take
   against the board's 12.288 MHz counter (see
   [DEVELOPMENT_tiliqua.md → M32](../DEVELOPMENT_tiliqua.md#what-is-left--m32-and-the-risk-register)).
   A full-length retake has since been made under the condition that produced the 67 % — Chrome
   holding the device and playing — and the counter scored it at **0.011 %** (652 frames of
   5,902,732, in 12 events); an ffmpeg capture of the same device scored 10.6 %. It was trimmed to
   one pass of *Prelude in C* against the measured 110.531 s loop period, and three frames were
   pulled at 30 s, 60 s and 110 s to confirm the panel and the lit tiles. A listener then caught
   what none of that had: **ten short clicks**. They are that same 0.011 % — and they are made on
   the board, by the `usb_tee` FIFO in this repo's own gateware, which was written off the
   motherboard's clock and read off the host's with no rate control between them and is required to
   drop rather than stall the codec. 110–123 ppm apart eats its 16 entries every 10.4 s, and a
   millisecond cut out of a sustained tone is a step. (`out0`/`out1` never see it — the tee is a
   copy that cannot push back on the DAC path.) **The rate control now exists** — the UAC2 capture
   endpoint sizes its own IN packets from `adc_fifo_level` instead of reporting the host's nominal
   48,000 fps, and a 120 s capture measures 0 lost frames in 0 events (M33 below). The rest of this
   entry is the record of finding that out, and is worth keeping for it. `scripts/declick.py` bridges them and
   `demo_video.sh` now runs it before the mux; the repaired file measures no seam above the music's
   own transients. It is worth
   noting how the check was passed and the take still wrong: the counter measures *arrival*, and
   nothing measured what it sounded like.

   A further take — the one that plays the panel *while* the demo runs, which is the point of the
   video — found the second half of that lesson. The declicker looked for its seams in the waveform,
   and a 7-bit CC dragged at the pointer's ~50 Hz is a burst of 1/128 steps 20 ms apart, which is
   the shape it was hunting: it found 50, bridged 49, and only ~12 were the clock. It now repairs
   the samples the board's counter names and nothing else (`counter_loss` returns positions;
   `--heuristic` keeps the old path for takes without a counter). `demo_video.sh` also no longer
   deletes the four-channel capture, which is what made that take unrepeatable — the mux keeps only
   ch0/1, so once the raw is gone the only way to re-run anything is to play the piece again.

   `media/m32-demo-v4.mp4` — that take, trimmed to one loop — **has now been watched end to end and
   accepted**, which retires the oldest item on this list. It answered the question the measurements
   could not: it was declicked by the *old* detector, so roughly 37 of its 49 bridges landed on knob
   moves rather than dropouts, and none of that is audible during the filter sweeps. A human also
   saw no A/V drift across the 111 s, which is the only check `SCREEN_LATENCY` / `CAM_OFFSET` have
   ever had — they are start-up times measured on one machine, not a closed loop, so nothing in the
   pipeline would catch a slow slide between the panel and the sound on a different one.

   It is now the README's hero, published as
   [`sWc6g7cgsd4`](https://youtu.be/sWc6g7cgsd4). One thing is still open, and it is not about the
   take. The acceptance was a listening judgement, not a measurement: there is still no check that
   says whether a declick landed on a dropout or on a knob, which only matters now for takes made
   before the counter fix.
2. ~~**The Aligner's mid-stream re-lock has not been demonstrated in the browser.**~~ **Done
   2026-08-10, and it found the re-lock was dead.** Writing the demonstration is what exposed it:
   the check scored `self.buf`, which `feed` empties of whole frames every call, so the guard
   asking for 4100 bytes there could only pass if one chunk carried that many, and real chunks are
   510–1020 bytes on both readers. So it had **never fired in either language** —
   the claim that "the JS port is byte-equivalent under test" was also empty, since no test
   compared them. Both are fixed: the check now scores the newest 2 kB of the stream (what has been
   emitted plus what has not, which are contiguous) every 4096 bytes fed, and
   `webui/check_aligner.py` + `webui/aligner_check.html` run Python and JS over the same 49 kB
   capture — a real odd (+3) phase shift at byte 16384 — and compare the SHA-256 of the aligned
   output at five chunk sizes. Browser verdict: PASS, identical at all five, healing 0–2 kB
   (0–16 ms) after the shift; with the re-lock disabled the same page fails with 64 bad windows a
   run, so the check has teeth. What it does **not** cover is live Web Serial: choosing a port is a
   permission prompt behind a user gesture and cannot be driven headlessly.

## Known debt — recorded, not scheduled

- **The shipped Basys 3 bitstream predates both engine DC fixes, so the two boards are no longer
  bit-exact** ([#41](https://github.com/kazunori279/xls32-fpga-synth/issues/41)).
  `290d00b` and `3aa0227` are `core/synth.x` changes and therefore belong to both boards, but only
  the Tiliqua archives have been rebuilt: both carry the fixes and pass `check_artefacts.py`,
  while `boards/basys3/firmware/top.bit` is still
  the 2026-08-11 M34 build and reports stale. Refreshing it needs Vivado on an x86 machine — the
  GCE build VM in `remote_build.sh`, which is not something CI can provide — and then a Basys 3 on
  the desk for `verify.sh`, since timing closure has come apart from what the synth sounds like on
  this project before. Until both happen, anyone flashing the Basys 3 blob gets a synth with the
  latched SVF residue and the pulse's duty offset still in it. Neither stops it playing; the note
  is in [`top.bit.md`](../boards/basys3/firmware/top.bit.md) for people who flash without running
  the check.
- **A residual DC offset on Tiliqua that is not the reverb tank's**
  ([#4](https://github.com/kazunori279/xls32-fpga-synth/issues/4)). Fixing the comb dead band
  (see [ARCHITECTURE_tiliqua.md → C3](../ARCHITECTURE_tiliqua.md#c3-the-freeverb-tank-at-half-length))
  took `stress_fx_tail`'s late-window level from +206 to +82.3, but not to zero, and
  `stress_silence_recovery` still settles at exactly **+115** — one unique sample value across the
  whole window, so a DC rail rather than a decaying tail. That case runs with `revwet == 0`, which
  puts the tank out of the path entirely: something *else* is holding a constant. Nor is it the
  pulse-duty offset below — that one is signal-dependent and this window has no signal in it. Basys 3 returns
  tail RMS 0 for the same case, so it is Tiliqua-side. Candidates not eliminated: the `>> 1` floors
  in the echo and chorus mixers, and the engine → AK4619 / UAC2 output path.

  **Paid off**, and by neither candidate: it was the latched SVF residue two entries down. This
  entry and that one were the same bug seen from two ends — this one measured at the mix what that
  one described per voice, which is why "something *else* is holding a constant" was right about
  the tank and wrong about it being a separate fault. Re-measured on the shipped 24-voice bitstream
  (`46ae31b`) with the exact stimuli from `test/cases_stress.py`: the `stress_silence_recovery` tail
  is `n=14400 mean=-0.006 min=-1 max=1`, two distinct values, where it was one value at +115; and
  `stress_fx_tail`'s late window is `mean=+0.30` (range −1..3) against +82.3. Cutting 32 voices to
  24 does not explain that — 24 latched constants where there were 32 predicts 115 × ¾ ≈ +86, not
  +0.3 — so `290d00b` removed the mechanism rather than diluting it. Still unexplained, and left on
  the issue rather than guessed at: why Basys 3 read tail RMS 0 *before* the fix, when `290d00b` is
  a `core/synth.x` change both boards share. Its blob is still pre-fix
  ([#41](https://github.com/kazunori279/xls32-fpga-synth/issues/41)), so that cannot be settled
  today.
- **The pulse wave's DC is still inside the engine; it is only the USB tee that is clean now**
  ([#2](https://github.com/kazunori279/xls32-fpga-synth/issues/2)).
  A pulse at anything but 50 % duty has a DC term; the Bach demo patch runs `PULSE W` 100 of 128 —
  about 78 % — so every sounding voice contributes an offset that tracks its own envelope. Measured
  on a full take of *Prelude in C* off the UAC2 input, before the fix: mean **+0.286**, with
  **89.6 % of the energy below 5 Hz**, leaving the audible band at −25.9 dBFS. This is *not* the DC
  rail above — it is signal-dependent and arithmetically expected.

  `boards/tiliqua/gateware/dc_block.py` now high-passes the tee at 7.5 Hz, and a 120 s capture
  measures mean **+0.00003** with **0.000 %** below 5 Hz, so **the debt is no longer observable
  over USB**. It is not paid off, only hidden from the one instrument that could see it. The
  blocker sits on the tee alone, deliberately — putting it in `fx.py` would break bit-exact parity
  with `fx_model.py` and Basys 3 — so the offset is still there in the engine and on `out0`/`out1`,
  where the AC coupling removes it before anyone hears it.

  **Paid off.** The open half was whether the offset costs headroom *inside* the engine, upstream
  of the output stage, where the tee's blocker cannot reach. `core/sim/tb_headroom.v` answered it:
  at the demo patches' 78 % duty four voices sit at a mean of **13,099** counts and the mix clamps
  on the positive rail only — 25 hits in 1,200 samples, the negative rail never touched — while the
  50 % control clamps symmetrically. `voice_wave` now subtracts the term, and the same testbench
  reports means of −11 to 683 counts from 1 to 32 voices, two-sided clipping matching the control,
  and the first clamping polyphony moved from 4 voices to 8. It is not free: re-centring grows the
  peak excursion, so `|voice_wave| ≤ 3904` rather than 2048, four voices at `pw = 88` clamp 48 times
  against 22 before, and the die goes 23,859 → 24,023 of 24,288 (98.9 %) and re-draws the seed
  lottery. `presetgen/pulse_dc.py` retired the objection that this re-scores the bank: 48W/28L over
  76 pulse presets under their own fitted loss, p = 0.029, 340/340 non-pulse controls identical.
  Confirmed on the module, not just in the testbench: `boards/tiliqua/check_headroom_hw.py` reads
  the clamp through the USB tee — which no direct measurement survives, since the tee's DC blocker
  and the FIR resampler between them destroy both the offset and the flat top — by taking each
  patch twice, loud and quiet, and watching whether its peak asymmetry *moves* with level. Nothing
  linear can do that; the clamp is the only stage in the path whose behaviour depends on how loud
  it is played. Both bitstreams loaded back to back over JTAG: `890d4be` fails 10 of 11 clipping
  rows, `3aa0227` fails 0 of 9, and the 50 % control holds to 0.026 on both. At 78 % duty and 16
  voices the before capture peaks at **+0.07 against −1.88** — the positive half of the waveform
  eaten whole — and the after capture at **+1.04 against −1.10**.
  See the pulse-DC section of [ARCHITECTURE.md](../ARCHITECTURE.md#b3-pwm).
- **Every voice's filter latches a small DC when its envelope dies, and never lets go.** The
  Chamberlin SVF leaks with `low2 = low1 - (low1 >> 7)`, and that shift rounds to zero for any
  value under 128 — so the state stops decaying at a small constant instead of reaching it. A part
  that has ever sounded contributes a few hundred counts of DC for the rest of the power cycle,
  and thirty-two of them add. Found by M34's `tb_panic`, which expected literal silence after All
  Sound Off and did not get it; the testbench now asserts that the mix stops *moving*, which is
  what the message actually promises. Note this is a *third* DC source, distinct from both of the
  two above: it is per-voice, it is inside the engine, and it is latched rather than
  signal-dependent.

  **Paid off**, and not the way this entry proposed. Clearing `flo`/`fbnd` in `apply_off` was the
  obvious fix and the expensive one — about 38 bits of mux across 32 slots, roughly 1,200
  `TRELLIS_COMB` against a few hundred free. `290d00b` closed the dead zone instead, by subtracting
  one more LSB when the state is positive so that the leak is symmetric about zero: **598 cells**
  on the bare engine, and no need to touch the note-off path at all. (Rounding the shift toward
  zero looks cheaper and measures 2,059, because the extra adder lands on the datapath's critical
  path and XLS re-schedules around it.) `core/sim/tb_dc.v` is the assertion the workaround could
  not make — reset, one voice, all 32 slots, and all 32 at maximum cutoff and resonance, each
  checked for a mix of *exactly* zero, all four passing. On the module, `check_panic_hw.py` reports
  `ptp 0.000` on all six of its silence cases; that is the weaker "stops moving" claim, since a
  latched constant also has zero peak-to-peak, so the exact-zero half of it rests on `tb_dc.v`.
- **`check_headroom_hw.py` flakes on its quietest row: 1 FAIL in 4 runs of the same bitstream**
  ([#42](https://github.com/kazunori279/xls32-fpga-synth/issues/42)). Found verifying the 24-voice
  build. The failing row is always CC75 = 100 at **2 voices**, shift −0.0808 against the threshold —
  the least-driven row in the sweep, so the smallest shift in the table and the one nearest the
  line. Every loud, high-polyphony row passes on all four runs, which is the wrong way round for a
  returning offset and the right way round for capture noise. The threshold is one constant across
  a sweep whose signal varies by polyphony; it should scale with the row. Worth fixing because this
  is the check that certifies the pulse DC (#2) stays fixed, so a false FAIL reads as a regression —
  and teaches whoever hits it to re-run until green, which would hide a real one.
- **The 24-voice build calls itself XLS32 to the host, and the two builds are indistinguishable
  from a connected machine** ([#43](https://github.com/kazunori279/xls32-fpga-synth/issues/43)).
  `build.sh` gets the slot name right (`NAME="${NAME:-XLS$VOICES}"` → `XLS24`), but `iProduct`,
  `build_id.TAG` and `xls_core`'s manifest `brief` all hardcode 32, so with 24 on slot 7 and 32 on
  slot 6 the only thing telling them apart over USB is the commit hash. Mostly *won't fix* by
  design: `XLS32` is the project name, and `iProduct` is half the UID CoreAudio remembers the device
  by — renaming it makes every machine forget its volume, rate, and the panel's saved OUT pick. That
  reasoning is now inline at both sites so it does not get rediscovered. What is left is the cheap
  half — deriving the voice count into `brief` — and it is stuck behind a rebuild twice over: the
  manifest is baked into the archive, and reading `$VOICES` from Python needs `export VOICES` in
  `build.sh`, which `check_artefacts.py` hashes **raw**, so the edit alone would mark both shipped
  archives stale. Rides with the next re-sweep, same queue as #40.
- **`filter_sweep` WARNs on Tiliqua at 80.7** ([#7](https://github.com/kazunori279/xls32-fpga-synth/issues/7))
  against 86 on Basys 3 — the same DSLX filter, the same sweep, a consistently worse score.
  Deferred rather than diagnosed; nothing yet rules out the 48 kHz coefficient set as the
  difference.
- **+369 TRELLIS_COMB from the part-select remap, still unexplained**
  ([#5](https://github.com/kazunori279/xls32-fpga-synth/issues/5)).
  The estimate was ~50. The cells were paid and the design fits, so nothing forced the question,
  but a sevenfold miss on a design with ~515 cells free is a hole in the area model, not a
  rounding error.
- **28/28 MULT18X18D, no margin at all** ([#6](https://github.com/kazunori279/xls32-fpga-synth/issues/6)).
  One route out is known and unused: a 44-entry ROM for
  `nrel * HUE_K` frees two multipliers, at the cost of the resource that is *actually* scarce.
  Worth doing only when a feature needs a multiplier and cannot have one. See
  [ARCHITECTURE_tiliqua.md → E3](../ARCHITECTURE_tiliqua.md#e3-multipliers-28-of-28).
- **Risk 3b — `sync`/`usb` fails static timing at 60 MHz, and it has now bitten.** Open since M25;
  promoted from a carried risk to an observed failure in August 2026, when the vendor ran the
  shipped bitstream on their own two Tiliquas and it worked on one and not the other. The build they
  tested measured 40.95 MHz against 60; what the repo ships now is the 24-voice build at
  **55.48 MHz**, which is a much smaller bet but still short. Two things this item used to say are
  wrong: the failing
  path is *not* only in `fx` — that is true at 97% occupancy and nowhere else — and "both run
  clean" was one die's evidence. Underneath `fx` is a **~20-LUT-level luna cone** that has sat at
  ~45 MHz since M25 and is depth-limited, not congestion-limited: 4.79 ns of pure logic against a
  16.7 ns period. **The "24 voices is measured and dead (+5.8 MHz, ceiling 46.79)" entry that used
  to sit here was wrong, and it was the load-bearing one** — it was measured on a netlist whose
  voice ring had been pruned away by a broken variant generator
  ([#35](https://github.com/kazunori279/xls32-fpga-synth/issues/35)). Built correctly, 24 voices is
  93.9 % occupancy and **55.48 MHz**, +9.1 over the 32-voice build, and it is what the repo now
  ships (see the next item). Still measured and dead:
  nextpnr 0.11.1 (bit-identical), `--placer static` (never legalises), `REGION`/`UGROUP` (absent
  from the wasm), and **SDC timing exceptions** — `nextpnr-ecp5` parses only `create_clock`,
  `get_ports`, `get_cells` and `set_false_path`, has no `set_multicycle_path` at all, and prints
  *"set_false_path from: %s, to: %s does not do anything(yet)"* for the one exception it does
  parse. The cone is a genuine multicycle and we cannot say so. The only remaining lever is a
  register: the path is the isochronous IN endpoint's combinational `ready` running from the ULPI
  transmit mux into `ChannelsToUSBStream`'s FIFO level counter, and a skid buffer at that module
  boundary splits 22.11 ns into 17.69 and ~4.7, i.e. ~56 MHz
  ([#34](https://github.com/kazunori279/xls32-fpga-synth/issues/34)). **Built and measured
  2026-08-19** ([`boards/tiliqua/patches/0001-usb-in-skid-buffer.patch`](../boards/tiliqua/patches/README.md)):
  the split lands — the cone leaves the report — but Fmax goes only 45.23 → **46.54 MHz**, because
  a path of ours was hiding 0.62 ns behind it. The new worst path is `fx.rsize` → `fx.csr`, 21.49
  ns, **9.96 ns of it logic**, all inside `boards/tiliqua/gateware/fx.py`. So the blocker was
  never USB at all. **M35, 2026-08-20:** two cuts of our own — a fourth tank phase plus a
  registered `rvg` in `fx.py`, and a `SyncFIFOBuffered` between the MIDI arbiter and the filter
  chain in `top.py`, which was the *next* path to surface at 23.40 ns — take five-seed post-route
  `clk` from **44.1–45.8 MHz (median 44.95)** to **47.3–50.9 (median 49.47)**. Both are
  behaviour-transparent: `test_fx.py` stays bit-exact and the 1,650 ms `XLS_SIM_MIDI=parts`
  capture is byte-identical. The skid-buffer patch is **not applied** — rebuilt with it, M35
  measures 48.90, inside the no-patch spread, and it cannot help further because both remaining
  cones now end *inside* luna (`transmitter.fsm_state`, `IsoStreamInEndpoint.bytes_left_in_frame`)
  behind the buffer rather than in front of it. The vendor's 50 MHz bar is met at the pinned seed
  and missed by 0.5 MHz at the median, so the next cut still has to come from somewhere. It
  blocks
  [#3](https://github.com/kazunori279/xls32-fpga-synth/issues/3) and therefore the webflasher PR
  ([#32](https://github.com/kazunori279/xls32-fpga-synth/issues/32)). See
  [ARCHITECTURE_tiliqua.md → E4](../ARCHITECTURE_tiliqua.md#e4-the-timing-shortfall-and-the-die-it-does-not-run-on).

  **M36 routes around it rather than closing it.** At 24 voices the shortfall is 55.48 against 60 —
  a bet that the silicon is 8 % faster than modelled, where 32 voices bets on 29 %. That is the
  formal bitstream now; the 32-voice build stays available and stays a risk. The gap itself is
  unchanged and still needs a register in luna's cone
  ([#34](https://github.com/kazunori279/xls32-fpga-synth/issues/34)).
- **The repo ships two Tiliqua bitstreams, and only one of them is a claim.** `xls24-r5.tar.gz` is
  the formal build — 24 voices, 93.9 % of the die, `clk` at 55.48 MHz, graded 99.8/100 (A+) on the
  module — and belongs in **slot 7**. `xls32-r5.tar.gz` is 32 voices at 98.9 % and 46.35 MHz, kept
  in **slot 6** as experimental: it works on this desk, and it did not work on one of the vendor's
  two modules. Anyone flashing slot 6 is carrying that. The two differ only in the voice count, which
  is generated into a throwaway copy of `core/synth.x` rather than living in the tree, so
  `check_artefacts.py` has to hash the generator and the count or it cannot tell them apart
  ([#10](https://github.com/kazunori279/xls32-fpga-synth/issues/10)).
- **The visualiser's bottom row is dead on the formal bitstream.** `gateware/viz.py` fixes the grid
  at `N_VOICE = 32`, `COLS, ROWS = 8, 4`, and `build.sh`'s `VOICES` never reaches the gateware — it
  only selects which `synth*.x` the engine comes from. So the 24-voice build writes tiles 0–23 and
  leaves 24–31 at their reset value, which `VoiceTiles` renders at `IDLE_V` in the lowest hue:
  eight dim tiles that never change. No audio or timing consequence, and invisible to the suite,
  which grades frame *timing* and never tile content. Not fixed with the rest of M36 because it is a
  netlist change, and at 93.9 % occupancy that costs a fresh seed sweep (seeds 1/4/5/6 span 7 MHz on
  this netlist) plus another hardware run — so it is queued against the next netlist change, with
  #39, as [#40](https://github.com/kazunori279/xls32-fpga-synth/issues/40).
- **Reduced-voice variants were unmeasurable for four milestones and nobody noticed.**
  `voices_variant.py` asserted a match count for every substitution it knew about and had no rule
  for one site — `rotate_in`'s tail writeback — so a 24-voice copy wrote the new voice to index 31
  of a 24-entry array. `update` out of range is a no-op, the ring never fills, and yosys prunes the
  voice state as unreachable: 91 % occupancy, 50.53 MHz and total silence, which reads as the best
  result in the table. Fixed 2026-08-20
  ([#35](https://github.com/kazunori279/xls32-fpga-synth/issues/35)); the corrected area curve is in
  [E1](../ARCHITECTURE_tiliqua.md#e1-the-area-budget) and the withdrawn conclusions in
  [#36](https://github.com/kazunori279/xls32-fpga-synth/issues/36). The lesson is not about regexes.
  A generator whose failure mode is *smaller and faster* will be believed, and the 32-voice
  byte-for-byte self-check that was supposed to guard it passes trivially at the one setting where
  every rewrite is the identity. The 16-voice rung is still unmeasured
  ([#38](https://github.com/kazunori279/xls32-fpga-synth/issues/38)).
- ~~**M24's DIN/TRS MIDI passes in simulation and has never had a cable in it.**~~ **Done — the
  Tiliqua TRS MIDI-In jack plays on hardware**, alongside USB-MIDI, as the arbiter was written to
  allow. This was open from M24 to now purely for want of a Type A cable. Basys 3's DIN input is a
  separate item and is still untested (next).
- **Basys 3's MIDI-DIN input (M7) and I2S DAC output (M8)** ([#8](https://github.com/kazunori279/xls32-fpga-synth/issues/8))
  are built and timing-closed but not hardware-tested — parts on order.
- ~~**The committed Basys 3 bitstream is stale, and known to be.**~~ **Done — rebuilt and verified
  2026-08-10.** `boards/basys3/firmware/top.bit` had been byte-for-byte the 2026-07-13
  initial-release blob for four months, an engine from before M22's 18×18 narrowing and M29,
  because refreshing it needs Vivado on x86 (`boards/basys3/scripts/remote_build.sh`) — which is
  exactly why it drifted. Rebuilt from `5cf8f83`: **0 failing endpoints** at 100 MHz, worst slack
  +0.012 ns, 26/90 DSP48E1, 32.5/50 BRAM, 50.4 % of the LUTs. Then flashed, because static timing
  is not a listening test and this project has walked into that gap twice (item 1 above): A major 7
  came back at 439.9 / 554.2 / 659.2 / 830.6 Hz, inside 0.05 % of nominal, peaking at 30,000 of
  32,768 without clipping, and ten interleaved flashes scored it level with the blob it replaces
  (13 of 15 notes against 12). Both boards now ship the engine the repo documents.
- ~~**`host/analyze.py --serial` grades every bitstream the same.**~~ **The whole file was deleted
  2026-08-10** — the flag was the half of it that had been noticed. What `--serial` did, for the
  record: it opened the port at 115200 and read 8-bit mono against a 4 kHz assumption, while the
  transport had moved to 2 Mbaud, 16-bit, stereo-interleaved (`host/transport/uart.py`,
  `SR = 32000`). It returned period 24 and peak-to-peak 128 *identically* on the 2026-07-13 blob
  and the 2026-08-10 rebuild — discriminating nothing, with a verdict of `CHECK` on both. It could
  not even reach that verdict after `abf12b9` (2026-08-01), which swapped the `glob` port-finder
  for `transport.uart.find_port` and dropped `time` out of the import beside `glob`, leaving a
  certain `NameError`: for 112 commits `boards/basys3/scripts/verify.sh` crashed before it measured
  anything. That import was fixed and `verify.sh` moved to `host/play.py`, which was right all
  along.

  **The stdin mode was assumed healthy and was not.** Removing `--serial` meant running the one
  path left — README §3's `iverilog … | grep '^S ' | analyze.py` — and it returns `CHECK` too,
  at HEAD and before the edit alike. Three assumptions in it had all expired: it masks each sample
  with `& 0xFF` and thresholds at 128, but `core/sim/tb.v:41` has emitted 16-bit unsigned samples
  (midpoint 32768) for a long time; it expects a period of 9.1 samples from 4 kHz when the engine
  runs at 32 kHz; and it looks for silence to prove the envelope falls, when tb.v sends four
  note-ons and no note-off, so there is no silence to find. It reported `period 3.0, envelope
  min=112, CHECK (freq BAD, envelope BAD)` — the same verdict on every simulation, for the same
  reason `--serial` gave the same verdict on every bitstream. Porting it was pointless:
  `host/analyze_fft.py` already reads the same stdin at full precision, already uses `synth.SR`,
  and its `CHORD` is already tb.v's four notes. On the same capture it prints `PASS: 4/4 chord
  tones` in 0.13 s. README §3 now pipes there, and `analyze.py` is gone.

  This is item 1's lesson from the other end: a check that passes nothing gets read by nobody, and
  a broken verifier is indistinguishable from an absent one. The sharper half is that the fix for
  one broken mode is worth nothing until the modes you left alone are run once.
- ~~**`host/play.py` is not repeatable on wide intervals, and the noise reads as a regression.**~~
  **Done — fixed and verified 2026-08-10.** The fault was in the capture, not the analysis.
  `read_bytes` lost bytes: **6 of 8 captures lost frame phase** mid-buffer (marker score
  0.22–0.44 at the break) against **0 of 8** through `Recorder`, measured alternating and with the
  order swapped every pair. Varying its read size (16 kB / 64 kB) and its idle sleep (1 ms /
  0.3 ms / none) changed nothing. *That comparison was confounded and the conclusion drawn from it
  — "the threaded reader does not do it" — was wrong; see the trigger below.*
  `samples_from_bytes` then locks byte alignment once, so everything after the break decodes
  as (Lhi, Rlo) pairs — full-scale hash. The hash is the loudest thing in the buffer, so
  `pick_window` chose it. That accounts for the whole "~68 Hz fundamental and fifty-odd partials
  the three notes cannot explain" signature: it was never audio.

  `play.py` and `record_wav.py` now capture through `UartTransport.record_start/record_stop`,
  which drains with `Recorder`, re-locks the frame phase every 128 bytes, trims the backlog against
  the wall clock, and reports whether the phase held. Two further changes stop the analysis
  choosing badly on its own. `pick_window(clean=True)` keeps only windows within 40 % of the
  loudest and then takes the one with the fewest jumps over `synth.glitches`' threshold. And the
  DFT window and band come from the notes asked for instead of a fixed 2048 samples over
  60–3000 Hz — 2048 localises a peak to about 31 Hz while C2 was required to land inside 10, and
  the 60 Hz floor sat 5 Hz below C2's 65.4 with no room for it to be a local maximum.

  Measured on one unchanged bitstream, C2/C4/C6, interleaved: **old 20 of 33 notes over 11 runs**
  (1, 2, 0, 3, 3, 3, 1, 3, 1, 1, 2) against **new 30 of 30 over 10** — and the new one returns the
  *same* peak list every single run, `[65, 261, 1045]` against a nominal 65.4 / 261.6 / 1046.5.
  All four waveforms now pass the wide spread, where the close chord was all the old one could
  hold. `record_wav.py` takes come back at exactly the wall-clock length with no jump over
  threshold, where they used to carry the 157 ms backlog.

  **The trigger, found the same day.** It is not the thread. `read_bytes` flushed twice with a
  50 ms sleep between; `Recorder` flushed once and read at once — so the first comparison varied
  two things and credited the wrong one. Crossing them separates them cleanly: with the pause it
  breaks in **both** threading modes, without it in **neither**. Nor is it the second flush —
  `flush,flush` back to back is clean, `flush,sleep 50 ms` alone breaks. Sweeping the pause gives
  a dose response — **0 ms 0/4, 2 ms 0/4, 5 ms 2/4, 10 ms 3/4, 20 ms 3/4, 50 ms 4/4** — and over
  every arm measured, **no pause 0/24, pause ≥ 5 ms 20/26**. `gc.disable()` changed nothing, and
  the longest stall in the loop was 7.6 ms, nowhere near the ~6 kB that goes missing. The break
  always lands at the same place, the seam between the start-up backlog and the live stream about
  20 kB in. So: nobody reading for 5 ms at 2 Mbaud is ~1.3 kB with nowhere to go, and something
  between the FT2232 and the tty discards a chunk that is not a whole number of frames. *Which*
  layer discards is still not identified — only the condition that provokes it.

  `read_bytes` now flushes once and reads immediately: **0 of 6**, alternated against `Recorder`'s
  0 of 6. That fixes its two remaining callers, both of which turned out to be worse off than the
  entry above assumed. `analyze_fft.py --serial` is clean, and both decoders now agree on its
  captures. `host/filter_demo.py` was broken in a *second* way that the pause fix does not touch:
  it concatenates 62 slices, each starting after its own `tcflush` that cut the stream mid-frame,
  so the buffer carries **20–25 phase changes** by construction. Aligning it once was a coin flip
  between 0 and 44 k jumps over threshold on otherwise identical captures — it now decodes each
  slice on its own with `frame_align`, measured 0–12 against whole-buffer `frame_align`'s 19–53.
  It also runs in 2.9 s instead of 6.2, since the 50 ms pause was longer than the 45 ms read.

  What is left. `test/analysis.py` still picks the loudest window, deliberately: the graded suite's
  published 0–100 scores would move and re-running it is a board-day. And the discard mechanism
  being unidentified is still the uncomfortable part — the Tiliqua's UAC2 path has no equivalent
  flush-then-pause that anyone has looked for, but nobody has looked.

  The drift is now *detected*, at least: `scripts/check_artefacts.py` hashes the sources that feed
  each artefact into `scripts/artefact_hashes.json` and compares on demand, catching uncommitted
  edits as well as commits. Both artefacts now have a real recorded provenance: the Basys 3 entry's
  honest `null` became the seven source hashes the 2026-08-10 build was made from.

  ~~It **hashes whole files, so a comment cries stale**.~~ **Fixed 2026-08-10.** The Tiliqua
  archive was reported stale against `afda87e`, which edited nothing in `gateware/top.py` but a
  docstring — the script's own module comment had already named the failure ("a source set that is
  too wide … cries stale over a comment in a test harness, and then nobody reads the output"),
  and it was firing on the first artefact anyone checked. Sources are now hashed after a
  `normalize` pass: `.py` through `tokenize` minus comments and docstrings, `.v`/`.x` through a
  scanner that skips `//` and `/* */` while respecting string literals. `.sh`/`.tcl`/`.xdc` keep
  their raw bytes deliberately — a naive `#` strip there risks a false *clean*, which is the one
  failure mode worse than the one being fixed. Both records were migrated by re-hashing the
  content at their own recorded commits (`git show <built_from_commit>:<src>`), **not** by
  `--update`, which would have stamped today's date onto a build from two days ago and turned a
  true provenance claim into a false one; the raw digests at those commits matched the old records
  first, so the migration is checkable. The stale explanation no longer names commits the hash
  cannot see, and `--self-test` deletes all 2877 code lines of all 18 tracked sources one at a
  time and asserts every one of them moves the hash — 0 blind, with the blindness deliberately
  reintroduced once to confirm the test fails when it should.

  ~~And **nothing runs it automatically** — no hook, no CI step, so it only helps someone who
  thinks to run it.~~ **Fixed 2026-08-20.** `.github/workflows/artefacts.yml` runs it on every push
  and PR. The script is pure stdlib and builds nothing, so the job needs no toolchain and finishes
  in seconds; it runs `--self-test` first, because a false green here is worse than no job.

  What made this possible was not the workflow — that part is six lines — but working out how a
  permanently-red check could be made to mean something. The Basys 3 blob **is** stale, for a
  reason that is written down and cannot currently be fixed (#41), so an unconditional check would
  have failed on its first run and been ignored by its second. A blanket "skip basys3" flag would
  have been worse than no automation at all: it would hide the drift nobody has seen yet along with
  the drift everybody knows about. So the waiver is **scoped** — `known_stale` in the record names
  the sources it covers, here `core/synth.x` alone, and lapses the moment anything outside that set
  changes. Verified by changing a second source: the verdict goes `known-stale` → `stale` and the
  exit code 0 → 1. `--strict` fails on the waived ones too, for running by hand.

  One gap remains in the check itself: it covers no **Tiliqua SDK** checkout, which is outside this
  repo and unhashable from here.

  The record now has a second consumer, which raises the cost of it being stale.
  `scripts/build_firmware_json.py` reads `built_from_commit` out of `artefact_hashes.json` and
  writes `webui/static/firmware.json`, the "what this repo ships" half of SETTINGS ▸ Firmware
  (#27). Everything *else* in that file is read out of the artefacts themselves — the Vivado
  header in `top.bit`, the tar entry mtime of the Tiliqua archive's inner bitstream — because a
  fresh clone rewrites every file mtime and an mtime-derived date would be a confident lie on any
  machine but the one that built it. The commit is the one field that cannot be recovered that
  way, so it is borrowed rather than duplicated, and a stale record is now visible on the panel
  instead of only in `git log`. The other half of that block is the board's own answer, which the
  Tiliqua reports in its USB `iManufacturer` string since #27 — see `gateware/build_id.py`.

  **Building on push was considered and cancelled**, not deferred: Vivado needs a licence and
  ~100 GB, so no hosted runner can produce the Basys 3 half, and a green tick covering one board of
  two claims more than it checks. `.github/workflows/pages.yml` deploys `webui/static/` and
  `docs/slides/` — it does not build, and it never will. `artefacts.yml` does not change this: it
  hashes sources, it does not compile, and it makes no claim about whether an artefact *works* —
  only about whether it was made from the tree it sits in.
