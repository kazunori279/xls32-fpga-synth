"""Full pipeline: render Surge factory targets, match each on our engine, emit the preset bank.

For each of ~16 presets/category: render the Surge .fxp target, run CMA-ES inverse synthesis,
keep the best-matching engine patch. Writes webui/presets_matched.json (name/category/values)
+ a per-preset loss report. Rerunnable.
"""
import os, sys, json, time, importlib
import numpy as np
import engine, loss, params, search

HERE = os.path.dirname(__file__)
PER_CAT = int(sys.argv[1]) if len(sys.argv) > 1 else 16
BUDGET = int(sys.argv[2]) if len(sys.argv) > 2 else 300
SOURCE = sys.argv[3] if len(sys.argv) > 3 else "nsynth"     # target module: nsynth | freesound
OUT = os.path.abspath(os.path.join(HERE, "..", "webui", f"presets_{SOURCE}.json"))
CATS = ["Bass", "Lead", "Pad", "Pluck", "Keys", "Brass", "Strings", "FX"]


def main():
    ns = importlib.import_module(SOURCE)                    # source module: list_targets() + load()
    # warm the JIT
    engine.render(params.preset_from_vec(params.seed_vec("Lead")), gate_s=search.GATE_S, tail_s=search.TAIL_S)
    targets = ns.list_targets(per_cat=PER_CAT)
    print(f"matching {len(targets)} presets (budget={BUDGET})")
    out, losses = [], []
    t0 = time.time()
    for i, (cat, name, path, note) in enumerate(targets):
        try:
            audio, sr = ns.load(path)
            tprep = loss.prep(audio, sr)
            preset, mloss, seedloss = search.match(tprep, category=cat, note=note, budget=BUDGET)
        except Exception as e:
            print(f"  [{i+1}/{len(targets)}] skip {cat}/{name}: {repr(e)[:70]}"); continue
        out.append({"name": name, "category": cat, "values": preset, "loss": round(mloss, 2)})
        losses.append(mloss)
        print(f"  [{i+1}/{len(targets)}] {cat:8} {name:22} loss {seedloss:5.1f} -> {mloss:5.1f}", flush=True)
    # sort within category by ascending loss (best first) so the browser shows the best matches
    order = {c: i for i, c in enumerate(CATS)}
    out.sort(key=lambda p: (order[p["category"]], p["loss"]))
    with open(OUT, "w") as f:
        json.dump({"presets": [{k: p[k] for k in ("name", "category", "values")} for p in out],
                   "meta": {"count": len(out), "budget": BUDGET}}, f)
    dt = time.time() - t0
    print(f"\nwrote {len(out)} presets -> {OUT}  ({dt/60:.1f} min)")
    if losses:
        print(f"loss: min {min(losses):.1f}  median {np.median(losses):.1f}  max {max(losses):.1f}")
        worst = sorted(out, key=lambda p: -p["loss"])[:8]
        print("worst matches (engine can't reach):", [(p["name"], p["loss"]) for p in worst])


if __name__ == "__main__":
    main()
