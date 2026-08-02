#!/bin/bash
# Runs ON the GCE build VM (native x86_64). Codegen (XLS) + F4PGA (docker, native).
# Env: STAGES, WCT (pipeline_stages / worst_case_throughput).
# Timing comes from the build's own route pass (common.mk patched to tee route_timing.log),
# so we route ONCE — no wasteful second route.
set -e
cd ~/build
X=$HOME/xls/xls-v0.0.0-10214-gcf49d0e31-linux-x64
STAGES=${STAGES:-48}; WCT=${WCT:-48}
# Shared with every other board — remote_build.sh ships core/codegen.sh here alongside synth.x.
XLS_DIR=$X STAGES=$STAGES WCT=$WCT SRC=synth.x OUT=engine.v bash ./codegen.sh
EX=$HOME/f4pga-examples; W=$EX/xc7/synth; mkdir -p $W
sudo rm -rf $W/build                     # root-owned from prior docker run
cp top.v engine.v basys3.xdc $W/
cat > $W/Makefile <<'MK'
current_dir := ${CURDIR}
TOP := top
SOURCES := ${current_dir}/top.v ${current_dir}/engine.v
ifeq ($(TARGET),basys3)
  XDC := ${current_dir}/basys3.xdc
endif
include ${current_dir}/../../common/common.mk
MK
echo "== F4PGA build (native) =="
SECONDS=0
sudo docker run --rm -v $EX:/wrk -w /wrk/xc7/synth \
  ghcr.io/hdl/conda/f4pga/xc7/a50t bash -lc 'make TARGET=basys3'
echo "== build wall: ${SECONDS}s =="
cp $W/build/basys3/top.bit ~/build/top.bit
echo "== timing (from build route pass) =="
grep -iE "critical path delay|Fmax|Final" $W/build/basys3/route_timing.log | tee ~/build/timing.txt
echo VMBUILD_DONE
