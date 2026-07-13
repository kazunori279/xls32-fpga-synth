#!/bin/bash
# Runs ON the GCE build VM (native x86_64). Codegen (XLS) + open-source Xilinx backend
# (yosys synth_xilinx -dsp -> nextpnr-xilinx -> fasm -> bitstream) via the regymm/openxc7 image.
# Parallel to vmbuild.sh (F4PGA); the XLS codegen step is identical.
# Env: STAGES, WCT (pipeline_stages / worst_case_throughput); SYNTH_FLAGS to override synth_xilinx.
set -e
cd ~/build
X=$HOME/xls/xls-v0.0.0-10214-gcf49d0e31-linux-x64
STAGES=${STAGES:-48}; WCT=${WCT:-48}
PART=xc7a35tcpg236-1
IMG=regymm/openxc7:latest
# synth_xilinx (yosys 0.62): DSP48E1 + BRAM inference are ON by default (disable via -nodsp/-nobram).
SYNTH_FLAGS=${SYNTH_FLAGS:-"-flatten -abc9 -family xc7 -top top"}

echo "== codegen (stages=$STAGES wct=$WCT) =="
$X/ir_converter_main --top=engine synth.x > engine.ir
$X/opt_main engine.ir > engine.opt.ir
$X/codegen_main --generator=pipeline --pipeline_stages=$STAGES --worst_case_throughput=$WCT \
  --delay_model=unit --use_system_verilog=false --reset=rst --reset_active_low=false \
  --reset_asynchronous=false --top=engine --module_name=xls_engine \
  --output_verilog_path=engine.v engine.opt.ir
python3 fix_verilog.py engine.v

# --- assemble a project dir + run the openXC7 flow in one container (venv stays active) ---
W=$HOME/nxbuild; rm -rf $W; mkdir -p $W/src $W/build
cp top.v engine.v $W/src/
cp basys3_nextpnr.xdc $W/src/basys3.xdc
sudo mkdir -p /opt/chipdb

cat > $W/run.sh <<EOSH
set -e
DB=/nextpnr-xilinx/xilinx/external/prjxray-db/artix7
CHIP=/chipdb/${PART}.bin
if [ ! -f "\$CHIP" ]; then
  echo "== chipdb (${PART}) — one-time =="
  python3 /nextpnr-xilinx/xilinx/python/bbaexport.py --device ${PART} --bba /chipdb/${PART}.bba
  bbasm -l /chipdb/${PART}.bba \$CHIP
  rm -f /chipdb/${PART}.bba
fi
cd /project
echo "== yosys synth_xilinx (${SYNTH_FLAGS}) =="
yosys -q -l build/yosys.log -p "synth_xilinx ${SYNTH_FLAGS}; write_json build/top.json" src/engine.v src/top.v
echo "== nextpnr-xilinx =="
nextpnr-xilinx -q -l build/nextpnr.log --chipdb \$CHIP --xdc src/basys3.xdc \
  --json build/top.json --fasm build/top.fasm
echo "== fasm -> frames -> bitstream =="
fasm2frames --part ${PART} --db-root \$DB build/top.fasm > build/top.frames
xc7frames2bit --part_file \$DB/${PART}/part.yaml --part_name ${PART} \
  --frm_file build/top.frames --output_file build/top.bit
EOSH

echo "== openXC7 build =="
SECONDS=0
sudo docker run --rm --init -v /opt/chipdb:/chipdb -v $W:/project -w /project $IMG \
  bash /project/run.sh
echo "== build wall: ${SECONDS}s =="

cp $W/build/top.bit ~/build/top.bit
echo "== resource utilisation (nextpnr) ==" | tee ~/build/timing.txt
grep -iE "Device utilisation|SLICE|LUT|FF|DSP|RAMB|Info:.*used" $W/build/nextpnr.log 2>/dev/null | tee -a ~/build/timing.txt
echo "== timing / Fmax (nextpnr) ==" | tee -a ~/build/timing.txt
grep -iE "Max frequency|Critical path|slack|constraint met|clock.*MHz" $W/build/nextpnr.log 2>/dev/null | tee -a ~/build/timing.txt
echo "== yosys cell stats ==" | tee -a ~/build/timing.txt
grep -iE "Number of cells|DSP48|RAMB36|RAMB18|LUT[0-9]|FDRE|MUXF" $W/build/yosys.log 2>/dev/null | tail -30 | tee -a ~/build/timing.txt
echo VMBUILD_DONE
