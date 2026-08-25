# XLS32 synth — end-to-end hardware test suite

Drives the **real board over USB** and grades the **actual audio output**. Covers every
feature (basic), typical feature combinations (integration), and boundary conditions
(stress), then produces a captioned spectrogram **video** and a scored **report**.

## Run

```bash
uv run python test/run_tests.py            # full suite: reflash + all tests + video + report
uv run python test/run_tests.py --smoke    # fast subset (pipeline check)
uv run python test/run_tests.py --only basic|integration|stress
uv run python test/run_tests.py --no-reflash --skip-video   # fastest iteration
uv run python test/regrade.py --clean       # no board: re-grade the stored wavs
```

`regrade.py` is the one that needs no hardware. Every graded take is on disk in `out/wav/` and
`check(samples)` is pure, so "what would this analysis change do to the scores?" is answerable
without a board — read its docstring for the two ways the offline numbers differ from the
published ones. It is what settled
[#11](https://github.com/kazunori279/xls32-fpga-synth/issues/11).

The board must be connected and its link free — **close the web UI's browser tab**, which holds
the port through Web Serial. A full run takes several minutes (all captures with best-of-N retry,
plus ffmpeg). Outputs land in `test/out/` (gitignored):

**Leave the host's USB alone for the duration.** Plugging, unplugging or re-flashing anything else
on the machine makes `missing_frames` non-zero — the kernel handles enumeration synchronously,
PortAudio's callback runs late, and the device's ring is overwritten while it keeps producing. The
threshold measured in [#9](https://github.com/kazunori279/xls32-fpga-synth/issues/9) is one callback
period, 85 ms here, and a re-enumeration costs well over a second. This is not theoretical: it
produced three false hardware findings before anyone checked the USB log
([#49](https://github.com/kazunori279/xls32-fpga-synth/issues/49)). The offending device does not
have to share a hub with the board.

**A hub port or a half-seated plug looks exactly like a broken module.** A device that comes up
short is not necessarily a device: on this desk one hub port lost frames with any module and any
cable in it, and after that hub was replaced a plug that was not fully home did the same thing
until it was reseated ([#51](https://github.com/kazunori279/xls32-fpga-synth/issues/51)). Two
things separate these from a module fault, and neither is a sequential table of one device's
numbers. `host/probe_capture.py` opens every attached module at once and reads them over the same
window — a host stall shows up on all of them, one bad link shows up on one. And `usb_watch.log`
now covers the hub the modules sit on (bus `0-1`), so a link that is dropping and re-enumerating
says so directly rather than arriving as missing frames:

    0-1:3  ... connect [Tiliqua XLS32]  ->  0101 power connect

Check the log for the run's window before swapping anything, then swap at the module end and at the
hub end — the first moves only the module, the second only the port, so two swaps separate module,
cable and port.

**One board at a time, or say which one.** Several modules of the same build are indistinguishable
on the wire — same VID, PID and `iProduct`, and a shared `iSerialNumber` — so with more than one
attached the suite stops and lists what it found. Pick with `XLS32_AUDIO_DEV` and `XLS32_MIDI_DEV`,
each taking a device index or a name/UID fragment:

```bash
XLS32_AUDIO_DEV=4 XLS32_MIDI_DEV=0 uv run python test/run_tests.py --board tiliqua
```

They have to agree: the audio device and the MIDI destination are two halves of one board, and
nothing checks that you paired them. `[usbaudio] audio[4] ... midi[0] ...` on stderr at the start of
the run is what was actually bound. The indices are enumeration order, not identity — they can move
when cables do ([#50](https://github.com/kazunori279/xls32-fpga-synth/issues/50)).

- `report.md` / `report.json` — per-test scores (0–100), verdicts, metrics, overall grade, plus
  what the transport measured about itself: `gap_rate_*`, `audio_clock_hz`, and
  `missing_frames_*` (frames the board produced that never arrived — the only loss no rate can
  see, see [#9](https://github.com/kazunori279/xls32-fpga-synth/issues/9)). All are written on
  every run, zeros included; `null` means the transport cannot measure that one.
- `report.mp4` — one video: before each test a caption card (title, description, expected,
  verdict + score) then that test's scrolling spectrogram.
- `wav/<id>.wav` — each test's captured audio; `cards/` — the caption PNGs.

## How it works

- **`harness.py`** — owns the board: reflash (`openFPGALoader`), per-test reset
  (all-notes-off over the used range + every CC to its `synthspec` default), capture via the
  background `Recorder`, and **best-of-N retry keeping the highest-scoring take** (the board's
  1 Mbaud MIDI RX drops the occasional CC under bursty traffic — a dropped setup shows as a
  low score, so a retry lands a clean take; a genuinely broken feature scores low on every
  take). `TestCase`/`Result` + 0–100 scoring live here.
- **`analysis.py`** — pure-stdlib audio metrics on the captured samples (peaks/harmonics,
  envelope, spectral centroid/band energy, beating, pitch tracking, glitch count, tail/latch),
  built on the project's DFT (`host/analyze_fft.py`).
- **`cases_basic.py` / `cases_integration.py` / `cases_stress.py`** — the test cases; each has
  a `setup` (CCs), a `perform` (notes/CCs played while recording), and a `check` returning a
  scored `Result` against an expected-outcome rubric.
- **`regrade.py`** — re-grades the stored `out/wav/` captures with the analysis code as it stands
  and diffs against the published `report.json`; `--clean` also grades with
  `pick_window(clean=True)` and shows what moves. No board, no reflash.
- **`captions.py` / `video.py`** — Pillow caption cards + ffmpeg spectrogram clips, concatenated
  into `report.mp4` (this ffmpeg build has no `drawtext`, hence Pillow).

## Scoring

Each test scores 0–100 by its rubric → **PASS ≥ 85 · WARN 60–84 · FAIL < 60**; the overall is
the weighted mean → letter grade. **Stress is strict**: glitches, clipping/railing, and
stuck/latched output (tail not returning to digital silence) lower the score. Reuses
`host/synth.py` (MIDI, sample maths), `host/transport/uart.py` (serial capture) and
`webui/synthspec.py` (CC map + factory presets).
