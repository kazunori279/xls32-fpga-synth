"""Apples-to-apples capture probe, matched to the vendor's own repro run.

Written to compare directly against the numbers Sebastian Holzapfel (apf.audio)
measured on his Linux and macOS machines against XBEAM v1.2.1 at 192 kHz. His
runs came back 100.0% / 0.00% zeros (Linux) and 100.5% / 0.00% (macOS 15,
Darwin 24.6.0). This records the same quantities plus the two things his
results showed our earlier probe was missing:

  * WHERE the zeros are. His Linux run had 46 zero frames, all bunched at the
    very start of the stream, which is benign startup settling rather than
    dropout. Our earlier probe only counted them, so we could not make that
    distinction. This one reports the position of every zero run.
  * The FULL jump-size array. His healthy Linux run produced 93 timeline jumps
    of exactly 1.0 ms each, which shows the jump *count* is not diagnostic on
    its own -- only the size distribution is. Our earlier probe truncated to
    the first 10.

Usage:
    uv run boards/tiliqua/probe/probe_compare.py                 # 192 kHz, 10 s
    uv run boards/tiliqua/probe/probe_compare.py --rate 48000
    uv run boards/tiliqua/probe/probe_compare.py --blocksize 1024
    uv run boards/tiliqua/probe/probe_compare.py --sweep         # blocksize sweep
    uv run boards/tiliqua/probe/probe_compare.py --label "short cable"

Every run appends a one-line summary to probe_compare.log so that cable and
blocksize variations can be compared afterwards without re-reading scrollback.
"""

import argparse
import json
import pathlib
import time

import numpy as np
import sounddevice as sd

LOG = pathlib.Path(__file__).with_name("probe_compare.log")


def find_device():
    devs = sd.query_devices()
    for i, d in enumerate(devs):
        if "tiliqua" in d["name"].lower() and d["max_input_channels"] > 0:
            return i, d["name"]
    raise SystemExit("no Tiliqua capture device found")


def capture(dev, rate, chans, secs, blocksize):
    """Run one capture, returning per-callback records and the audio itself."""
    rows, chunks = [], []

    def cb(indata, frames, time_info, status):
        rows.append((time_info.inputBufferAdcTime, frames, str(status)))
        chunks.append(indata.copy())

    with sd.InputStream(device=dev, channels=chans, samplerate=rate,
                        dtype="int32", blocksize=blocksize, callback=cb) as s:
        opened = (float(s.samplerate), s.blocksize, float(s.latency))
        sd.sleep(int(secs * 1000))

    return rows, chunks, opened


def runs_of_true(mask):
    """Yield (start, length) for each contiguous True run in a bool array."""
    if not mask.any():
        return []
    edges = np.diff(np.concatenate(([0], mask.view(np.int8), [0])))
    starts = np.where(edges == 1)[0]
    ends = np.where(edges == -1)[0]
    return list(zip(starts.tolist(), (ends - starts).tolist()))


