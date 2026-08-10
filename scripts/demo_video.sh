#!/usr/bin/env bash
# Record a demo MP4: the web UI (screen) + the board (webcam, picture-in-picture) + the
# synth's own audio. Three captures, run side by side and muxed at the end.
#
# The audio used to come from the server (`/api/capture` in LOCAL mode), which no longer exists:
# since M31 the browser owns the board's link and plays the audio itself. So the sound is captured
# from an audio *input* device -- digitally, never a room mic. Two ways, depending on the board:
#
#   Tiliqua -- point AUD_DEV at the board itself. Its UAC2 interface enumerates as an input
#     ("Tiliqua XLS32", 4ch @ 48 kHz), which is the synth's own output before the host touches it.
#     No extra software, and one fewer resampling stage than a loopback. Only ch0/1 are audio:
#     ch2/3 carry the gray-coded audio-clock counter, which AFILTER drops -- see below.
#   Basys 3 (or the fallback) -- a **loopback device**, capturing what the browser plays. BlackHole
#     2ch is free (`brew install blackhole-2ch`); make it the Mac's output, or better, build a
#     Multi-Output Device in Audio MIDI Setup so you can still hear the demo while it records.
#
# WHY THE SOUND IS NOT CAPTURED BY FFMPEG. On macOS, ffmpeg's avfoundation input drops audio in
# whole 512-frame buffers and reports nothing. Measured against the board's own 12.288 MHz counter
# (`scripts/rec_audio.py --check`), 12-15 s captures on one Mac mini:
#
#     ffmpeg -f avfoundation -i ":N" ................ 10-21 % of frames lost, ~10 events/s
#       ... with a second avfoundation input in the      ~90 % lost
#           same process (a screen or camera grab)
#       ... and Chrome holding the device as well ...    ~67 % lost   (the take that shipped)
#     scripts/rec_audio.py (PortAudio, blocksize=0) ... 0.00-0.02 % lost
#
# None of it is visible without the counter: the packets that arrive keep honest wall-clock
# timestamps, so duration, levels and waveform all look right while two thirds of the samples are
# missing. A 125 s take of *Prelude in C* went out that way and only a listener caught it. So the
# sound now comes from `rec_audio.py`, which checks itself, and ffmpeg captures only pictures --
# the screen and the camera in **separate processes**, because two avfoundation inputs in one
# process starve each other whatever they are (it is the input count, not the pixel rate: dropping
# the webcam to 640x480@30 changes nothing).
#
# Prereqs: the UI is open in Chrome with a board connected and POWER pressed -- either the hosted
# panel at https://kazunori279.github.io/xls32-fpga-synth/ (nothing to run) or a local copy served
# with `python3 -m http.server 8765 -d webui/static`. Terminal needs macOS **Screen Recording**,
# **Camera** and **Microphone** permissions (System Settings > Privacy).
#
# Usage:
#   scripts/demo_video.sh [out.mp4]
# Env overrides (see `ffmpeg -f avfoundation -list_devices true -i ""` for the video indices):
#   SCREEN_IDX=2  CAM_IDX=0  AUD_DEV=Tiliqua  DUR=45  CAM_W=480  AFILTER=…
#   AUD_DEV          the audio INPUT -- a substring of its name, or a PortAudio index. The board
#                    itself ("Tiliqua"), or a loopback ("BlackHole"). Names, not indices, because
#                    the indices renumber whenever a device appears: plugging in a pair of AirPods
#                    mid-session moved the board from 1 to 0 while this script was being written.
#                    `uv run scripts/rec_audio.py --device nope --secs 0 --out /dev/null` lists them.
#   SCREEN_IDX/CAM_IDX  avfoundation *video* indices, which renumber the same way -- with a
#                    Tiliqua plugged in and no webcam, screen 0 is video 0, so the defaults below
#                    are both wrong. Always check the list first.
#   CROP=w:h:x:y      crop the screen grab to just the browser window (drop the rest of the
#                     desktop). Get the geometry from the browser: window.screenX/screenY +
#                     (outerHeight-innerHeight) for the content top, innerWidth/innerHeight
#                     for size (× devicePixelRatio on a HiDPI display). Empty = full screen.
#   CAM_CROP=w:h:x:y  crop the webcam to just the board, before it is scaled into the PIP — a
#                     webcam framed on a Eurorack module sees a lot of rack either side of it, and
#                     cropping first means CAM_W is spent on the module instead of the room.
#                     Coordinates are in CAM_SIZE pixels. Empty = the whole frame.
#   CAM_PREVIEW=path  grab a still from the webcam (CAM_CROP applied, if set) and exit without
#                     recording. This is how you find the rectangle: preview, measure, re-run.
#   SCREEN_LATENCY=0.46 CAM_OFFSET=0.39   the three captures start together but their devices do
#                     not. The recorder says when its first buffer landed, so the audio's zero is
#                     known exactly; the two ffmpeg captures need their device start-up measured.
#                     Here the screen delivers its first frame 0.46 s after launch and the webcam
#                     0.85 s, so the audio's first 0.46 s is trimmed and the PIP is delayed by the
#                     0.39 s difference. Measure yours (repeatable to ~10 ms) with
#                       time ffmpeg -f avfoundation -i "<idx>:none" -frames:v 1 -f null -
#                     for each device. Both default to 0 having no effect other than a skew.
#   CAM_SIZE=1280x720 CAM_FPS=60  capture the webcam at a real 60fps (must be a mode the
#                     device supports — list them with an invalid -video_size). OUT_FPS=60
#                     keeps that smoothness in the muxed file.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-demo.mp4}"
SCREEN_IDX="${SCREEN_IDX:-2}"     # avfoundation "Capture screen 0"
CAM_IDX="${CAM_IDX:-0}"           # avfoundation "Logitech StreamCam"
AUD_DEV="${AUD_DEV:-Tiliqua}"     # audio input, by name substring (see above)
DUR="${DUR:-45}"                  # seconds to record (covers ~1–2 loops of a demo song)
CAM_W="${CAM_W:-480}"             # webcam PIP width (px), bottom-right corner
CAM_SIZE="${CAM_SIZE:-1280x720}"  # webcam capture resolution (must be a supported mode)
CAM_FPS="${CAM_FPS:-60}"          # webcam capture frame rate (StreamCam does 60 at 720p/1080p)
OUT_FPS="${OUT_FPS:-60}"          # output frame rate (60 to preserve the webcam's smoothness)
CROP="${CROP:-}"                  # w:h:x:y to crop the screen to the browser window (empty = full)
CAM_CROP="${CAM_CROP:-}"          # w:h:x:y to crop the webcam to just the board (empty = full frame)
SCREEN_LATENCY="${SCREEN_LATENCY:-0.46}"  # screen device start-up; trimmed off the audio
CAM_OFFSET="${CAM_OFFSET:-0.39}"          # webcam start-up minus the screen's; delays the PIP
# Take the first two channels and nothing else. The Tiliqua's UAC2 input is 4 channels: ch0/1 are
# the audio, ch2/3 are the 31-bit gray-coded audio-clock counter the recorder checks itself
# against -- and bit 15 of ch2 is forced high as a never-zero dropout marker, so those two
# channels sit near full scale. Chrome escapes this by asking for 4 and being handed 2; a capture
# gets all four, and without this the counter is encoded into the AAC track and folds into the mix
# on any downmix. On a 2ch loopback input this is the identity. AFILTER=anull disables it.
#
# Then a 20 Hz high-pass, which is not cosmetic. A pulse wave at anything but 50 % duty has a DC
# component -- the Bach patch runs PULSE W 100 of 128, so about 78 % -- and nothing in the digital
# path removes it, so every sounding voice adds an offset proportional to its envelope. Measured on
# a full take of *Prelude in C*: DC +0.30, and **98.7 % of the energy below 5 Hz**, leaving the
# audible band 26 dB down with the headroom spent on something no one can hear. It is not a bug:
# analogue outputs AC-couple this away, and out0/out1 do. The USB tee is a tap *before* that, so
# the capture has to do it instead. It costs level -- the take that shipped came off the tee at
# -4.9 dBFS once the DC and the clicks were out of it -- which is what TARGET_PEAK below buys back.
AFILTER="${AFILTER:-pan=stereo|c0=c0|c1=c1,highpass=f=20:poles=2}"
# Normalise the muxed audio to this peak, in dBFS. Measured after AFILTER and after the head trim,
# on the take itself, so it is the peak that actually reaches the file -- a fixed make-up gain
# cannot be right when the performance includes turning REVERB and CUTOFF up. Set empty to disable.
TARGET_PEAK="${TARGET_PEAK:--1.0}"
MAX_GAIN=24                       # ceiling, in dB: a near-silent take must not be amplified to hiss
WARMUP="${WARMUP:-2}"             # let the camera/screen stream settle before the demo starts

