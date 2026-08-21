#!/usr/bin/env bash
# M24: XLS engine -> Tiliqua bitstream (or Verilator simulation).
#
#   bash boards/tiliqua/build.sh              # build build/tiliqua/build/xls24-<hw>/top.bit
#   VOICES=32 bash boards/tiliqua/build.sh    # the experimental full-ring build
#   SIM=1 bash boards/tiliqua/build.sh        # verilate + run, leaves build/tiliqua/out0.txt
#   SKIP_BUILD=1 bash boards/tiliqua/build.sh # elaborate only (fast wiring check)
#
# M28 added an XLS32_VARIANT=cv build because CV in and the effects did not fit on one die; M29
# freed the space and M31 removed the variant. Override NAME for a one-off.
#
# --- Why the default is 24 voices (M36) ---
#
# The 32-voice build fills 98.9% of the die and closes `clk` at 46.35 MHz against a 60 MHz
# constraint -- a bet that the silicon is 29% faster than nextpnr models it. It runs here. It did
# not run on one of the vendor's two modules (issue #3). 24 voices is 93.9% and 55.48 MHz: the same
# bet at +8%, and graded 99.8/100 on the module. So 24 is what this repo stands behind and 32 is
# kept as the experimental one (issue #37).
#
# The knee is at 24, not 28 -- 28 voices buys only +2.4 MHz -- because the critical path changes
# character across it: 4.49 ns logic / 15.30 ns routing at 98.9%, against 7.73 / 10.29 at 93.9%.
# Below the congestion, what is left is a depth-limited cone inside luna (#34) that area cannot buy
# back. See ARCHITECTURE_tiliqua.md E4.
#
# Voice count is not a gateware parameter and does not need to be: the engine's sample rate is set
# by the codec's backpressure, not by the ring length (gateware/xls_core.py), so a 24-voice engine
# runs at the same pitch with the same everything else. What it needs is a rewritten copy of
# core/synth.x, because the count lives there as bare literals -- core/synth.x itself stays at 32
# since it is also the shipping Basys 3 gateware. spike/voices_variant.py does the rewrite and
# asserts a match count for every site, which matters more than it sounds: when it silently missed
# one, the resulting build pruned its whole voice ring and reported a *better* Fmax at a *smaller*
# area while playing nothing at all (#35).
#
# In SIM mode, XLS_SIM_MS, XLS_SIM_OUT and XLS_SIM_MIDI reach the harness; see sim_xls_core.cpp.
#
# Three toolchains, three different ideas of what a path is:
#   * XLS ships linux-x64 only, so codegen runs in the amd64 container core/build uses. The
#     output is cached under $CACHE, keyed on a hash of synth.x so a DSLX edit invalidates it.
#   * yosys/nextpnr come from yowasp -- WebAssembly under WASI, which can only see files
#     beneath its working directory. Amaranth runs them with cwd=build_dir, so that is fine
#     as long as everything they need was written there.
#   * The Tiliqua SDK's own sim/build helpers use paths relative to *their* cwd -- notably
#     `./src/tb_cpp/*.h`. So both actions run from $WORK, which carries a `src` symlink into
#     the SDK. That also keeps every artefact inside this repo's gitignored build/ tree
#     rather than dirtying the SDK checkout, which we treat as read-only.
set -euo pipefail
cd "$(dirname "$0")/../.."                     # repo root
REPO="$PWD"

STAGES="${STAGES:-12}"; WCT="${WCT:-$STAGES}"
VOICES="${VOICES:-24}"                         # see the header; 32 is the experimental build
CACHE="${CACHE:-/tmp/xls-synth-work}"          # docker-side, outside the repo
XLS_TAG="${XLS_TAG:-v0.0.0-10214-gcf49d0e31}"
IMG="${IMG:-xls-ubuntu:24.04}"
NAME="${NAME:-XLS$VOICES}"
TILIQUA_SDK="${TILIQUA_SDK:-$HOME/Documents/GitHub/tiliqua/gateware}"
WORK="$REPO/build/tiliqua"

