#!/usr/bin/env bash
# M21 spike: one (STAGES, FREQ) data point for the engine on an LFE5U-25F.
#
#   STAGES=48 FREQ=60 bash boards/tiliqua/spike/ecp5_build.sh
#
# Writes build/spike/s<STAGES>f<FREQ>.* and prints one JSON row on stdout.
#
# Two toolchains, two different sandboxes, and they do not agree on what a path is:
#   * XLS ships linux-x64 only, so codegen runs in the same amd64 container build.sh uses.
#     Docker can mount /tmp, so the (slow) codegen output is cached there across sweeps.
#   * yosys and nextpnr come from yowasp, which is WebAssembly under WASI and can only see
#     files beneath its working directory -- /tmp is invisible to it, and it reports that as
#     "not found or is a directory". So the cached engine.v is staged into the repo tree and
#     every path handed to those two is relative.
set -euo pipefail
cd "$(dirname "$0")/../../.."          # repo root; every path below is relative to it

STAGES="${STAGES:-48}"; WCT="${WCT:-$STAGES}"; FREQ="${FREQ:-60}"
# SRCX swaps the DSLX in, for the reduced-voice variants voices_variant.py emits. VARIANT only
# names the artefacts, so two builds of the same STAGES from different sources do not collide.
SRCX="${SRCX:-core/synth.x}"; VARIANT="${VARIANT:-}"
CACHE="${CACHE:-/tmp/xls-synth-work}"  # docker-side, outside the repo
OUT="${OUT:-build/spike}"              # wasm-side, must be under the repo
SPIKE="boards/tiliqua/spike"
XLS_TAG="${XLS_TAG:-v0.0.0-10214-gcf49d0e31}"
IMG="${IMG:-xls-ubuntu:24.04}"
# LFE5U-25F-6BG256C, the SoldierCrab R3's part.
DEV_SIZE="${DEV_SIZE:---25k}"; DEV_PKG="${DEV_PKG:-CABGA256}"; DEV_SPEED="${DEV_SPEED:-6}"

mkdir -p "$OUT"
ENG_NAME="engine_${VARIANT:+${VARIANT}_}s${STAGES}w${WCT}.v"
ENG_CACHE="$CACHE/$ENG_NAME"
ENG="$OUT/$ENG_NAME"                   # the copy yosys is actually allowed to read
TAG="${VARIANT:+${VARIANT}_}s${STAGES}f${FREQ}"

# --- 1. synth.x -> engine.v (cached; codegen does not depend on FREQ) ---
if [ ! -s "$ENG_CACHE" ]; then
  cp "$SRCX" "$CACHE/synth_in.x"; cp core/codegen.sh "$CACHE/"
  docker run --rm --platform linux/amd64 -v "$CACHE":/w -w /w \
    -e XLS_DIR="/w/xls-$XLS_TAG-linux-x64" -e SRC=/w/synth_in.x -e OUT="/w/$ENG_NAME" \
    -e STAGES="$STAGES" -e WCT="$WCT" -e FIXUP=0 "$IMG" bash /w/codegen.sh >/dev/null
  # fix_verilog.py unrolls the dynamic-index generate loops yosys cannot handle, and adds `ce`.
  uv run core/fix_verilog.py "$ENG_CACHE" >/dev/null
fi
[ -s "$ENG_CACHE" ] || { echo "codegen produced no $ENG_CACHE" >&2; exit 1; }
cp "$ENG_CACHE" "$ENG"

# --- 2. yosys -> ECP5 netlist (DSP and BRAM inference are on by default) ---
uvx --from yowasp-yosys yowasp-yosys -q -l "$OUT/$TAG.yosys.log" \
  -p "read_verilog $ENG $SPIKE/stub_top.v; synth_ecp5 -top stub_top -json $OUT/$TAG.netlist.json"
[ -s "$OUT/$TAG.netlist.json" ] || { echo "yosys produced no netlist; see $OUT/$TAG.yosys.log" >&2; exit 1; }

# --- 3. nextpnr-ecp5 -> place, route, timing ---
# --lpf-allow-unconstrained: the spike has no pinout, and inventing one would change neither the
# engine's area nor its internal critical path.
set +e
uvx --from yowasp-nextpnr-ecp5 yowasp-nextpnr-ecp5 \
  "$DEV_SIZE" --package "$DEV_PKG" --speed "$DEV_SPEED" \
  --json "$OUT/$TAG.netlist.json" --freq "$FREQ" --lpf-allow-unconstrained \
  --report "$OUT/$TAG.report.json" > "$OUT/$TAG.log" 2>&1
PNR_RC=$?
set -e

# --- 4. scrape one row ---
uv run "$SPIKE/scrape.py" --stages "$STAGES" --wct "$WCT" --freq "$FREQ" \
  --report "$OUT/$TAG.report.json" --log "$OUT/$TAG.log" --rc "$PNR_RC" \
  --yosys-log "$OUT/$TAG.yosys.log"
