#!/usr/bin/env bash
# Record a demo MP4: the web UI (screen) + the board (webcam, picture-in-picture) + the
# synth's own audio. All three are captured live with ffmpeg and muxed at the end.
#
# The audio used to come from the server (`/api/capture` in LOCAL mode), which no longer exists:
# since M31 the browser owns the board's link and plays the audio itself. So the sound is captured
# from an audio *input* device -- digitally, never a room mic. Two ways, depending on the board:
#
#   Tiliqua -- point AUD_IDX at the board itself. Its UAC2 interface enumerates as an input
#     ("Tiliqua XLS32", 4ch @ 48 kHz), which is the synth's own output before the host touches it.
#     No extra software, and one fewer resampling stage than a loopback. Only ch0/1 are audio:
#     ch2/3 carry the gray-coded audio-clock counter, which AFILTER drops -- see below. Untested
#     alongside a browser streaming the same device; if CoreAudio refuses the second client, fall
#     back to the loopback.
#   Basys 3 (or the fallback) -- a **loopback device**, capturing what the browser plays. BlackHole
#     2ch is free (`brew install blackhole-2ch`); make it the Mac's output, or better, build a
#     Multi-Output Device in Audio MIDI Setup so you can still hear the demo while it records.
#
# Prereqs: the UI is open in Chrome with a board connected and POWER pressed -- either the hosted
# panel at https://kazunori279.github.io/xls32-fpga-synth/ (nothing to run) or a local copy served
# with `python3 -m http.server 8765 -d webui/static`. Terminal needs macOS **Screen Recording**,
# **Camera** and **Microphone** permissions (System Settings > Privacy).
#
# Usage:
#   scripts/demo_video.sh [out.mp4]
# Env overrides (see `ffmpeg -f avfoundation -list_devices true -i ""` for indices):
#   SCREEN_IDX=2  CAM_IDX=0  AUD_IDX=1  DUR=45  CAM_W=480  AV_OFFSET=0  AFILTER=…
#   AUD_IDX          avfoundation index of the audio INPUT — the Tiliqua itself, or a loopback
#                    (BlackHole 2ch etc.). Required; the list_devices output above names it.
#                    Note the indices are per-machine and per-session: with a Tiliqua plugged in
#                    and no webcam, screen 0 is video 0 and the board is audio 0, so the defaults
#                    below (SCREEN_IDX=2, CAM_IDX=0) are both wrong. Always check the list first.
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
AUD_IDX="${AUD_IDX:-}"            # avfoundation loopback input (BlackHole 2ch)
DUR="${DUR:-45}"                  # seconds to record (covers ~1–2 loops of a demo song)
CAM_W="${CAM_W:-480}"             # webcam PIP width (px), bottom-right corner
CAM_SIZE="${CAM_SIZE:-1280x720}"  # webcam capture resolution (must be a supported mode)
CAM_FPS="${CAM_FPS:-60}"          # webcam capture frame rate (StreamCam does 60 at 720p/1080p)
OUT_FPS="${OUT_FPS:-60}"          # output frame rate (60 to preserve the webcam's smoothness)
CROP="${CROP:-}"                  # w:h:x:y to crop the screen to the browser window (empty = full)
# Take the first two channels and nothing else. The Tiliqua's UAC2 input is 4 channels: ch0/1 are
# the audio, ch2/3 are the 31-bit gray-coded audio-clock counter check_loop.py measures the board's
# real clock with -- and bit 15 of ch2 is forced high as a never-zero dropout marker, so those two
# channels sit near full scale. avfoundation hands ffmpeg whatever the device offers (4), unlike
# Chrome, which asks for 4 and is given 2; without this the counter is encoded into the AAC track
# and folds into the mix on any downmix. On a 2ch loopback input this is the identity. AFILTER=anull
# disables it.
AFILTER="${AFILTER:-pan=stereo|c0=c0|c1=c1}"
# Audio and video now start together on the same ffmpeg invocation, so the old 1.3 s server-capture
# skew is gone. Left as a knob because avfoundation warm-up still differs per machine.
AV_OFFSET="${AV_OFFSET:-0}"
WARMUP="${WARMUP:-2}"             # let the camera/screen stream settle before the demo starts

if [ -z "$AUD_IDX" ]; then
  echo "set AUD_IDX to the loopback input's index:  ffmpeg -f avfoundation -list_devices true -i ''" >&2
  exit 2
fi

VID=/tmp/demo_video.mp4

# Keep the webcam's full frame rate. overlay emits output frames at its main (first) input's
# cadence, so the SCREEN must be a genuine OUT_FPS grid phase-locked with the output — otherwise
# the smooth 60fps webcam is decimated to the screen's rate (choppy). Two things make that work:
#   1. capture the screen natively at OUT_FPS (the `-framerate` on its avfoundation input below), and
#   2. fps=OUT_FPS on the screen branch to guarantee a CFR grid.
# The camera branch is scale-only — resampling it a second time (its own fps filter) beats against
# the output grid and drops ~1/4 of its frames. Verified: camera stays ~98% fresh at 60fps.
CROPF=""; [ -n "$CROP" ] && CROPF="crop=${CROP},"
FILTER="[0:v]${CROPF}fps=${OUT_FPS}[scr];[1:v]scale=${CAM_W}:-1[cam];[scr][cam]overlay=W-w-24:H-h-24[v]"

echo "==> recording ${DUR}s of screen[$SCREEN_IDX]${CROP:+ (crop $CROP)} + webcam[$CAM_IDX] @${CAM_SIZE}/${CAM_FPS}fps + audio[$AUD_IDX] -> $VID"
ffmpeg -hide_banner -loglevel warning -y \
  -f avfoundation -capture_cursor 1 -framerate "${OUT_FPS}" -i "${SCREEN_IDX}:none" \
  -f avfoundation -pixel_format nv12 -video_size "${CAM_SIZE}" -framerate "${CAM_FPS}" -i "${CAM_IDX}:none" \
  -f avfoundation -itsoffset "$AV_OFFSET" -i ":${AUD_IDX}" \
  -t "$DUR" \
  -filter_complex "$FILTER" \
  -map "[v]" -map 2:a -af "$AFILTER" -r "${OUT_FPS}" -fps_mode cfr \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -b:a 192k "$VID" &
FF=$!

sleep "$WARMUP"
echo
echo "   >>> NOW: in the browser, open DEMO and click the song you want (e.g. Bach) <<<"
echo
wait "$FF"

mv "$VID" "$OUT"
echo "wrote $OUT"