[ -d "$TILIQUA_SDK" ] || { echo "TILIQUA_SDK not found: $TILIQUA_SDK" >&2; exit 1; }
PY="$TILIQUA_SDK/.venv/bin/python"
[ -x "$PY" ] || { echo "no SDK venv at $PY (run 'pdm install' in $TILIQUA_SDK)" >&2; exit 1; }

# --- 1. synth.x -> engine.v (cached on the DSLX hash, so an edit rebuilds) ---
mkdir -p "$CACHE" "$WORK"

# Resolve the DSLX source. An explicit SRCX wins; otherwise 32 voices is core/synth.x as-is and
# anything else is a generated copy. Regenerated every run rather than cached -- it costs
# milliseconds, and a stale variant sitting next to a freshly edited synth.x is precisely the
# class of mistake #35 was. The generator asserts every substitution count, so a literal that
# moves in synth.x stops the build instead of quietly shipping the wrong ring.
if [ -z "${SRCX:-}" ]; then
  if [ "$VOICES" = 32 ]; then
    SRCX="core/synth.x"
  else
    SRCX="$WORK/synth$VOICES.x"
    uv run boards/tiliqua/spike/voices_variant.py --voices "$VOICES" --out "$SRCX"
  fi
fi

HASH="$(shasum -a 256 "$SRCX" core/fix_verilog.py | shasum -a 256 | cut -c1-12)"
ENG_NAME="engine_tlq_s${STAGES}w${WCT}_${HASH}.v"
ENG_CACHE="$CACHE/$ENG_NAME"
if [ ! -s "$ENG_CACHE" ]; then
  echo "==> codegen $SRCX -> $ENG_NAME (stages=$STAGES wct=$WCT)"
  bash core/fetch_xls.sh "$CACHE" "$XLS_TAG" "$IMG"      # /tmp gets cleaned; issue #33
  cp "$SRCX" "$CACHE/synth_in.x"; cp core/codegen.sh "$CACHE/"
  docker run --rm --platform linux/amd64 -v "$CACHE":/w -w /w \
    -e XLS_DIR="/w/xls-$XLS_TAG-linux-x64" -e SRC=/w/synth_in.x -e OUT="/w/$ENG_NAME" \
    -e STAGES="$STAGES" -e WCT="$WCT" -e FIXUP=0 "$IMG" bash /w/codegen.sh >/dev/null
  # fix_verilog.py unrolls the dynamic-index generate loops yosys cannot handle, and adds `ce`.
  uv run core/fix_verilog.py "$ENG_CACHE" >/dev/null
fi
[ -s "$ENG_CACHE" ] || { echo "codegen produced no $ENG_CACHE" >&2; exit 1; }
cp "$ENG_CACHE" "$WORK/engine.v"
echo "==> engine: $WORK/engine.v ($(wc -l < "$WORK/engine.v") lines)"

# --- 2. Amaranth elaboration + yosys/nextpnr (or Verilator) ---
ln -sfn "$TILIQUA_SDK/src" "$WORK/src"
# The yowasp launchers are console scripts in the SDK venv. `pdm run` would put them on PATH;
# we invoke the venv interpreter directly, so amaranth's require_tool() has to be told where
# to look or it reports the tools as missing despite the variables below being set.
export PATH="$TILIQUA_SDK/.venv/bin:$PATH"
export AMARANTH_USE_YOSYS=builtin
export YOSYS=yowasp-yosys
export NEXTPNR_ECP5=yowasp-nextpnr-ecp5
export ECPPACK=yowasp-ecppack
export XLS_ENGINE_V="$WORK/engine.v"
# The gateware side needs the voice count too, and until #43 it did not get it: viz.py drew a
# fixed 32 tiles and the `brief` the bootloader prints said "XLS32 synth" on a 24-voice build.
# `gateware/voices.py` reads this one variable; nothing else sets it, and its default is the
# same 24 as line 51, so an SDK-side `python top.py build` cannot disagree with this script.
export VOICES

