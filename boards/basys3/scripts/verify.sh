#!/usr/bin/env bash
# Flash and verify the synth over USB (headless): confirm `done`, then play a chord and
# FFT-check that the pitches came back at the frequencies they were asked for.
#   ./verify.sh                                          # build/top.bit, A major 7
#   BIT=boards/basys3/firmware/top.bit ./verify.sh 36 60 84
set -euo pipefail
cd "$(dirname "$0")/../../.."   # project root (script lives in boards/basys3/scripts/)
BIT="${BIT:-build/top.bit}"
echo "==> flashing (JTAG): $BIT"; openFPGALoader -b basys3 "$BIT"
echo "==> playing a chord and checking the pitches"
# host/play.py, which is where this check lives. It used to be host/analyze.py --serial, back when
# the transport was 4 kHz 8-bit; that path read the 2 Mbaud 16-bit stereo stream as 8-bit mono at
# 115200 and returned CHECK on a bitstream that plays perfectly -- measured on the 2026-07-13 blob
# and the 2026-08-10 rebuild alike. Its stdin mode had expired the same way against a 16-bit
# 32 kHz simulation, so the whole file went on 2026-08-10; README 3's iverilog run now pipes into
# host/analyze_fft.py, which is this same FFT check reading a simulation instead of a board.
uv run host/play.py "$@"
