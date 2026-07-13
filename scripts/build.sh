#!/usr/bin/env bash
# synth.x (DSLX proc `engine`) -> pipelined Verilog (XLS) -> F4PGA -> build/top.bit.
# Uses the XLS *pipeline* generator (auto-inserts stage registers to meet 100 MHz).
# All container work runs under /tmp (Docker can't mount ~/Documents).
set -euo pipefail
cd "$(dirname "$0")/.."; PROJ="$PWD"   # project root (script lives in scripts/)

WORKROOT="${WORKDIR:-/tmp/xls-synth-work}"
XLS_TAG="v0.0.0-10214-gcf49d0e31"
XLS_DIR="$WORKROOT/xls-$XLS_TAG-linux-x64"
UBUNTU_IMG="xls-ubuntu:24.04"
F4PGA_IMG="ghcr.io/hdl/conda/f4pga/xc7/a50t"
EX="$WORKROOT/f4pga-examples"
mkdir -p "$WORKROOT"

if [ ! -x "$XLS_DIR/codegen_main" ]; then
  echo "==> downloading XLS $XLS_TAG"
  curl -sSL -o "$WORKROOT/xls.tar.gz" \
    "https://github.com/google/xls/releases/download/$XLS_TAG/xls-$XLS_TAG-linux-x64.tar.gz"
  tar xzf "$WORKROOT/xls.tar.gz" -C "$WORKROOT"
fi
if ! docker image inspect "$UBUNTU_IMG" >/dev/null 2>&1; then
  echo "==> importing ubuntu-base rootfs"
  curl -sSL -o "$WORKROOT/ubuntu-base.tar.gz" \
    "https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-amd64.tar.gz"
  docker import --platform linux/amd64 "$WORKROOT/ubuntu-base.tar.gz" "$UBUNTU_IMG"
fi

# --- codegen the engine proc as a pipeline @ 100 MHz ---
cp "$PROJ/rtl/synth.x" "$WORKROOT/"
echo "==> XLS: codegen engine (pipeline, 100 MHz)"
docker run --rm --platform linux/amd64 -v "$WORKROOT":/w -w /w "$UBUNTU_IMG" bash -c "
  set -e
  X=/w/xls-$XLS_TAG-linux-x64
  \$X/ir_converter_main --top=engine /w/synth.x > /w/engine.ir
  \$X/opt_main /w/engine.ir > /w/engine.opt.ir
  \$X/codegen_main --generator=pipeline --pipeline_stages=48 --worst_case_throughput=48 \
     --delay_model=unit --use_system_verilog=false --reset=rst --reset_active_low=false \
     --reset_asynchronous=false --top=engine --module_name=xls_engine \
     --output_verilog_path=/w/engine.v /w/engine.opt.ir
"
cp "$WORKROOT/engine.v" "$PROJ/rtl/engine.v"
uv run "$PROJ/rtl/fix_verilog.py" "$PROJ/rtl/engine.v"   # unroll dynamic-index genvar loops for yosys

# --- F4PGA build ---
if [ ! -f "$EX/common/common.mk" ]; then
  rm -rf "$EX"; git clone --depth 1 https://github.com/chipsalliance/f4pga-examples "$EX"
fi
WORK="$EX/xc7/synth"; mkdir -p "$WORK"
cp "$PROJ/rtl/top.v" "$PROJ/rtl/engine.v" "$PROJ/rtl/basys3.xdc" "$WORK/"
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

mkdir -p "$PROJ/build"; cp "$WORK/build/basys3/top.bit" "$PROJ/build/top.bit"
echo "==> done: build/top.bit"; ls -l "$PROJ/build/top.bit"
