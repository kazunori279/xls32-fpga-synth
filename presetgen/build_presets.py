"""Full pipeline: render Surge factory targets, match each on our engine, emit the preset bank.

For each of ~16 presets/category: render the Surge .fxp target, run CMA-ES inverse synthesis,
keep the best-matching engine patch. Writes webui/presets_matched.json (name/category/values)
+ a per-preset loss report. Rerunnable.
"""
import os, sys, json, time, importlib
import numpy as np
import engine, params, protocol, search
import loss as stft_loss     # for its SEG flag; the objective itself goes through loss_deep
import loss_deep as loss          # $LOSS selects the distance; "stft" (default) is loss.py verbatim

HERE = os.path.dirname(__file__)
PER_CAT = int(sys.argv[1]) if len(sys.argv) > 1 else 16
BUDGET = int(sys.argv[2]) if len(sys.argv) > 2 else 300
SOURCE = sys.argv[3] if len(sys.argv) > 3 else "nsynth"     # target module: nsynth | freesound
# A bank is only meaningful next to the distance that made it -- losses are not comparable across
# definitions, so a stored `loss` fitted under CDPAM cannot be read against one fitted under STFT.
# A non-default distance therefore writes its own bank file rather than overwriting the shipped
# one: webui/synthspec.py tags each bank with its filename stem, so the browser gets a second
# source tab and the two can be A/B'd by ear, which is the only comparison that settles anything.
LOSSNAME = loss.backend()
STEM = os.environ.get("BANK") or (SOURCE if LOSSNAME == "stft"
                                  else f"{SOURCE}{LOSSNAME.replace('+', '')}")
OUT = os.path.abspath(os.path.join(HERE, "..", "webui", f"presets_{STEM}.json"))
CATS = ["Bass", "Lead", "Pad", "Pluck", "Keys", "Brass", "Strings", "FX"]


def main():
    ns = importlib.import_module(SOURCE)                    # source module: list_targets() + load()
    # The corpus owns the note-on/note-off window; the render and the segment weighting both read
    # it from there, so the two sides of every comparison agree on where the note ends.
    win = protocol.window(ns)
    engine.render(params.preset_from_vec(params.seed_vec("Lead")), gate_s=win[0], tail_s=win[1])
    targets = ns.list_targets(per_cat=PER_CAT)
    # ONLY= restricts the run to named presets, for a pilot that answers a question about the
    # objective without paying for all 128. ONLY_FROM= is the same thing spelled as an existing
    # bank: the shipped bank is 64 of the corpus's 128 slots (consolidate.py), and a re-fit that
    # is meant to replace it has to be over those 64 and not over the 128 they were drawn from.
    only = [n for n in os.environ.get("ONLY", "").split("|") if n]
    src = os.environ.get("ONLY_FROM")
    if src:
        ref = os.path.abspath(os.path.join(HERE, "..", "webui", f"presets_{src}.json"))
        only += [p["name"] for p in json.load(open(ref))["presets"]]
    if only:
        targets = [t for t in targets if t[1] in only]
    print(f"matching {len(targets)} presets (budget={BUDGET}, loss={LOSSNAME}, "
          f"space={params.SPACE}/{params.DIM}d, window={win}, seg={stft_loss.SEG})")
    out, losses = [], []
    t0 = time.time()
    for i, (cat, name, path, note) in enumerate(targets):
        try:
            audio, sr = ns.load(path)
            tprep = loss.prep(audio, sr, window=win)
            preset, mloss, seedloss = search.match(tprep, category=cat, note=note, budget=BUDGET,
                                                   window=win)
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
                   # "targets" names the corpus, not the bank: with $BANK the two can differ, and
                   # every consumer (previews, attack_audit) needs the corpus to find the target.
                   # "space"/"dim": which CCs the search could reach. Two banks fitted at
                   # different widths are not each other's control, and without this the
                   # difference is invisible in the file.
                   "meta": {"count": len(out), "budget": BUDGET, "loss": LOSSNAME,
                            "targets": SOURCE, "window": list(win),
                            "space": params.SPACE, "dim": params.DIM,
                            "seg": bool(stft_loss.SEG)}}, f)
    dt = time.time() - t0
    print(f"\nwrote {len(out)} presets -> {OUT}  ({dt/60:.1f} min)")
    if losses:
        print(f"loss: min {min(losses):.1f}  median {np.median(losses):.1f}  max {max(losses):.1f}")
        worst = sorted(out, key=lambda p: -p["loss"])[:8]
        print("worst matches (engine can't reach):", [(p["name"], p["loss"]) for p in worst])


if __name__ == "__main__":
    main()
