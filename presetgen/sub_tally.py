#!/usr/bin/env python3
"""Pool sub_check.html sessions: did halving the bank cost a sound, or only a slot? (#22)

`consolidate.py` cut `presets_soundfont.json` from 128 slots to 64 and defended it with mean
nearest-neighbour CLAP distance 0.060 -> 0.083 and "all 46 instruments kept". The second claim is
true by name and is what this asks an ear about, because for 48 of the 64 dropped presets the
nearest-SOUNDING survivor belongs to a different instrument than the one they share a name with.

Same storage rule as `ab_tally.py`: one session per file under
`presetgen/listening/consolidate-128-to-64/`, read the directory rather than any one file.

    uv run python presetgen/sub_tally.py presetgen/listening/consolidate-128-to-64

The verdict is a property of the PAIR -- "are these two interchangeable" is the same question
whichever clip plays first -- so the order should not enter into it, and a verdict that flips with
the order is not a tie but a question this rig put badly. Both hearings must agree for a pair to
count, and the flips are reported rather than averaged in.

What the answer decides. If `different` tracks the CLAP gap, `consolidate.py`'s distance measures
substitutability, the cut was defended with the right number, and the wide-gap end of the survey is
a re-voicing list. If sounds at 0.05 are missed as often as sounds at 0.30, the number does not
answer the question it was used for, and how many slots were lost is a separate matter from whether
the metric could tell.
"""
import glob
import json
import os
import sys
from collections import defaultdict
from math import comb

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "listening", "consolidate-128-to-64")
KEEP = ("different", "same")


def fisher(a, b, c, d):
    """Two-sided Fisher exact on a 2x2. Small n and a fixed margin: chi-square would overstate."""
    n = a + b + c + d
    if not n:
        return 1.0
    row, col = a + b, a + c

    def prob(x):
        return comb(row, x) * comb(c + d, col - x) / comb(n, col)

    obs = prob(a)
    return min(1.0, sum(prob(x) for x in range(max(0, col - (c + d)), min(row, col) + 1)
                        if prob(x) <= obs + 1e-12))


def load(args):
    out = []
    for a in args:
        paths = sorted(glob.glob(os.path.join(a, "*.json"))) if os.path.isdir(a) \
            else (sorted(glob.glob(a)) or [a])
        for p in paths:
            try:
                d = json.load(open(p))
            except (OSError, ValueError) as e:
                print(f"skipped {p}: {e}")
                continue
            if "votes" not in d:
                continue
            if d.get("void"):
                # Kept as evidence about the rig rather than about the bank; `_note` says why.
                print(f"skipped {os.path.basename(p)}: void — "
                      f"{d.get('_note', 'no reason recorded').split('.')[0]}.")
                continue
            d["_path"] = os.path.basename(p)
            d.setdefault("listener", "author")
            out.append(d)
    return out


def concord(votes):
    """{dropped preset: verdict | 'flip' | '?'}. Both hearings must agree or the pair is dropped.

    Keyed by the dropped preset's NAME, not the trial id. Trial ids are per-render stems, and
    consecutive sessions deliberately ask about different presets -- `s00` is Celesta in one render
    and Harpsichord in the next, so pooling on the id would merge two unrelated pairs.
    """
    by = defaultdict(list)
    for r in votes:
        by[r["dropped"]].append(r["verdict"])
    return {name: ("?" if len(h) < 2 else h[0] if h[0] == h[1] else "flip")
            for name, h in by.items()}


