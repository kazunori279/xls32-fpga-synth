#!/usr/bin/env bash
# Mac-side: push sources to the GCE build VM, build natively (fast), pull top.bit back.
# Then flash locally with ./verify or openFPGALoader. Env: STAGES, WCT, BACKEND.
#   BACKEND=f4pga   (default) -> vmbuild.sh        (yosys + VPR + prjxray, no DSP/BRAM)
#   BACKEND=nextpnr           -> vmbuild_nextpnr.sh (openXC7: synth_xilinx + nextpnr-xilinx; BRAM, no DSP)
#   BACKEND=vivado            -> vmbuild_vivado.sh  (Vivado: DSP48 + BRAM + real timing)
set -euo pipefail
cd "$(dirname "$0")/.."   # project root (script lives in scripts/)
# Set these to your own GCE VM (or export them in your environment).
Z="${GCE_ZONE:-YOUR_ZONE}"; P="${GCE_PROJECT:-YOUR_GCP_PROJECT}"; VM="${GCE_VM:-YOUR_VM}"
BACKEND="${BACKEND:-f4pga}"
gcloud compute ssh "$VM" --zone="$Z" --project="$P" --command="mkdir -p ~/build" >/dev/null 2>&1
# sources land FLAT on the VM (~/build/), which is what the vmbuild scripts expect.
gcloud compute scp --zone="$Z" --project="$P" \
  rtl/synth.x rtl/top.v rtl/basys3.xdc rtl/basys3_nextpnr.xdc rtl/fix_verilog.py rtl/build_vivado.tcl \
  scripts/vmbuild.sh scripts/vmbuild_nextpnr.sh scripts/vmbuild_vivado.sh "$VM":~/build/ >/dev/null
case "$BACKEND" in
  nextpnr) RB=vmbuild_nextpnr.sh ;;
  vivado)  RB=vmbuild_vivado.sh ;;
  *)       RB=vmbuild.sh ;;
esac
echo "=== building on $VM (BACKEND=$BACKEND) ==="
gcloud compute ssh "$VM" --zone="$Z" --project="$P" \
  --command="STAGES=${STAGES:-48} WCT=${WCT:-48} bash ~/build/$RB"
mkdir -p build
gcloud compute scp --zone="$Z" --project="$P" "$VM":~/build/top.bit ./build/top.bit >/dev/null
gcloud compute scp --zone="$Z" --project="$P" "$VM":~/build/timing.txt ./build/timing.txt >/dev/null 2>&1 || true
# Vivado backend also emits full reports — pull them back if present (best-effort).
gcloud compute scp --zone="$Z" --project="$P" "$VM":~/build/util.rpt ./build/util.rpt >/dev/null 2>&1 || true
gcloud compute scp --zone="$Z" --project="$P" "$VM":~/build/timing.rpt ./build/timing.rpt >/dev/null 2>&1 || true
echo "=== timing ==="; cat build/timing.txt 2>/dev/null || true
echo "=== build/top.bit ready — flash with: openFPGALoader -b basys3 build/top.bit ==="
