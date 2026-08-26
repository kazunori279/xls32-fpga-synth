"""Record every attached module's audio while the web UI keeps playing it.

CoreAudio lets a second process open a capture device Chrome already holds, so this taps the same
UAC2 streams the page is listening to rather than the machine's speaker output -- no loopback
device needed, and each board lands in its own file. Channels 0/1 are the audio; 2/3 carry the
cycle counter (`transport/usbaudio.py`) and are dropped. The `*2` undoes the board's 6 dB Eurorack
output pad, the same scaling `usbaudio.py`'s `record_stop` and `transport.js`'s `attachAudio` use,
so levels match everything in `test/`.

    uv run python host/record_rig.py                    # until Ctrl-C
    uv run python host/record_rig.py --secs 30          # fixed length
    uv run python host/record_rig.py --devs 3,5         # only these PortAudio indices
    uv run python host/record_rig.py --out ~/take       # ~/take-board1.wav, -board2.wav, ...

Audio streams to disk as it arrives rather than being held in RAM, and the writing happens on its
own thread: a `wave.writeframes` that blocks inside the PortAudio callback *is* a late callback,
and a late callback is the one thing this rig treats as a hardware fault (#9, #49). SIGTERM and
SIGINT both close the files cleanly. SIGKILL does not -- the wav headers keep their zero length.

**Files of different lengths mean frames went missing, not that the boards drifted apart.** Board
clocks differ by tens of ppm, which is milliseconds over an hour; a USB dropout costs seconds. The
missing audio is *cut out*, not zero-filled, so everything after the gap shifts earlier and the
file ends short -- align by inserting silence at the splice, not by padding the end. Read
`/tmp/usb_watch.log` for the take's window to find where and why (`test/README.md`).
"""
import argparse
import os
import queue
import signal
import sys
import threading
import time
import wave

import numpy as np
import sounddevice as sd

RATE = 48000
PAD = 2                      # undo the board's 6 dB Eurorack output pad


def tiliqua_devices():
    return [i for i, d in enumerate(sd.query_devices())
            if "Tiliqua" in d["name"] and d["max_input_channels"] >= 2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, help="stop after this long (default: run until signalled)")
    ap.add_argument("--devs", help="comma-separated PortAudio indices (default: every Tiliqua)")
    ap.add_argument("--out", default="/tmp/xls32", help="path prefix (default /tmp/xls32)")
    a = ap.parse_args()

    devs = [int(s) for s in a.devs.split(",")] if a.devs else tiliqua_devices()
    if not devs:
        raise SystemExit("no Tiliqua capture device — is a module attached?")

    paths = {d: f"{a.out}-board{n}.wav" for n, d in enumerate(devs, 1)}
    writers, frames, peaks = {}, {d: 0 for d in devs}, {d: 0 for d in devs}
    for d in devs:
        w = wave.open(paths[d], "wb")
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        writers[d] = w

    q = queue.Queue()
    stop = threading.Event()

    def writer():
        while True:
            item = q.get()
            if item is None:
                return
            d, pcm = item
            writers[d].writeframes(pcm.tobytes())
            frames[d] += len(pcm)

    t = threading.Thread(target=writer, daemon=True)
    t.start()

    streams = []
    for d in devs:
        def cb(indata, nframes, tinfo, status, _d=d):
            x = indata[:, :2].astype(np.int64) * PAD
            peaks[_d] = max(peaks[_d], int(np.abs(x).max()))
            q.put((_d, np.clip(x >> 16, -32768, 32767).astype("<i2")))
        s = sd.InputStream(device=d, channels=2, dtype="int32", samplerate=RATE,
                           blocksize=0, callback=cb)
        s.start()
        streams.append(s)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    started = time.strftime("%H:%M:%S")
    how = f"{a.secs:g}s" if a.secs else "until SIGTERM or Ctrl-C"
    print(f"recording audio{devs} -> {a.out}-board*.wav  ({how}, started {started}, "
          f"pid {os.getpid()})", flush=True)
    if a.secs:
        stop.wait(a.secs)
    else:
        while not stop.is_set():
            stop.wait(1.0)          # wake periodically so the signal handler's set() is seen

    for s in streams:
        s.stop()
        s.close()
    q.put(None)
    t.join()
    for d in devs:
        writers[d].close()

    stopped = time.strftime("%H:%M:%S")
    print(f"\nstopped {stopped} (started {started})")
    for n, d in enumerate(devs, 1):
        pk = peaks[d] / 2 ** 31
        print(f"  audio[{d}] -> {paths[d]}  {frames[d] / RATE:.4f}s  peak {pk * 100:.1f}%"
              + ("  CLIPPED" if pk >= 1.0 else ""))
    if not any(frames.values()):
        print("  nothing arrived on any device", file=sys.stderr)
        return
    # A few tenths of a second of spread is the streams starting and stopping a callback apart
    # (~85 ms per block here). A re-enumeration costs well over a second, so anything past SKEW is
    # worth looking up rather than living with.
    SKEW = 0.5
    spread = (max(frames.values()) - min(frames.values())) / RATE
    if spread > SKEW:
        print(f"\n  lengths differ by {spread:.4f}s, past the {SKEW:g}s of start/stop skew — "
              f"that is a dropout,\n  not clock drift. Check /tmp/usb_watch.log for "
              f"{started}–{stopped}.", file=sys.stderr)


if __name__ == "__main__":
    main()
