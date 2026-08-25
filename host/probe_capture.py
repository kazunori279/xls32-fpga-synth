"""Which attached module is losing USB capture frames, and is it losing them alone?

Every capture device is opened at once and read over the same window, so a device that comes up
short can be compared against its neighbours at the same instant rather than across runs. That
comparison is the whole point of the tool, because the two known causes of missing frames look
identical in a single device's numbers:

- **A host stall** (#49) is host-wide by construction. The kernel handles USB enumeration
  synchronously, PortAudio's callback runs past its block period, and every open stream is late
  together. It cannot pick one device out of three.
- **Something on one link** -- the module, the hub port or the cable -- shows up on one device while
  the others read clean in the same window (#51).

So: all three short in the same rounds means look at the host, and one short alone means look at
that link. A sequential table cannot tell them apart, and reading one into the other is how #49
collected three retracted hardware explanations in a day.

Missing frames are counted the way `transport/usbaudio.py` counts them, on the same 31-bit cycle
counter the gateware stamps into channels 2 and 3, because a second implementation of that
arithmetic would be a second thing to be wrong (see `_ch16` and #48). `gap_rate` cannot see any of
this: a dropped frame arrives as zeros and holds its slot, while a frame that never arrived leaves
nothing behind and the capture is simply short.

    uv run python host/probe_capture.py            # 10 rounds over every Tiliqua attached
    uv run python host/probe_capture.py 24         # longer, when the rate is low
    XLS32_PROBE_DEVS=3,5 uv run python host/probe_capture.py

**Read `/tmp/usb_watch.log` over the window this prints before concluding anything.** A quiet log is
what makes the host explanation checkable rather than merely unlikely; the rule and the evidence
behind it are in `test/README.md`.

PortAudio indices are not identity. Every module reports the same USB serial (#50), the indices move
when anything is re-plugged, and this tool has no way to name the box behind one. Re-establish that
by ear -- `rig.py --identify` -- after every re-plug, or a table comparing configurations is
comparing nothing.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np                                                    # noqa: E402
import sounddevice as sd                                              # noqa: E402

from transport.usbaudio import CYCLES_PER_FRAME, _ch16                # noqa: E402

SECS = 1.2                  # long enough to hold several callbacks, short enough to run 10 of them
CHANNELS = 4                # ch2/ch3 carry the counter; 2 is not enough to measure anything here
RATE = 48000


def tiliqua_devices():
    """Every Tiliqua capture device, by PortAudio index, or whatever `XLS32_PROBE_DEVS` names."""
    spec = os.environ.get("XLS32_PROBE_DEVS")
    if spec:
        return [int(s) for s in spec.split(",") if s.strip()]
    return [i for i, d in enumerate(sd.query_devices())
            if "Tiliqua" in d["name"] and d["max_input_channels"] >= CHANNELS]


class Probe:
    """One device's stream, capturing only between `arm()` and `read()`.

    The stream stays open across every round. Opening one per round would measure the first-capture
    transient instead -- PortAudio's delivery has not settled then, and `usbaudio.py` documents the
    same effect biasing its own rate estimate 3% low.
    """

    def __init__(self, dev):
        self.dev = dev
        self.blocks, self.stamps, self.on = [], [], False
        self.stream = sd.InputStream(device=dev, channels=CHANNELS, dtype="int32",
                                     samplerate=RATE, blocksize=0, callback=self._cb)
        self.stream.start()

    def _cb(self, indata, nframes, tinfo, status):
        if self.on:
            self.blocks.append(indata.copy())
            self.stamps.append(tinfo.inputBufferAdcTime)

    def arm(self):
        self.blocks, self.stamps, self.on = [], [], True

    def read(self):
        """(missing frames, audio clock in MHz) for the window just closed."""
        self.on = False
        if len(self.blocks) < 2:
            return None, float("nan")
        frames = np.concatenate(self.blocks)
        live = np.flatnonzero((_ch16(frames[:, 2]) & 0x8000) != 0)
        if len(live) < 2:
            return None, float("nan")
        word = (_ch16(frames[:, 2]) & 0x7FFF) | ((_ch16(frames[:, 3]) & 0xFFFF) << 15)
        # Signed wrap, and only positive jumps: the count is latched crossing into the frame's own
        # clock domain, so a boundary reads one cycle early a few hundred times a second.
        step = ((np.diff(word[live].astype(np.int64)) + (1 << 30)) % (1 << 31)) - (1 << 30)
        short = (step - np.diff(live) * CYCLES_PER_FRAME) // CYCLES_PER_FRAME
        sizes = np.asarray([len(b) for b in self.blocks], dtype=float)
        spf = float(np.median(np.diff(np.asarray(self.stamps, dtype=float)) / sizes[:-1]))
        lo, hi = int(live[0]), int(live[-1])
        seconds = (hi - lo) * spf
        cycles = int(word[hi] - word[lo]) % (1 << 31)
        # An inflated clock is not a second symptom. Missing frames shorten the index span the cycle
        # count is divided by, so a reading far off 12.29 MHz is the same event seen twice.
        mhz = cycles / seconds / 1e6 if seconds > 0 else float("nan")
        return int(short[short > 0].sum()), mhz

    def close(self):
        self.stream.stop()
        self.stream.close()


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    devs = tiliqua_devices()
    if not devs:
        raise SystemExit("no Tiliqua capture device with 4 input channels — is a module attached, "
                         "and has the browser panel released it? A page holding a device at 2 "
                         "channels makes this open fail with PaErrorCode -9998.")
    probes = [Probe(d) for d in devs]
    time.sleep(0.6)

    print(f"\n{rounds} rounds of {SECS}s, {len(devs)} stream(s) open together\n")
    print("  round  " + "  ".join(f"audio[{d}]  miss     MHz" for d in devs))
    hits = {d: 0 for d in devs}
    started = time.strftime("%H:%M:%S")
    for r in range(rounds):
        for p in probes:
            p.arm()
        time.sleep(SECS)
        cells = []
        for p in probes:
            miss, mhz = p.read()
            if miss:
                hits[p.dev] += 1
            cells.append(f"{'-' if miss is None else miss:>15}  {mhz:6.2f}")
        print(f"  {r + 1:>5}  " + "  ".join(cells))
        time.sleep(0.3)
    finished = time.strftime("%H:%M:%S")

    for p in probes:
        p.close()

    print("\n  rounds losing frames: "
          + ", ".join(f"audio[{d}] {hits[d]}/{rounds}" for d in devs))
    if len(devs) > 1:
        bad = [d for d in devs if hits[d]]
        if not bad:
            print("  every device clean — nothing to attribute")
        elif len(bad) == len(devs):
            print("  every device short in the same rounds — that shape is the host, not a link")
        else:
            print(f"  only audio[{', '.join(map(str, bad))}] short, its neighbours clean in the "
                  "same windows — a host stall cannot do that; look at the module, port and cable")
    print(f"  window {started}–{finished} — read /tmp/usb_watch.log over exactly this range "
          "before believing any of it")


if __name__ == "__main__":
    main()
