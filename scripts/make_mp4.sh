#!/usr/bin/env bash
# Convert a captured .wav into an MP4 with a scrolling spectrogram video track
# (AAC audio resampled to 44.1 kHz so it plays inline in Drive / on phones).
# Usage: make_mp4.sh in.wav [out.mp4]
set -euo pipefail
IN="${1:?usage: make_mp4.sh in.wav [out.mp4]}"
OUT="${2:-${IN%.wav}.mp4}"

# Spectrogram computed on an 8 kHz copy (Nyquist 4 kHz) with a log freq axis so
# the synth's ~200-900 Hz notes are clearly visible; audio kept separate at 44.1k.
ffmpeg -y -i "$IN" -filter_complex \
"[0:a]asplit=2[s][m];\
[s]aresample=8000,showspectrum=s=1000x400:mode=combined:slide=scroll:color=intensity:scale=cbrt:fscale=log:legend=1[v];\
[m]aresample=44100[a]" \
-map "[v]" -map "[a]" -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 128k -shortest "$OUT"
echo "wrote $OUT"