def analyse(rows, chunks, rate, chans, secs, opened, label):
    adc = np.array([r[0] for r in rows])
    nf = np.array([r[1] for r in rows])
    flagged = sum(1 for r in rows if r[2])
    expected = int(rate * secs)
    delivered = int(nf.sum())

    audio = np.concatenate(chunks) if chunks else np.zeros((0, chans), np.int32)
    zero_mask = (audio == 0).all(1)
    nzero = int(zero_mask.sum())

    print(f"\n=== {label} ===")
    print(f"opened sr={opened[0]} blocksize={opened[1]} latency={opened[2]:.4f}")
    print(f"callbacks={len(rows)}  total_frames={delivered}  expected={expected}"
          f"  ({100*delivered/expected:.2f}%)")
    print(f"frames/callback: unique={np.unique(nf)[:8]} min={nf.min()} max={nf.max()}")
    print(f"zero frames: {nzero} ({100*nzero/max(delivered,1):.3f}%)")
    print(f"callbacks with status flags: {flagged}")

    # --- WHERE are the zeros? This is the test Seb used to clear his own run. ---
    zr = runs_of_true(zero_mask)
    startup_frames = rate // 10  # first 100 ms
    if zr:
        after = [(s, n) for s, n in zr if s >= startup_frames]
        in_startup = nzero - sum(n for s, n in zr if s >= startup_frames)
        print(f"zero runs: {len(zr)}  "
              f"({in_startup} frames in the first 100 ms, "
              f"{nzero - in_startup} after)")
        print(f"  run lengths: min={min(n for _, n in zr)} "
              f"median={int(np.median([n for _, n in zr]))} "
              f"max={max(n for _, n in zr)}")
        print(f"  first 12 runs (start_frame, length): {zr[:12]}")
        if after:
            spacing = np.diff([s for s, _ in after])
            if len(spacing):
                print(f"  spacing between post-startup runs (frames): "
                      f"median={int(np.median(spacing))} min={spacing.min()} "
                      f"max={spacing.max()}")
        print("  VERDICT: " + ("benign -- all zeros are startup settling"
                               if not after else
                               f"NOT startup -- {len(after)} runs occur mid-stream"))
    else:
        print("zero runs: none")

    # --- Timeline jumps, full array retained. ---
    d = np.diff(adc)
    jump = d - nf[:-1] / rate
    big = np.where(jump > 0.5e-3)[0]
    print(f"\nADC delta: median={np.median(d)*1000:.3f} ms "
          f"min={d.min()*1000:.3f} max={d.max()*1000:.3f}")
    print(f"timeline jumps >0.5 ms: {len(big)}")
    sizes = (jump[big] * 1000).round(3)
    if len(big):
        print(f"  size (ms): median={np.median(sizes):.3f} min={sizes.min():.3f} "
              f"max={sizes.max():.3f}")
        print(f"  all sizes (ms): {np.sort(sizes)[::-1].tolist()}")
        if len(big) > 2:
            iv = np.diff(adc[big])
            print(f"  interval (s): median={np.median(iv):.4f} min={iv.min():.4f} "
                  f"max={iv.max():.4f}")

    rec = {
        "label": label, "rate": rate, "secs": secs,
        "blocksize_req": opened[1], "latency": round(opened[2], 4),
        "callbacks": len(rows), "delivered": delivered, "expected": expected,
        "delivered_pct": round(100 * delivered / expected, 2),
        "zero_frames": nzero,
        "zero_pct": round(100 * nzero / max(delivered, 1), 3),
        "zero_runs": len(zr),
        "zero_runs_after_startup": len([1 for s, _ in zr if s >= startup_frames]),
        "flagged": flagged,
        "jumps": len(big),
        "jump_ms_median": float(np.median(sizes)) if len(big) else None,
        "jump_ms_max": float(sizes.max()) if len(big) else None,
    }
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rate", type=int, default=192000)
    p.add_argument("--channels", type=int, default=4)
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--blocksize", type=int, default=0)
    p.add_argument("--label", default="")
    p.add_argument("--sweep", action="store_true",
                   help="sweep blocksize 128..2048 as the vendor suggests")
    args = p.parse_args()

    dev, name = find_device()
    print(f"device [{dev}] {name}")

    if args.sweep:
        results = []
        for bs in (0, 128, 256, 512, 1024, 2048):
            lbl = f"{args.label or 'sweep'} bs={bs or 'auto'}"
            rows, chunks, opened = capture(dev, args.rate, args.channels,
                                           args.seconds, bs)
            results.append(analyse(rows, chunks, args.rate, args.channels,
                                   args.seconds, opened, lbl))
            time.sleep(1.0)  # the unit wedges on rapid open/close; give it room
        print("\n=== sweep summary ===")
        print(f"{'blocksize':>10} {'delivered':>10} {'zeros':>8} {'jumps':>6}")
        for r in results:
            print(f"{r['blocksize_req'] or 'auto':>10} "
                  f"{r['delivered_pct']:>9.2f}% {r['zero_pct']:>7.3f}% "
                  f"{r['jumps']:>6}")
    else:
        lbl = args.label or f"{args.rate//1000}k bs={args.blocksize or 'auto'}"
        rows, chunks, opened = capture(dev, args.rate, args.channels,
                                       args.seconds, args.blocksize)
        analyse(rows, chunks, args.rate, args.channels, args.seconds, opened, lbl)

    print(f"\nappended to {LOG}")


if __name__ == "__main__":
    main()
