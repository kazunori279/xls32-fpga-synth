# M21 · ECP5 feasibility spike

Answers one question: does the XLS32 engine fit an `LFE5U-25F`, and at what settings. **It does** —
32 voices × 4 parts at `STAGES=12` costs 66% of the LUTs, 38% of the flip-flops, 0 BRAM and 86% of
the multipliers. Results and the reasoning are in [the port history](../../../DEVELOPMENT_tiliqua.md)
(M21) and [DEVELOPMENT.md](../../../DEVELOPMENT.md) (Milestone 21); raw rows are in `results/`.

Nothing here is part of a bitstream. It is measurement scaffolding, kept because the numbers have to
be reproducible when `synth.x` changes.

## Reproducing

```bash
STAGES=12 FREQ=60 bash boards/tiliqua/spike/ecp5_build.sh   # one row of the table
bash boards/tiliqua/spike/sweep.sh 12 24 48                 # several, serially

# cycles per sample -- the number that decides the operating point
iverilog -g2005 -o /tmp/rate build/spike/engine_s12w12.v boards/tiliqua/spike/tb_rate.v
vvp /tmp/rate

# reduced-voice variants, against a throwaway copy of synth.x
uv run boards/tiliqua/spike/voices_variant.py --voices 16 --out build/spike/synth_v16.x
SRCX=build/spike/synth_v16.x VARIANT=v16 STAGES=12 bash boards/tiliqua/spike/ecp5_build.sh
```

Needs Docker (XLS is linux-x64 only), `uv`/`uvx` (fetches yowasp yosys + nextpnr-ecp5 on demand),
and `iverilog` for the rate measurement. No oss-cad-suite install required.

## The files

| | |
|---|---|
| `stub_top.v` | Minimal top whose only job is to not lie — every engine input comes from a real pin so nothing constant-folds, every output reaches a real pin so nothing is dead logic |
| `ecp5_build.sh` | One `(STAGES, FREQ, SRCX)` → one JSON row. XLS codegen in an amd64 container, yosys + nextpnr via yowasp |
| `scrape.py` | Pulls utilisation, Fmax and a verdict out of the nextpnr report and log. Still prints a row when the run fails — "did not fit" is a result |
| `sweep.sh` | Runs `ecp5_build.sh` over a list of `STAGES`, appending to `build/spike/sweep_f<FREQ>.jsonl` |
| `tb_rate.v` | Counts clocks between `audio_out` handshakes. This is what turned the resource table from suggestive into decisive |
| `voices_variant.py` | Rewrites a copy of `core/synth.x` to fewer voices, asserting every substitution count. `--voices 32` must reproduce the original byte for byte |
| `results/` | The rows the milestone was decided on |

## The one trap worth knowing

XLS runs under Docker, which can see `/tmp`. yosys and nextpnr come from yowasp, which is
WebAssembly under **WASI and can only open files beneath its working directory** — `/tmp` is
invisible to it, and it says so as `File '…' not found or is a directory`, which reads like a
missing file and is actually a permissions model. `ecp5_build.sh` caches codegen output in `/tmp`
and stages a copy into `build/spike/`, handing the wasm tools relative paths only.
