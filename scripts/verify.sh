#!/usr/bin/env bash
# Flash and verify the synth over USB (headless): confirm `done`, then read the
# UART sample stream and check sine period + ADSR envelope.
set -euo pipefail
cd "$(dirname "$0")/.."   # project root (script lives in scripts/)
echo "==> flashing (JTAG)"; openFPGALoader -b basys3 build/top.bit
echo "==> reading UART sample stream"
uv run host/analyze.py --serial "${1:-}" "${2:-4}"
