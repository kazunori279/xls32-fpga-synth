#!/usr/bin/env python3
"""Record an audio input to a WAV through PortAudio, and prove no samples went missing.

`scripts/demo_video.sh` uses this instead of letting ffmpeg capture the sound, because on macOS
ffmpeg's avfoundation input drops audio and says nothing about it. Measured against the Tiliqua's
own 12.288 MHz counter (see `--check` below), over 15 s captures on one Mac mini:

    ffmpeg -f avfoundation -i ":N"                     ~10.7 % of frames lost, ~10 events/s
      ... with a second avfoundation input in the      ~90 %  lost
          same process (a screen or camera grab)
      ... and with Chrome holding the device too       ~67 %  lost   (the take that shipped)
    this script (PortAudio, blocksize=0)                0.00-0.02 % lost

Passing the check is not the same as sounding clean, and the gap between the two is smaller than
it looks. The residual 0.011 % is not random, and it is not the host: it is the tee FIFO in our own
gateware (`boards/tiliqua/gateware/top.py:310`), 16 entries deep, written once per codec frame off
the board's `clk0` and read at whatever rate the host's USB SOF asks for. Two crystals, no rate
control, 110-123 ppm apart on the two takes measured here -- so the FIFO's ~0.33 ms of slack is
gone every ~10.4 s and a run of ~60 frames is dropped, by design, because the tee is forbidden from
stalling the codec. That is far under the 0.1 % this script rejects at, and every one is an audible
click, because a millisecond cut out of a sustained tone is a step. `scripts/declick.py` bridges
them; `scripts/demo_video.sh` runs it before the mux. This script counts the damage and does not
repair it.

Two things follow. The loss is an artefact of *recording*, not of playing: `dry` reaches the codec
whatever the tee does, so `out0`/`out1` never had a click in them. And the quantum is the FIFO's,
not the host's -- ~60 frames, not the 512-frame buffers ffmpeg loses, which is how the two failure
modes tell themselves apart. Neither leaves a trace a casual check can see: the packets that do
arrive keep honest wall-clock timestamps, so duration, levels and waveform all look normal.

`host/transport/usbaudio.py` already carried the matching note -- "blocksize=0, PortAudio picks;
forcing 1024 loses 86% of frames" -- which is the ffmpeg failure from the other side: it is the
fixed block size that kills it, and ffmpeg's is fixed.

Usage:
    uv run scripts/rec_audio.py --secs 45 --out /tmp/demo_audio.wav
    uv run scripts/rec_audio.py --device "BlackHole" --secs 45 --out /tmp/a.wav --no-check

Prints `READY` on stdout the moment the first buffer arrives -- `demo_video.sh` waits for that
line before starting the video capture, so the two are aligned to the device's own start, not to
whenever Python finished importing numpy.
"""

import argparse
import queue
import sys
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

# The Tiliqua's UAC2 input is 4 channels: ch0/1 audio, ch2/3 a 31-bit gray-coded counter clocked
# by the audio domain -- ch2 holds bits 0..14 with bit 15 forced high as an "alive" marker, ch3
# holds bits 15..30. See boards/tiliqua/gateware/top.py. One audio frame is 48 kHz and the domain
# is 12.288 MHz, so consecutive frames must differ by exactly 256 ticks; anything larger is
# frames that never arrived, and the gap says exactly how many.
TICKS_PER_FRAME = 256
ALIVE_BIT = 0x8000
# The board's own gap rate is ~0.001% (docs/TILIQUA_USB_DROPOUTS.md). Anything at or above a
# tenth of a percent is the host losing buffers, not the link.
LOSS_FAIL_PCT = 0.1


def pick_device(spec):
    """Resolve an index, or the first input device whose name contains `spec`."""
    devices = sd.query_devices()
    if spec is not None:
        try:
            return int(spec)
        except ValueError:
            pass
    needle = (spec or "Tiliqua").lower()
    for i, d in enumerate(devices):
        if needle in d["name"].lower() and d["max_input_channels"] > 0:
            return i
    raise SystemExit(
        f"no input device matching {needle!r}. Inputs seen:\n"
        + "\n".join(f"  [{i}] {d['name']} ({d['max_input_channels']}ch)"
                    for i, d in enumerate(devices) if d["max_input_channels"] > 0)
    )


def _median3(x):
    """Three-point median. Kills isolated single-frame outliers, preserves real steps."""
    if len(x) < 3:
        return x
    out = x.copy()
    out[1:-1] = np.median(np.stack([x[:-2], x[1:-1], x[2:]]), axis=0)
    return out


