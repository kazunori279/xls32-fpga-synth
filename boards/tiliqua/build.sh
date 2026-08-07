#!/usr/bin/env bash
# M24: XLS engine -> Tiliqua bitstream (or Verilator simulation).
#
#   bash boards/tiliqua/build.sh              # build build/tiliqua/build/xls32-<hw>/top.bit
#   SIM=1 bash boards/tiliqua/build.sh        # verilate + run, leaves build/tiliqua/out0.txt
#   SKIP_BUILD=1 bash boards/tiliqua/build.sh # elaborate only (fast wiring check)
#
# M28: XLS32_VARIANT=cv builds the Eurorack bitstream instead -- CV/gate in, effects bypassed.
# The two do not fit on one die (see the block comment in gateware/top.py) and go in separate
# bootloader slots. They get separate NAMEs so they also get separate build directories.
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
VARIANT="${XLS32_VARIANT:-fx}"
export XLS32_VARIANT="$VARIANT"
case "$VARIANT" in
  fx) NAME="${NAME:-XLS32}";;
  cv) NAME="${NAME:-XLS32CV}";;
  *)  echo "XLS32_VARIANT must be fx or cv, not '$VARIANT'" >&2; exit 1;;
esac
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