# Picking CAM_CROP blind is guesswork, so offer a still to measure off. `-update 1` overwrites the
# same file every frame, which means the picture you get is the LAST one of the WARMUP window --
# past the exposure and white-balance ramp a webcam's first frames are stuck in.
if [ -n "${CAM_PREVIEW:-}" ]; then
  ffmpeg -hide_banner -loglevel warning -y \
    -f avfoundation -pixel_format nv12 -video_size "${CAM_SIZE}" -framerate "${CAM_FPS}" -i "${CAM_IDX}:none" \
    -t "$WARMUP" -vf "${CAM_CROP:+crop=$CAM_CROP,}null" -update 1 "$CAM_PREVIEW"
  echo "wrote $CAM_PREVIEW — webcam[$CAM_IDX] @${CAM_SIZE}${CAM_CROP:+, cropped to $CAM_CROP}"
  exit 0
fi

SCR=/tmp/demo_screen.mkv          # screen video
CAM=/tmp/demo_cam.mkv             # webcam, cropped and scaled to the PIP
AUD=/tmp/demo_audio.wav           # the synth, from PortAudio
AUD_CLEAN=/tmp/demo_audio_clean.wav   # ... with the clock-drift seams bridged
VID=/tmp/demo_video.mp4
FIFO=/tmp/demo_rec_ready
trap 'rm -f "$FIFO"' EXIT

