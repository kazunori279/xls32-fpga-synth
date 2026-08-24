"""Does the UAC2 capture path have the flush-then-pause defect the UART path had? (#9)

On the Basys 3 UART, `tcflush` followed by a pause before the first read loses about 6 kB at the
seam, with a clean dose response over the pause length: 0 ms 0/4, 5 ms 2/4, 50 ms 4/4. The
mechanism was never identified, only the condition. Nobody had checked whether the Tiliqua's UAC2
path does the same thing, which is the open half of #9.

The analogue here is `record_stop` -> pause -> `record_start`. While `_recording` is false the
PortAudio callback still fires and throws `indata` away, so the host is discarding exactly the way
`tcflush` discards, and then resuming after a pause. If something below us drops a chunk when
nobody is draining, the frames lost should land at the *start* of the next capture and should grow
with the pause.

What this deliberately does not do is stop and restart the stream. `usbaudio.py`'s docstring
records that nine open/close cycles wedged the device hard enough to need a power cycle, so the
stream is opened once here too.

    uv run python host/probe_uac2_pause.py
"""
import os
import statistics as st
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))   # repo root, for `boards`

import numpy as np                                                    # noqa: E402

from boards import get_board                                          # noqa: E402
from transport.usbaudio import UsbAudioTransport, _ch16               # noqa: E402

PAUSES_MS = [0, 2, 5, 10, 20, 50, 200]
REPEATS   = 3
SECS      = 1.0
#: A "seam" loss is one that lands in the first 10 ms of the capture, which is where the UART bug
#: put its missing bytes. Losses spread through the whole capture are ordinary dropouts (#7/#48)
#: and are not what this is looking for.
SEAM_S    = 0.010


def capture(t, secs):
    """One capture, returning the alive mask rather than the audio."""
    t.record_start()
    time.sleep(secs)
    time.sleep(0)
    # record_stop() repairs and discards the mask, so read the blocks directly instead.
    time.sleep(0.08)                      # the same DRAIN_S the transport uses
    t._recording = False
    blocks, t._blocks = t._blocks, []
    t._stamps.clear()
    if not blocks:
        return None
    frames = np.concatenate(blocks)
    return (_ch16(frames[:, 2]) & 0x8000) != 0


def describe(alive, sr):
    n = len(alive)
    dead = int((~alive).sum())
    seam = int((~alive[:int(SEAM_S * sr)]).sum())
    return {"frames": n, "dead": dead, "rate": dead / n if n else 0.0, "seam_dead": seam}


def main():
    # Name the board. `get_board()` defaults to basys3 at 32 kHz, and opening the Tiliqua's 48 kHz
    # stream at 32 kHz makes the OS resample: the first run of this probe read a flat 1.56 % of
    # frames "dead" at every pause length, which was the rate mismatch and not the device.
    t = UsbAudioTransport(board=get_board("tiliqua"))
    t.open()
    sr = t.sr
    print(f"device open, sr={sr}, seam window = first {int(SEAM_S * sr)} frames\n")

    # The first capture after open is the startup-backlog seam, the other place the UART bug hit.
    first = describe(capture(t, SECS), sr)
    print(f"first capture after open: {first['frames']} frames, {first['dead']} dead "
          f"({first['rate']*100:.4f} %), {first['seam_dead']} of them in the seam\n")

    print(f"{'pause':>7}  {'frames':>8}  {'dead':>6}  {'rate %':>8}  {'seam dead':>9}")
    rows = {}
    for ms in PAUSES_MS:
        runs = []
        for _ in range(REPEATS):
            time.sleep(ms / 1000.0)
            a = capture(t, SECS)
            if a is None:
                continue
            runs.append(describe(a, sr))
        rows[ms] = runs
        for r in runs:
            print(f"{ms:5d}ms  {r['frames']:8d}  {r['dead']:6d}  {r['rate']*100:8.4f}  "
                  f"{r['seam_dead']:9d}")

    print("\nsummary — seam losses per pause length (this is the #9 question):")
    for ms, runs in rows.items():
        if not runs:
            continue
        seam = [r["seam_dead"] for r in runs]
        rate = [r["rate"] * 100 for r in runs]
        print(f"  {ms:5d} ms  seam {sum(seam):4d} over {len(runs)} runs  "
              f"(max {max(seam)})   whole-capture rate mean {st.mean(rate):.4f} %")

    t.close()
    hit = sum(r["seam_dead"] for runs in rows.values() for r in runs)
    print(f"\ntotal seam losses across every pause length: {hit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
