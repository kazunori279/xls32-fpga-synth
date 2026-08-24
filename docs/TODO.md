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

## ~~Queued behind the next rebuild~~ — paid off 2026-08-22

**Landed together in M37**, which is what this section was arguing for. #39 (`--router2-tmg-ripup`),
#40 (the visualiser grid) and #43 (the manifest `brief`) were each individually cheap and each
blocked by the same rebuild, so they were batched: one netlist change, one seed sweep, one board
day. The 24-voice archive came out at **54.30 MHz on seed 3** and re-graded **99.8/100 (A+)**.

Two of the three costs this section predicted were real. The seed lottery did have to be won again —
seed 4's 55.48 MHz did not survive, and the new draw put seed 3 on top at 54.30 with a 5.2 MHz
spread across the six. The board verification did have to be re-earned, 625 s of suite plus the
setup around it.

The third prediction was the interesting one, and it was understated. It said only #39 and #43 touch
`build.sh` and that `check_artefacts.py` hashes `.sh` raw, so either edit marks both archives stale
before anything is rebuilt. True, and still true. What it missed is that the **build stamp** does
the same thing to the *bitstream*: `build_id.py` bakes `<utc>-<commit>` into a ROM, a dirty tree
appends `-dirty`, and those six characters are worth 240 cells. A seed measured on a dirty tree was
2 MHz ahead of the field and turned out to be the worst of six on the netlist that shipped. Sweep on
the netlist you are going to ship, and count a dirty tree as a different netlist.

