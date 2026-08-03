"""Does rapid stream open/close degrade capture? Testing the contamination theory.

`docs/TILIQUA_USB_DROPOUTS.md` records that nine consecutive open/close cycles
drove the unit into a state where it returned one block and then stopped, with
delivery collapsing to 1-8%. The archived `probe_gaps.py` sweep -- which is
where the 97.4% and the "blocksize=1024 collapses to ~14%" figures came from --
opens and closes the stream once per (rate, latency, blocksize) combination
with no pause between them. That is exactly the pattern that wedges it.

If this reproduces, the earlier numbers were measuring the wedge, not a
streaming defect, and the wedge itself is the real device-side finding.

Usage:
    uv run boards/tiliqua/probe/probe_wedge.py [--rate 192000] [--cycles 9]
"""

import argparse
import time

import numpy as np
import sounddevice as sd


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rate", type=int, default=192000)
    p.add_argument("--channels", type=int, default=4)
    p.add_argument("--cycles", type=int, default=9)
    p.add_argument("--secs", type=float, default=3.0)
    args = p.parse_args()

    dev = next(i for i, d in enumerate(sd.query_devices())
               if "tiliqua" in d["name"].lower() and d["max_input_channels"] > 0)
    print(f"device [{dev}] {sd.query_devices()[dev]['name']}  rate={args.rate}")

    def measure(tag):
        rows = []

        def cb(indata, frames, time_info, status):
            rows.append((frames, int((indata == 0).all(1).sum()), str(status)))

        with sd.InputStream(device=dev, channels=args.channels,
                            samplerate=args.rate, dtype="int32",
                            blocksize=0, callback=cb):
            sd.sleep(int(args.secs * 1000))

        nf = sum(r[0] for r in rows)
        nz = sum(r[1] for r in rows)
        exp = int(args.rate * args.secs)
        print(f"  {tag:32s} cb={len(rows):4d} frames={nf:8d}/{exp} "
              f"({100*nf/exp:6.2f}%) zeros={100*nz/max(nf,1):.3f}% "
              f"flags={sum(1 for r in rows if r[2])}")
        return 100 * nf / exp

    print("\nbaseline (rested):")
    base = measure("before any churn")

    print(f"\n{args.cycles} rapid open/close cycles, no pause:")
    for i in range(args.cycles):
        try:
            with sd.InputStream(device=dev, channels=args.channels,
                                samplerate=args.rate, dtype="int32",
                                blocksize=0, callback=lambda *a: None):
                sd.sleep(120)
        except Exception as e:
            print(f"  cycle {i}: {type(e).__name__}: {e}")

    print("\nimmediately after churn:")
    post = [measure(f"post-churn {k+1}") for k in range(3)]

    print("\nafter a 5 s rest:")
    time.sleep(5)
    rested = measure("rested 5 s")

    worst = min(post)
    print(f"\nbaseline {base:.2f}%  worst post-churn {worst:.2f}%  "
          f"after rest {rested:.2f}%")
    print("VERDICT: " + ("wedge REPRODUCED -- churn degrades capture"
                         if worst < base - 5 else
                         "no wedge -- churn does not degrade capture here"))


if __name__ == "__main__":
    main()
