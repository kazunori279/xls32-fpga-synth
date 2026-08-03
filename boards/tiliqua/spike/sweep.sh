#!/usr/bin/env bash
# M21 sweep driver: run ecp5_build.sh once per STAGES and collect the rows.
#
#   bash boards/tiliqua/spike/sweep.sh 24 32 48 64
#
# Serial on purpose. yosys and nextpnr are each effectively single-threaded but memory-hungry,
# and running them side by side on a laptop makes every data point slower without making the
# sweep finish sooner. One config is ~3-6 min wall.
set -uo pipefail
cd "$(dirname "$0")/../../.."

FREQ="${FREQ:-60}"
OUT="${OUT:-build/spike}"
ROWS="$OUT/sweep_f${FREQ}.jsonl"
mkdir -p "$OUT"

for s in "$@"; do
  echo "=== STAGES=$s FREQ=$FREQ ===" >&2
  row=$(STAGES="$s" FREQ="$FREQ" bash boards/tiliqua/spike/ecp5_build.sh 2>/dev/null | tail -1)
  if [ -n "$row" ]; then
    echo "$row" | tee -a "$ROWS"
  else
    echo "{\"stages\": $s, \"verdict\": \"BUILD_FAILED\"}" | tee -a "$ROWS"
  fi
done

echo >&2
echo "rows appended to $ROWS" >&2
