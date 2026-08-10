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
# `--seed 3` is not a preference either. At M34's 23,729 TRELLIS_COMB (97.7%) the default seed
# bottoms out at 135 overused nets and then the ripup cascade runs away; seed 2 bottoms at 117 and
# does the same. Seed 3 routes. The placer knobs were measured and are worse -- `--router2-alt-
# weights` plateaus at 765, `--no-tmdriv` at 2,779 -- so this is the seed lottery, won, and written
# down. If the netlist changes size the lottery has to be drawn again -- one seed at a time, the
# wasm nextpnr cannot take two. See DEVELOPMENT_tiliqua.md M34 "The area squeeze".
export AMARANTH_nextpnr_opts="${AMARANTH_nextpnr_opts:---timing-allow-fail --router router2 --seed 3}"

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
