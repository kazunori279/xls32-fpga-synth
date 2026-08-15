"""Score two banks fitted under different distances on the same yardsticks.

`build_presets.py` stores the loss that drove the search, and those numbers are not comparable
across definitions -- a bank fitted under "clap+stft" carries stft + 15*clap, so its median looks
worse than an stft bank's by construction. This re-renders every preset, re-loads the target it was
fitted to, and scores the pair under *each* distance separately, so both banks are read on both
yardsticks. Whoever wins its opponent's metric won something.

The banks must come from the same source corpus and the same per-category count, or the name lookup
misses. What this still cannot tell you is which one sounds better: that is the browser's job.

    uv sync --extra deepfit
    uv run python presetgen/bank_compare.py webui/presets_a.json webui/presets_b.json [source]
"""
import json
import os
import sys
import importlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine                                                              # noqa: E402
import loss_deep                                                           # noqa: E402
import search                                                              # noqa: E402
from name_audit import note_of                                             # noqa: E402

CATS = ["Bass", "Lead", "Pad", "Pluck", "Keys", "Brass", "Strings", "FX"]
BANKS = [a for a in sys.argv[1:] if a.endswith(".json")]
SOURCE = ([a for a in sys.argv[1:] if not a.endswith(".json")] or ["soundfont"])[0]
PER_CAT = int(os.environ.get("PER_CAT", 16))


def main():
    ns = importlib.import_module(SOURCE)
    engine.render(engine._DEFAULTS, gate_s=search.GATE_S, tail_s=search.TAIL_S)
    targets = {name: (path, note) for _, name, path, note in ns.list_targets(per_cat=PER_CAT)}

    # Prepare each target once under each distance; the banks share the corpus, so this is the
    # expensive half and it is paid once for both.
    prepped = {}
    for name, (path, note) in targets.items():
        audio, sr = ns.load(path)
        prepped[name] = {b: (loss_deep.select(b), loss_deep.prep(audio, sr))[1]
                         for b in ("stft", "clap")}

    rows = {}
    for path in BANKS:
        bank = json.load(open(path))
        label = os.path.basename(path)
        per = {b: {c: [] for c in CATS} for b in ("stft", "clap")}
        miss = 0
        for p in bank["presets"]:
            if p["name"] not in prepped:
                miss += 1
                continue
            a = engine.render(p["values"], note=note_of(p["name"], p["category"]),
                              gate_s=search.GATE_S, tail_s=search.TAIL_S)
            for b in ("stft", "clap"):
                loss_deep.select(b)
                per[b][p["category"]].append(
                    float(loss_deep.loss(a, prepped[p["name"]][b], a_sr=engine.SR, b_prepped=True)))
        if miss:
            print(f"{label}: {miss} presets had no target in {SOURCE} (skipped)")
        rows[label] = per

    for b, fmt in (("stft", "8.2f"), ("clap", "8.4f")):
        print(f"\nmean {b} distance to the fitted target (lower = closer)")
        print(f"  {'bank':34}" + "".join(f"{c[:5]:>8}" for c in CATS) + f"{'all':>9}")
        for label, per in rows.items():
            allv = [v for c in CATS for v in per[b][c]]
            print(f"  {label:34}"
                  + "".join(format(np.mean(per[b][c]) if per[b][c] else float("nan"), fmt)
                            for c in CATS)
                  + format(np.mean(allv), fmt))


if __name__ == "__main__":
    main()
