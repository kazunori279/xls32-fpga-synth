#!/bin/bash
# Runs ON the GCE build VM (native x86_64). Codegen (XLS) + F4PGA (docker, native).
# Env: STAGES, WCT (pipeline_stages / worst_case_throughput).
# Timing comes from the build's own route pass (common.mk patched to tee route_timing.log),
# so we route ONCE — no wasteful second route.
set -e
cd ~/build
X=$HOME/xls/xls-v0.0.0-10214-gcf49d0e31-linux-x64
STAGES=${STAGES:-48}; WCT=${WCT:-48}
echo "== codegen (stages=$STAGES wct=$WCT) =="
$X/ir_converter_main --top=engine synth.x > engine.ir
$X/opt_main engine.ir > engine.opt.ir
$X/codegen_main --generator=pipeline --pipeline_stages=$STAGES --worst_case_throughput=$WCT \
  --delay_model=unit --use_system_verilog=false --reset=rst --reset_active_low=false \
  --reset_asynchronous=false --top=engine --module_name=xls_engine \
  --output_verilog_path=engine.v engine.opt.ir
python3 fix_verilog.py engine.v
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
