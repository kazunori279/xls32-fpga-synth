#!/usr/bin/env python3
"""Read a saved ab_check.html vote file and say what it does and does not establish (#22).

The page prints its own summary when the last vote lands, which is the right thing to show a
listener mid-session and not enough to close an issue on. What this adds:

  THE ORDER CHECK, first, because it decides whether the rest is worth reading. The first run of
  this rig was void on exactly this: the listener picked the first-played clip in 19 of 22 decided
  trials (p = 0.001) against a bank split of 13-9 (p = 0.52). Balancing which bank plays first --
  which ab_render.py already did -- keeps a position preference from favouring a bank and leaves it
  free to decide every trial, so the bank number was noise with a p-value attached.

  CONCORDANCE. Each pair is now heard twice, once each way round, and only a pair naming the same
  bank both times counts. A discordant pair is not a tie: a tie is the listener saying the two are
  alike, and a discordant pair is this protocol failing to ask a question.

  WHETHER THE VOTE TRACKS THE SPREAD. Trials come from the widest CLAP gaps per category, so every
  pair audibly differs -- but if the winner is unrelated to how far apart the two fits are, CLAP is
  ordering pairs by something the listener does not respond to. That is a statement about the
  metric, separate from which bank won.

  WHETHER `closer` AND `better` AGREE. Fidelity to the GM target, and whether the patch is worth
  playing. The bank was fit for the first and is shipped for the second, so a disagreement is the
  most useful thing here.

A null result is a result. `bank_compare.py` reported the shipped bank 29% closer under CLAP; if
the pairs that survive counterbalancing cannot separate the banks, that 29% is not a claim about
anything audible, and the honest move is to say so rather than re-run with a different seed.

Old single-hearing vote files (one record per pair, no `first` field) still read: the concordance
section is skipped and the flat split is printed with a warning naming what is wrong with it.

    uv run python presetgen/ab_tally.py ~/Downloads/ab_votes.json
"""
import json
import os
import sys
from collections import defaultdict
from math import comb

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MANIFEST = os.path.join(REPO, "webui", "ab", "manifest.json")


def sign_p(k, n):
    """Exact two-sided sign test. Ties are excluded from n, not split: a listener who cannot hear
    a difference is evidence about the pair, not half a vote for either bank."""
    if not n:
        return 1.0
    lo = min(k, n - k)
    return min(1.0, 2 * sum(comb(n, j) for j in range(lo + 1)) / 2 ** n)


