"""Score two banks fitted under different distances on the same yardsticks.

`build_presets.py` stores the loss that drove the search, and those numbers are not comparable
across definitions -- a bank fitted under "clap+stft" carries stft + 15*clap, so its median looks
worse than an stft bank's by construction. This re-renders every preset, re-loads the target it was
fitted to, and scores the pair under *each* distance separately, so both banks are read on both
yardsticks. Whoever wins its opponent's metric won something.

Means are not enough on their own. Two banks over the same targets are a PAIRED sample -- "Strings 1
is hard" is a property of Strings 1 and it lands on both sides -- so the second half of the report
is per-preset: how many slots each bank wins against the first one listed, with an exact two-sided
sign test. A bank can carry a better mean on two outliers while losing the majority of its slots,
and for a bank the majority is what a browser scroll actually meets.

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
import protocol                                                            # noqa: E402
from name_audit import note_of                                             # noqa: E402

CATS = ["Bass", "Lead", "Pad", "Pluck", "Keys", "Brass", "Strings", "FX"]
BANKS = [a for a in sys.argv[1:] if a.endswith(".json")]
SOURCE = ([a for a in sys.argv[1:] if not a.endswith(".json")] or ["soundfont"])[0]
PER_CAT = int(os.environ.get("PER_CAT", 16))


def sign_test(wins, losses):
    """Exact two-sided sign test on the slots that moved. Ties carry no information about which
    bank is better, so they are dropped rather than split -- splitting them would manufacture
    evidence out of presets where nothing happened."""
    n = wins + losses
    if n == 0:
        return 1.0
    from scipy.stats import binomtest
    return float(binomtest(wins, n, 0.5, alternative="two-sided").pvalue)


def main():
    ns = importlib.import_module(SOURCE)
    # The corpus's window, not the global one. They happen to agree for soundfont, but reading it
    # from here is the whole reason protocol.py exists: a render and the target it is scored
    # against must end their note at the same moment or the comparison is of two different notes.
    win = protocol.window(ns)
    engine.render(engine._DEFAULTS, gate_s=win[0], tail_s=win[1])
    targets = {name: (path, note) for _, name, path, note in ns.list_targets(per_cat=PER_CAT)}

    # Prepare each target once under each distance; the banks share the corpus, so this is the
    # expensive half and it is paid once for both.
    prepped = {}
    for name, (path, note) in targets.items():
        audio, sr = ns.load(path)
        prepped[name] = {b: (loss_deep.select(b), loss_deep.prep(audio, sr, window=win))[1]
                         for b in ("stft", "clap")}

    rows, byname = {}, {}
    for path in BANKS:
        bank = json.load(open(path))
        label = os.path.basename(path)
        per = {b: {c: [] for c in CATS} for b in ("stft", "clap")}
        one = {b: {} for b in ("stft", "clap")}
        miss = 0
        for p in bank["presets"]:
            if p["name"] not in prepped:
                miss += 1
                continue
            a = engine.render(p["values"], note=note_of(p["name"], p["category"]),
                              gate_s=win[0], tail_s=win[1])
            for b in ("stft", "clap"):
                loss_deep.select(b)
                v = float(loss_deep.loss(a, prepped[p["name"]][b], a_sr=engine.SR, b_prepped=True))
                per[b][p["category"]].append(v)
                one[b][p["name"]] = v
        if miss:
            print(f"{label}: {miss} presets had no target in {SOURCE} (skipped)")
        rows[label], byname[label] = per, one

    for b, fmt in (("stft", "8.2f"), ("clap", "8.4f")):
        print(f"\nmean {b} distance to the fitted target (lower = closer)")
        print(f"  {'bank':34}" + "".join(f"{c[:5]:>8}" for c in CATS) + f"{'all':>9}")
        for label, per in rows.items():
            allv = [v for c in CATS for v in per[b][c]]
            print(f"  {label:34}"
                  + "".join(format(np.mean(per[b][c]) if per[b][c] else float("nan"), fmt)
                            for c in CATS)
                  + format(np.mean(allv), fmt))

    if len(BANKS) < 2:
        return
    ref = os.path.basename(BANKS[0])
    # Same per-backend precision as the tables above: clap distances live in the third decimal,
    # and a shared "%+8.3f" prints every real clap difference as +0.000.
    for b, fmt in (("stft", "+8.3f"), ("clap", "+8.5f")):
        print(f"\npaired against {ref} ({b}): per-preset wins on the slots the two share")
        for label in list(byname)[1:]:
            shared = [n for n in byname[label][b] if n in byname[ref][b]]
            d = [byname[ref][b][n] - byname[label][b][n] for n in shared]      # >0 = label closer
            w, l = sum(x > 0 for x in d), sum(x < 0 for x in d)
            p = sign_test(w, l)
            print(f"  {label:34} {w:3d}W {l:3d}L {len(d)-w-l:2d}=  of {len(d):3d}"
                  f"   mean delta {format(np.mean(d), fmt)}   sign test p={p:.4f}"
                  f"{'  *' if p < 0.05 else ''}")


if __name__ == "__main__":
    main()
