"""Single-open capture that records PortAudio status flags per callback.

Distinguishes host-side underflow (PortAudio starved by the OS) from
device-side starvation (device simply did not deliver packets).
"""

import numpy as np
import sounddevice as sd

devs = sd.query_devices()
dev = next(i for i, d in enumerate(devs)
           if "tiliqua" in d["name"].lower() and d["max_input_channels"] > 0)
SR, CH, SEC, BS = 192000, 4, 3.0, 1024

chunks, flags, calls = [], [], [0]


def cb(indata, frames, time, status):
    calls[0] += 1
    if status:
        flags.append((calls[0], frames, str(status)))
    chunks.append(indata.copy())


with sd.InputStream(device=dev, channels=CH, samplerate=SR, dtype="int32",
                    blocksize=BS, latency="high", callback=cb) as s:
    print(f"opened: samplerate={s.samplerate} blocksize={s.blocksize} "
          f"latency={s.latency:.4f} dtype={s.dtype}")
    sd.sleep(int(SEC * 1000))

buf = np.concatenate(chunks) if chunks else np.zeros((0, CH), np.int32)
z = (buf == 0).all(1).astype(np.int8)
d = np.diff(np.concatenate(([0], z, [0])))
starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]

print(f"callbacks={calls[0]}  frames={len(buf)} (expected {int(SR*SEC)})")
print(f"gaps={len(starts)}  zeroframes={int(z.sum())} "
      f"({100*z.mean() if len(buf) else 0:.3f}%)")
print(f"callbacks with status flags: {len(flags)}")
for c, f, st in flags[:15]:
    print(f"  cb#{c} frames={f} status={st}")

if len(starts):
    lens = ends - starts
    print(f"gap len: min={lens.min()} max={lens.max()} "
          f"median={int(np.median(lens))}  multiple_of_blocksize="
          f"{bool(np.all(lens % BS == 0))}")
    print(f"gap starts (first 8): {starts[:8]}")
    print(f"gap start % blocksize: {(starts[:8] % BS)}")

np.save("build/tiliqua/probe_status.npy", buf)
