#!/usr/bin/env bash
# M24: XLS engine -> Tiliqua bitstream (or Verilator simulation).
#
#   bash boards/tiliqua/build.sh              # build build/tiliqua/build/xls32-<hw>/top.bit
#   SIM=1 bash boards/tiliqua/build.sh        # verilate + run, leaves build/tiliqua/out0.txt
#   SKIP_BUILD=1 bash boards/tiliqua/build.sh # elaborate only (fast wiring check)
#
# One bitstream. M28 added an XLS32_VARIANT=cv build because CV in and the effects did not fit on
# one die; M29 freed the space and M31 removed the variant. Override NAME for a one-off.
#
# In SIM mode, XLS_SIM_MS, XLS_SIM_OUT and XLS_SIM_MIDI reach the harness; see sim_xls_core.cpp.
#
# Three toolchains, three different ideas of what a path is:
#   * XLS ships linux-x64 only, so codegen runs in the amd64 container core/build uses. The
#     output is cached under $CACHE, keyed on a hash of synth.x so a DSLX edit invalidates it.
#   * yosys/nextpnr come from yowasp -- WebAssembly under WASI, which can only see files
#     beneath its working directory. Amaranth runs them with cwd=build_dir, so that is fine
#     as long as everything they need was written there.
#   * The Tiliqua SDK's own sim/build helpers use paths relative to *their* cwd -- notably
#     `./src/tb_cpp/*.h`. So both actions run from $WORK, which carries a `src` symlink into
#     the SDK. That also keeps every artefact inside this repo's gitignored build/ tree
#     rather than dirtying the SDK checkout, which we treat as read-only.
set -euo pipefail
cd "$(dirname "$0")/../.."                     # repo root
REPO="$PWD"

STAGES="${STAGES:-12}"; WCT="${WCT:-$STAGES}"
SRCX="${SRCX:-core/synth.x}"
CACHE="${CACHE:-/tmp/xls-synth-work}"          # docker-side, outside the repo
XLS_TAG="${XLS_TAG:-v0.0.0-10214-gcf49d0e31}"
IMG="${IMG:-xls-ubuntu:24.04}"
NAME="${NAME:-XLS32}"
TILIQUA_SDK="${TILIQUA_SDK:-$HOME/Documents/GitHub/tiliqua/gateware}"
WORK="$REPO/build/tiliqua"

[ -d "$TILIQUA_SDK" ] || { echo "TILIQUA_SDK not found: $TILIQUA_SDK" >&2; exit 1; }
PY="$TILIQUA_SDK/.venv/bin/python"
[ -x "$PY" ] || { echo "no SDK venv at $PY (run 'pdm install' in $TILIQUA_SDK)" >&2; exit 1; }

# --- 1. synth.x -> engine.v (cached on the DSLX hash, so an edit rebuilds) ---
mkdir -p "$CACHE" "$WORK"
HASH="$(shasum -a 256 "$SRCX" core/fix_verilog.py | shasum -a 256 | cut -c1-12)"
ENG_NAME="engine_tlq_s${STAGES}w${WCT}_${HASH}.v"
ENG_CACHE="$CACHE/$ENG_NAME"
if [ ! -s "$ENG_CACHE" ]; then
  echo "==> codegen $SRCX -> $ENG_NAME (stages=$STAGES wct=$WCT)"
  bash core/fetch_xls.sh "$CACHE" "$XLS_TAG" "$IMG"      # /tmp gets cleaned; issue #33
  cp "$SRCX" "$CACHE/synth_in.x"; cp core/codegen.sh "$CACHE/"
  docker run --rm --platform linux/amd64 -v "$CACHE":/w -w /w \
    -e XLS_DIR="/w/xls-$XLS_TAG-linux-x64" -e SRC=/w/synth_in.x -e OUT="/w/$ENG_NAME" \
    -e STAGES="$STAGES" -e WCT="$WCT" -e FIXUP=0 "$IMG" bash /w/codegen.sh >/dev/null
  # fix_verilog.py unrolls the dynamic-index generate loops yosys cannot handle, and adds `ce`.
  uv run core/fix_verilog.py "$ENG_CACHE" >/dev/null
fi
[ -s "$ENG_CACHE" ] || { echo "codegen produced no $ENG_CACHE" >&2; exit 1; }
cp "$ENG_CACHE" "$WORK/engine.v"
echo "==> engine: $WORK/engine.v ($(wc -l < "$WORK/engine.v") lines)"

