#!/usr/bin/env python3
"""M28 exit check: does the CV input track 1 V/oct across five octaves?

Requires the **CV bitstream** (``XLS32_VARIANT=cv bash boards/tiliqua/build.sh``) and **one patch
cable, out2 → in0**. Nothing else: the board generates its own sweep, plays it, and records it.

  * out2 carries a DC level the host sets over CC102 (``gateware/cvin.py``, ``CvTestRamp``).
  * the cable returns it to in0, where ``CvIn`` converts it to a note plus a pitch bend on MIDI
    channel 4 -- and holds the gate on, because in1 is unpatched, so the note drones.
  * the USB tee carries the resulting audio back, and this FFTs it.

**What is being graded is the slope, not the tuning.** Neither converter is calibrated in a
non-SoC bitstream, so both ends carry 86-116 mV of DC (docs/TILIQUA_PORT.md:140). That is a
constant transposition -- it moves every point by the same number of cents and falls out of the
fit's intercept. A *gain* error does not, and that is the thing 1 V/oct is a claim about. So the
report leads with cents-per-volt against the ideal 1200, and with the worst residual around the
fitted line; the absolute pitch at 0 V is printed but not asserted on.

The residual is the interesting number. It is where a converter non-linearity would show up, and
also where the converter's share of ``test_cvin.py``'s 0.99-cent arithmetic error lands -- that sim
proves the maths in ``CvIn`` and can prove nothing about the silicon in front of it, which is the
whole reason this exists as well.

    uv run boards/tiliqua/check_cv.py
    uv run boards/tiliqua/check_cv.py --steps 11 --secs 0.6
"""

import argparse
import math
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "host"))
os.environ.setdefault("XLS32_BOARD", "tiliqua")

sys.path.insert(0, os.path.join(_ROOT, "boards", "tiliqua", "gateware"))

import numpy as np                                                    # noqa: E402

from boards import get_board                                         # noqa: E402
from check_loop import SKIP_FRACTION, dominant_hz                    # noqa: E402
from cv_proto import BASE_NOTE, CC_RAMP, RAMP_STEP                   # noqa: E402
from synth import cc                                                 # noqa: E402
from transport.base import open_transport                            # noqa: E402

COUNTS_PER_VOLT = 4000                  # ASQ full scale 32768 = 8.192 V
VOLTS_PER_STEP = RAMP_STEP / COUNTS_PER_VOLT
MAX_STEP = int(5.0 / VOLTS_PER_STEP)    # the top of the five octaves M28 has to hold

# "Within a few cents", spent on the converters. test_cvin.py already holds the arithmetic to
# 0.99 cents against a 2.0 budget, so this is the hardware's share and then some.
TOL_SLOPE_CENTS = 12.0                  # on 1200 cents/V -- 1%
TOL_RESIDUAL_CENTS = 8.0


def sounded_hz(t, secs):
    """Record for `secs` and return the peak frequency, skipping the envelope attack."""
    t.record_start()
    time.sleep(secs)
    s = t.record_stop()
    if len(s) < 4096:
        sys.exit(f"FAIL: captured only {len(s)} samples -- is the tee running?")
    body = s[int(len(s) * SKIP_FRACTION):]
    return dominant_hz(body, get_board().sr), max(abs(v) for v in s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=21, help="sweep points across 0-5 V")
    ap.add_argument("--secs", type=float, default=1.0, help="capture length per point")
    ap.add_argument("--settle", type=float, default=0.25,
                    help="pause after each CC102 before recording; covers the retrigger")
    args = ap.parse_args()

    board = get_board()
    # Coprime-ish with the 60 semitones in the sweep, so the points do not all land on exact
    # semitones. On an exact-semitone grid CvIn's bend is never exercised, and the check would
    # pass while saying nothing about two thirds of the converter -- the same trap test_cvin.py
    # documents at its own sweep.
    values = [round(i * MAX_STEP / (args.steps - 1)) for i in range(args.steps)]

    t = open_transport(board).open()
    rows = []
    try:
        clock = t.clock_note() if hasattr(t, "clock_note") else None
        if clock:
            text, clock_ok = clock
            print(text)
            if not clock_ok:
                sys.exit("FAIL: the audio clock is wrong; see check_loop.py for the recovery "
                         "ritual. Every pitch below would be scaled by the same wrong ratio.")

        t.send_midi(cc(CC_RAMP, 0))
        time.sleep(0.5)
        for v in values:
            t.send_midi(cc(CC_RAMP, v))
            time.sleep(args.settle)
            hz, peak = sounded_hz(t, args.secs)
            rows.append((v * VOLTS_PER_STEP, hz, peak))
        t.send_midi(cc(CC_RAMP, 0))
    finally:
        t.close()

    volts = np.array([r[0] for r in rows])
    hz = np.array([r[1] for r in rows])
    peaks = np.array([r[2] for r in rows])

    silent = [f"{v:.2f} V" for v, p in zip(volts, peaks) if p < 800]
    if silent:
        sys.exit(f"FAIL: silent at {', '.join(silent)} -- is out2 patched to in0? Without a "
                 f"cable in in0,\n  CvIn emits nothing at all, by design (cvin.py jack gating).")

    # Cents against the pitch 0 V should give: BASE_NOTE, times two per volt. The intercept is
    # free precisely because the uncalibrated DC offset lives in it.
    base_hz = 440.0 * 2 ** ((BASE_NOTE - 69) / 12.0)
    cents = 1200 * np.log2(hz / base_hz)
    slope, intercept = np.polyfit(volts, cents, 1)
    resid = cents - (slope * volts + intercept)

    print(f"\n  {'volts':>6s} {'Hz':>9s} {'cents':>9s} {'resid':>7s}")
    for v, f, c, r in zip(volts, hz, cents, resid):
        print(f"  {v:6.2f} {f:9.2f} {c:9.1f} {r:+7.2f}")

    worst = float(np.abs(resid).max())
    err = slope - 1200.0
    print(f"\n  slope    {slope:.1f} cents/V  ({err:+.1f} against 1200, {100*err/1200:+.2f}%)")
    print(f"  offset   {intercept:+.1f} cents at 0 V  "
          f"(= {intercept/1200:+.3f} V of uncalibrated DC, not graded)")
    print(f"  residual worst {worst:.2f} cents over {volts[-1]:.2f} V")

    fails = []
    if abs(err) > TOL_SLOPE_CENTS:
        fails.append(f"slope off by {err:+.1f} cents/V, tolerance ±{TOL_SLOPE_CENTS:.0f}")
    if worst > TOL_RESIDUAL_CENTS:
        fails.append(f"worst residual {worst:.2f} cents, tolerance {TOL_RESIDUAL_CENTS:.0f}")
    if fails:
        sys.exit("FAIL: " + "; ".join(fails))
    print(f"\nPASS: 1 V/oct tracks within {worst:.2f} cents across "
          f"{volts[-1]:.2f} octaves.")


if __name__ == "__main__":
    main()
