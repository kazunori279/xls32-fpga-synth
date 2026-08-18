#!/usr/bin/env bash
# core/synth.x (DSLX proc `engine`) -> pipelined Verilog (XLS) -> F4PGA -> build/top.bit.
# Uses the XLS *pipeline* generator (auto-inserts stage registers to meet 100 MHz).
# All container work runs under /tmp (Docker can't mount ~/Documents).
set -euo pipefail
cd "$(dirname "$0")/../../.."; PROJ="$PWD"   # project root (script lives in boards/basys3/scripts/)
BOARD="$PROJ/boards/basys3"

WORKROOT="${WORKDIR:-/tmp/xls-synth-work}"
XLS_TAG="v0.0.0-10214-gcf49d0e31"
XLS_DIR="$WORKROOT/xls-$XLS_TAG-linux-x64"
UBUNTU_IMG="xls-ubuntu:24.04"
F4PGA_IMG="ghcr.io/hdl/conda/f4pga/xc7/a50t"
EX="$WORKROOT/f4pga-examples"
mkdir -p "$WORKROOT"

bash "$PROJ/core/fetch_xls.sh" "$WORKROOT" "$XLS_TAG" "$UBUNTU_IMG"

# --- codegen the engine proc as a pipeline @ 100 MHz (core/, shared with every board) ---
cp "$PROJ/core/synth.x" "$PROJ/core/codegen.sh" "$WORKROOT/"
mkdir -p "$PROJ/build"
# FIXUP=0: ubuntu-base has no python, so fix_verilog.py runs on the host afterwards.
docker run --rm --platform linux/amd64 -v "$WORKROOT":/w -w /w \
  -e XLS_DIR="/w/xls-$XLS_TAG-linux-x64" -e SRC=/w/synth.x -e OUT=/w/engine.v \
  -e STAGES="${STAGES:-48}" -e WCT="${WCT:-48}" -e FIXUP=0 \
  "$UBUNTU_IMG" bash /w/codegen.sh
cp "$WORKROOT/engine.v" "$PROJ/build/engine.v"
uv run "$PROJ/core/fix_verilog.py" "$PROJ/build/engine.v"   # unroll dynamic-index genvar loops for yosys

# --- F4PGA build ---
if [ ! -f "$EX/common/common.mk" ]; then
  rm -rf "$EX"; git clone --depth 1 https://github.com/chipsalliance/f4pga-examples "$EX"
fi
WORK="$EX/xc7/synth"; mkdir -p "$WORK"
cp "$BOARD/rtl/top.v" "$PROJ/build/engine.v" "$BOARD/rtl/basys3.xdc" "$WORK/"
cat > "$WORK/Makefile" <<'MK'
current_dir := ${CURDIR}
TOP := top
SOURCES := ${current_dir}/top.v ${current_dir}/engine.v
ifeq ($(TARGET),basys3)
  XDC := ${current_dir}/basys3.xdc
endif
include ${current_dir}/../../common/common.mk
MK
echo "==> F4PGA build (slow under amd64 emulation)"
docker run --rm --platform linux/amd64 -v "$EX":/wrk -w /wrk/xc7/synth \
  "$F4PGA_IMG" bash -lc 'make TARGET=basys3'

cp "$WORK/build/basys3/top.bit" "$PROJ/build/top.bit"
echo "==> done: build/top.bit"; ls -l "$PROJ/build/top.bit"
