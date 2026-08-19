#!/usr/bin/env bash
# Rewrite synth.x for a smaller voice count.
#
#   bash core/set_voices.sh 24 > build/synth_v24.x
#   bash core/set_voices.sh 24 core/synth.x build/synth_v24.x
#
# The engine's DSP datapath is time-shared -- one voice per proc tick, 32 ticks per sample -- so the
# oscillators, filter, VCA and envelopes are the same size at any voice count. What this changes is
# the voice register file and the two unrolled scans, `apply_on` and `apply_off`. That is why the
# saving is so lopsided: 32 -> 24 frees 4,161 cells and 24 -> 16 frees only 409 more. See
# ARCHITECTURE_tiliqua.md E2 for the measured census, and #30 for why 24 is the interesting one --
# it puts the Tiliqua build at 80% occupancy, where five seeds out of five route.
#
# Eight sites, all of them voice-related; `grep -n 'Voice\[32\]\|u5:31' core/synth.x` finds them all.
# N = 32 is a byte-identical no-op, so build scripts can call this unconditionally.
set -euo pipefail

N="${1:?usage: set_voices.sh <voices> [src] [dst]}"
SRC="${2:-$(dirname "$0")/synth.x}"
DST="${3:-}"

case "$N" in (*[!0-9]*|'') echo "voices must be a number, got '$N'" >&2; exit 1;; esac
[ "$N" -ge 2 ] && [ "$N" -le 32 ] || { echo "voices must be 2..32, got $N" >&2; exit 1; }

# The 31 rule runs first on purpose: at N = 32 the 32 rule would otherwise rewrite `u32:0..u32:32`
# to itself and then the 31 rule would find nothing, but at N = 31 the 32 rule would produce
# `u32:0..u32:31` and the 31 rule would rewrite it a second time.
render() {
    sed -e "s/u32:0\.\.u32:31/u32:0..u32:$((N - 1))/g" \
        -e "s/u32:0\.\.u32:32/u32:0..u32:$N/g" \
        -e "s/Voice\[32\]/Voice[$N]/g" \
        -e "s/u5:31/u5:$((N - 1))/g" \
        "$SRC"
}

if [ -n "$DST" ]; then render > "$DST"; else render; fi
