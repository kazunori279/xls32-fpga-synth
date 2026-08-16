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

**Read `param L1` next to the two columns beside it, never alone.** It is a mean over a vector whose
select dimensions are quantised on the way out of `vec_from_preset` -- an exactly correct recovery
still lands half a bin from a continuous ground truth -- so part of it is the ruler and not the
miss. `floor` is that part, measured by round-tripping the hidden vector through the preset dict and
back. And `seed L1` is where the search *started*, the same distance from the same truth, which is
the only number that says whether CMA-ES moved toward the patch or away from it. Issue #17 was
opened on `param L1` alone.

Budget takes a comma list, and the sweep runs in one process because the point is one question --
is the budget the binding constraint? -- and because `seed=1` is fixed in `search.match`, so budget
300 and budget 3000 are the same trajectory stopped at two places, not two experiments.

    uv sync --extra deepfit
    uv run python presetgen/loss_bench.py [N] [budget[,budget...]] [backends...]
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
BUDGETS = [int(b) for b in (sys.argv[2] if len(sys.argv) > 2 else "200").split(",")]
BACKENDS = sys.argv[3:] or ["stft", "cdpam", "clap", "clap+stft"]
CATS = ["Bass", "Lead", "Pad", "Pluck", "Keys", "Brass", "Strings", "FX"]
DIM_NAMES = params.KNOBS + params.SEL_IDS


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
    # Render every target once, not once per budget: they do not depend on the search at all, and at
    # three budgets x four backends that is the difference between 8 renders and 96.
    targets = [engine.render(params.preset_from_vec(tv), note=nt,
                             gate_s=search.GATE_S, tail_s=search.TAIL_S) for _, tv, nt in tasks]
    rows = {(b, bg): [] for b in BACKENDS for bg in BUDGETS}
    for bg in BUDGETS:
        for (cat, tv, note), tgt in zip(tasks, targets):
            # Both baselines, measured against the same truth by the same yardstick as the result.
            # `floor` is what a perfect answer still scores, because preset_from_vec() rounds each
            # select to an option index and vec_from_preset() hands back that bin's midpoint.
            floor = float(np.abs(params.vec_from_preset(params.preset_from_vec(tv)) - tv).mean())
            sv = params.seed_vec(cat)                      # already select-quantised, so comparable
            seed_l1 = float(np.abs(sv - tv).mean())
            for b in BACKENDS:
                loss_deep.select(b)
                t0 = time.time()
                preset, f, seedf = search.match(loss_deep.prep(tgt, engine.SR), cat, note, bg)
                dt = time.time() - t0
                a = engine.render(preset, note=note, gate_s=search.GATE_S, tail_s=search.TAIL_S)
                s = score_all(a, tgt, note)
                gv = params.vec_from_preset(preset)
                s["param_l1"] = float(np.abs(gv - tv).mean())
                s["seed_l1"], s["floor"] = seed_l1, floor
                s["per_dim"] = np.abs(gv - tv)             # kept for the breakdown below
                s["per_dim_seed"] = np.abs(sv - tv)
                s["own"], s["own_seed"], s["sec"] = f, seedf, dt
                rows[(b, bg)].append(s)
                print(f"  b{bg:<5} {cat:8} {b:10} own {f:8.4f} (seed {seedf:8.4f})  "
                      f"stft {s['stft']:7.2f}  cdpam {s['cdpam']:.4f}  clap {s['clap']:.4f}  "
                      f"|dp| {s['param_l1']:.3f} (seed {seed_l1:.3f}, floor {floor:.3f})  "
                      f"{dt:5.1f}s", flush=True)

    print(f"\nmean over {N} hidden patches (all columns lower = closer to the patch that was hidden)")
    print(f"  {'budget':>7}  {'drove the search':18}{'stft':>9}{'cdpam':>9}{'clap':>9}"
          f"{'param L1':>10}{'seed L1':>9}{'floor':>8}{'sec':>8}")
    for bg in BUDGETS:
        for b in BACKENDS:
            r = rows[(b, bg)]
            print(f"  {bg:>7}  {b:18}"
                  + "".join(f"{np.mean([x[k] for x in r]):9.3f}" for k in ("stft", "cdpam", "clap"))
                  + f"{np.mean([x['param_l1'] for x in r]):10.3f}"
                  + f"{np.mean([x['seed_l1'] for x in r]):9.3f}"
                  + f"{np.mean([x['floor'] for x in r]):8.3f}"
                  + f"{np.mean([x['sec'] for x in r]):8.1f}")

    # Which coordinates the search actually recovers. The mean hides this: if half the vector is
    # nailed and half is noise, that reads the same as a uniformly mediocre fit, and the two call for
    # opposite fixes (clamp the hopeless dims vs. spend more budget everywhere). `got - seed` is the
    # only signed number here -- negative is movement toward the hidden patch.
    bg = BUDGETS[-1]
    for b in BACKENDS:
        r = rows[(b, bg)]
        got = np.mean([x["per_dim"] for x in r], axis=0)
        sd = np.mean([x["per_dim_seed"] for x in r], axis=0)
        order = np.argsort(got - sd)
        print(f"\nper-dimension |dp| at budget {bg}, driven by {b} (sorted by what the search bought)")
        print(f"  {'param':10}{'seed':>8}{'got':>8}{'delta':>9}")
        for i in order:
            print(f"  {DIM_NAMES[i]:10}{sd[i]:8.3f}{got[i]:8.3f}{got[i] - sd[i]:+9.3f}")


if __name__ == "__main__":
    main()
