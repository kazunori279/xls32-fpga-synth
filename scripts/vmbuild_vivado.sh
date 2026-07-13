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
echo "== codegen (stages=$STAGES wct=$WCT) =="
$X/ir_converter_main --top=engine synth.x > engine.ir
$X/opt_main engine.ir > engine.opt.ir
$X/codegen_main --generator=pipeline --pipeline_stages=$STAGES --worst_case_throughput=$WCT \
  --delay_model=unit --use_system_verilog=false --reset=rst --reset_active_low=false \
  --reset_asynchronous=false --top=engine --module_name=xls_engine \
  --output_verilog_path=engine.v engine.opt.ir
python3 fix_verilog.py engine.v

W=$HOME/vivbuild; rm -rf $W; mkdir -p $W
cp top.v engine.v basys3.xdc build_vivado.tcl $W/
cd $W
echo "== Vivado build =="
SECONDS=0
vivado -mode batch -notrace -source build_vivado.tcl -log vivado.log -journal vivado.jou
echo "== build wall: ${SECONDS}s =="
cp $W/top.bit ~/build/top.bit
echo "== utilisation (DSP/BRAM/slice) ==" | tee ~/build/timing.txt
grep -iE "DSP48|Block RAM|RAMB|Slice LUTs|Slice Registers|CLB LUTs|CARRY" $W/util.rpt 2>/dev/null | head -30 | tee -a ~/build/timing.txt
echo "== timing (critical path) ==" | tee -a ~/build/timing.txt
grep -iE "WNS|TNS|Data Path Delay|Slack|requirement" $W/timing.rpt 2>/dev/null | head -20 | tee -a ~/build/timing.txt
echo VMBUILD_DONE
