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
```

The board must be connected and the **web server stopped** (it owns the serial port):
`pkill -f webui/server.py`. A full run takes several minutes (all captures with best-of-N retry,
plus ffmpeg). Outputs land in `test/out/` (gitignored):

- `report.md` / `report.json` — per-test scores (0–100), verdicts, metrics, overall grade.
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
- **`captions.py` / `video.py`** — Pillow caption cards + ffmpeg spectrogram clips, concatenated
  into `report.mp4` (this ffmpeg build has no `drawtext`, hence Pillow).

## Scoring

Each test scores 0–100 by its rubric → **PASS ≥ 85 · WARN 60–84 · FAIL < 60**; the overall is
the weighted mean → letter grade. **Stress is strict**: glitches, clipping/railing, and
stuck/latched output (tail not returning to digital silence) lower the score. Reuses
`host/uartaudio.py` (serial, MIDI, capture) and `webui/synthspec.py` (CC map + factory presets).