# --- the stamp the board reports over USB (issue #27) ---
# Two variables, both consumed by gateware/build_id.py, which turns them into iManufacturer. The
# rule for computing them lives in that file too, so `eval` here rather than a second copy in
# shell. Pre-set either one to pin it -- a bit-reproducible build wants a fixed XLS32_BUILD_UTC.
# Keep the stamp fixed-width if you change its format: the string is a ROM, and its *length* moves
# the netlist and re-draws the seed lottery below. Its characters do not. See build_id.py.
# Leaving both unset (which is what a bare `python top.py build` does) produces a bitstream with no
# stamp at all, indistinguishable from a pre-#27 one; the panel reports that honestly.
eval "$("$PY" "$REPO/boards/tiliqua/gateware/build_id.py")"
echo "==> build stamp: $XLS32_BUILD_UTC-$XLS32_BUILD_COMMIT"

# nextpnr's default router does not converge on this design. At 97% TRELLIS_COMB, router1 spent
# two hours ripping up more arcs than it laid -- 62,719 of 105,900 still unrouted, the count
# rising, and each 1000 iterations taking 240 s against 0.4 s at the start. router2 finishes the
# same netlist in 81 s with overused=0. It is not a tuning preference; it is the difference
# between a six-minute build and one that never ends.
#
# This is an *override*, not an addition: whatever the SDK passes as `nextpnr_opts` is replaced
# wholesale, so `--timing-allow-fail` has to be repeated here. Dropping it turns the known `clk`
# shortfall (39.92 MHz against a 60 MHz constraint, unmet since M25 and so far harmless -- the
# engine runs in `audio_clk`) from a warning into an error that fails the build after it has routed.
#
# `--seed 5` is not a preference either. At 97% TRELLIS_COMB the router either converges or runs
# away, and which it does is decided by the seed. At M34 (23,729 cells) that seed was 3: the
# default bottomed out at 135 overused nets and then the ripup cascade ran away, seed 2 bottomed
# at 117 and did the same, seed 3 routed. The placer knobs were measured and are worse --
# `--router2-alt-weights` plateaus at 765, `--no-tmdriv` at 2,779.
#
# #27's build stamp re-drew the lottery, exactly as the M34 note warned it would. The stamp adds
# no logic of its own, but it is a string descriptor, and a wider ROM is a different netlist:
# 23,792 cells here against M34's 23,729. On that netlist seed 3 climbs past 10,000 overused nets
# and never comes back, and so do 2, 5 and 6. Seed 4 reaches overused=0 at iteration 158, and
# leaves 40.95 MHz on the `clk`/USB domain that has failed its 60 MHz constraint since M25 (issue
# #3) -- more margin than the 39.92 of the M34 bitstream it replaces. Roughly one seed in four
# won here; budget for that.
#
# And then the DC fixes re-drew it twice more, which is the pattern now: any netlist change at
# this occupancy costs a seed sweep. #1's SVF rounding took the design to 23,859 cells and seed 4
# ran away on it (overused climbing 13,623 -> 15,694 over iterations 110-116), so that netlist
# never had a bitstream. #2's pulse-DC subtraction took it to 24,023 (98.9%, 265 cells free) and
# seed 4 ran away again, faster. Of 1, 2, 3, 5, 6, 7 run in parallel on that netlist, three won:
# seed 2 at iteration 112, seed 5 at 132, seed 7 at 115. Seed 5 is pinned here because it is the
# best of the three on both domains -- 22.94 MHz on `audio_clk` (constraint 12.29) and 46.35 MHz
# on `clk`, against 22.79/44.74 for seed 2 and 21.53/42.22 for seed 7. Three in six is a better
# draw than the one in four above, but do not read anything into that; it is the same lottery.
#
# Redrawing costs an afternoon unless you skip the front of the build. nextpnr reads `top.json`,
# which yosys already wrote, so run it by hand out of `build/tiliqua/build/xls<N>-r5/` with
# `--log x$S.tim --textcfg x$S.config` per seed and the inputs shared read-only. Four at once
# finish in about fifteen minutes on an M-series laptop; one process still cannot take two seeds.
# A losing seed never terminates on its own -- watch `overused=` and kill the ones that climb.
# See DEVELOPMENT_tiliqua.md M34 "The area squeeze".
#
# M36: the 24-voice netlist has its own lottery and its own winner. Converged seeds measured
# 51.17 (1), 55.48 (4), 48.45 (5), 54.03 (6) -- four of five, against three of six at 32 voices, so
# 6% of the die back is worth something in routability as well as in megahertz. Seed 4 is pinned
# below for 24 voices and seed 5 stays pinned for 32. Note the winners do not transfer: seed 5 is
# the best of the 32-voice draw and among the worst of the 24-voice one.
#
# M37 (#39): `--router2-tmg-ripup` is now on. It was measured at M36 and held back because
# turning it on means a new bitstream, and the one shipping then was the one graded on the module
# -- that reason expired the moment #40's tile grid changed the netlist. It is worth +1.04 MHz at 32
# voices and it lifts the weak 24-voice seeds (1: 51.17 -> 53.19, 6: 54.03 -> 55.33) without
# raising the ceiling (4: 55.48 -> 55.40), so read it as insurance against a bad draw rather than
# as headroom. Do not confuse it with `--tmg-ripup`, which is router1's and therefore inert here.
#
# M37's own 24-voice draw, all six seeds, on this netlist with the router option on:
# 52.50 (1), 54.06 (2), **54.30 (3)**, 49.12 (4), 51.40 (5), 53.07 (6). Every one converged, where
# M36 had four of five -- which is the insurance above showing up as routability rather than as
# megahertz. Seed 3 wins, and only by 0.24 MHz: the spread across the six is 5.2 MHz with no gap
# worth calling a winner's margin, so treat the pin as "the best measured" and not as a property
# of the design.
#
# Seed 4 is worth a warning, because measuring it is how this sweep first got the wrong answer.
# It read 56.33 MHz -- best of the draw by 2 MHz -- on a build whose tree was dirty, and
# `9fca1a0-dirty` is six characters longer than a clean seven-digit sha. The stamp is a ROM, so
# that was a different netlist: 22,745 cells against 22,985 for the clean one. Re-measured properly
# it is 49.12, the *worst* of the six. Two lessons. The whole sweep has to run on the netlist that
# will ship, dirty trees included in "different netlist"; and seed rankings really do not transfer,
# not even from the same seed on a netlist 240 cells away.
#
# The 32-voice seed below is still the M36 draw and has *not* been re-swept on this netlist. Sweep
# before trusting it.
case "$VOICES" in
  32) SEED="${SEED:-5}" ;;
  *)  SEED="${SEED:-3}" ;;
esac
PNR_OPTS="--timing-allow-fail --router router2 --router2-tmg-ripup --seed $SEED"
export AMARANTH_nextpnr_opts="${AMARANTH_nextpnr_opts:-$PNR_OPTS}"

cd "$WORK"
if [ -n "${SIM:-}" ]; then
  # 250 ms is past the ADSR attack and decay with ~8k sustain samples left over, which is what
  # check_pitch.py needs to resolve a peak. It costs about ten seconds.
  export XLS_SIM_MS="${XLS_SIM_MS:-250}"
  export XLS_SIM_OUT="${XLS_SIM_OUT:-$WORK/out0.txt}"
  exec "$PY" "$REPO/boards/tiliqua/gateware/top.py" sim --name "$NAME" "$@"
fi
exec "$PY" "$REPO/boards/tiliqua/gateware/top.py" build --name "$NAME" \
     ${SKIP_BUILD:+--skip-build} "$@"
