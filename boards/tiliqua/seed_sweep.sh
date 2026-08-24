#!/usr/bin/env bash
# Re-place an already-synthesised netlist under N different placer seeds, in parallel.
#
#   bash boards/tiliqua/build.sh                       # once, to write top.json
#   bash boards/tiliqua/seed_sweep.sh 1 2 3 4 5 6      # then sweep it
#   bash boards/tiliqua/seed_sweep.sh --report         # read the results back
#
# Why this exists: at this occupancy the placer seed decides both whether the router converges at
# all and what Fmax you get, the rankings do not transfer between netlists, and so every netlist
# change costs a sweep. See the long note in build.sh for the measured draws.
#
# nextpnr reads `top.json`, which yosys already wrote, so a sweep skips the whole front of the
# build. One process cannot take two seeds; six at once finish in about fifteen minutes on an
# M-series laptop. A losing seed never terminates on its own -- `--report` marks those `runaway`,
# and you kill them yourself (`pkill -f yowasp-nextpnr-ecp5`).
set -uo pipefail
cd "$(dirname "$0")/../.."

TILIQUA_SDK="${TILIQUA_SDK:-$HOME/Documents/GitHub/tiliqua/gateware}"
VOICES="${VOICES:-24}"
DIR="${DIR:-build/tiliqua/build/xls$VOICES-r5}"
export PATH="$TILIQUA_SDK/.venv/bin:$PATH"

[ -s "$DIR/top.json" ] || { echo "no netlist at $DIR/top.json -- run build.sh first" >&2; exit 1; }

if [ "${1:-}" = "--report" ]; then
  # Two frequency summaries get printed per run: one after placement, one after routing, one line
  # per clock in each. So a run that finished routing has printed every clock name twice, and one
  # that has not has printed each once -- count one clock rather than all of them, because how
  # many clocks the design has depends on the netlist (four here, five with XLS32_SPLIT_CLOCKS).
  printf '%-6s %-10s %-10s %s\n' seed usb sync state
  for f in "$DIR"/x*.tim; do
    [ -e "$f" ] || continue
    s=$(basename "$f" .tim); s=${s#x}
    first=$(grep -m1 -oE "Max frequency for clock +'[^']*'" "$f")
    n=$([ -n "$first" ] && grep -cF "$first" "$f" || echo 0)
    usb=$(grep "Max frequency.*ulpi" "$f" | tail -1 | grep -oE "[0-9]+\.[0-9]+ MHz" | head -1)
    syn=$(grep "Max frequency.*glbnet.clk'" "$f" | tail -1 | grep -oE "[0-9]+\.[0-9]+ MHz" | head -1)
    [ "$n" -ge 2 ] && state=routed || state=runaway
    printf '%-6s %-10s %-10s %s\n' "$s" "${usb:-—}" "${syn:-—}" "$state"
  done
  exit 0
fi

[ $# -gt 0 ] || { echo "usage: $0 <seed> [seed ...]   |   $0 --report" >&2; exit 1; }

cd "$DIR"
for S in "$@"; do
  echo "=== seed $S ===" >&2
  yowasp-nextpnr-ecp5 --quiet --timing-allow-fail --router router2 --router2-tmg-ripup \
    --seed "$S" --log "x$S.tim" --25k --package CABGA256 --speed 6 \
    --json top.json --lpf top.lpf --textcfg "x$S.config" >/dev/null 2>&1 &
done
wait
