"""CMA-ES inverse synthesis: find engine params whose render best matches a target spectrogram.

match() is a single CMA-ES run over the 23-dim [0,1] vector (params.py), seeded from a
per-category region, with a guard against silent/degenerate patches.

Benchmarking (8 targets/category, equal-budget A/B) showed the loss is BUDGET-limited, not
local-minima-limited: at budget 900 a single run beat per-waveform multi-start (which starved
each run) AND continuous-space restarts. So the effective knob is per-run budget (diminishing
returns past ~900); a single well-converged run is best. Remaining loss beyond that is the
engine's expressive reach (see the loss-driven roadmap), not the optimizer.
"""
import numpy as np
import cma
import engine, loss, params

GATE_S, TAIL_S = 1.6, 0.3            # render window (sample targets hold a while)


def _objective(vec, target, note):
    preset = params.preset_from_vec(vec)
    a = engine.render(preset, note=note, gate_s=GATE_S, tail_s=TAIL_S)
    if np.sqrt(np.mean(a * a)) < 1e-4:               # silent patch -> reject
        return 1e3
    return loss.loss(a, target, a_sr=engine.SR, b_prepped=True)


def match(target, category="Lead", note=60, budget=800, seed=1):
    x0 = list(params.seed_vec(category))
    es = cma.CMAEvolutionStrategy(x0, 0.30, {
        "bounds": [0.0, 1.0], "maxfevals": budget, "verbose": -9, "seed": seed})
    best_v, best_f = x0, _objective(x0, target, note)   # include the seed itself
    while not es.stop():
        xs = es.ask()
        fs = [_objective(x, target, note) for x in xs]
        es.tell(xs, fs)
        i = int(np.argmin(fs))
        if fs[i] < best_f:
            best_v, best_f = xs[i], fs[i]
    return params.preset_from_vec(best_v), best_f, _objective(x0, target, note)


if __name__ == "__main__":
    import os, importlib
    src = importlib.import_module(os.environ.get("SRC", "soundfont"))
    B = int(os.environ.get("B", "800"))
    engine.render(params.preset_from_vec(params.seed_vec("Lead")), gate_s=GATE_S, tail_s=TAIL_S)
    for cat, name, path, note in src.list_targets(per_cat=1):
        a, sr = src.load(path); tp = loss.prep(a, sr)
        _, f, s = match(tp, cat, note, B)
        print(f"  {cat:8} {name[:22]:22} seed {s:6.2f} -> matched {f:6.2f}")
