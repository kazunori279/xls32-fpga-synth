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
   the board, by the `usb_tee` FIFO in this repo's own gateware, which is written off the
   motherboard's clock and read off the host's with no rate control between them and is required to
   drop rather than stall the codec. 110–123 ppm apart eats its 16 entries every 10.4 s, and a
   millisecond cut out of a sustained tone is a step. (`out0`/`out1` never see it — the tee is a
   copy that cannot push back on the DAC path.) `scripts/declick.py` bridges them and
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
  puts the tank out of the path entirely: something *else* is holding a constant. Nor is it the
  pulse-duty offset below — that one is signal-dependent and this window has no signal in it. Basys 3 returns
  tail RMS 0 for the same case, so it is Tiliqua-side. Candidates not eliminated: the `>> 1` floors
  in the echo and chorus mixers, and the engine → AK4619 / UAC2 output path. At ~−49 dBFS it fails
  no checker, which is exactly why it is written down here rather than fixed in passing.
- **The USB tee carries the pulse wave's DC, and nothing removes it.** A pulse at anything but
  50 % duty has a DC term; the Bach demo patch runs `PULSE W` 100 of 128 — about 78 % — so every
  sounding voice contributes an offset that tracks its own envelope. Measured on a full take of
  *Prelude in C* off the UAC2 input: mean **+0.286**, with **89.6 % of the energy below 5 Hz**,
  which leaves the audible band at −25.9 dBFS and spends the headroom on something no one can hear.
  This is *not* the DC rail above — it is signal-dependent, it is arithmetically expected, and the
  same signature is present in the July take, so it predates the capture rewrite. It also does not
  reach anyone's ears: `out0`/`out1` are AC-coupled and remove it. The digital tee is a tap taken
  before that point, so anything recording from USB has to high-pass for itself, which
  `scripts/demo_video.sh` now does at 20 Hz. What has not been checked, for want of the hardware,
  is whether the offset costs any headroom *inside* the engine before the output stage — if it
  does, loud four-part passages are clipping asymmetrically and no test would say so.
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
- **`host/analyze.py --serial` grades every bitstream the same.** It opens the port at 115200 and
  reads 8-bit mono against a 4 kHz assumption; the transport is 2 Mbaud, 16-bit, stereo-interleaved
  (`host/transport/uart.py`, `SR = 32000`). So it returns period 24 and peak-to-peak 128
  *identically* on the 2026-07-13 blob and the 2026-08-10 rebuild — it discriminates nothing, and
  its verdict on both is `CHECK`. It could not even reach that verdict since `abf12b9`
  (2026-08-01), which swapped the `glob` port-finder for `transport.uart.find_port` and dropped
  `time` out of the import beside `glob`, leaving a certain `NameError`: for 112 commits
  `boards/basys3/scripts/verify.sh` crashed before it measured anything. The import is fixed and
  `verify.sh` now calls `host/play.py`, which was right all along. What is left is the serial path
  itself — documented as stale rather than repaired, and the honest options are to port it onto
  `transport/uart.py` or to delete it. The stdin mode is unaffected and is what README §3's
  iverilog run pipes into. This is item 1's lesson from the other end: a check that passes nothing
  gets read by nobody, and a broken verifier is indistinguishable from an absent one.
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

  Three gaps remain in the check itself. It covers no **Tiliqua SDK** checkout (outside this repo,
  unhashable from here). **Nothing runs it automatically** — no hook, no CI step, so it only helps
  someone who thinks to run it. And it **hashes whole files, so a comment cries stale**: the Tiliqua
  archive is reported stale today against `afda87e`, which edited nothing in `gateware/top.py` but a
  docstring. Its own module comment names this failure — "a source set that is too wide … cries
  stale over a comment in a test harness, and then nobody reads the output" — and firing on the
  first artefact anyone checks is how that ends. The archive is not actually behind.

  **Building on push was considered and cancelled**, not deferred: Vivado needs a licence and
  ~100 GB, so no hosted runner can produce the Basys 3 half, and a green tick covering one board of
  two claims more than it checks. `.github/workflows/pages.yml` deploys `webui/static/` and
  `docs/slides/` — it does not build, and it never will.
