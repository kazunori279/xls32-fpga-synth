#!/usr/bin/env bash
# Run every preset that rails on the Basys 3 hardware through core/sim/tb_preset_rail.v, plus
# controls that do not, and report which of them rail in simulation.
#
#   bash core/sim/sweep_rail.sh
#   PACE=0 bash core/sim/sweep_rail.sh        # unpaced CCs, harsher than validate_hw
#
# The six names are the ones validate_hw.py flagged in M27. If none of them rail here, the fault
# is not in the RTL's arithmetic and the search moves to what simulation cannot see -- setup
# timing, which build/timing_endpoints.rpt is there to settle.
set -uo pipefail
cd "$(dirname "$0")/../.."

RAILERS=("Clavinet" "Clavinet G3" "Trumpet G4" "Synth Strings 1" "Atmosphere G4" "Brightness")
# Controls chosen to be NEAR the railers in parameter space, not far from them: `Pizzicato G4`
# (reso 121) and `Celesta G4` (reso 117, cutoff 124) sit above every railing preset's resonance
# and stay clean on hardware. A control that merely sounds different proves nothing.
CONTROLS=("Pizzicato G4" "Celesta G4")

# Build once, serially, so the six concurrent runs below do not race on the same object dir.
verilator --binary -j 0 -Wno-lint -Wno-fatal --timing -o tbrail --Mdir /tmp/vrail \
    core/sim/tb_preset_rail.v boards/basys3/rtl/top.v build/engine.v > /dev/null

pids=()
for p in "${RAILERS[@]}" "${CONTROLS[@]}"; do
    slug=$(echo "$p" | tr -c 'A-Za-z0-9' '_')
    DUMP=/tmp/rail_$slug.txt bash core/sim/run_rail.sh "$p" > "/tmp/railout_$slug.txt" 2>&1 &
    pids+=($!)
done
for pid in "${pids[@]}"; do wait "$pid"; done

echo "=== rails on hardware ==="
for p in "${RAILERS[@]}"; do cat "/tmp/railout_$(echo "$p" | tr -c 'A-Za-z0-9' '_').txt"; done
echo "=== controls (clean on hardware) ==="
for p in "${CONTROLS[@]}"; do cat "/tmp/railout_$(echo "$p" | tr -c 'A-Za-z0-9' '_').txt"; done