# Keep the webcam's full frame rate. overlay emits output frames at its main (first) input's
# cadence, so the SCREEN must be a genuine OUT_FPS grid phase-locked with the output — otherwise
# the smooth 60fps webcam is decimated to the screen's rate (choppy). Two things make that work:
#   1. capture the screen natively at OUT_FPS (the `-framerate` on its avfoundation input below), and
#   2. fps=OUT_FPS on the screen branch to guarantee a CFR grid.
# The camera branch does no *temporal* resampling — an fps filter of its own beats against the
# output grid and drops ~1/4 of its frames. Verified: camera stays ~98% fresh at 60fps. crop is
# spatial and per-frame, so it costs nothing on that count; it goes before scale so the pixels
# thrown away are never scaled.
CROPF=""; [ -n "$CROP" ] && CROPF="crop=${CROP},"
CAMF="";  [ -n "$CAM_CROP" ] && CAMF="crop=${CAM_CROP},"

echo "==> recording ${DUR}s of screen[$SCREEN_IDX]${CROP:+ (crop $CROP)} + webcam[$CAM_IDX] @${CAM_SIZE}/${CAM_FPS}fps${CAM_CROP:+ (crop $CAM_CROP)} + audio[$AUD_DEV]"

# The sound first, and on its own clock. It prints READY when its first buffer lands, which is the
# zero the video captures are aligned to; it records SCREEN_LATENCY extra so there is something to
# trim once they catch up. It also exits non-zero if the board's counter says frames went missing.
rm -f "$FIFO"; mkfifo "$FIFO"
AUD_SECS=$(awk -v d="$DUR" -v l="$SCREEN_LATENCY" 'BEGIN{printf "%.3f", d + l + 0.5}')
uv run scripts/rec_audio.py --device "$AUD_DEV" --secs "$AUD_SECS" --out "$AUD" > "$FIFO" &
RECPID=$!
read -r _ < "$FIFO"               # blocks until the recorder's first buffer arrives

# The camera, on its own. Cropped and scaled to the PIP here so the intermediate stays small and
# the overlay pass has nothing left to resize. ultrafast/crf 14 is a scratch file, not the output.
ffmpeg -hide_banner -loglevel warning -y \
  -f avfoundation -pixel_format nv12 -video_size "${CAM_SIZE}" -framerate "${CAM_FPS}" -i "${CAM_IDX}:none" \
  -t "$DUR" -vf "${CAMF}scale=${CAM_W}:-1" -an \
  -c:v libx264 -preset ultrafast -crf 14 -pix_fmt yuv420p "$CAM" &
CAMPID=$!

ffmpeg -hide_banner -loglevel warning -y \
  -f avfoundation -capture_cursor 1 -framerate "${OUT_FPS}" -i "${SCREEN_IDX}:none" \
  -t "$DUR" -vf "${CROPF}fps=${OUT_FPS}" -fps_mode cfr -an \
  -c:v libx264 -preset ultrafast -crf 14 -pix_fmt yuv420p "$SCR" &
SCRPID=$!

sleep "$WARMUP"
echo
echo "   >>> NOW: in the browser, open DEMO and click the song you want (e.g. Bach) <<<"
echo
wait "$CAMPID"
wait "$SCRPID"
if ! wait "$RECPID"; then
  echo "the audio capture failed its own dropout check — see above; nothing was muxed" >&2
  exit 1
fi

