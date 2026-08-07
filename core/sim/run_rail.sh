#!/usr/bin/env bash
# Play one preset through the real Basys 3 RTL and apply validate_hw.py's RAIL test to the result.
#
#   bash core/sim/run_rail.sh Brightness              # a preset that rails on hardware
#   bash core/sim/run_rail.sh 'Acoustic Grand Piano'  # one that does not (control)
#   BANK=nsynth bash core/sim/run_rail.sh <name>
#   PACE=0 bash core/sim/run_rail.sh <name>           # unpaced CCs, the harsher case
#
# See core/sim/tb_preset_rail.v for why this exists: M28a's dropped-MIDI-byte explanation for the
# 6/274 railing presets was disproved in simulation, so the open question is whether the rail is
# in the RTL at all.
set -euo pipefail
cd "$(dirname "$0")/../.."

PRESET=${1:?usage: run_rail.sh <preset name>}
BANK=${BANK:-soundfont}
PACE=${PACE:-3}
OBJ=${OBJ:-/tmp/vrail}
STREAM=/tmp/stream_$(echo "$PRESET" | tr -c 'A-Za-z0-9' '_').hex

uv run python core/sim/gen_midi_stream.py --bank "$BANK" --pace-ms "$PACE" "$PRESET" > "$STREAM"

# Rebuild only when the RTL or the testbench is newer than the binary: the sweep runs this once
# per preset and a 3 s relink each time is most of the cost for short captures.
if [ ! -x "$OBJ/tbrail" ] \
   || [ core/sim/tb_preset_rail.v -nt "$OBJ/tbrail" ] \
   || [ boards/basys3/rtl/top.v -nt "$OBJ/tbrail" ] \
   || [ build/engine.v -nt "$OBJ/tbrail" ]; then
    verilator --binary -j 0 -Wno-lint -Wno-fatal --timing -o tbrail --Mdir "$OBJ" \
        core/sim/tb_preset_rail.v boards/basys3/rtl/top.v build/engine.v > /dev/null
fi

DUMP=${DUMP:-}
printf '%-28s bank=%s pace=%sms  ' "$PRESET" "$BANK" "$PACE"
"$OBJ/tbrail" "+stream=$STREAM" ${DUMP:+"+dump=$DUMP"} | grep -E "captured|RAIL|clean" | tr '\n' ' '
echo
