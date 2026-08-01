#!/usr/bin/env bash
# Convert a captured .wav (or any audio file) into an MP4 with a scrolling spectrogram
# video track (AAC audio resampled to 44.1 kHz so it plays inline in Drive / on phones).
# Usage: make_mp4.sh in.wav [out.mp4]
# Env:  SPAN=<sec>  how much time the 1000 px window shows at once (default: the whole
#                   clip, capped at 30 s). This sets the SCROLL SPEED: showspectrum draws
#                   one pixel column per FFT hop, so with the filter's default hop a
#                   1000 px window takes ~128 s to fill and anything shorter than that
#                   plays against a mostly-black frame. Deriving the hop from SPAN keeps
#                   the picture full whatever the clip length.
#       CRF=<n>     x264 quality (default 30 — lower is better and bigger).
#       FPSMAX=<n>  cap the output frame rate (default 20).
#       ABR=<rate>  AAC bitrate (default 192k).
# GitHub's blob view only plays an MP4 under ~5 MB (bigger ones get "we can't show files
# that are this big"), and the raw URL is served as application/octet-stream, so it
# downloads rather than plays. The two README clips are made with
#   CRF=33 FPSMAX=12 ABR=128k scripts/make_mp4.sh in docs/assets/out.mp4
set -euo pipefail
IN="${1:?usage: make_mp4.sh in.wav [out.mp4]}"
OUT="${2:-${IN%.*}.mp4}"

W=1000; H=400; RATE=8000; WIN=1024         # window px, spectrogram rate, and the FFT size
                                           # showspectrum derives from H (next pow2 >= 2*H)
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$IN")
SPAN="${SPAN:-$(awk -v d="$DUR" 'BEGIN{s=(d<30)?d:30; print (s<1)?1:s}')}"
# hop = samples per pixel column; overlap is what showspectrum actually takes.
HOP=$(awk -v r=$RATE -v s="$SPAN" -v w=$W 'BEGIN{h=int(r*s/w); print (h<8)?8:h}')
OVERLAP=$(awk -v h="$HOP" -v n=$WIN 'BEGIN{printf "%.4f", 1-h/n}')
FPSMAX="${FPSMAX:-20}"
FPS=$(awk -v r=$RATE -v h="$HOP" -v m="$FPSMAX" 'BEGIN{f=r/h; printf "%d", (f>m)?m:((f<1)?1:f)}')
CRF="${CRF:-30}"                           # the spectrogram is decoration next to the audio:
                                           # a high CRF keeps a 2-minute song a few MB, not 16

# Spectrogram computed on an 8 kHz copy (Nyquist 4 kHz) with a log freq axis so
# the synth's ~200-900 Hz notes are clearly visible; audio kept separate at 44.1k.
ffmpeg -y -i "$IN" -filter_complex \
"[0:a]asplit=2[s][m];\
[s]aresample=${RATE},showspectrum=s=${W}x${H}:mode=combined:slide=scroll:color=intensity:scale=cbrt:fscale=log:legend=1:overlap=${OVERLAP},fps=${FPS}[v];\
[m]aresample=44100[a]" \
-map "[v]" -map "[a]" -c:v libx264 -preset slow -crf "$CRF" -pix_fmt yuv420p \
-c:a aac -b:a "${ABR:-192k}" -shortest "$OUT"
echo "wrote $OUT (${SPAN}s window, ${FPS} fps, crf ${CRF})"
