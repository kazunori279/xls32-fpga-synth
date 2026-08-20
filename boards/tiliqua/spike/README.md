# M21 · ECP5 feasibility spike

Answers one question: does the XLS32 engine fit an `LFE5U-25F`, and at what settings. **It does** —
32 voices × 4 parts at `STAGES=12` costs 66% of the LUTs, 38% of the flip-flops, 0 BRAM and 86% of
the multipliers. Results and the reasoning are in [the port history](../../../DEVELOPMENT_tiliqua.md)
(M21) and [DEVELOPMENT.md](../../../DEVELOPMENT.md) (Milestone 21); raw rows are in `results/`.

Nothing here is part of a bitstream **except one file**: `voices_variant.py` graduated from
scaffolding to a build dependency at M36, and is what `boards/tiliqua/build.sh` runs to produce the
shipped 24-voice engine. It is hashed as a source of `tiliqua-24` in `scripts/artefact_hashes.json`
and is not free to change. Everything else here is measurement scaffolding, kept because the numbers
have to be reproducible when `synth.x` changes.

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
| `voices_variant.py` | Rewrites a copy of `core/synth.x` to fewer voices, asserting every substitution count. `--voices 32` must reproduce the original byte for byte. **Also a build dependency** — see the note above, and the warning below |
| `results/` | The rows the milestone was decided on |

## A missed substitution is silent, and it lies in your favour

`voices_variant.py` asserts a count on every rule, so a rule that stops matching is loud. A site
with **no rule at all** is not, and that is what happened until M36: `rotate_in`'s tail writeback
kept its literal `u32:31` against a `Voice[24]`.

`update` with an out-of-range index in DSLX is a **no-op**. The new voice never lands in the ring,
every voice slot becomes unreachable state, and yosys prunes the lot. The build then reports a
*smaller* area and a *better* Fmax than the real variant, and the bitstream plays **nothing at
all** — 28 voices read 50.53 MHz at 91 % that way, against 48.72 at 96.2 % once fixed. Four
milestones of "reduced voice counts are measured and dead" rested on it (#35, #36).

**The byte-for-byte check cannot catch this.** At N=32 every substitution is a no-op by
construction, so the identity test passes whether or not a site has a rule at all. It proves the
rules are harmless; it says nothing about whether they are complete. Completeness has to be checked
against a *reduced* output, by reading it for surviving voice-count literals:

```bash
uv run boards/tiliqua/spike/voices_variant.py --voices 24 --out /tmp/v24.x
grep -n 'u32:3[12]\|\[32\]\|v\[31\]' /tmp/v24.x      # should find nothing about voices
```

## The one trap worth knowing

XLS runs under Docker, which can see `/tmp`. yosys and nextpnr come from yowasp, which is
WebAssembly under **WASI and can only open files beneath its working directory** — `/tmp` is
invisible to it, and it says so as `File '…' not found or is a directory`, which reads like a
missing file and is actually a permissions model. `ecp5_build.sh` caches codegen output in `/tmp`
and stages a copy into `build/spike/`, handing the wasm tools relative paths only.