def counter_loss(a):
    """(lost_frames, expected_frames, events, positions) from the board's counter, or None.

    `positions` are sample indices: frames went missing between index i and i+1. That is the whole
    reason they are returned -- `scripts/declick.py` repairs exactly those, and a detector that has
    to *infer* where the gap was from the waveform cannot tell a dropped millisecond from a MIDI CC
    arriving; on a take where the panel was played while the demo ran it chased 40 knob moves.
    """
    if a.ndim < 2 or a.shape[1] < 4 or np.mean((a[:, 2].astype(np.uint16) & ALIVE_BIT) != 0) < 0.99:
        return None                                   # not a Tiliqua tee; nothing to check against
    lo = (a[:, 2].astype(np.uint16) & (ALIVE_BIT - 1)).astype(np.int64)
    hi = a[:, 3].astype(np.uint16).astype(np.int64)
    # The two halves are latched a cycle apart, so ch3 occasionally reads one off at a carry --
    # a single-frame ±32768 spike in the reassembled counter, which would otherwise be scored as
    # 128 lost frames. It is always isolated, and a real gap is a step, so a 3-median removes the
    # one without touching the other. ch2 gets no such filter: it wraps every 128 frames and a
    # median would smear the wrap.
    delta = np.diff(lo | (_median3(hi) << 15))
    # The 31-bit counter itself wraps every 175 s; that shows up as one enormous delta.
    # Round to whole frames rather than truncating: the reassembly is not perfect (the median and
    # ch2's 128-frame wrap disagree at a few boundaries), which leaves deltas a handful of ticks
    # either side of 256. Those are noise in the arithmetic, not audio -- a frame that never
    # arrived costs a clean multiple of 256. Rounding scores them 0 and keeps the count honest.
    idx = np.flatnonzero(delta < 1 << 30)
    gaps = np.round(delta[idx] / TICKS_PER_FRAME).astype(np.int64) - 1
    keep = gaps > 0
    lost = int(gaps[keep].sum())
    return lost, len(a) + lost, int(keep.sum()), idx[keep]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device", help="input device index, or a substring of its name "
                                    "(default: the first input matching 'Tiliqua')")
    p.add_argument("--secs", type=float, help="seconds to record")
    p.add_argument("--out", help="WAV to write")
    p.add_argument("--rate", type=int, default=48000)
    p.add_argument("--no-check", action="store_true",
                   help="skip the counter check (non-Tiliqua inputs skip it automatically)")
    p.add_argument("--check", metavar="WAV",
                   help="score an existing 4-channel WAV against the counter and exit — how the "
                        "ffmpeg numbers in the docstring were measured")
    args = p.parse_args()

    # Scoring a file someone else recorded is the same arithmetic, and it is what proved ffmpeg
    # was the problem: capture the same device both ways, run this on each.
    if args.check:
        a, sr = sf.read(args.check, dtype="int16", always_2d=True)
        res = counter_loss(a)
        if res is None:
            sys.exit(f"{args.check}: no board counter on these channels — nothing to check")
        lost, expected, events, _ = res
        pct = 100.0 * lost / expected if expected else 0.0
        print(f"{args.check}: {lost} frames lost of {expected} ({pct:.3f}%) in {events} events "
              f"— {lost / sr:.2f}s of audio missing")
        return

    if args.secs is None or args.out is None:
        p.error("--secs and --out are required unless --check is given")

    dev = pick_device(args.device)
    info = sd.query_devices(dev)
    channels = info["max_input_channels"]

    blocks = queue.SimpleQueue()
    first = None

    def cb(indata, frames, tinfo, status):
        nonlocal first
        if first is None:
            first = time.monotonic()
            print("READY", flush=True)
        blocks.put(indata.copy())

    print(f"recording {args.secs}s from [{dev}] {info['name']} ({channels}ch @ {args.rate})",
          file=sys.stderr)
    # blocksize=0 lets PortAudio choose, which is the whole point: a fixed block size is what
    # makes this device shed buffers (see the module docstring).
    #
    # int32, not int16, even though the stream is 16-bit: asking PortAudio for int16 puts a
    # dithered conversion in the way, and while ±1 LSB is nothing to the audio it is fatal to the
    # counter -- ch3 carries counter bits 15..30, so a wobbling LSB there is ±32768 ticks and the
    # gap check reads it as 128 lost frames on almost every frame. Taking int32 and rounding down
    # to 16 bits ourselves keeps both exact.
    with sd.InputStream(device=dev, channels=channels, dtype="int32",
                        samplerate=args.rate, blocksize=0, callback=cb):
        while first is None:
            time.sleep(0.005)
        time.sleep(args.secs)

    chunks = []
    while not blocks.empty():
        chunks.append(blocks.get())
    raw = np.concatenate(chunks)[: int(args.secs * args.rate)]
    a = np.clip((raw.astype(np.int64) + 0x8000) >> 16, -32768, 32767).astype(np.int16)
    sf.write(args.out, a, args.rate, subtype="PCM_16")
    print(f"wrote {args.out} — {len(a)} frames ({len(a) / args.rate:.3f}s)", file=sys.stderr)

    if args.no_check:
        return
    res = counter_loss(a)
    if res is None:
        print("no board counter on this input — sample loss NOT verified", file=sys.stderr)
        return
    lost, expected, events, _ = res
    pct = 100.0 * lost / expected if expected else 0.0
    print(f"counter check: {lost} frames lost of {expected} ({pct:.3f}%) in {events} events",
          file=sys.stderr)
    if pct >= LOSS_FAIL_PCT:
        sys.exit(f"FAIL: {pct:.2f}% of the audio never arrived. Do not publish this take.")


if __name__ == "__main__":
    main()