def spearman(x, y):
    """Rank correlation, ties averaged. Spearman rather than Pearson because the vote is ordinal
    (a win is not twice a tie) and n is too small to trust a linear fit."""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def order_check(out, b0, b1):
    """Did the first-played clip win, whichever bank it was? Returns True if it decided anything."""
    if not all("first" in r for r in out):
        # Pre-counterbalancing vote files did not record it; the manifest can supply it if the run
        # that produced them is still on disk. It is gitignored, so often it is not.
        if not os.path.exists(MANIFEST):
            print("order check skipped: no `first` in the votes and no webui/ab/manifest.json")
            return False
        m = json.load(open(MANIFEST))
        if m.get("seed") != json.load(open(MANIFEST)).get("seed"):
            return False
        pos = {t["id"]: t["a"] for t in m["trials"]}
        for r in out:
            r["first"] = pos.get(r["id"])
        if not all(r.get("first") for r in out):
            print("order check skipped: the manifest on disk is from a different run")
            return False
    hit = False
    print("order check -- did the first-played clip win, whichever bank it was?")
    for k in ("closer", "better"):
        d = [r for r in out if r[k] != "tie"]
        f = sum(1 for r in d if r[k] == r["first"])
        p = sign_p(f, len(d))
        hit = hit or p < 0.05
        print(f"  {k:7}  first {f:2}   second {len(d) - f:2}   p = {p:.3f}"
              f"{'   * order is deciding trials' if p < 0.05 else ''}")
    return hit


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/Downloads/ab_votes.json")
    v = json.load(open(path))
    b0, b1 = v["banks"]
    out = v["votes"]

    by_id = defaultdict(list)
    for r in out:
        by_id[r["id"]].append(r)
    paired = all(len(h) == 2 for h in by_id.values())

    print(f"{len(out)} hearings over {len(by_id)} pairs   {b0} (incumbent) vs {b1}   "
          f"seed {v.get('seed')}")
    print()
    ordered = order_check(out, b0, b1)
    print()

    if not paired:
        print("!! this vote file has one hearing per pair, so nothing here controls for order.")
        print("!! re-render with the current ab_render.py and run it again; the flat split follows")
        print("!! only so the file is not silently unreadable.")
        print()
        for k in ("closer", "better"):
            w0 = sum(1 for r in out if r[k] == b0)
            w1 = sum(1 for r in out if r[k] == b1)
            tie = sum(1 for r in out if r[k] == "tie")
            print(f"  {k:7}  {b0} {w0:2}   {b1} {w1:2}   tie {tie:2}   p = {sign_p(w0, w0 + w1):.3f}")
    else:
        verdict = {}
        for k in ("closer", "better"):
            w = {b0: [], b1: []}
            flip, tie = [], []
            for pid, h in by_id.items():
                a, b = h[0][k], h[1][k]
                if a == "tie" and b == "tie":
                    tie.append(pid)
                elif a == b:
                    w[a].append(pid)
                else:
                    flip.append(pid)
            verdict[k] = {"w": w, "flip": flip, "tie": tie}
            p = sign_p(len(w[b0]), len(w[b0]) + len(w[b1]))
            print(f"  {k:7}  {b0} {len(w[b0]):2}   {b1} {len(w[b1]):2}   "
                  f"both-ways tie {len(tie):2}   order-dependent {len(flip):2}   "
                  f"p = {p:.3f}{'  *' if p < 0.05 else ''}")

        # A pair that survives counterbalancing is the only kind that carries a bank preference,
        # so the spread correlation is computed over those and nothing else.
        print("\ndoes a surviving preference track the CLAP spread between the two fits?")
        spread = {pid: h[0]["spread"] for pid, h in by_id.items()}
        for k in ("closer", "better"):
            w = verdict[k]["w"]
            ids = w[b0] + w[b1]
            if len(ids) < 4:
                print(f"  {k:7}  only {len(ids)} decided pairs; not worth a correlation")
                continue
            rho = spearman([spread[p] for p in ids], [1 if p in w[b1] else 0 for p in ids])
            print(f"  {k:7}  rho = {rho:+.2f} over {len(ids)} decided pairs")
        allsp = sorted(spread.values())
        flipsp = sorted(spread[p] for p in verdict["closer"]["flip"])
        if flipsp:
            print(f"  order-dependent pairs span spread {flipsp[0]:.3f}-{flipsp[-1]:.3f}; "
                  f"all pairs span {allsp[0]:.3f}-{allsp[-1]:.3f}")

        both = set(verdict["closer"]["w"][b0] + verdict["closer"]["w"][b1]) & \
               set(verdict["better"]["w"][b0] + verdict["better"]["w"][b1])
        split = [pid for pid in both
                 if (pid in verdict["closer"]["w"][b0]) != (pid in verdict["better"]["w"][b0])]
        print(f"\n{len(both)} pairs decided on both questions; "
              f"{len(split)} of them name a different bank for each")
        for pid in split:
            h = by_id[pid][0]
            print(f"  {h['category']:8} {h['name']:20} closer {h['closer']:9} "
                  f"better {h['better']}")

        # The two questions sit on one screen with identical buttons, so the cheapest way to answer
        # the second is to repeat the first. If they never once diverge across every hearing, that
        # is what happened: `better` collected no independent evidence and must not be reported as
        # a second, agreeing result. Perfect agreement is a warning, not a corroboration.
        lock = sum(1 for r in out if r["closer"] == r["better"])
        if lock == len(out):
            print(f"\n!! `closer` and `better` are identical on all {len(out)} hearings, ties")
            print("!! included. Two questions with the same answer every time are one question;")
            print("!! read the `better` line as a copy of `closer`, not as confirmation of it.")
            print("!! Ask them in separate passes if `better` is wanted as evidence.")

        print(f"\nby category (closer / better; 0 = {b0}, 1 = {b1}, ~ = order-dependent, "
              f"- = tie both ways)")
        for c in sorted({r["category"] for r in out}):
            ids = [pid for pid, h in by_id.items() if h[0]["category"] == c]
            def col(k):
                s = ""
                for pid in ids:
                    v_ = verdict[k]
                    s += ("0" if pid in v_["w"][b0] else "1" if pid in v_["w"][b1]
                          else "-" if pid in v_["tie"] else "~")
                return s
            print(f"  {c:8} {col('closer')}  {col('better')}")

        n = len(by_id)
        print(f"\n{n} pairs. Requiring both hearings to agree shrinks n on purpose. A pile of")
        print("order-dependent pairs is not a tie -- it means this ear cannot separate the banks")
        print("at single notes, which is the answer to a 29% CLAP improvement, not a failed test.")

    if ordered:
        print("\nThe order effect is significant. Whatever the bank columns say above, most of what")
        print("was voted on was which button came first; treat the result as provisional.")


if __name__ == "__main__":
    main()
