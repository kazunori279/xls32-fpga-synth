#!/usr/bin/env python3
"""Pool ab_check.html sessions and say what they do and do not establish (#20, #22).

A session is one question, one listener, one pass over the counterbalanced sequence, saved as its
own file under `presetgen/listening/<incumbent>-vs-<challenger>/`. This reads any number of them --
files, or a directory -- and reports per session and pooled. That is #20's "a way to pool votes from
more than one listener" and its "stored result format that accumulates across sessions": a second
ear is a second file dropped in the directory, not a re-run that replaces the first.

What it checks, in the order it checks it:

  THE ORDER CHECK, first, because it decides whether the rest is worth reading. The original run of
  this rig was void on exactly this: the listener picked the first-played clip in 19 of 22 decided
  trials (p = 0.001) against a bank split of 13-9 (p = 0.52). Balancing which bank plays first --
  which ab_render.py already did -- keeps a position preference from favouring a bank and leaves it
  free to decide every trial, so the bank number was noise with a p-value attached. Playing the
  target immediately before each candidate removed the whole effect: the re-run came back 19-18,
  p = 1.000.

  CONCORDANCE. Each pair is heard twice, once each way round, and only a pair naming the same bank
  both times counts. A discordant pair is not a tie: a tie is the listener saying the two are alike,
  a discordant pair is the protocol failing to ask.

  WHETHER THE VOTE TRACKS THE SPREAD, by the band the pair was sampled from. `ab_render.py` used to
  take the widest-spread pairs per category on the assumption that the pairs the metric separates
  are the pairs an ear separates. The counterbalanced run measured it: rho = +0.07 among decided
  pairs, and the order-dependent pairs spanned nearly the whole range. Stratified sampling is what
  makes this table answerable at all.

  AGREEMENT BETWEEN LISTENERS, on the pairs more than one of them decided. #20's second item is
  that the one listener also built the banks -- blinded to the assignment, not to the hypothesis.
  Two ears that disagree bound what any pooled number can mean.

  WHETHER THE QUESTIONS COLLAPSED. Asked on one screen, `closer` and `better` came back identical
  on all 48 hearings. They are separate sessions now, with different audio; if they still agree
  everywhere, that is worth knowing and it is no longer explicable as button-copying.

A null result is a result. `bank_compare.py` reported the shipped bank 29% closer under CLAP; if the
pairs that survive counterbalancing cannot separate the banks, that 29% is not a claim about
anything audible.

    uv run python presetgen/ab_tally.py presetgen/listening/prev128-vs-soundfont
    uv run python presetgen/ab_tally.py ~/Downloads/ab_*.json
"""
import glob
import json
import os
import sys
from collections import defaultdict
from math import comb

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "listening")


def sign_p(k, n):
    """Exact two-sided sign test. Ties are excluded from n, not split: a listener who cannot hear
    a difference is evidence about the pair, not half a vote for either bank."""
    if not n:
        return 1.0
    lo = min(k, n - k)
    return min(1.0, 2 * sum(comb(n, j) for j in range(lo + 1)) / 2 ** n)


def pfmt(p):
    """Three decimals, except where that would round a decisive result to 0.000."""
    return f"{p:.3f}" if p >= 5e-4 else f"{p:.1e}"


def load(args):
    """Sessions from files or directories. Pre-split files are fanned out into one per question."""
    paths = []
    for a in args:
        if os.path.isdir(a):
            paths += sorted(glob.glob(os.path.join(a, "*.json"))
                            + glob.glob(os.path.join(a, "*", "*.json")))
        else:
            paths += sorted(glob.glob(a)) or [a]
    out = []
    for p in paths:
        try:
            d = json.load(open(p))
        except (OSError, ValueError) as e:
            print(f"skipped {p}: {e}")
            continue
        if "votes" not in d:
            continue
        if d.get("void"):
            # A run kept as evidence about the protocol rather than about the banks. It stays in the
            # directory so the next person can see what went wrong; it must not reach the pool.
            print(f"skipped {os.path.basename(p)}: marked void -- "
                  + (d.get("_note", "").split(" -- ")[0] or "no reason recorded"))
            continue
        d["_path"] = os.path.basename(p)
        d.setdefault("listener", "author")   # every pre-#20 session was the author's
        # The pre-split format asked both questions per hearing and had no `vote`. Fan it out into
        # one pseudo-session per question so the old runs stay readable, and flag them: nothing in
        # them controls for the collapse, and the closer/better agreement in them means nothing.
        if "question" not in d:
            for q in ("closer", "better"):
                if all(q in r for r in d["votes"]):
                    e = dict(d, question=q, _legacy=True,
                             votes=[dict(r, vote=r[q]) for r in d["votes"]])
                    out.append(e)
            continue
        out.append(d)
    return out


