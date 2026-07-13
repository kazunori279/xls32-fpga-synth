#!/usr/bin/env bash
# Record a demo MP4: the web UI (screen) + the board (webcam, picture-in-picture) + the
# synth's own audio. Video is captured live with ffmpeg; the audio is a *pristine* copy of the
# board's output pulled from the running server (/api/capture, LOCAL mode), then muxed in — so
# the sound is the real digital signal, not a room mic.
#
# Prereqs: the server is running (webui/server.py) with the board connected; Terminal has been
# granted macOS **Screen Recording** and **Camera** permissions (System Settings > Privacy).
#
# Usage:
#   scripts/demo_video.sh [out.mp4]
# Env overrides (see `ffmpeg -f avfoundation -list_devices true -i ""` for indices):
#   SCREEN_IDX=2  CAM_IDX=0  DUR=45  BASE=https://localhost:8765  CAM_W=480  AV_OFFSET=1.3
#   CROP=w:h:x:y      crop the screen grab to just the browser window (drop the rest of the
#                     desktop). Get the geometry from the browser: window.screenX/screenY +
#                     (outerHeight-innerHeight) for the content top, innerWidth/innerHeight
#                     for size (× devicePixelRatio on a HiDPI display). Empty = full screen.
#   CAM_SIZE=1280x720 CAM_FPS=60  capture the webcam at a real 60fps (must be a mode the
#                     device supports — list them with an invalid -video_size). OUT_FPS=60
#                     keeps that smoothness in the muxed file.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-demo.mp4}"
SCREEN_IDX="${SCREEN_IDX:-2}"     # avfoundation "Capture screen 0"
CAM_IDX="${CAM_IDX:-0}"           # avfoundation "Logitech StreamCam"
DUR="${DUR:-45}"                  # seconds to record (covers ~1–2 loops of a demo song)
CAM_W="${CAM_W:-480}"             # webcam PIP width (px), bottom-right corner
CAM_SIZE="${CAM_SIZE:-1280x720}"  # webcam capture resolution (must be a supported mode)
CAM_FPS="${CAM_FPS:-60}"          # webcam capture frame rate (StreamCam does 60 at 720p/1080p)
OUT_FPS="${OUT_FPS:-60}"          # output frame rate (60 to preserve the webcam's smoothness)
CROP="${CROP:-}"                  # w:h:x:y to crop the screen to the browser window (empty = full)
BASE="${BASE:-https://localhost:8765}"
AV_OFFSET="${AV_OFFSET:-1.3}"     # audio delay vs video (s) — ffmpeg avfoundation warm-up; tune if lips/notes drift
WARMUP="${WARMUP:-2}"             # let the camera/screen stream settle before the demo starts

VID=/tmp/demo_video.mp4
WAV=/tmp/local_out.wav

# Keep the webcam's full frame rate. overlay emits output frames at its main (first) input's
# cadence, so the SCREEN must be a genuine OUT_FPS grid phase-locked with the output — otherwise
# the smooth 60fps webcam is decimated to the screen's rate (choppy). Two things make that work:
#   1. capture the screen natively at OUT_FPS (the `-framerate` on its avfoundation input below), and
#   2. fps=OUT_FPS on the screen branch to guarantee a CFR grid.
# The camera branch is scale-only — resampling it a second time (its own fps filter) beats against
# the output grid and drops ~1/4 of its frames. Verified: camera stays ~98% fresh at 60fps.
CROPF=""; [ -n "$CROP" ] && CROPF="crop=${CROP},"
FILTER="[0:v]${CROPF}fps=${OUT_FPS}[scr];[1:v]scale=${CAM_W}:-1[cam];[scr][cam]overlay=W-w-24:H-h-24[v]"

echo "==> LOCAL mode on (server plays audio on this Mac + enables capture)"
curl -sk -X POST "$BASE/api/local" -H 'Content-Type: application/json' \
  -d '{"on":true,"chans":[0]}' >/dev/null

echo "==> recording ${DUR}s of screen[$SCREEN_IDX]${CROP:+ (crop $CROP)} + webcam[$CAM_IDX] @${CAM_SIZE}/${CAM_FPS}fps -> $VID"
ffmpeg -hide_banner -loglevel warning -y \
  -f avfoundation -capture_cursor 1 -framerate "${OUT_FPS}" -i "${SCREEN_IDX}:none" \
  -f avfoundation -pixel_format nv12 -video_size "${CAM_SIZE}" -framerate "${CAM_FPS}" -i "${CAM_IDX}:none" \
  -t "$DUR" \
  -filter_complex "$FILTER" \
  -map "[v]" -r "${OUT_FPS}" -fps_mode cfr -c:v libx264 -preset veryfast -pix_fmt yuv420p "$VID" &
FF=$!

sleep "$WARMUP"
echo
echo "   >>> NOW: in the browser, open DEMO and click the song you want (e.g. Bach) <<<"
echo
# Pull the exact audio the board is producing for the rest of the window.
CAP=$(python3 -c "print(max(1, $DUR - $WARMUP - 1))")
curl -sk -X POST "$BASE/api/capture" -H 'Content-Type: application/json' \
  -d "{\"secs\": $CAP}" >/dev/null

wait "$FF"
curl -sk -X POST "$BASE/api/demo_stop" >/dev/null 2>&1 || true

echo "==> muxing video + pristine synth audio -> $OUT (audio offset ${AV_OFFSET}s)"
ffmpeg -hide_banner -loglevel warning -y \
  -i "$VID" -itsoffset "$AV_OFFSET" -i "$WAV" \
  -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -shortest "$OUT"
echo "wrote $OUT"