**Settled 2026-08-24.** The 32-voice archive was rebuilt against all of it
([#46](https://github.com/kazunori279/xls32-fpga-synth/issues/46)) and the waiver is gone. The
prediction that a rebuild costs a sweep of which about half converge was right to the seed: seven of
thirteen. Seed 10 ships at 48.37 MHz against 46.35. The prediction it got wrong was pessimism about
the old pin — seed 5 read 46.34 on the new netlist, so it had not gone bad, it had only been
overtaken. Sweeping finds better seeds; it does not rescue you from broken ones, because that is not
what happens.

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
   [`sWc6g7cgsd4`](https://youtu.be/sWc6g7cgsd4). ~~One thing is still open, and it is not about the
   take. The acceptance was a listening judgement, not a measurement: there is still no check that
   says whether a declick landed on a dropout or on a knob.~~ **Fixed 2026-08-20**
   ([#12](https://github.com/kazunori279/xls32-fpga-synth/issues/12)). `declick.py --audit` runs
   both detectors over one take and matches them: a waveform hit with no counter event under it is
   a knob move the heuristic was about to rebuild. It exits 1 if it finds any.

   The reason this could be a measurement rather than a listening judgement is that the ground
   truth was always in the same file as the music — ch2/3 carry the counter, so nothing had to be
   inferred, only lined up. Tolerance is `MERGE_S`, the window the detector already uses to call
   two hits one seam. Verified against a synthesised take with one real dropout and three knob
   steps: **1 corroborated, 3 spurious (75%), 0 missed, exit 1** — and `--heuristic --dry-run` on
   the same file duly reports four seams, which is the bug the audit exists to name.

   Misses are reported and do **not** fail, deliberately. A spurious bridge lands ~0.18 from what
   was there against a CC step of 0.0078, so it is about twenty times louder than the thing it
   aimed at; a miss just leaves a click that was already there. Scoring them the same would give no
   reason to prefer the counter path. With no counter on the take it exits 2 rather than guessing,
   because an audit against nothing is the listening judgement again with a number on it.
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

- ~~**The shipped Basys 3 bitstream predates both engine DC fixes, so the two boards are no longer
  bit-exact**~~ ([#41](https://github.com/kazunori279/xls32-fpga-synth/issues/41)). **Paid off
  2026-08-22.** `290d00b` and `3aa0227` are `core/synth.x` changes and therefore belong to both
  boards, but for two weeks only the Tiliqua archives carried them; refreshing this one needed
  Vivado on an x86 machine — the GCE build VM in `remote_build.sh`, which is not something CI can
  provide — and then a Basys 3 on the desk for `verify.sh`. Both happened: the build reports `ok`
  against `2fcb2b9`, and `verify.sh` returned 4 of 4 notes on hardware. The `known_stale` waiver
  is gone from the record.

  What the entry did not predict is that the rebuild would **not close timing**. The M35 DC fixes
  lengthened the engine's combinational backpressure chain, and that turned `ardy` — the audio-ready
  handshake, which reaches the clock-enable pin of nearly every register in the engine — into a
  genuine 10 ns path: 861 of 879 failing endpoints, worst −0.565 ns. The remaining 18 were an
  exception Vivado had quietly dissolved by absorbing `revwetL_reg` into a DSP48 as its B input
  register, taking the name-matched `/6` constraint with it. So the entry's real cost was not the
  x86 machine and the desk; it was that two weeks of engine changes had gone unbuilt on this board,
  and a rebuild is the only thing that reads that back. Fixed in `2fcb2b9`, closed at +1.322 ns.
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
  a `core/synth.x` change both boards share. Its blob was pre-fix when that was written; it is not
  any more ([#41](https://github.com/kazunori279/xls32-fpga-synth/issues/41)), so the measurement
  is now available to whoever wants it — re-run `stress_silence_recovery` against the 2026-08-22
  Basys 3 build and the before/after pair on that board is finally comparable.
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
- ~~**`check_headroom_hw.py` flakes on its quietest row: 1 FAIL in 4 runs of the same bitstream**~~
  ([#42](https://github.com/kazunori279/xls32-fpga-synth/issues/42)). **Fixed 2026-08-21.** Found
  verifying the 24-voice build. The failing row was CC75 = 100 at **2 voices**, shift −0.0808
  against the threshold — the least-driven row in the sweep, so the smallest shift in the table and
  the one nearest the line. Every loud, high-polyphony row passed on all four runs, which is the
  wrong way round for a returning offset and the right way round for capture noise.

  The note here used to say the threshold "should scale with the row", meaning a per-row constant
  by polyphony. That would have been the wrong fix, and the run that fixed it proved so: on
  2026-08-21 the row that went marginal was not the 2-voice one at all but **CC75 = 74 at 4
  voices**, which had been comfortable at 3.1× its scatter the day before and came back at 0.42×,
  its spread having gone 0.026 → 0.165 with nothing changed but the draw. The scatter is a property
  of the *run*, not of the row, so no table of per-row constants can track it.

  What went in instead: `row` already returned the spread across its three repeats and `grade`
  never saw it. Now it does, and a shift must clear both `TOL` **and** its own repeat spread
  (`MARGIN = 1.0`) before its sign is read. Self-calibrating, no constant per row. On a known-good
  build the two populations separate cleanly — genuine rows 1.3–19×, the flake 0.35/0.53/0.72× over
  its three recorded appearances. A row that fails it is `marginal`: printed with its numbers, not
  failed, and not counted as a pass either, because it is evidence of nothing. If marginal rows
  outnumber readable ones the run is `INCONCLUSIVE` rather than green off the survivors — the same
  mistake one level up.

  Raising `TOL` until the flake fitted under it was the other option and is worse: it blinds the
  check to every genuine row below the new floor, and the 8-voice row sits at 0.12. The remaining
  soft spot is that 1.3× is not a comfortable margin, so a noisy run will set aside a real row; that
  costs one row of evidence out of a dozen that all have to agree, and the output says how many.
  Also added `--self-test`, which grades 20 rows recorded off the module with no board attached —
  the flake lives in an `if`, and finding out what a change to it did should not cost ten minutes of
  board time.
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
- ~~**`filter_sweep` WARNs on Tiliqua at 80.7 against 86 on Basys 3**~~
  ([#7](https://github.com/kazunori279/xls32-fpga-synth/issues/7)). **Diagnosed 2026-08-22, on the
  desk, and it was two bugs rather than a filter.** The 86 was a pre-#28a capture: on Basys 3 the
  case's centroid reads 486→948 Hz interleaved and **962→1765 Hz** de-interleaved, and post-fix
  Basys 3 grades `basic` 99.8 with `filter_sweep` not warning at all. What is left is a threshold
  with no margin. `_chk_sweep` is `score_scale(rise, good=800, bad=100)`, so Basys 3's **803 Hz**
  rise clears `good` by **0.4%** while Tiliqua's 665–690 Hz warns — 17% apart on centroid rise,
  straddling a bar calibrated on one board. The 48 kHz hypothesis is ruled out by construction:
  `boards/tiliqua/gateware/xls_core.py:57` is `ENGINE_FS = 32000` — the codec runs at 48 kHz and
  `dsp.Resample` pulls the engine at exactly 2/3 of it — and `core/synth.x:353` feeds the SVF a raw
  integer coefficient with no sample rate anywhere in the expression. Same CC74, same corner, both
  boards. The remaining suspect is named rather than guessed: `dsp.Resample`'s anti-imaging FIR is
  in the Tiliqua path and nowhere in the Basys 3 path, and `centroid_over_time` measures to
  `fmax = 8000`, so a rolloff inside that band shaves the bright end and leaves the dark end alone —
  which is the shape of the data (starts agree within 6%, ends diverge the other way). Re-deriving
  `good` from both boards rides with [#11](https://github.com/kazunori279/xls32-fpga-synth/issues/11):
  same file, same board day.

  **The rise is not fixed, which sharpens that.** M41's 32-voice build reads **961→1687 Hz**, a
  726 Hz rise, and scores **89.4** — the first time this case has not warned on Tiliqua, and the
  reason the suite printed 175/0/0. Nothing in the filter changed between the builds; the voice
  count and the routing did. So the 665–690 Hz above is one build's number rather than the board's,
  and a bar this case clears or misses depending on the rebuild is a bar, not a measurement.
- ~~**+369 TRELLIS_COMB from the part-select remap, still unexplained**~~
  ([#5](https://github.com/kazunori279/xls32-fpga-synth/issues/5)). **Closed 2026-08-22 as a class,
  not a case.** The reason it was urgent has expired — the ~1,200-cell budget it was measured
  against was for clearing `flo`/`fbnd` in `apply_off`, and that fix was never built: `290d00b`
  closed the SVF dead zone a different way for **598 cells**. And the miss is no longer a single
  data point. [ARCHITECTURE_tiliqua.md → E1](../ARCHITECTURE_tiliqua.md#e1-the-six-hard-constraints)
  now carries three: +369 against an estimate of 50, **+4 against an estimate of −150** when
  `TrsPanicInject` and the arbiter's third source were deleted, and −136 on a `core` whose RTL did
  not change. The second one is what settles it — removing real logic moved the total the wrong
  way — so this is post-pack re-synthesis swing, and E1's standing conclusion covers it: nothing
  under ~100 cells can be costed on this design without building it both ways.
- **Basys 3 cannot report which bitstream is flashed on it, and structurally cannot**
  ([#29](https://github.com/kazunori279/xls32-fpga-synth/issues/29), closed 2026-08-22 as designed
  out). #27 gave Tiliqua a stamp the board itself reports, in its USB `iManufacturer` string. Basys 3
  has no equivalent for two reasons that no amount of gateware fixes: every USB string belongs to
  the FT2232H bridge and is identical on every Basys 3 ever made, with the FPGA downstream of it;
  and the return path is a continuous 4-byte PCM frame that `Aligner` locks onto by the LSB channel
  markers and never stops, so there is no idle gap and no framing escape to put a reply in. SETTINGS
  ▸ Firmware therefore shows Basys 3 as "no stamp" and falls back to `firmware.json` — the Vivado
  header date out of the committed `top.bit`, which answers *what this repo ships* and does not
  pretend to answer *what is on your board*. One mechanism was costed and rejected: steal bit 1 of
  each sample while a reply is in flight (sync word, payload, CRC, release), inaudible at −90 dBFS
  and frame-shape-preserving, but a protocol change on both ends — `top.v`,
  `host/transport/uart.py`, `webui/static/transport.js` — plus teaching the aligner check that a
  stamped frame in `webui/testdata/` is still a valid frame. Worth revisiting only if a *second*
  thing ever needs to come back out-of-band, at which point the timestamp rides along for free.
- **27/28 MULT18X18D — one spare since M38, and it came from a mistake**
  ([#6](https://github.com/kazunori279/xls32-fpga-synth/issues/6)). This read 28/28 from M24 to
  M37. The one that freed up was never wanted: `edly = dtime_c * ECHO_STEP` is a 7-bit value times
  the constant 192, and yosys spent a DSP on it. **The remaining 27, audited from `top.json` on
  2026-08-22:** 19 in `core.engine` (XLS output, both operands live), 3 in `fx` (`mul_a/b/g`, the
  Q15 tank multiplies, both operands live), 2 vendor (`core.resample.filt`, `pmod0.calibrator` —
  read-only), and **3 in `tiles`, of which two are constant multiplies like the one just removed**:
  `hue = (nrel * HUE_K) >> 8` and `v = IDLE_V + ((i_level * (255 - IDLE_V)) >> 8)`. Neither
  decomposes as cheaply as 192 did — `HUE_K` is 6091 (9 set bits) and `255 - IDLE_V` is 235 (6) —
  so these are the case the route below was written for: a 44-entry ROM for `nrel * HUE_K` frees
  two multipliers, at the cost of the resource that is *actually* scarce. Neither is on the
  critical path today.
  Worth doing only when a feature needs a multiplier and cannot have one. See
  [ARCHITECTURE_tiliqua.md → E3](../ARCHITECTURE_tiliqua.md#e3-multipliers-27-of-28).
- **Risk 3b — `sync`/`usb` fails static timing at 60 MHz, and it has now bitten.** Open since M25;
  promoted from a carried risk to an observed failure in August 2026, when the vendor ran the
  shipped bitstream on their own two Tiliquas and it worked on one and not the other. The build they
  tested measured 40.95 MHz against 60; what the repo ships now is the 24-voice build at
  **56.63 MHz**, which is a much smaller bet but still short. Two things this item used to say are
  wrong: the failing
  path is *not* only in `fx` — that is true at 97% occupancy and nowhere else — and "both run
  clean" was one die's evidence. Underneath `fx` is a **~20-LUT-level luna cone** that has sat at
  ~45 MHz since M25 and is depth-limited, not congestion-limited: 4.79 ns of pure logic against a
  16.7 ns period. **The "24 voices is measured and dead (+5.8 MHz, ceiling 46.79)" entry that used
  to sit here was wrong, and it was the load-bearing one** — it was measured on a netlist whose
  voice ring had been pruned away by a broken variant generator
  ([#35](https://github.com/kazunori279/xls32-fpga-synth/issues/35)). Built correctly, 24 voices is
  93.9 % occupancy and **55.48 MHz**, +9.1 over the 32-voice build, and it is what the repo now
  ships — at 93.5 % and 56.63 MHz since M38 took a DSP out of the echo tap and re-swept 24 seeds
  on the netlist that left (see the next item). Still measured and dead:
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

  **M36 routes around it rather than closing it.** At 24 voices the shortfall is 56.63 against 60 —
  a bet that the silicon is 6 % faster than modelled, where 32 voices bets on 29 %. That is the
  formal bitstream now; the 32-voice build stays available and stays a risk. The gap itself is
  unchanged and still needs a register in luna's cone
  ([#34](https://github.com/kazunori279/xls32-fpga-synth/issues/34)).

  **M38, 2026-08-22: the sweep found a bug in our own code, and #34 is now the only lever by
  measurement rather than by assumption.** Sweeping 18 more seeds on the shipped netlist (24 in
  total: mean 53.15, 49.12–56.30, 2 at or above the vendor's 55) turned up seed 20 at 56.30 — and on
  seed 20 the critical path was not luna's. It ran from the CC82 clamp in `fx.py` through
  `dtime_c * ECHO_STEP` into the echo line's write pointer, 17.76 ns, **3.93 of it inside a
  MULT18X18D that should never have existed**: a constant multiply of a 7-bit value by 192, which
  yosys handed a DSP on a die where all 28 were already spoken for
  ([#6](https://github.com/kazunori279/xls32-fpga-synth/issues/6)). 192 = 128 + 64, so it is two
  shifts and an add — same value, no DSP, no extra cycle, `test_fx.py` bit-exact against `FxModel`
  on all five scenarios including the CC82=127 clamp. 22,985 cells → 22,722, 28/28 multipliers →
  27. **What the fix bought is the floor, not the ceiling.** Re-sweeping all 24 seeds on the new
  netlist: mean 53.15 → **53.99**, sd 1.71 → 1.39, worst draw 49.12 → **51.60**, seeds at or above
  55 MHz **2 of 24 → 6 of 24**, best 56.30 → **56.63** (seed 7). Removing a 3.93 ns cell from the
  middle of the design barely moved the top because the top was never that cell. On seed 7 the
  worst path is luna again — `usb.timer.counter[7]` → `usb.data_crc.crc[1]`, 17.66 ns, 13.04 of it
  routing — with no fx cell and no multiplier anywhere in it. Rankings still do not transfer: seed
  7 read 50.90 on the old netlist and old-winner seed 20 reads 54.54 on this one.
  **Then it went on hardware the same day, and held.** Seed 7's build, SRAM-loaded over dirtyJtag
  and stamped `2026-08-22T06:27Z-76401a5` on the board: **99.8/100 (A+), 174 pass / 1 warn / 0
  fail** over the full 175 cases in 618 s, USB frame gaps 0.00 %, audio clock 12.288 MHz. The same
  grade as seed 3's, with the same single WARN (`filter_sweep`, 80.9) — 2.33 MHz of timing margin
  bought no audible change, which is what a design that already met its own rates should show.
  `xls24-r5.tar.gz` is now that build and `check_artefacts.py` reports tiliqua-24 clean again;
  #46's waiver on the 32-voice archive stands, since that one still has not been rebuilt.
  **M39, 2026-08-24: splitting `sync` from `usb` was the last cheap lever, and it is not enough.**
  Both were the same physical net — one PLL output driving both, so one 60 MHz constraint over all
  22,722 cells — and only `usb` has a reason to be 60. `CLKOS` was already making 120 MHz for a
  PSRAM DQS that M29 deleted, so `CLKOS_DIV` 5 → 12 gives `sync` its own 50 MHz off the same VCO
  with no new PLL (`boards/tiliqua/gateware/clocks.py`, behind `XLS32_SPLIT_CLOCKS=1`). Twelve
  seeds, ten routed: usb **mean 55.26, best 59.40, worst 51.32**, against M38's 24-seed unsplit
  draw of mean 53.99 / best 56.63 / worst 51.60. So **+1.27 on the mean and +2.77 on the best draw,
  and still a FAIL at 60** — with routability slightly worse, 10 of 12 converging against 24 of 24.

  The critical path is the finding. On the best seed it is 16.84 ns, 4.34 logic / 12.49 routing,
  and it runs seventeen LUT levels across four modules — `usb.token_detector.timer.counter` into
  `USBControlEndpoint.request_mux.stall`, out through `endpoint_mux.valid` and
  `translator.phy_ready`, back through the same mux, then `IsoStreamInEndpoint.bytes_left_in_frame`
  into the SDK's `channels_to_usb_stream.fifo_postprocess_state` and its level counter — with **not
  one cell of ours in it**. Routing is still three quarters of it, but for a reason occupancy
  cannot fix: a seventeen-level cone spanning four modules has no local placement to find at any
  occupancy. #47's premise was congestion and the answer is depth, which puts the whole shortfall
  back on #34 item 3 and now names where the register goes. That is vendor code, so upstreaming it
  is the only route. The split stays in the tree, off by default: it buys a megahertz on a domain
  that still fails, and turning it on is a different netlist with its own seed lottery. What it did
  prove on its own is that the engine side was never the constraint — `sync` clears its 50 on every
  seed that routed, by 1.55 MHz at worst and 7.84 at best, and those are floors rather than
  ceilings, since nextpnr stops optimising a domain that already meets its constraint.
  [#47](https://github.com/kazunori279/xls32-fpga-synth/issues/47).

  **The suite's first published `missing_frames` total is wrong**, and it is worth writing down
  before it becomes a baseline: 73,646,868 frames reported missing over 49 of 175 captures, when
  the whole run lasted 618 s and the device can only have produced ~29.7 M frames in it. Every
  case still graded, peaks are normal and `gap_rate` reads 0.00 %, so this is the counter
  reconstruction in `usbaudio.py:401` and not the audio — the same order as the 75 M the first
  probe misreported by reading the wrap unsigned, which suggests the signed fix is incomplete
  rather than absent. [#48](https://github.com/kazunori279/xls32-fpga-synth/issues/48), filed
  separately from [#9](https://github.com/kazunori279/xls32-fpga-synth/issues/9): that one is the
  dropouts, this one is the instrument built to measure them.

  **2026-08-24: found, and it was neither the wrap nor the audio.** PortAudio does not hand back
  the device's bits. CoreAudio delivers float32 and the conversion into `int32` lands a few LSBs
  either side of the exact value — measured live, ch2 reads `0x80fd0003` where the gateware wrote
  `0x80fd`, ch3 reads `0x25c6ffff` where it wrote `0x25c7`. A truncating `>> 16` therefore reads
  ch3 **one low**, and ch3 carries the counter's high bits, so the reassembled counter sits 32768
  cycles under. Every frame where that error switched on or off then looked like a 128-frame jump
  in a counter that had in fact stepped by exactly 256. The fix is `_ch16` in `usbaudio.py`:
  round to the nearest multiple of 65536 instead of truncating, which recovers the written value
  exactly, since the conversion error is three LSBs against a 32768 LSB spacing. A/B on 24
  identical captures, 1,851,392 frames delivered: truncating reports **22,641,160 missing over
  24 of 24 captures**, rounding reports **0 over 0**. The per-capture totals decay monotonically
  from 3.19 M to 640 across the run, which is the tell — the conversion's rounding direction
  depends on the sample's magnitude, so the error sweeps in and out as the counter climbs, and
  that is also why the suite saw it on 49 of 175 captures and not on all of them.

  Two things came with it. `alive` now tests the gateware's bit-15 marker rather than "channel 2
  is nonzero", because with a few LSBs of noise a frame of true zeros can come back as 1 and read
  as delivered. And the audio channels get the same rounded read, where it is worth half an LSB
  and nothing else. Published `missing_frames` totals from before this date are the instrument
  and not the transport; the graded runs they came from are unaffected.

  **The vendor has now run the 24-voice build on more than one die, and it held.** Sebastian
  Holzapfel, 2026-08-22, on merging the webflasher PR: *"I have tested it on a couple of Tiliquas
  and with the timing fixes now it seems to be robust, and I think it should work everywhere."*
  That is the measurement this project could not make — every other result on this risk comes from
  the one module on this desk, which has always worked. It is evidence about **M37's 54.30 MHz
  24-voice build specifically**: the 32-voice archive is unchanged and untested by him since the
  failure, and he did not say which of his modules were in the "couple", so the die that failed is
  not named as having passed.

  **2026-08-23 he answered the question, and it is the first of the three: no enumeration.**
  *"The symptom was no USB enumeration/communication at all. As I mentioned, the previous bitstream
  I tested was failing timing at 45MHz, which could cause all kinds of problems, including the
  above."* That rules out the other two readings —
  [#3](https://github.com/kazunori279/xls32-fpga-synth/issues/3) was never audio that came up and
  glitched, and never a missing video output — and it points at the USB cone, which is exactly
  where the failing paths on that build were (`usb.timer.counter[7]` → `usb.data_crc.crc[1]`, and
  luna's `transmitter.fsm_state` and `IsoStreamInEndpoint.bytes_left_in_frame` before it). A device
  that never enumerates is one whose USB logic is not meeting its own timing, not one whose engine
  is wrong.

  What is still not measured is the causal link. He attributes it to that build missing timing at
  45 MHz, which is an inference from the report and not a capture off the failing die, and nobody
  has re-run the old bitstream on it to confirm. So #3 has a symptom and a named suspect rather
  than a proof, and it stays open on that basis. The consequence for what ships: `xls32-r5.tar.gz`
  sat at 46.35 MHz, within a megahertz of the build that failed to enumerate, and M41's rebuild
  moves it to 48.37 — three megahertz clear rather than one, which is not enough to change the label
  on slot 6. The 24-voice build at 56.63 is the one that has run on more than one die.

  **2026-08-24: the 32-voice build has now run on a second die too, and it graded the same.** Two
  more R5 modules arrived unflashed; one of them (call it die #2) took `xls32-r5.tar.gz`'s `top.bit`
  — stamp `2026-08-20T05:45Z-f7b52c4`, 46.35 MHz — as an SRAM load and enumerated on `usb2` first
  try. `check_loop.py` read 440.01 Hz (+0.1 cents) and 0.000 % frame gaps, and the full suite
  returned **99.7/100 (A+), 174 pass / 1 warn / 0 fail**, 175 cases in 616 s, frame gaps mean and
  worst both 0.00 %, audio clock mean 12.288 MHz over 175 captures (spread 29.3 kHz).
  `stress_32voice` scored 100.0 with zero glitches. The lone WARN is `filter_sweep` at 82 — the same
  [#7](https://github.com/kazunori279/xls32-fpga-synth/issues/7) that die #1 shows, on both
  bitstreams, so it travels with the design and not with the silicon. The 83,844,053 `missing_frames`
  over 55 of 175 captures is [#48](https://github.com/kazunori279/xls32-fpga-synth/issues/48)
  re-confirmed, not audio — since fixed, and the number was the host's `int32` conversion rounding
  the counter's high half down.

  **Then the same die was flashed to slot 6 and booted from it, which is the path a user takes.**
  `pdm flash archive` wrote the bitstream to `0x700000` and the manifest to `0x7f0000`, and the cold
  boot brought up `Tiliqua XLS32` on `usb2` with `iManufacturer` reading
  `apf.audio XLS32/2026-08-20T05:45Z-f7b52c4`. The manifest's `clk0_hz: 12288000` loaded on its own —
  `check_loop.py` read 12.288 MHz and 440.01 Hz with no bootloader dance, which is the part an SRAM
  load cannot demonstrate. The suite returned **99.8/100 (A+), 174 pass / 1 warn / 0 fail**, 175
  cases in 621 s, gaps 0.00 %, clock mean 12.288 MHz (spread 23.6 kHz), WARN `filter_sweep` at 80.

  What that is worth, stated narrowly: two dies out of two on this desk run the 46.35 MHz build
  clean, one of them from flash by the documented procedure, where before there was one. The module
  that actually failed is Sebastian's and is not here, and two winners do not refute a marginal path
  — a path some silicon loses is not required to lose often. What it does remove is the reading that
  die #1 is unusually good. Die #2 came out of the box, was never flashed, and grades within 0.1 of
  it across four runs.
- **The repo ships two Tiliqua bitstreams, and only one of them is a claim.** `xls24-r5.tar.gz` is
  the formal build — 24 voices, 93.5 % of the die, `clk` at 56.63 MHz, graded 99.8/100 (A+) on the
  module — and belongs in **slot 7**. `xls32-r5.tar.gz` is 32 voices at 98.3 % and 48.37 MHz, kept
  in **slot 6** as experimental: it works on this desk, and it did not work on one of the vendor's
  two modules. Anyone flashing slot 6 is carrying that.

  Both were verified this way — flashed or SRAM-loaded from the same archive and confirmed by the
  build stamp in `iManufacturer` before any test ran, so neither number is off a stale image; both
  runs are 2026-08-24, on the archives M41 rebuilt. They grade the same to a tenth: **99.8/100
  (A+)**, 175 cases in ~615 s, 0.00 % USB
  frame gaps. The 24-voice run is 174/1/0 with `filter_sweep` at 83.4; the 32-voice one is **175/0/0**,
  the suite's first clean sweep, with the same case at 89.4 — which is
  [#7](https://github.com/kazunori279/xls32-fpga-synth/issues/7) sitting near its 85 threshold and
  not a timing symptom either way. Worth stating plainly what that is and is not evidence of: the
  32-voice build misses 60 MHz by 19.4 %
  and still returns zero glitches across 175 cases *on the die that has always worked*. It says
  nothing about the die that did not, and if anything it sharpens the case that the failure is a
  marginal path some silicon wins and some loses
  ([#3](https://github.com/kazunori279/xls32-fpga-synth/issues/3),
  [#34](https://github.com/kazunori279/xls32-fpga-synth/issues/34)).

  **There is now a third copy, and it is deliberately one netlist behind.** The webflasher carries
  `xls24-9976c4e-r5.tar.gz` — M37's archive at 54.30 MHz — merged into `apfaudio/tiliqua-webflash`
  on 2026-08-22 ([PR #5](https://github.com/apfaudio/tiliqua-webflash/pull/5),
  [#32](https://github.com/kazunori279/xls32-fpga-synth/issues/32)). M38's 56.63 MHz archive has
  *not* been sent after it, on purpose. That copy is the one the vendor tested on several of his
  own modules before merging, and it is the only build with evidence from more than one die; M38's
  has been graded on exactly the die that has always worked. Against that, the swap buys a 6 %
  bet instead of an 11 % one and nothing audible — the two grade identically, down to the same
  lone WARN. So it waits and goes up with the next change that is worth a stranger reflashing for.
  The corollary is that the number a webflasher user sees is 54.30 until then, and the repo should
  not quietly start claiming otherwise.

  The two differ only in the voice count, which
  is generated into a throwaway copy of `core/synth.x` rather than living in the tree, so
  `check_artefacts.py` has to hash the generator and the count or it cannot tell them apart
  ([#10](https://github.com/kazunori279/xls32-fpga-synth/issues/10)).
- ~~**The visualiser's bottom row is dead on the formal bitstream.**~~ **Paid off 2026-08-22**
  ([#40](https://github.com/kazunori279/xls32-fpga-synth/issues/40)). `gateware/viz.py` fixed the
  grid at `N_VOICE = 32`, `COLS, ROWS = 8, 4`, and `build.sh`'s `VOICES` never reached the gateware
  — it only selected which `synth*.x` the engine came from. So the 24-voice build wrote tiles 0–23
  and left 24–31 at their reset value, which `VoiceTiles` renders at `IDLE_V` in the lowest hue:
  eight dim tiles that never change. No audio or timing consequence, and invisible to the suite,
  which grades frame *timing* and never tile content — which is why it survived M36 and needed a
  human to look at the panel.

  The count now comes from `gateware/voices.py`, which reads `$VOICES` and picks the grid from it.
  The cost estimate here was right about the seed sweep and wrong about what else was lurking: at
  COLS = 6 the tile index stops being a free `Cat(col, row)` and `row * COLS + col` costs a
  MULT18X18D, of which this die had none to spare at the time
  ([#6](https://github.com/kazunori279/xls32-fpga-synth/issues/6)). The build fails in the placer
  with "no BELs remaining", which points nowhere near `viz.py`. M38 has since freed one, so that
  exact build would survive today — by spending 3.93 ns and a DSP on two adds, which is the
  mistake M38 undid. It is written as `(row << 1) + (row << 2)` either way.
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
- **The preset workstream is closed, and the shipped bank is board-measured but not
  board-validated.** Eight presetgen issues were closed 2026-08-21 without further fitting
  ([#45](https://github.com/kazunori279/xls32-fpga-synth/issues/45),
  [#44](https://github.com/kazunori279/xls32-fpga-synth/issues/44),
  [#24](https://github.com/kazunori279/xls32-fpga-synth/issues/24),
  [#23](https://github.com/kazunori279/xls32-fpga-synth/issues/23),
  [#28](https://github.com/kazunori279/xls32-fpga-synth/issues/28),
  [#26](https://github.com/kazunori279/xls32-fpga-synth/issues/26),
  [#18](https://github.com/kazunori279/xls32-fpga-synth/issues/18),
  [#19](https://github.com/kazunori279/xls32-fpga-synth/issues/19)); the tools each of them asked
  for were written and the findings recorded, and none of it changed `presets_soundfont.json`. The
  reason is #22: the loss orders *quality* (19 of 24 blind pairs decided) and does not order
  *closeness to the target* (5 of 24, rho = +0.07), and closeness is the axis CMA-ES descends. More
  budget against that objective buys loss and not sound, which is the same verdict the two search
  widenings already returned. Three consequences stay open and unscheduled: attacks run **0.3–0.6**
  of the target's spectral centroid across the bank; **16 of 64** consolidated-away presets sit
  above #22's 0.09 audibility crossing from their nearest survivor, with a better cut available
  (#45, 12 of 64) that cannot be taken without a refit; and the shipped bank's board grade in
  `presetgen/bank_hw_soundfont.json` came from `excess = distance − floor`, which is unsound —
  25 of its 64 rows are negative. #24's replacement (`marginal`, against the nearest note-on state)
  is verified only on synthetic captures and has never been run against a board, so
  DEVELOPMENT.md's "sim-optimal until board-validated" rule has a standing exception here. Full
  evaluation in
  [DEVELOPMENT.md → Closing the preset workstream](../DEVELOPMENT.md#closing-the-preset-workstream-the-objective-is-the-limit).
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

  What is left: *which layer* discards on the UART side, still unidentified — only the condition
  that provokes it. ~~And `test/analysis.py` still picks the loudest window, deliberately: the
  graded suite's published 0–100 scores would move and re-running it is a board-day.~~
  **Settled 2026-08-22, and the board-day was never needed** ([#11](https://github.com/kazunori279/xls32-fpga-synth/issues/11)).
  `run_tests.py` saves every graded take to `test/out/wav/`, and `check(samples)` is a pure
  function of those samples, so the question could always have been answered off the disk.
  `test/regrade.py` does that now. Re-graded both ways, `clean=True` picks a different window on
  68 of 350 picks and moves **exactly one score, downward**: `filter_sweep`, 81.7 → 80.4, because
  `centroid_over_time` re-picks inside each of its eight slices and a quieter window flattens the
  rise the check measures. Nothing improves, and that one case is the suite's only WARN. Only two
  of the 68 differing picks had the default landing on the glitchier window (`filter_notch`), and
  the take-level pathologies `clean=True` was written for are rejected upstream by
  `harness._bad_take` before grading. So the default stays `False` on evidence. The honest limit:
  this is the Tiliqua USB set, and the pathologies in question are the UART's — no Basys 3 capture
  set is on disk to check. Two caveats on reading the tool's absolute numbers, both in its
  docstring: the wav is `normalize`d, which shifts the five factory presets by ≤1.4 points because
  `_chk_preset` thresholds glitches and clipping in absolute counts; and it is the best take of up
  to five retries, not the session.

  **The Tiliqua half, looked at 2026-08-21** (`boards/tiliqua/probe/probe_discard.py`, 24-voice
  build from flash slot 7). The instrument is the frame counter the gateware already tees onto
  channels 2 and 3: 256 audio cycles per frame, counting what the *device* produced whether or not
  any of it arrived. So between two delivered frames the counter must advance by exactly 256 per
  frame of separation, and it needs no wall clock and no reference — the board timestamps its own
  output and the arithmetic closes or it does not. That also separates two failures `gap_rate`
  conflates: a **zero-filled** frame arrived carrying nothing and holds its slot, which is what
  `_repair` rests on; a **missing** frame never arrived, leaves no zeros behind, and makes the
  capture simply short while every rate computed from it still reads clean.

  Four findings, and the useful one is the last.

  1. **The production path cannot reach this bug**, and that is now measured rather than argued.
     `record_start`/`record_stop` only move a buffer pointer — the `InputStream` runs from `open()`
     to `close()`, so there is no interval where the host stops collecting. Sweeping an idle pause
     between captures out to a full second: 0 missing, 0 zeroed, 0 flags, at every step.
  2. **The mechanism does exist**, and its shape is the UART's. Sleeping *inside* the callback so
     the host genuinely stops collecting is clean to 0.90 of a block period and falls off a cliff
     at 1.10 — 24576 frames delivered of ~216000 produced. The threshold is one callback period,
     85 ms here against the UART's 5 ms, about seventeen times the tolerance. Losses quantise to
     multiples of 12000 frames (exactly 250 ms), and a few deliveries come back *out of order* —
     the counter steps backwards by thousands of frames, meaning a superseded buffer was handed
     over. The first version of the probe reported 75 million frames missing out of 16384 delivered
     because it read the counter's wrap unsigned, which turns any backwards step into an 8.4 M-frame
     jump forward.
  3. **`input_overflow` is never raised. Not once, at any stall.** The obvious fix before measuring
     was to have `usbaudio.py` watch PortAudio's `status`, which its callback takes and ignores.
     That would have watched a flag that never sets. Worth writing down, because it is the second
     time on this issue that the plausible explanation was the wrong one.
  4. So the counter arithmetic is the only thing that can see it, and it is cheap. `record_stop`
     now reports `missing_frames` alongside `gap_rate`. The suite has therefore never been able to
     distinguish "no dropouts" from "a capture that was silently short"; it can now.

  **2026-08-22: the number is now published, not just printed.** It was collected only when
  non-zero and never written to `report.json` or `report.md` — which is the failure the entry two
  paragraphs above names about the gap rate, repeated on the number that replaced it: a figure that
  appears only when it is bad cannot be watched, because a clean report and a report from a build
  where the measurement silently stopped working read identically. Every capture that can measure
  it is now recorded, zeros included, as `missing_frames_total` / `missing_frames_captures` in
  `report.json` and one line in `report.md`. `null` means the transport cannot measure it (the
  Basys 3 UART); `0` means it measured none.

  **2026-08-24: and publishing it is what caught it being wrong.** The first two published totals
  read 73.6 M and 83.8 M frames missing out of runs that could contain 29.7 M, which is
  [#48](https://github.com/kazunori279/xls32-fpga-synth/issues/48) — fixed above. Worth the note
  here because it is the argument in this entry running the other way: the figure that only shows
  when it is bad cannot be watched, and the figure that is always published gets read, and a
  number that is read gets checked against what it can physically be.

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
  permanently-red check could be made to mean something. The Basys 3 blob **was** stale, for a
  reason that was written down and could not be fixed that week (#41 — since paid off, and the
  waiver with it), so an unconditional check would
  have failed on its first run and been ignored by its second. A blanket "skip basys3" flag would
  have been worse than no automation at all: it would hide the drift nobody has seen yet along with
  the drift everybody knows about. So the waiver is **scoped** — `known_stale` in the record names
  the sources it covers, here `core/synth.x` alone, and lapses the moment anything outside that set
  changes. Verified by changing a second source: the verdict goes `known-stale` → `stale` and the
  exit code 0 → 1. `--strict` fails on the waived ones too, for running by hand.

  ~~One gap remains in the check itself: it covers no **Tiliqua SDK** checkout, which is outside
  this repo and unhashable from here.~~ **Closed 2026-08-21**, by giving up on hashing it. Half of
  a Tiliqua bitstream is the vendor's — luna, luna-soc and the `tiliqua` gateware, linked in from
  `$TILIQUA_SDK` — and a file-by-file digest of it would not have travelled: CI has no checkout, so
  the one machine that could compute the hashes is the one machine that never needed them. A
  **commit** is the thing both ends can name. `sdk_state()` reads it out of the checkout (walking up
  for the repo root rather than assuming `$TILIQUA_SDK` is `<repo>/gateware`, since that variable is
  the user's to set), `--update` records it beside `built_from_commit`, and `check()` compares. It
  reaches further than one repo, too: luna and luna-soc are pinned by the `pdm.lock` inside that
  same checkout, so the single sha covers all three. A dirty vendor tree gets a `-` on the sha,
  which matters more here than anywhere else in this file — `build.sh` treats the SDK as read-only,
  so a dirty one means somebody was editing it.

  Three outcomes, and only one of them is a failure. Recorded and different is **stale**, and
  deliberately not waivable: `known_stale` names *sources*, and an SDK bump is exactly the drift a
  source-scoped waiver should not quietly cover. Recorded but with no checkout to compare against
  — CI, or anyone else's clone — says so and stays **ok**, because "cannot verify" is not "wrong".
  And **never recorded**, which is every archive built before today, stays ok with a note. That
  last one is the tempting mistake: backfilling the current sha into those records would make them
  look complete and would be a false provenance claim, because `--update` can only see the SDK as
  it is *now*, not as it was in August. The two shipped archives will carry the note until their
  next rebuild, and that is the honest state of what is known about them.

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
