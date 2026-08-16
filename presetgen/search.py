"""CMA-ES inverse synthesis: find engine params whose render best matches a target spectrogram.

match() is a single CMA-ES run over the [0,1]^params.DIM vector (params.py), seeded from a
per-category region, with a guard against silent/degenerate patches. DIM is 22 by default and
$SPACE widens it to 25, 26 or 29; nothing here has to know which, but note that budget buys less
per dimension, so an equal-budget run at a wider space is the only fair control for one.

Benchmarking (8 targets/category, equal-budget A/B) showed the loss is BUDGET-limited, not
local-minima-limited: at budget 900 a single run beat per-waveform multi-start (which starved
each run) AND continuous-space restarts. So the effective knob is per-run budget; a single
well-converged run is best.

That benchmark also said "diminishing returns past ~900", and **that half is wrong**. It was run
on corpus targets, where no patch is exactly right, so the loss it plateaued at was the engine's
reach and not the optimizer's. On targets the engine can play exactly, the return has no knee at
all: `loss_bench.py 8 300,800,3000` under clap+stft goes 8.99 -> 3.35 -> 1.81 on the STFT column
and 0.049 -> 0.035 -> 0.022 on CLAP, monotone. The shipped `budget=800` is a cost decision, not a
converged one (`loss_bench_budget.json`, issue #17).

What that extra budget does NOT buy is the right parameters. The same sweep measures error against
a known ground truth and it does not move -- 0.190 -> 0.181 while the loss falls 5x -- and it stays
worse than the seed the run started from in 44 of 48 runs, including one that reached loss 0.000.
The map from these 22 CCs to a 1.9 s render is many-to-one under every distance we have, so `match`
returns *a* patch that sounds like the target and never *the* patch. Restarts do not help a search
that already found the global optimum.
"""
import numpy as np
import cma
import engine, params
# $LOSS picks the distance: "stft" (default) dispatches to loss.py unchanged, so the numbers and
# the banks are exactly what they were; "cdpam"/"clap"/"cdpam+stft"/"clap+stft" are the learned
# alternatives. See loss_deep.py -- none of them is known to be better yet.
import loss_deep as loss
import protocol

# Re-exported, not defined here: the window is a property of the corpus being fitted, not of the
# optimizer, and having two files own a copy of it is what put the target's note-off 100 ms away
# from the render's. See protocol.py. These two names stay for the callers that already read them.
GATE_S, TAIL_S = protocol.GATE_S, protocol.TAIL_S
SILENT = 1e3                         # score for a silent/degenerate patch


def _objective(vec, target, note, window=None):
    return _objective_batch([vec], target, note, window)[0]


def _objective_batch(vecs, target, note, window=None):
    """Score a whole CMA-ES generation at once. The FFT loss does not care, but a learned distance
    runs one model call for the population instead of one per candidate, which is where its cost
    would otherwise land."""
    gate_s, tail_s = window or (GATE_S, TAIL_S)
    renders, live = [], []
    for i, v in enumerate(vecs):
        a = engine.render(params.preset_from_vec(v), note=note, gate_s=gate_s, tail_s=tail_s)
        if np.sqrt(np.mean(a * a)) >= 1e-4:          # silent patch -> reject, do not embed it
            renders.append(a); live.append(i)
    out = np.full(len(vecs), SILENT)
    if renders:
        out[live] = loss.dists(renders, target, a_sr=engine.SR, b_prepped=True)
    return out


def match(target, category="Lead", note=60, budget=800, seed=1, window=None):
    x0 = list(params.seed_vec(category))
    es = cma.CMAEvolutionStrategy(x0, 0.30, {
        "bounds": [0.0, 1.0], "maxfevals": budget, "verbose": -9, "seed": seed})
    best_v, best_f = x0, _objective(x0, target, note, window)   # include the seed itself
    while not es.stop():
        xs = es.ask()
        # The whole generation in one call, which is what _objective_batch exists for -- scoring
        # one at a time went through it a population of 1 at a time and never used it. Worth ~8%
        # and no more: CLAP on CPU is compute-bound, so 13 clips cost 13 clips' FLOPs whether they
        # arrive together or not. Results are unchanged (a re-fit of Crystal reproduces the shipped
        # bank's 30.79 exactly); this is the honest shape, not a speedup.
        fs = list(_objective_batch(xs, target, note, window))
        es.tell(xs, fs)
        i = int(np.argmin(fs))
        if fs[i] < best_f:
            best_v, best_f = xs[i], fs[i]
    return params.preset_from_vec(best_v), best_f, _objective(x0, target, note, window)


if __name__ == "__main__":
    import os, importlib
    src = importlib.import_module(os.environ.get("SRC", "soundfont"))
    B = int(os.environ.get("B", "800"))
    win = protocol.window(src)
    engine.render(params.preset_from_vec(params.seed_vec("Lead")), gate_s=win[0], tail_s=win[1])
    for cat, name, path, note in src.list_targets(per_cat=1):
        a, sr = src.load(path); tp = loss.prep(a, sr, window=win)
        _, f, s = match(tp, cat, note, B, window=win)
        print(f"  {cat:8} {name[:22]:22} seed {s:6.2f} -> matched {f:6.2f}")
