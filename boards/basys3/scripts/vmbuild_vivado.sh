#!/bin/bash
# Runs ON the GCE build VM. Codegen (XLS) + Vivado (DSP48 + BRAM + real timing) backend.
# Fallback used because nextpnr-xilinx can't route the DSP48 CARRYCASCIN constant.
# Requires Vivado installed under /opt/Xilinx/Vivado/<ver>/. Env: STAGES, WCT.
set -e
cd ~/build
X=$HOME/xls/xls-v0.0.0-10214-gcf49d0e31-linux-x64
STAGES=${STAGES:-48}; WCT=${WCT:-48}
VIV=$(ls -d /opt/Xilinx/Vivado/*/settings64.sh 2>/dev/null | sort | tail -1)
if [ -z "$VIV" ]; then echo "ERROR: Vivado not found under /opt/Xilinx/Vivado"; exit 1; fi
source "$VIV"
# Shared with every other board — remote_build.sh ships core/codegen.sh here alongside synth.x.
XLS_DIR=$X STAGES=$STAGES WCT=$WCT SRC=synth.x OUT=engine.v bash ./codegen.sh

W=$HOME/vivbuild; rm -rf $W; mkdir -p $W
cp top.v engine.v basys3.xdc build_vivado.tcl $W/
cd $W
echo "== Vivado build =="
SECONDS=0
vivado -mode batch -notrace -source build_vivado.tcl -log vivado.log -journal vivado.jou
echo "== build wall: ${SECONDS}s =="
cp $W/top.bit ~/build/top.bit
cp $W/util.rpt ~/build/util.rpt 2>/dev/null || true          # full utilization report (pulled back)
cp $W/timing.rpt ~/build/timing.rpt 2>/dev/null || true      # full timing summary (pulled back)
echo "== utilisation (DSP/BRAM/slice) ==" | tee ~/build/timing.txt
grep -iE "DSP48|Block RAM|RAMB|Slice LUTs|Slice Registers|CLB LUTs|CARRY" $W/util.rpt 2>/dev/null | head -30 | tee -a ~/build/timing.txt
echo "== timing (critical path) ==" | tee -a ~/build/timing.txt
grep -iE "WNS|TNS|Data Path Delay|Slack|requirement" $W/timing.rpt 2>/dev/null | head -20 | tee -a ~/build/timing.txt
echo VMBUILD_DONE
