"""Decide whether the zero-filled gaps are delivered-but-empty or never-delivered.

Records, per callback: frame count, PortAudio status flags, the ADC timestamp,
and how many of the delivered frames are all-zero. If the device simply
under-delivers, total frames < expected and status flags stay clear. If
PortAudio is zero-filling underruns, status flags fire.
"""

import numpy as np
import sounddevice as sd

devs = sd.query_devices()
dev = next(i for i, d in enumerate(devs)
           if "tiliqua" in d["name"].lower() and d["max_input_channels"] > 0)
SR, CH, SEC = 48000, 4, 10.0

rows, chunks = [], []


def cb(indata, frames, time_info, status):
    z = int((indata == 0).all(1).sum())
    rows.append((time_info.inputBufferAdcTime, frames, z, str(status)))
    chunks.append(indata.copy())


with sd.InputStream(device=dev, channels=CH, samplerate=SR, dtype="int32",
                    blocksize=0, callback=cb) as s:
    print(f"opened sr={s.samplerate} blocksize={s.blocksize} "
          f"latency={s.latency:.4f}")
    sd.sleep(int(SEC * 1000))

adc = np.array([r[0] for r in rows])
nf = np.array([r[1] for r in rows])
nz = np.array([r[2] for r in rows])
flagged = [r for r in rows if r[3]]

print(f"callbacks={len(rows)}  total_frames={nf.sum()}  "
      f"expected={int(SR*SEC)}  ({100*nf.sum()/(SR*SEC):.2f}%)")
print(f"frames/callback: unique={np.unique(nf)[:8]} "
      f"min={nf.min()} max={nf.max()}")
print(f"zero frames inside delivered data: {nz.sum()} "
      f"({100*nz.sum()/max(nf.sum(),1):.3f}%)")
print(f"callbacks with status flags: {len(flagged)}")
for r in flagged[:10]:
    print(f"   frames={r[1]} zeros={r[2]} status={r[3]}")

d = np.diff(adc)
print(f"\nADC timestamp delta: median={np.median(d)*1000:.3f} ms "
      f"min={d.min()*1000:.3f} max={d.max()*1000:.3f}")
expected_dt = nf[:-1] / SR
jump = d - expected_dt
big = np.where(jump > 0.5e-3)[0]
print(f"callbacks preceded by a timeline jump >0.5 ms: {len(big)}")
if len(big):
    print(f"  jump sizes (ms): {(jump[big][:10]*1000).round(3)}")
    print(f"  at times (s):    {(adc[big][:10]-adc[0]).round(3)}")
    if len(big) > 2:
        iv = np.diff(adc[big])
        print(f"  interval between jumps (s): median={np.median(iv):.4f} "
              f"min={iv.min():.4f} max={iv.max():.4f}")

# Which callbacks contained the zeros?
idx = np.where(nz > 0)[0]
print(f"\ncallbacks containing zeros: {len(idx)} of {len(rows)}")
if len(idx):
    print(f"  their zero counts: {nz[idx][:12]}")
    print(f"  their frame counts: {nf[idx][:12]}")
    print(f"  fully-zero callbacks: {int((nz[idx]==nf[idx]).sum())}")
