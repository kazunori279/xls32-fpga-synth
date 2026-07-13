"""Measure the sim<->board gap on the ACTUAL optimized preset banks (not just probes).

Samples presets across categories from webui/presets_*.json, renders each on the sim and the
board, and reports the spectrogram loss split by feature usage (dry vs FX vs unison) — the real
"does the offline search transfer to hardware?" number. Stop webui/server.py first.
"""
import os, sys, json, glob
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "host")))
import uartaudio as u
import time
import engine, loss
from calibrate import board_capture, NOTE, GATE, TAIL
from validate_hw import recover as drain     # verified-quiet recovery (polls until the board is
#                                              actually silent) — a fixed sleep lets a diverged
#                                              SVF cascade into the next capture (inflates the gap).

PER_CAT = int(os.environ.get("PER_CAT", "4"))     # presets sampled per category
WEBUI = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "webui"))


def load_bank():
    banks = {}
    for path in sorted(glob.glob(os.path.join(WEBUI, "presets_*.json"))):
        src = os.path.basename(path)[len("presets_"):-len(".json")]
        d = json.load(open(path))
        banks[src] = d["presets"] if isinstance(d, dict) else d
    return banks


def sample(presets, per_cat):
    by_cat = {}
    for p in presets:
        by_cat.setdefault(p["category"], []).append(p)
    out = []
    for cat, ps in by_cat.items():
        step = max(1, len(ps) // per_cat)
        out += ps[::step][:per_cat]
    return out


def feat(vals):
    fx = (vals.get("fx", 0) >> 4) & 7
    uni = (vals.get("unison", 0) >> 5) & 3
    if fx:  return "fx"
    if uni: return "unison"
    return "dry"


def main():
    src = os.environ.get("SRC", "soundfont")
    banks = load_bank()
    presets = sample(banks[src], PER_CAT)
    dev, fd = u.open_port(rw=True)
    print(f"board: {dev}   source: {src}   sampled: {len(presets)}")
    engine.render(presets[0]["values"], gate_s=GATE, tail_s=TAIL)   # warm JIT
    rows = []
    for p in presets:
        vals = p["values"]
        sim = engine.render(vals, note=NOTE, gate_s=GATE, tail_s=TAIL)
        drain(fd)                       # clear the previous preset's FX tail first
        brd = board_capture(fd, vals)
        if len(brd) < 4000:
            brd = board_capture(fd, vals)
        d = loss.loss(sim, brd, a_sr=engine.SR, b_sr=u.SR)
        f = feat(vals)
        rows.append((d, f, p["name"], np.sqrt(np.mean(sim**2)), np.sqrt(np.mean(brd**2))))
        print(f"  [{f:6}] {p['name'][:22]:22} loss {d:6.2f}  (sim {rows[-1][3]:.3f} / brd {rows[-1][4]:.3f})", flush=True)
    os.close(fd)
    print("\n--- sim<->board gap by feature ---")
    for f in ("dry", "unison", "fx"):
        g = [r[0] for r in rows if r[1] == f]
        if g:
            print(f"  {f:6}  n={len(g):2}  mean {np.mean(g):6.2f}  median {np.median(g):6.2f}  max {max(g):6.2f}")
    print(f"  ALL     n={len(rows):2}  mean {np.mean([r[0] for r in rows]):6.2f}")
    print("\nReference: preset-matching loss ~9-22; noise-vs-tone ~137.")


if __name__ == "__main__":
    main()
