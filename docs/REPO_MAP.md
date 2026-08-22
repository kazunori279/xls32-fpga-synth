# Repository map

A directory-by-directory guide to the source tree — what lives where, and why the split falls
where it does. This is the companion to the [Builder's guide](../README.md#3-builders-guide);
**you do not need any of it to flash a board and play the synth**, which is
[§2 of the README](../README.md#2-user-guide).

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
    `midi_arb.py`, `fx.py`, `fx_model.py`, `viz.py`, `voices.py` (`$VOICES` → the voice count and
    the tile grid, in one place), `build_id.py` (the build stamp the module
    reports over USB, in its `iManufacturer` string) and their Verilator/Amaranth harnesses),
    `sim/` (iverilog reference for the pitch check), `build.sh`, `area.py` (per-block cell
    census), `check_pitch.py` / `check_midi.py` / `check_loop.py` / `check_panic.py` (the per-milestone
    exit checks), `check_panic_hw.py` (the same channel mode messages on the module, over
    USB-MIDI, judged on the USB audio capture), `check_headroom_hw.py` (issue #2 on the module:
    plays each pulse patch loud and quiet and reads how far its peak asymmetry moved, which is the
    only signature of one-sided clipping that survives the tee's DC blocker and the FIR resampler)
    and `check_descriptors.py` (the three USB strings,
    read back out of the descriptor collection without a board — that the stamp matches the parser
    in `transport.js`, that `iProduct` and `iSerialNumber` have not moved, and that the stamp is
    still the fixed width the router seed was drawn against),
    `spike/` (the M21/M22 fit sweeps), `firmware/` (the committed prebuilt bitstream archive).
- **`scripts/`** — board-agnostic tools: `spectro.sh` (.wav → PNG), `make_mp4.sh` (.wav →
  spectrogram MP4), `demo_video.sh`, and `check_artefacts.py` — which hashes the sources behind
  each committed bitstream into `artefact_hashes.json` and tells you when one has drifted. It
  hashes them with the comments stripped, so editing prose does not report a good bitstream as
  stale; `--self-test` deletes every code line in the tree one at a time to prove the stripping
  is not also blind to real edits. `build_firmware_json.py` writes `webui/static/firmware.json`
  from the same artefacts — when each was built, read out of the Vivado header in `top.bit` and
  the tar entry of the Tiliqua archive's inner bitstream, never out of a file mtime, which a fresh
  clone rewrites. That is what SETTINGS ▸ Firmware shows next to what the boards themselves say.
- **`host/`** — host tools: `synth.py` (MIDI + sample maths, board-agnostic), `transport/`
  (`base.py` the contract, `uart.py` the 2 Mbaud serial link for Basys 3, `usbaudio.py` the
  Tiliqua's UAC2 + USB-MIDI link — everything that talks to a board goes through here: the graded
  suite, the web UI, the presetgen hardware tools, and since 2026-08-10 `play.py` and
  `record_wav.py`, whose own capture loop was dropping bytes — it paused after flushing,
  which is enough to make the link discard a partial frame),
  `analyze_fft.py` ([DFT](https://en.wikipedia.org/wiki/Discrete_Fourier_transform) chord-peak
  check — reads a simulation on stdin or a board with `--serial`, and since 2026-08-10 it is what
  README §3's iverilog run pipes into; the `analyze.py` that used to sit here graded every
  bitstream and every simulation the same and was deleted), `play.py` (host sends
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
  into the multitimbral editor — tweak each part live and the header's **💾 SAVE ▸ TONES** writes
  them straight back out as `demos.json`, which is the
  **single source of truth** for the bank (re-running `build_demos.py` regenerates the notes but
  carries tone edits over by song name). The same button's **▸ PATCH** entry puts the patch on the
  panel into a USER slot — asking which with the preset browser itself, opened on its USER bank as a
  slot picker — and writes the whole bank out as `patches.json` on the way past, which is the only
  way that file is written (a copy — `localStorage` stays the live bank, and nothing reads the file
  back at boot). **📂 LOAD** reads either file back — replacing the bank, since both files are
  written whole. Everything goes through the File System Access API and remembers where you put it:
  the handles are structured-cloned into IndexedDB (`synth.files`) because `localStorage` cannot
  hold one, with the file name mirrored into `localStorage` for the tooltip. LOAD and SAVE share one
  handle per file, so a round trip costs no dialog; shift-click either to re-pick. A handle has no
  path — `.name` is all of it — so the Files rows name the file and a **📁 Show** button opens the
  system dialog in its folder (`startIn:`), which is the only "where is it" the API allows. A
  **Clear** button beside it is the way back to a first visitor's state — an empty USER bank, the
  shipped demo songs, no remembered target — and never touches the file itself. **⚙ SETTINGS**
  (the header button where OUT used to be) holds the audio-output picker, the two file targets, the
  MIDI/audio status readouts and the key map — the last two used to be a footer under the keyboard.
  The matched preset banks live here as
  `presets_*.json`. See the [Web UI](../DEVELOPMENT.md#web-ui--a-browser-synth-panel-done-hardware-verified) and
  [Preset banks](../DEVELOPMENT.md#preset-browser--ai-matched-preset-banks-inverse-synthesis) sections.
  The page drives **up to four Tiliquas at once** — one USB cable each, the same bitstream on all
  of them, 16 parts and 128 voices — by treating a part number as a (board, channel) pair;
  `static/transport.js` finds every board and `static/app.js` does the routing.
  Three checks here have a pass/fail. `check_aligner.py` + `aligner_check.html` run the
  Python and JS frame aligners over the same capture of real board bytes — `testdata/`, and it has
  a genuine mid-stream phase shift in it — and compare the SHA-256 of the aligned output, so
  "the JS is a port of the Python" is checkable rather than asserted. `route_check.html` drives the
  real panel in an iframe and hashes the MIDI it emits, against `testdata/route_trace.json`
  recorded *before* the multi-board work: a single board must not be able to tell that four boards
  became possible. `audio_check.html` is the other half of that question and needs the boards: it
  opens every one of them through the same `attachAudio()` the panel uses, holds a sine chord on
  all four parts, and counts dropouts and splices per board from inside an AudioWorklet — the four
  free-running UAC2 clocks in one AudioContext are the part of the multi-board rig that cannot be
  reasoned about. It self-tests its own detector offline first, so a green run means the counters
  were alive. `usb_check.html` is the fourth, and it needs one board: it reads the module's build
  stamp twice, once through Web MIDI and once through WebUSB, and shows them side by side. They
  disagree, and that is the point — on macOS `MIDIPort.manufacturer` is a CoreMIDI cache entry
  keyed on fields the design pins across builds, so it keeps reporting the firmware you just
  replaced. `navigator.usb` reads the descriptor from IOKit instead, which is why SETTINGS ▸
  Firmware has an **Ask the board** button. `cd webui && python3 -m http.server 8123`, then open
  any of them (the panel is same-origin at `/static/`, so a WebUSB grant made in the check page is
  the one the panel uses).
- **`presetgen/`** — offline **inverse-synthesis** preset generator: a NumPy/numba software
  model of the engine (`engine.py`), a multi-resolution spectrogram loss (`loss.py`), the
  CMA-ES search (`search.py`), target sources (`nsynth.py`, `freesound.py`), a sim↔board
  calibration probe (`calibrate.py`), and the orchestrator (`build_presets.py`). See
  [Preset banks](../DEVELOPMENT.md#preset-browser--ai-matched-preset-banks-inverse-synthesis).
  `demo_balance.py` reuses the same engine on the DEMO songs: it renders every note of a song into
  a per-part track, scores each part's A-weighted loudness, and can write back the per-part CC7
  that levels them — the mix the board does is one hard-clipped accumulator, so this is the
  difference between four parts and one.
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
