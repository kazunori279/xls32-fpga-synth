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