def concord(votes):
    """{pair id: winning bank | 'tie' | 'flip' | '?'} over both hearings of each pair."""
    by = defaultdict(list)
    for r in votes:
        by[r["id"]].append(r["vote"])
    v = {}
    for pid, h in by.items():
        v[pid] = ("?" if len(h) < 2 else "tie" if all(x == "tie" for x in h)
                  else h[0] if h[0] == h[1] else "flip")
    return v


def counts(verdict, b0, b1, ids=None):
    ids = list(verdict) if ids is None else ids
    g = lambda k: [p for p in ids if verdict.get(p) == k]
    return g(b0), g(b1), g("tie"), g("flip")


def main():
    args = sys.argv[1:]
    if not args:
        have = sorted(d for d in glob.glob(os.path.join(STORE, "*")) if os.path.isdir(d))
        sys.exit("name a comparison directory or some session files. In the store:\n  "
                 + ("\n  ".join(os.path.relpath(d) for d in have) or "(nothing yet)"))
    sess = load(args)
    if not sess:
        sys.exit(f"no sessions found in {' '.join(args)}")
    banks = sess[0]["banks"]
    b0, b1 = banks
    odd = [s for s in sess if s["banks"] != banks]
    if odd:
        sys.exit(f"{odd[0]['_path']} compares {odd[0]['banks']}, not {banks}; tally one at a time")

    print(f"{len(sess)} session(s), {b0} (incumbent) vs {b1}")
    for s in sess:
        print(f"  {s['_path']:56} {s['question']:7} {s['listener']:8} "
              f"{len(s['votes'])} hearings" + ("   [pre-split format]" if s.get("_legacy") else ""))

    # --- per session
    for s in sess:
        v = s["votes"]
        print(f"\n=== {s['question']} · {s['listener']}")
        if all("first" in r for r in v):
            d = [r for r in v if r["vote"] != "tie"]
            f = sum(1 for r in d if r["vote"] == r["first"])
            p = sign_p(f, len(d))
            print(f"  order   first {f:2}   second {len(d) - f:2}   p = {pfmt(p)}"
                  f"{'   * order is deciding trials' if p < 0.05 else ''}")
        else:
            print("  order   not checkable: this file has no `first`")
        verdict = concord(v)
        once = [p for p, x in verdict.items() if x == "?"]
        if len(once) == len(verdict):
            # Every pair heard once. There is no concordance to compute, and the raw split is the
            # number the order check above is about -- print it, but never as a verdict.
            r0 = sum(1 for r in v if r["vote"] == b0)
            r1 = sum(1 for r in v if r["vote"] == b1)
            print(f"  raw     {b0} {r0:2}   {b1} {r1:2}   tie {len(v) - r0 - r1:2}   "
                  f"p = {pfmt(sign_p(r0, r0 + r1))}")
            print("  !! one hearing per pair: nothing here separates a bank preference from a")
            print("  !! position preference. Re-render and re-run to get a concordance column.")
            continue
        if once:
            print(f"  {len(once)} pair(s) heard once and dropped; partial session")
        w0, w1, tie, flip = counts(verdict, b0, b1)
        p = sign_p(len(w0), len(w0) + len(w1))
        print(f"  result  {b0} {len(w0):2}   {b1} {len(w1):2}   both-ways tie {len(tie):2}   "
              f"order-dependent {len(flip):2}   p = {pfmt(p)}{'  *' if p < 0.05 else ''}")

        bands = defaultdict(list)
        for r in v:
            bands[r.get("band")].append(r["id"])
        if len(bands) > 1:
            print("  by CLAP spread band the pair was sampled from (0 = narrowest):")
            for b in sorted(x for x in bands if x is not None):
                ids = sorted(set(bands[b]))
                c0, c1, ct, cf = counts(verdict, b0, b1, ids)
                sp = [r["spread"] for r in v if r["id"] in set(ids)]
                print(f"    band {b}  {b0} {len(c0)}  {b1} {len(c1)}  tie {len(ct)}  "
                      f"order-dependent {len(cf)}   spread {min(sp):.3f}-{max(sp):.3f}")
        else:
            print("  spread bands: not recorded, or all pairs from one band -- re-render to get "
                  "the stratified set")

    # --- pooled per question, across listeners
    byq = defaultdict(list)
    for s in sess:
        byq[s["question"]].append(s)
    print("\n=== pooled by question")
    print("  a pair counts once if every listener who decided it named the same bank; `contested`")
    print("  is the rest. Pairs only one listener decided still count, so with unequal sessions the")
    print("  pooled split leans on whoever heard more -- read it next to the agreement line.")
    per_q = {}
    for q, ss in byq.items():
        # Two sessions from the same ear are more hearings of the same pairs, not two opinions --
        # concatenate them before concording, so a pair split across sittings still resolves.
        by_who = defaultdict(list)
        for s in ss:
            by_who[s["listener"]] += s["votes"]
        verdicts = {who: concord(v) for who, v in by_who.items()}
        per_q[q] = verdicts
        ids = sorted({p for v in verdicts.values() for p in v})
        # A pair counts for the pool only if every listener who decided it named the same bank.
        # Pooling by majority would let one confident ear outvote a genuine disagreement, and with
        # two listeners there is no majority to take.
        pooled, split = {}, []
        for pid in ids:
            named = {v[pid] for v in verdicts.values() if v.get(pid) in (b0, b1)}
            if len(named) == 1:
                pooled[pid] = named.pop()
            elif len(named) > 1:
                split.append(pid)
                pooled[pid] = "split"
        w0 = sum(1 for x in pooled.values() if x == b0)
        w1 = sum(1 for x in pooled.values() if x == b1)
        who = f"{len(verdicts)} listener(s), {len(ss)} session(s)"
        if not w0 + w1:
            print(f"  {q:7}  {who}   no pair survived concordance; nothing to pool")
        else:
            p = sign_p(w0, w0 + w1)
            print(f"  {q:7}  {who}   {b0} {w0:2}   {b1} {w1:2}   "
                  f"contested {len(split):2}   p = {pfmt(p)}{'  *' if p < 0.05 else ''}")
        if len(verdicts) > 1:
            names = sorted(verdicts)
            for x in range(len(names)):
                for y in range(x + 1, len(names)):
                    va, vb = verdicts[names[x]], verdicts[names[y]]
                    both = [pid for pid in ids
                            if va.get(pid) in (b0, b1) and vb.get(pid) in (b0, b1)]
                    same = sum(1 for pid in both if va[pid] == vb[pid])
                    print(f"    {names[x]} vs {names[y]}: agree on {same}/{len(both)} pairs "
                          f"both decided" + ("  -- coin-flip agreement" if both
                                             and abs(same / len(both) - .5) < .2 else ""))

    # --- did the two questions collapse again?
    if len(per_q) == 2 and "closer" in per_q and "better" in per_q:
        legacy = any(s.get("_legacy") for s in sess)
        for who in sorted(set(per_q["closer"]) & set(per_q["better"])):
            a, b = per_q["closer"][who], per_q["better"][who]
            both = [p for p in a if a.get(p) in (b0, b1) and b.get(p) in (b0, b1)]
            if not both:
                continue
            same = sum(1 for p in both if a[p] == b[p])
            print(f"\n  {who}: closer and better name the same bank on {same}/{len(both)} pairs "
                  "decided on both")
            if same == len(both):
                print("  !! identical. If these came from separate sessions with different audio,")
                print("  !! that is a real finding; if from one screen, it is button-copying and")
                print("  !! the `better` line is a copy rather than a second result."
                      + ("  THIS FILE IS PRE-SPLIT." if legacy else ""))


if __name__ == "__main__":
    main()
