"""Can each distance find a patch it is told to find? A corpus-free benchmark of the loss itself.

The honest way to compare `loss.py` against a learned distance is human ratings, and that harness
does not exist yet. This is the check that does not need one: take a preset the engine can play
*exactly*, render it, throw the parameters away, and ask each distance to lead CMA-ES back to it.
Ground truth is known, so every run is scored three ways that do not depend on which distance drove
it -- the STFT loss, CDPAM and CLAP all judge every result -- plus the parameter error against the
patch that was hidden.

A distance that cannot recover a patch its own engine produced cannot fit a real sample either, so
this is a floor, not a ranking. Passing it says nothing about how the result *sounds*: that is what
the listening set is for.

    uv sync --extra deepfit
    uv run python presetgen/loss_bench.py [N] [budget] [backends...]
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine                                                              # noqa: E402
import loss as stft_loss                                                   # noqa: E402
import loss_deep                                                           # noqa: E402
import params                                                              # noqa: E402
import search                                                              # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 4
BUDGET = int(sys.argv[2]) if len(sys.argv) > 2 else 200
BACKENDS = sys.argv[3:] or ["stft", "cdpam", "clap", "clap+stft"]
CATS = ["Bass", "Lead", "Pad", "Pluck", "Keys", "Brass", "Strings", "FX"]


def hidden_patches(n, seed=7):
    """n ground-truth vectors: a category seed nudged off its starting point, so the target is
    reachable by construction but is not the point CMA-ES starts from."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        cat = CATS[i % len(CATS)]
        v = np.clip(params.seed_vec(cat) + rng.normal(0, 0.22, params.DIM), 0, 1)
        out.append((cat, v, 48 if cat == "Bass" else 60))
    return out


def score_all(a, target_raw, note):
    """Judge one render against the target under every distance, whichever drove the search."""
    out = {}
    for b in ("stft", "cdpam", "clap"):
        loss_deep.select(b)
        out[b] = float(loss_deep.loss(a, loss_deep.prep(target_raw, engine.SR), a_sr=engine.SR,
                                      b_prepped=True))
    return out


def main():
    engine.render(engine._DEFAULTS, gate_s=search.GATE_S, tail_s=search.TAIL_S)
    tasks = hidden_patches(N)
    rows = {b: [] for b in BACKENDS}
    for cat, tv, note in tasks:
        tgt = engine.render(params.preset_from_vec(tv), note=note,
                            gate_s=search.GATE_S, tail_s=search.TAIL_S)
        for b in BACKENDS:
            loss_deep.select(b)
            t0 = time.time()
            preset, f, seedf = search.match(loss_deep.prep(tgt, engine.SR), cat, note, BUDGET)
            dt = time.time() - t0
            a = engine.render(preset, note=note, gate_s=search.GATE_S, tail_s=search.TAIL_S)
            s = score_all(a, tgt, note)
            s["param_l1"] = float(np.abs(params.vec_from_preset(preset) - tv).mean())
            s["own"], s["own_seed"], s["sec"] = f, seedf, dt
            rows[b].append(s)
            print(f"  {cat:8} {b:10} own {f:8.4f} (seed {seedf:8.4f})  "
                  f"stft {s['stft']:7.2f}  cdpam {s['cdpam']:.4f}  clap {s['clap']:.4f}  "
                  f"|dp| {s['param_l1']:.3f}  {dt:5.1f}s", flush=True)

    print(f"\nmean over {N} hidden patches, budget {BUDGET} "
          f"(all columns lower = closer to the patch that was hidden)")
    print(f"  {'drove the search':18}{'stft':>9}{'cdpam':>9}{'clap':>9}{'param L1':>10}{'sec':>8}")
    for b in BACKENDS:
        r = rows[b]
        print(f"  {b:18}" + "".join(f"{np.mean([x[k] for x in r]):9.3f}"
                                    for k in ("stft", "cdpam", "clap"))
              + f"{np.mean([x['param_l1'] for x in r]):10.3f}"
              + f"{np.mean([x['sec'] for x in r]):8.1f}")


if __name__ == "__main__":
    main()