# Passing the counter check is not the same as sounding clean. The board's 48 kHz and the host's are
# two free-running clocks, so the host discards a buffer every ~10.4 s to stay in step -- about a
# millisecond, 0.011 % of the take, nowhere near the 0.1 % that would fail it. But each one is a
# step in a sustained tone, and a step is a click: ten of them were plainly audible in a take the
# counter had passed. This bridges them, at the samples ch2/3 say they happened at -- so it repairs
# what the clock did and leaves the performance alone, which matters the moment anyone touches a
# knob while the demo runs. `--channels 0,1` is what gets rebuilt; ch2/3 stay as recorded, because
# they are the evidence.
uv run scripts/declick.py "$AUD" "$AUD_CLEAN" --channels 0,1

# Make-up gain, measured rather than assumed. AFILTER throws away the DC that most of the tee's
# energy is in, so what is left is quiet; how quiet depends on the take, and on a take where
# someone is turning REVERB and CUTOFF it depends on the performance. So measure the peak of
# exactly what will be muxed -- same filter chain, same head trim -- and scale it to TARGET_PEAK.
# volumedetect prints max_volume on stderr and produces no output file, hence -f null.
MUXFILTER="$AFILTER"
if [ -n "$TARGET_PEAK" ]; then
  MAXVOL=$(ffmpeg -hide_banner -nostats -ss "$SCREEN_LATENCY" -i "$AUD_CLEAN" \
             -af "${AFILTER},volumedetect" -f null - 2>&1 |
           sed -n 's/.*max_volume: \(-*[0-9.]*\) dB.*/\1/p' | tail -1)
  if [ -z "$MAXVOL" ]; then
    echo "   (could not measure the peak — leaving the level alone)" >&2
  else
    GAIN=$(awk -v t="$TARGET_PEAK" -v m="$MAXVOL" -v c="$MAX_GAIN" \
               'BEGIN{g=t-m; if (g>c) g=c; printf "%.2f", g}')
    echo "==> peak ${MAXVOL} dBFS -> ${TARGET_PEAK} dBFS (${GAIN} dB make-up)"
    MUXFILTER="${AFILTER},volume=${GAIN}dB"
  fi
fi

echo "==> muxing (audio trimmed ${SCREEN_LATENCY}s, PIP delayed ${CAM_OFFSET}s) -> $VID"
ffmpeg -hide_banner -loglevel warning -y \
  -i "$SCR" -itsoffset "$CAM_OFFSET" -i "$CAM" -ss "$SCREEN_LATENCY" -i "$AUD_CLEAN" \
  -filter_complex "[0:v][1:v]overlay=W-w-24:H-h-24:eof_action=pass[v]" \
  -map "[v]" -map 2:a -af "$MUXFILTER" -r "${OUT_FPS}" -fps_mode cfr -shortest \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart "$VID"

mv "$VID" "$OUT"
# The screen and camera grabs are large and reproducible from nothing; delete them. $AUD is neither.
# It is the only four-channel copy of the take -- the counter lives on ch2/3 and the mux throws them
# away -- so it is the only thing that can re-score the recording or re-run the declicker at a
# different setting. A take that deletes it can only be fixed by playing the whole piece again, which
# is how one two-and-a-half-minute performance was lost.
# Parked beside the video rather than left in /tmp, where the next take would overwrite it.
RAW="${OUT%.*}-raw.wav"
rm -f "$SCR" "$CAM" "$AUD_CLEAN"
mv "$AUD" "$RAW"
echo "   (kept the 4-channel capture as $RAW — delete it once the take is accepted)"

# Belt and braces. The recorder's counter check is the real one, but it only exists on a Tiliqua
# input; on a loopback nothing has verified anything, and a mux mistake can still shorten the
# track. Decoding it and counting what comes out costs a second and catches both.
VSEC=$(ffprobe -v error -select_streams v -show_entries stream=duration -of default=nw=1:nk=1 "$OUT")
ffmpeg -v error -y -i "$OUT" -map 0:a -c:a pcm_s16le -f wav /tmp/demo_audio_check.wav
ASEC=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 /tmp/demo_audio_check.wav)
rm -f /tmp/demo_audio_check.wav
echo "wrote $OUT — video ${VSEC}s, audio ${ASEC}s of samples"
awk -v a="$ASEC" -v v="$VSEC" 'BEGIN {
  if (v > 0 && a < 0.98 * v) {
    printf "WARNING: the audio track is only %.1f%% of the video length — samples went missing.\n", 100*a/v > "/dev/stderr"
    printf "         Do not publish this take; see the note at the top of this script.\n" > "/dev/stderr"
    exit 1
  }
}'