# --- 2. Amaranth elaboration + yosys/nextpnr (or Verilator) ---
ln -sfn "$TILIQUA_SDK/src" "$WORK/src"
# The yowasp launchers are console scripts in the SDK venv. `pdm run` would put them on PATH;
# we invoke the venv interpreter directly, so amaranth's require_tool() has to be told where
# to look or it reports the tools as missing despite the variables below being set.
export PATH="$TILIQUA_SDK/.venv/bin:$PATH"
export AMARANTH_USE_YOSYS=builtin
export YOSYS=yowasp-yosys
export NEXTPNR_ECP5=yowasp-nextpnr-ecp5
export ECPPACK=yowasp-ecppack
export XLS_ENGINE_V="$WORK/engine.v"

# --- the stamp the board reports over USB (issue #27) ---
# Two variables, both consumed by gateware/build_id.py, which turns them into iManufacturer. The
# rule for computing them lives in that file too, so `eval` here rather than a second copy in
# shell. Pre-set either one to pin it -- a bit-reproducible build wants a fixed XLS32_BUILD_UTC.
# Keep the stamp fixed-width if you change its format: the string is a ROM, and its *length* moves
# the netlist and re-draws the seed lottery below. Its characters do not. See build_id.py.
# Leaving both unset (which is what a bare `python top.py build` does) produces a bitstream with no
# stamp at all, indistinguishable from a pre-#27 one; the panel reports that honestly.
eval "$("$PY" "$REPO/boards/tiliqua/gateware/build_id.py")"
echo "==> build stamp: $XLS32_BUILD_UTC-$XLS32_BUILD_COMMIT"

# nextpnr's default router does not converge on this design. At 97% TRELLIS_COMB, router1 spent
# two hours ripping up more arcs than it laid -- 62,719 of 105,900 still unrouted, the count
# rising, and each 1000 iterations taking 240 s against 0.4 s at the start. router2 finishes the
# same netlist in 81 s with overused=0. It is not a tuning preference; it is the difference
# between a six-minute build and one that never ends.
#
# This is an *override*, not an addition: whatever the SDK passes as `nextpnr_opts` is replaced
# wholesale, so `--timing-allow-fail` has to be repeated here. Dropping it turns the known `clk`
# shortfall (39.92 MHz against a 60 MHz constraint, unmet since M25 and so far harmless -- the
# engine runs in `audio_clk`) from a warning into an error that fails the build after it has routed.
#
# `--seed 4` is not a preference either. At 97% TRELLIS_COMB the router either converges or runs
# away, and which it does is decided by the seed. At M34 (23,729 cells) that seed was 3: the
# default bottomed out at 135 overused nets and then the ripup cascade ran away, seed 2 bottomed
# at 117 and did the same, seed 3 routed. The placer knobs were measured and are worse --
# `--router2-alt-weights` plateaus at 765, `--no-tmdriv` at 2,779.
#
# #27's build stamp re-drew the lottery, exactly as the M34 note warned it would. The stamp adds
# no logic of its own, but it is a string descriptor, and a wider ROM is a different netlist:
# 23,792 cells here against M34's 23,729. On that netlist seed 3 climbs past 10,000 overused nets
# and never comes back, and so do 2, 5 and 6. Seed 4 reaches overused=0 at iteration 158, and
# leaves 40.95 MHz on the `clk`/USB domain that has failed its 60 MHz constraint since M25 (issue
# #3) -- more margin than the 39.92 of the M34 bitstream it replaces. Roughly one seed in four
# won here; budget for that.
#
# Redrawing costs an afternoon unless you skip the front of the build. nextpnr reads `top.json`,
# which yosys already wrote, so run it by hand out of `build/tiliqua/build/xls32-r5/` with
# `--log x$S.tim --textcfg x$S.config` per seed and the inputs shared read-only. Four at once
# finish in about fifteen minutes on an M-series laptop; one process still cannot take two seeds.
# A losing seed never terminates on its own -- watch `overused=` and kill the ones that climb.
# See DEVELOPMENT_tiliqua.md M34 "The area squeeze".
export AMARANTH_nextpnr_opts="${AMARANTH_nextpnr_opts:---timing-allow-fail --router router2 --seed 4}"

cd "$WORK"
if [ -n "${SIM:-}" ]; then
  # 250 ms is past the ADSR attack and decay with ~8k sustain samples left over, which is what
  # check_pitch.py needs to resolve a peak. It costs about ten seconds.
  export XLS_SIM_MS="${XLS_SIM_MS:-250}"
  export XLS_SIM_OUT="${XLS_SIM_OUT:-$WORK/out0.txt}"
  exec "$PY" "$REPO/boards/tiliqua/gateware/top.py" sim --name "$NAME" "$@"
fi
exec "$PY" "$REPO/boards/tiliqua/gateware/top.py" build --name "$NAME" \
     ${SKIP_BUILD:+--skip-build} "$@"
