# Repository map

A directory-by-directory guide to the source tree — what lives where, and why the split falls
where it does. This is the companion to the [Builder's guide](../README.md#3-builders-guide);
**you do not need any of it to flash a board and play the synth**, which is
[§2 of the README](../README.md#2-getting-started).

For what the code *does* rather than where it sits, see
[ARCHITECTURE.md](../ARCHITECTURE.md) (core engine + Basys 3 shell) and
[ARCHITECTURE_tiliqua.md](../ARCHITECTURE_tiliqua.md) (the ECP5 shell); for how it got that way,
[DEVELOPMENT.md](../DEVELOPMENT.md) and [DEVELOPMENT_tiliqua.md](../DEVELOPMENT_tiliqua.md).

## The tree

The tree is split down one line: what is the *synth*, and what is the *board*. The engine
knows nothing about pins, clock rates or how audio gets to a host, so the second board was an
addition rather than a rewrite.

- **`core/`** — board-independent hardware sources: `synth.x` (DSLX — the whole engine),
  `codegen.sh` (DSLX → IR → optimised IR → pipelined Verilog; **every board calls this one
  script**, differing only in `STAGES`), `fix_verilog.py` (post-codegen fixups), `gen_lut.py`
  (LUT + phase-increment generator), and `sim/tb*.v` ([iverilog](http://iverilog.icarus.com/) testbenches). `engine.v` is
  generated into `build/`.
- **`boards/`** — one package per target, plus a small registry (`get_board()`, selected with
  `$XLS32_BOARD`, default `basys3`). A board package owns its descriptor (sample rate, transport,
  flash command), its shell sources, and its build script — and nothing else:
  - **`boards/basys3/`** — `board.py` (descriptor: 32 kHz, UART transport), `rtl/` (`top.v`
    Verilog shell, `basys3.xdc`, `build_vivado.tcl`), `scripts/` (`build.sh` local
    [Docker](https://www.docker.com/); `remote_build.sh` + `vmbuild*.sh` native x86 GCE build; `verify.sh` flash +
    pitch check), `firmware/` (the committed prebuilt `top.bit`).
  - **`boards/tiliqua/`** — `board.py` (descriptor: 48 kHz, UAC2 + USB-MIDI transport),
    `gateware/` (`top.py` + `xls_core.py`, an [Amaranth](https://amaranth-lang.org/) shell that
    `Instance()`s the same generated `engine.v`, plus `usb_iface.py`, `midi_filter.py`,
    `midi_arb.py`, `fx.py`, `fx_model.py`, `viz.py` and their Verilator/Amaranth harnesses),
    `sim/` (iverilog reference for the pitch check), `build.sh`, `area.py` (per-block cell
    census), `check_pitch.py` / `check_midi.py` / `check_loop.py` (the per-milestone exit checks),
    `spike/` (the M21/M22 fit sweeps), `firmware/` (the committed prebuilt bitstream archive).
- **`scripts/`** — board-agnostic tools: `spectro.sh` (.wav → PNG), `make_mp4.sh` (.wav →
  spectrogram MP4), `demo_video.sh`, and `check_artefacts.py` — which hashes the sources behind
  each committed bitstream into `artefact_hashes.json` and tells you when one has drifted.
- **`host/`** — host tools: `synth.py` (MIDI + sample maths, board-agnostic), `transport/`
  (`base.py` the contract, `uart.py` the 2 Mbaud serial link for Basys 3, `usbaudio.py` the
  Tiliqua's UAC2 + USB-MIDI link — everything that talks to a board goes through here: the graded
  suite, the web UI, the presetgen hardware tools, and since 2026-08-10 `play.py` and
  `record_wav.py`, whose own capture loop was dropping bytes — it paused after flushing,
  which is enough to make the link discard a partial frame), `analyze.py`
  (envelope/pitch checks on a simulation's stdout; its `--serial` mode is stale, see
  [TODO](TODO.md)), `analyze_fft.py` ([DFT](https://en.wikipedia.org/wiki/Discrete_Fourier_transform) chord-peak check), `play.py` (host sends
  MIDI → FFT-verifies), `record_wav.py` (capture stream → .wav), `filter_demo.py`; and
  **`host/demos/`** — the per-milestone showcase scripts (`demo*.py`).
- **`webui/`** — the browser synth UI: a **static page** that talks to either board itself over
  Web MIDI / Web Serial, with no server behind it (`synthspec.py` is the CC map/preset source,
  baked to `static/spec.json` at build time;
  `static/` UI, a Serum/Vital-style **preset browser**, and a **DEMO player** — 4 authored
  4-part songs in `static/demos.json`, played live to the board: complete public-domain classical
  pieces arranged for the 4 parts (Bach's *Prelude in C* BWV 846 and *Goldberg* Aria,
  Saint-Saëns' *Le Cygne*, Vivaldi's *Winter* Largo — notes in `presetgen/demo_scores.py`).
  While a demo plays, its 4 part tones load
  into the multitimbral editor — tweak each part live and **💾 TONES** saves them straight back
  out as `demos.json` (File System Access API — drop it into `static/`), which is the
  **single source of truth** for the bank (re-running `build_demos.py` regenerates the notes but
  carries tone edits over by song name). The matched preset banks live here as
  `presets_*.json`. See the [Web UI](../DEVELOPMENT.md#web-ui--a-browser-synth-panel-done-hardware-verified) and
  [Preset banks](../DEVELOPMENT.md#preset-browser--ai-matched-preset-banks-inverse-synthesis) sections.
- **`presetgen/`** — offline **inverse-synthesis** preset generator: a NumPy/numba software
  model of the engine (`engine.py`), a multi-resolution spectrogram loss (`loss.py`), the
  CMA-ES search (`search.py`), target sources (`nsynth.py`, `freesound.py`), a sim↔board
  calibration probe (`calibrate.py`), and the orchestrator (`build_presets.py`). See
  [Preset banks](../DEVELOPMENT.md#preset-browser--ai-matched-preset-banks-inverse-synthesis).
- **`test/`** — the end-to-end hardware test suite: drives **either** board over USB, scores the
  captured audio (0–100), and builds a captioned report video. See
  [§3](../README.md#3-builders-guide) and `test/README.md`.
- **`docs/`** — diagrams & spectrogram PNGs (in `docs/assets/`), the open-TODO list
  ([`docs/TODO.md`](TODO.md)), the USB dropout report, and the session slides
  (`docs/slides/`, self-contained HTML, no build step). **`media/`** (captured
  .wav/.mp4/screenshots) and **`build/`** (bitstream build output) are gitignored.

## Publishing

Both the web UI and the slides are served from **GitHub Pages**, assembled by
[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) on every push to `main` that
touches `webui/static/` or `docs/slides/`. `webui/static/` becomes the site root and
`docs/slides/` becomes `/slides/`; nothing is rewritten, because a real directory resolves the
decks' relative `src="assets/…"` on its own. There is no separate publish step — `git push` is
the publish step.

The workflow **deploys, it does not build**. No XLS codegen, no yosys, no Vivado: a green run
says the page is up, not that the bitstreams under `boards/*/firmware/` still match their
sources. That second question is answered separately and on demand, by
[`scripts/check_artefacts.py`](../scripts/check_artefacts.py) — which since the 2026-08-10 Basys 3
rebuild has real source hashes for both boards, but still has to be run by hand. See
[`docs/TODO.md`](TODO.md) for that, and for why building on push was cancelled.

[`docs/slides/publish_gist.py`](slides/publish_gist.py) is the **legacy** path: it PATCHes
the [public gist](https://gist.github.com/kazunori279/36e7232e247738f36460c5d1a97191ab) the
decks used to be linked from, rewriting `src="assets/…"` to `raw.githubusercontent.com` URLs on
`main` because a gist has no directories. That URL has been shared, so it is kept working —
run the script (after pushing, since the rewritten URLs read from `main`) only if you care
about it.
