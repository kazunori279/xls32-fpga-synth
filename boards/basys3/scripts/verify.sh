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
# host/play.py rather than host/analyze.py --serial. The transport moved to 2 Mbaud, 16-bit,
# stereo-interleaved (host/transport/uart.py); analyze.py's serial path still opens the port at
# 115200 and reads it as 8-bit mono against a 4 kHz assumption, so it returns CHECK on a bitstream
# that plays perfectly -- measured on the 2026-07-13 blob and the 2026-08-10 rebuild alike. Its
# stdin mode is unaffected, and is what the iverilog run in README 3 pipes into.
uv run host/play.py "$@"
