#!/usr/bin/env bash
# Render a spectrogram PNG for VERIFYING the audio. A single-window FFT peak-check
# can pass on one clean slice while the rest is garbage; a spectrogram shows the
# whole capture, so broadband haze/clipping/dropouts are obvious.
# Usage: spectro.sh in.wav [out.png]
set -euo pipefail
IN="${1:?usage: spectro.sh in.wav [out.png]}"
OUT="${2:-${IN%.wav}_spec.png}"
ffmpeg -y -i "$IN" -lavfi showspectrumpic=s=1100x420:legend=1:scale=log:fscale=log "$OUT"
echo "wrote $OUT"
