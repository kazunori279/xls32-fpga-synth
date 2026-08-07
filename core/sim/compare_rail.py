#!/usr/bin/env python3
"""Is core/sim/tb_preset_rail.v actually playing the preset?

The testbench reporting "clean: no rail" is only evidence if the thing it captured is the preset
and not, say, the mild filter `validate_hw.recover()` leaves behind. This scores the dumped
simulation capture against `engine.render()` with the same multi-resolution loss `validate_hw.py`
uses for its model-vs-hardware agreement number, so the rig is judged on the project's own metric
rather than on a peak level looking plausible.

A matched loss in the same range as validate_hw's hardware figure means the simulator and the
board are playing the same preset, and "no rail in simulation" can be trusted.

    uv run --extra presetgen python core/sim/compare_rail.py Brightness /tmp/rail_Brightness.txt
"""
import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, os.path.join(ROOT, "presetgen"))
import engine  # noqa: E402
import loss as lossmod  # noqa: E402
from calibrate import GATE, NOTE, TAIL  # noqa: E402

BOARD_SR = 32000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset")
    ap.add_argument("dump", help="sample dump from tb_preset_rail.v +dump=")
    ap.add_argument("--bank", default="soundfont")
    a = ap.parse_args()

    bank = json.load(open(os.path.join(ROOT, "webui", f"presets_{a.bank}.json")))["presets"]
    hit = [p for p in bank if p["name"] == a.preset]
    if not hit:
        sys.exit(f"no preset named {a.preset!r} in presets_{a.bank}.json")
    vals = hit[0]["values"]

    cap = np.loadtxt(a.dump, dtype=np.float32) / 32768.0
    sim = engine.render(vals, note=NOTE, gate_s=GATE, tail_s=TAIL)
    d = lossmod.loss(lossmod.prep(sim, engine.SR), lossmod.prep(cap, BOARD_SR),
                     a_prepped=True, b_prepped=True)
    print(f"{a.preset}: {len(cap)} samples  rtl-sim peak {np.max(np.abs(cap)):.3f} "
          f"rms {np.sqrt(np.mean(cap**2)):.3f} | model peak {np.max(np.abs(sim)):.3f} "
          f"rms {np.sqrt(np.mean(sim**2)):.3f} | loss {d:.2f}")


if __name__ == "__main__":
    main()