def bands(rows, verdict):
    """Tertiles of the CLAP gap over the pairs actually run, widest first."""
    s = sorted(rows, key=lambda r: -r["dist"])
    w = max(1, -(-len(s) // 3))
    for label, part in (("widest gap", s[:w]), ("middle", s[w:2 * w]),
                        ("narrowest gap", s[2 * w:])):
        if not part:
            continue
        dec = [r for r in part if verdict.get(r["dropped"]) in KEEP]
        miss = sum(1 for r in dec if verdict[r["dropped"]] == "different")
        yield (label, miss, len(dec), len(part),
               min(r["dist"] for r in part), max(r["dist"] for r in part))


def main():
    args = sys.argv[1:] or [STORE]
    sess = load(args)
    if not sess:
        sys.exit(f"no sessions found in {' '.join(args)}")

    print(f"{len(sess)} session(s), 128 -> 64 consolidation")
    for s in sess:
        single = "" if any("rev" in r for r in s["votes"]) else "   [one hearing per pair]"
        print(f"  {s['_path']:40} {s['listener']:10} {len(s['votes'])} hearings{single}")

    verdicts = {}
    for s in sess:
        v = s["votes"]
        pairs = {r["dropped"]: r for r in v}
        print(f"\n=== {s['listener']}")

        # Order first. This is not the A/B rig's "did the first clip win" -- there is no clip to
        # win. It is whether hearing the dropped preset second made the pair sound less alike, which
        # would mean the verdict is partly about recency and not about the two sounds.
        dec = [r for r in v if r["verdict"] in KEEP]
        g = {k: [r for r in dec if r.get("gone") == k] for k in ("1", "2")}
        cnt = {k: sum(1 for r in g[k] if r["verdict"] == "different") for k in g}
        if g["1"] and g["2"]:
            p = fisher(cnt["1"], len(g["1"]) - cnt["1"], cnt["2"], len(g["2"]) - cnt["2"])
            print(f"  order   dropped played first  different {cnt['1']}/{len(g['1'])}   "
                  f"second  different {cnt['2']}/{len(g['2'])}   p = {p:.3f}"
                  f"{'   * the order is deciding the verdict' if p < 0.05 else ''}")
        else:
            print("  order   not checkable: this file has no `gone`")

        ver = concord(v)
        verdicts[s["listener"]] = (ver, pairs)
        if all(x == "?" for x in ver.values()):
            raw = {k: sum(1 for r in v if r["verdict"] == k) for k in KEEP + ("tie",)}
            print(f"  raw     different {raw['different']}   interchangeable {raw['same']}   "
                  f"can't tell {raw['tie']}   of {len(v)}")
            print("  !! one hearing per pair, so nothing here separates the two sounds from the")
            print("  !! order they arrived in. Re-render and re-run for a concordance column.")
            # The gap breakdown is the point of the test and still reads off single hearings -- it
            # is the shape that is informative, not the counts. Printed under the warning above.
            flat = {r["dropped"]: r["verdict"] for r in v}
            print("  by CLAP gap, single hearings (missed / decided of pairs run):")
            for label, miss, ndec, tot, lo, hi in bands(list(pairs.values()), flat):
                print(f"    {label:14} {miss:2}/{ndec} of {tot:2}   gap {lo:.3f}-{hi:.3f}")
            continue

        n = lambda k: sum(1 for x in ver.values() if x == k)
        print(f"  result  would be missed {n('different'):2}   interchangeable {n('same'):2}   "
              f"can't tell {n('tie'):2}   order-dependent {n('flip'):2}   of {len(ver)} pairs")
        print("  by CLAP gap to the same-instrument survivor (missed / decided of pairs run):")
        for label, miss, ndec, tot, lo, hi in bands(list(pairs.values()), ver):
            print(f"    {label:14} {miss:2}/{ndec} of {tot:2}   gap {lo:.3f}-{hi:.3f}")

        # The survey's own finding, asked of the ear: when some OTHER instrument sounds nearer than
        # the surviving slot that shares the dropped preset's name, is the pair heard as different?
        # That is the "all 46 instruments kept" claim, tested rather than asserted.
        q = [(r["nearest_same_instrument"], ver[r["dropped"]] == "different")
             for r in pairs.values() if ver.get(r["dropped"]) in KEEP]
        a = sum(1 for same_i, miss in q if not same_i and miss)
        b = sum(1 for same_i, miss in q if not same_i and not miss)
        c = sum(1 for same_i, miss in q if same_i and miss)
        d = sum(1 for same_i, miss in q if same_i and not miss)
        if (a + b) and (c + d):
            print(f"  another instrument sounds nearer than the same-name survivor:")
            print(f"    yes  missed {a}/{a + b}      no  missed {c}/{c + d}"
                  f"      p = {fisher(a, b, c, d):.3f}")

    # Single-hearing sessions are left out of the pool. Their pairs would only inflate the
    # denominator: none of them can produce a concordant verdict, so they contribute nothing but
    # a larger "of N pairs" that reads as coverage.
    usable = {k: v for k, v in verdicts.items() if any(x != "?" for x in v[0].values())}
    if len(usable) < 2:
        if len(usable) < len(verdicts):
            print(f"\n{len(verdicts) - len(usable)} single-hearing session(s) left out of the pool")
        return
    print("\n=== pooled")
    print("  a pair counts once if every listener who decided it said the same thing.")
    print("  Sessions ask about DIFFERENT dropped presets on purpose, so the pool is mostly")
    print("  coverage: overlap only builds up where two ears heard the same preset.")
    ids = sorted({p for ver, _ in usable.values() for p in ver})
    pooled, split = {}, 0
    for pid in ids:
        said = {ver[pid] for ver, _ in usable.values() if ver.get(pid) in KEEP}
        if len(said) == 1:
            pooled[pid] = said.pop()
        elif said:
            split += 1
    miss = sum(1 for x in pooled.values() if x == "different")
    print(f"  would be missed {miss}   interchangeable {len(pooled) - miss}   contested {split}"
          f"   of {len(ids)} pairs across {len(usable)} listeners")
    names = sorted(usable)
    for x in range(len(names)):
        for y in range(x + 1, len(names)):
            va, vb = usable[names[x]][0], usable[names[y]][0]
            both = [p for p in ids if va.get(p) in KEEP and vb.get(p) in KEEP]
            if both:
                same = sum(1 for p in both if va[p] == vb[p])
                print(f"  {names[x]} vs {names[y]}: agree on {same}/{len(both)} pairs both decided")
            else:
                print(f"  {names[x]} vs {names[y]}: no preset decided by both, nothing to compare")
    meta = {}                       # sessions ask about different presets on purpose
    for _, pairs in usable.values():
        meta.update(pairs)
    print("  by CLAP gap:")
    for label, m, ndec, tot, lo, hi in bands([meta[k] for k in ids if k in meta], pooled):
        print(f"    {label:14} {m:2}/{ndec} of {tot:2}   gap {lo:.3f}-{hi:.3f}")


if __name__ == "__main__":
    main()
