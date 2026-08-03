"""Measure USB isochronous dropout rate on the Tiliqua UAC2 input.

sd.rec() with default settings showed 16 zero-filled gaps (5.1% of frames) in a
1 s capture at 192 kHz. Sweep latency / blocksize to find a gap-free
configuration -- the verification loop in M25 needs continuous capture.
"""

import queue
import sys

import numpy as np
import sounddevice as sd

dev = next(i for i, d in enumerate(sd.query_devices())
           if "tiliqua" in d["name"].lower() and d["max_input_channels"] > 0)
info = sd.query_devices(dev)
ch = info["max_input_channels"]
print(f"device [{dev}] {info['name']!r} {ch}ch "
      f"low_lat={info['default_low_input_latency']:.4f} "
      f"high_lat={info['default_high_input_latency']:.4f}")


def gaps(buf):
    """Return (n_runs, total_zero_frames) for all-channels-zero runs."""
    z = (buf == 0).all(1).astype(np.int8)
    d = np.diff(np.concatenate(([0], z, [0])))
    return int((d == 1).sum()), int(z.sum())


def run(sr, latency, blocksize, seconds=2.0):
    q = queue.Queue()
    over = [0]

    def cb(indata, frames, time, status):
        if status.input_overflow:
            over[0] += 1
        q.put(indata.copy())

    try:
        with sd.InputStream(device=dev, channels=ch, samplerate=sr,
                            dtype="int32", latency=latency,
                            blocksize=blocksize, callback=cb) as s:
            actual = s.latency
            sd.sleep(int(seconds * 1000))
    except Exception as e:  # noqa: BLE001
        print(f"  sr={sr} lat={latency} bs={blocksize}: FAILED ({e})")
        return

    chunks = []
    while not q.empty():
        chunks.append(q.get())
    if not chunks:
        print(f"  sr={sr} lat={latency} bs={blocksize}: no data")
        return
    buf = np.concatenate(chunks)
    n, tot = gaps(buf)
    got = len(buf)
    want = int(sr * seconds)
    print(f"  sr={sr:6d} lat={str(latency):>6} bs={blocksize:5d} -> "
          f"frames={got:7d}/{want} ({100*got/want:5.1f}%)  "
          f"gaps={n:3d} zeroframes={tot:6d} ({100*tot/max(got,1):5.2f}%)  "
          f"overflows={over[0]}  actual_lat={actual:.4f}")
    return buf


print("\n=== 192 kHz ===")
for lat, bs in (("low", 0), ("high", 0), (0.05, 0), (0.2, 0), ("high", 8192)):
    run(192000, lat, bs)

print("\n=== 48 kHz (the rate XLS32 would actually use) ===")
for lat, bs in (("low", 0), ("high", 0), (0.2, 0), ("high", 4096)):
    run(48000, lat, bs)
