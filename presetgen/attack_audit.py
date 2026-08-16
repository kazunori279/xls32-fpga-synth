"""How far is a bank's *attack* from its targets, measured without asking the loss.

A loss cannot grade a change to itself. Every number in `bank_compare.py` moves when the objective
moves, and a re-fit under a new objective will always look good under that objective. So this
measures two things the loss never sees, on the phase the ear cares about most:

  centroid     spectral centroid over the first 80 ms, as a ratio ours/target. This is "how bright
               is the attack". Measured against the shipped soundfont bank it sits at 0.3-0.6 on
               every struck patch -- our attacks come out one to two octaves too dull, which is
               exactly the metallic transient that goes missing next to the GM sample.
  inharmonic   share of attack energy further than 4% from any integer multiple of f0, as a
               difference ours - target. This is "how bell-like is the attack", and it separates
               the two failure modes: Glockenspiel is 80% inharmonic and needs cross-mod, while
               Clavinet is 2% inharmonic and just needs to be brighter.

Both are ratios/differences against the same target, so they are comparable across objectives,
across banks, and before/after a change. Neither is a listening test; they are the cheap check
that runs between listening tests.

    uv run python presetgen/attack_audit.py [bank ...]        # default: presets_soundfont
"""
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine                                                             # noqa: E402
import protocol                                                           # noqa: E402
from name_audit import note_of                                            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEBUI = os.path.abspath(os.path.join(HERE, "..", "webui"))
ATTACK_S = 0.08           # the transient, not the AD phase: 250 ms already includes the decay
FMAX = 16000              # the engine runs at 32 kHz, so nothing above this is even representable


def _spec(x, sr, t0, t1):
    a = np.asarray(x[int(t0 * sr):int(t1 * sr)], dtype=np.float64)
    if len(a) < 64:
        return None, None
    S = np.abs(np.fft.rfft(a * np.hanning(len(a)), 1 << 16))
    f = np.fft.rfftfreq(1 << 16, 1 / sr)
    m = f < FMAX
    return f[m], S[m]


def measures(x, sr, f0, t0=0.0, t1=ATTACK_S, tol=0.04):
    f, S = _spec(x, sr, t0, t1)
    if f is None:
        return None
    P = S ** 2
    tot = float(P.sum()) + 1e-30
    k = np.maximum(np.round(f / f0), 1)
    near = np.abs(f - k * f0) <= tol * k * f0
    return {"centroid": float((P * f).sum() / tot), "inharm": float(P[~near].sum() / tot)}


def audit(bank_name, per_cat=16):
    import importlib
    path = os.path.join(WEBUI, f"presets_{bank_name}.json")
    d = json.load(open(path))
    presets, meta = d["presets"], d.get("meta") or {}
    src = meta.get("targets", bank_name)
    ns = importlib.import_module(src)
    win = protocol.window(ns)
    targets = {n: p for _, n, p, _ in ns.list_targets(per_cat=per_cat)}
    alias = meta.get("names") or {}

    rows = []
    for p in presets:
        tname = alias.get(p["name"], p["name"])
        if tname not in targets:
            continue
        note = note_of(tname, p["category"])
        f0 = 440 * 2 ** ((note - 69) / 12)
        a, sr = ns.load(targets[tname])
        w = engine.render(p["values"], note=note, gate_s=win[0], tail_s=win[1])
        mt, mo = measures(a, sr, f0), measures(w, engine.SR, f0)
        if not mt or not mo:
            continue
        rows.append({"name": p["name"], "category": p["category"],
                     "centroid_t": mt["centroid"], "centroid_o": mo["centroid"],
                     "centroid_ratio": mo["centroid"] / (mt["centroid"] + 1e-9),
                     "inharm_t": mt["inharm"], "inharm_o": mo["inharm"],
                     "inharm_gap": mo["inharm"] - mt["inharm"]})
    return rows


def report(bank_name, rows):
    # Geometric mean of the ratio, because a bank half as bright and one twice as bright are the
    # same size of error and an arithmetic mean would call the second one worse.
    r = np.array([x["centroid_ratio"] for x in rows])
    gm = float(np.exp(np.mean(np.log(r))))
    err = float(np.exp(np.mean(np.abs(np.log(r)))))
    print(f"\n=== {bank_name}: {len(rows)} presets with a target")
    print(f"  attack centroid ours/target: geo-mean {gm:.3f}   "
          f"typical error x{err:.2f}   too dull on {int((r < 1).sum())}/{len(r)}")
    print(f"  attack inharmonic share:     ours {np.mean([x['inharm_o'] for x in rows])*100:5.1f}%"
          f"   target {np.mean([x['inharm_t'] for x in rows])*100:5.1f}%")
    cats = sorted({x["category"] for x in rows})
    for c in cats:
        sub = [x for x in rows if x["category"] == c]
        cr = np.array([x["centroid_ratio"] for x in sub])
        print(f"    {c:8} n={len(sub):3d}  centroid x{float(np.exp(np.mean(np.log(cr)))):.2f}"
              f"   inharm {np.mean([x['inharm_o'] for x in sub])*100:5.1f}% vs"
              f" {np.mean([x['inharm_t'] for x in sub])*100:5.1f}%")
    return {"geo_mean_centroid_ratio": gm, "typical_error": err, "n": len(rows)}


def main():
    banks = sys.argv[1:] or ["soundfont"]
    out = {}
    engine.render(engine._DEFAULTS, gate_s=protocol.GATE_S, tail_s=protocol.TAIL_S)
    for b in banks:
        rows = audit(b)
        out[b] = {"summary": report(b, rows), "rows": rows}
    json.dump(out, open(os.path.join(HERE, "attack_audit.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
