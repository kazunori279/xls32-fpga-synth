"""Halve a bank, keeping the sounds and dropping the copies.

A fitted bank is not 128 sounds. `soundfont.py` lists each GM program at several pitches, and every
pitch becomes its own slot, so the browser ships "Synth Bass 1", "Synth Bass 1 G2", "Synth Bass 1
G3", "Synth Bass 1 C2", "Synth Bass 1 E3", "Synth Bass 1 C4" -- six slots that CMA-ES fitted
independently and that mostly landed on the same patch. Scrolling past five near-copies to find the
next real timbre is the cost, and it is paid on every single browse.

So: keep half, chosen for spread rather than for score. Two ideas, and the second is the one that
matters:

  distance   Perceptual, not parametric. Two presets with different knob values can sound the same
             (the filter is closed, so the waveform select stops mattering) and two with similar
             values can not. So each preset is rendered and embedded with CLAP -- the same encoder
             the bank was fitted under -- and distance is cosine on that embedding.
  coverage   Farthest-point selection alone would fill the bank with outliers: the loudest, most
             extreme fit in a category is always the farthest thing from everything else. And it
             would happily keep three pitches of one instrument while dropping another instrument
             entirely, because pitch moves a CLAP embedding more than timbre does. So instrument
             coverage is a HARD constraint ahead of distance: no second slot for any instrument
             until every instrument in the category has one. Distance only breaks ties within that
             rule, which is exactly where it is trustworthy.

Selection is per category, keeping each category's count at half. The category rail is the browser's
main axis and a "diverse" bank that emptied Brass to make room for six FX patches would be worse to
use, whatever a global spread metric said about it.

The first slot for each instrument is its BEST-FITTING pitch (lowest clap+stft distance to its own
target, recomputed here -- build_presets.py does not keep it). Further slots go to whichever
remaining preset is farthest from everything already kept. So a category with eight instruments
keeps one of each, and a category with three keeps its three best plus the five most distinct
alternates.

That is `--rule name`, and it is what shipped. #22 then put the resulting cut to an ear and #45
took it apart, so there is a second rule and a second number:

  --rule coverage   Drops the instrument-coverage constraint and selects to minimise the WORST gap
                    a dropped preset is left with (`pick_coverage`). The objective was never one
                    slot per instrument; it was covering the sound space with half the slots.
  the gap report    Every run now prints the distribution of gaps from the presets that GO to the
                    nearest one that stays, against the 0.09 crossing #22 measured. `spread()` --
                    the number the 128 -> 64 cut was defended with -- is about the survivors'
                    spacing, which is a different set of presets and can improve while the gaps get
                    worse. On this bank it did: spread 0.060 -> 0.083 while 41 of the 64 dropped
                    presets landed above the crossing.

    uv sync --extra deepfit
    LOSS=clap+stft uv run python presetgen/consolidate.py [bank|path.json] \
        [--keep N] [--rule name|coverage] [--compare other.json] [--out path] [--dry-run]

`bank` is a stem under webui/ or a path, so the pre-consolidation 128 can be re-cut and the two
rules compared head to head without touching what shipped:

    git show ccd17d7^:webui/presets_soundfont.json > /tmp/prev128.json
    LOSS=clap+stft uv run python presetgen/consolidate.py /tmp/prev128.json --rule coverage \
        --keep 64 --compare webui/presets_soundfont.json --dry-run
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine                                                              # noqa: E402
import loss_deep                                                           # noqa: E402
import protocol                                                            # noqa: E402
from name_audit import _NOTE_RE, note_of                                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEBUI = os.path.abspath(os.path.join(HERE, "..", "webui"))


def instrument(name):
    """The GM program a slot came from: the name with its pitch tag removed, and NOTHING else.
    Not name_audit.clean_name() -- that also strips a trailing index, which would make "E-Piano 1"
    and "E-Piano 2" (a Rhodes and a DX) one instrument, and "Synth Bass 1"/"Synth Bass 2" likewise.
    Under that key the coverage rule reads Keys as three instruments instead of four and happily
    keeps four E-Piano slots while dropping three of the four Clavinets."""
    return _NOTE_RE.sub("", name).strip() or name


def embed(presets, win):
    """CLAP embedding per preset, rendered at the note it was fitted at."""
    audio = [engine.render(p["values"], note=note_of(p["name"], p["category"]),
                           gate_s=win[0], tail_s=win[1]) for p in presets]
    return loss_deep.clap_audio_emb(audio, engine.SR), audio


def fit_losses(presets, audio, corpus, win):
    """Each preset's distance to its own target, under the bank's objective. Missing target -> inf,
    so a preset nothing can grade is never chosen as an instrument's representative."""
    targets = {n: (p, note) for _, n, p, note in corpus.list_targets(per_cat=16)}
    out = []
    for p, a in zip(presets, audio):
        t = targets.get(p["name"])
        if t is None:
            out.append(np.inf); continue
        tp = loss_deep.prep(*corpus.load(t[0]), window=win)
        out.append(float(loss_deep.dists([a], tp, a_sr=engine.SR, b_prepped=True)[0]))
    return np.array(out)


WORST = 85          # percentile of within-category fit loss above which a slot is a last resort
# The crossing measured in #22: 24 pairs, each heard twice in both playback orders, asked "if you
# had this one, would you miss the one that went?" against the CLAP gap between them. AUC 0.975,
# permutation p = 0.0001, and the two verdicts do not overlap -- everything at or below 0.081 was
# called interchangeable, everything at or above 0.103 was missed. Read it against the gap to ANY
# survivor rather than to the same-instrument one: the listener was never shown a name, so what the
# number grades is the distance between two sounds, whatever they happen to be called.
MISSED = 0.09


def pick(idx, E, loss, names, keep):
    """Greedy: instrument coverage first, then farthest-from-what-is-kept. Deterministic."""
    inst = {i: instrument(names[i]) for i in idx}
    # Max-min selection favours outliers by construction, and in a bank fitted to named targets the
    # outlier is usually the fit that MISSED -- a patch nothing else resembles because it does not
    # resemble its own target either. It still carries that target's name, so shipping it as
    # "diversity" ships a mislabelled slot. The worst tail is therefore ranked last, but only after
    # coverage: if it is the only slot an instrument has, it is still kept.
    cut = np.percentile([loss[i] for i in idx if np.isfinite(loss[i])] or [0], WORST)
    chosen, have = [], set()
    while len(chosen) < keep and len(chosen) < len(idx):
        rest = [i for i in idx if i not in chosen]
        if not chosen:
            # Seed on fit quality, not on distance: the first pick has nothing to be far from, and
            # "the best fit in the category" is a defensible thing for the top of the list to be.
            chosen.append(min(rest, key=lambda i: (loss[i], names[i])))
        else:
            D = 1.0 - E[rest] @ E[chosen].T          # cosine distance to every kept preset
            near = D.min(axis=1)
            # Lexicographic: an unrepresented instrument beats everything, then a fit that landed
            # beats one that missed, then farthest wins. The name breaks exact ties so two runs of
            # this file agree.
            order = sorted(range(len(rest)),
                           key=lambda j: (inst[rest[j]] in have, loss[rest[j]] > cut,
                                          -near[j], names[rest[j]]))
            chosen.append(rest[order[0]])
        have.add(inst[chosen[-1]])
    return chosen


def _score(E, sel, idx):
    """(how many dropped presets land above the crossing, the worst gap, the median gap).

    Lexicographic, and deliberately not pure max-min. Greedy k-centre optimises the worst gap
    alone, which spends picks on one unreachable outlier while several presets sit just over the
    crossing. What #22 measured is a step at 0.09, so the count over it is the thing worth
    minimising and the worst gap is the tie-break.
    """
    drop = [i for i in idx if i not in set(sel)]
    if not drop:
        return (0, 0.0, 0.0)
    g = (1.0 - E[drop] @ E[list(sel)].T).min(axis=1)
    return (int((g > MISSED).sum()), float(g.max()), float(np.median(g)))


def pick_coverage(idx, E, loss, names, keep, all_instruments=False):
    """Select for coverage: leave as few sounds as possible with nothing near them.

    #45's objective, stated plainly, and a greedy farthest-point seed refined by swaps. Greedy
    alone is the standard 2-approximation for k-centre and it is not good enough here -- on the
    soundfont 128 it leaves 17 presets above the crossing where a swap pass finds 9.

    The seed is still the best-fitting preset in the category, because the first pick has nothing
    to be far from. What is gone is `pick`'s WORST-percentile demotion: it exists to stop max-min
    chasing an outlier that is really a fit that MISSED, but it costs three presets' worth of
    coverage here, and `_score` counting a step instead of maximising a distance already blunts
    the outlier chase it was guarding against. `main` prints how many picked slots are in that
    worst tail so the trade is visible rather than silently taken.

    `all_instruments` keeps `pick`'s hard constraint as an option, because losing an instrument
    from the browser is a real cost and not one this objective can see. It is the difference
    between 13 above the crossing with all 46 names kept and 9 with 39 -- the decision #45 leaves
    open, made explicit rather than assumed either way.
    """
    keep = min(keep, len(idx))
    chosen = [min(idx, key=lambda i: (loss[i], names[i]))]
    while len(chosen) < keep:
        rest = [i for i in idx if i not in set(chosen)]
        near = (1.0 - E[rest] @ E[chosen].T).min(axis=1)
        chosen.append(rest[min(range(len(rest)), key=lambda j: (-near[j], names[rest[j]]))])

    if all_instruments:
        want = {instrument(names[i]) for i in idx}
        ok = lambda sel: {instrument(names[i]) for i in sel} == want          # noqa: E731
        # The greedy seed does not respect the constraint, so start the search from the rule that
        # does. `pick` is feasible by construction and the swaps only ever improve on it.
        if not ok(chosen):
            chosen = pick(idx, E, loss, names, keep)
    else:
        ok = lambda sel: True                                                 # noqa: E731

    # Steepest-descent swaps to a local optimum. The neighbourhood is every (kept, dropped) pair,
    # which is 64 x 64 per category at worst and runs in seconds off a cached embedding.
    while True:
        base, best = _score(E, chosen, idx), None
        for a in range(len(chosen)):
            for b in [i for i in idx if i not in set(chosen)]:
                trial = chosen[:a] + chosen[a + 1:] + [b]
                if not ok(trial):
                    continue
                s = _score(E, trial, idx)
                if s < base:
                    base, best = s, trial
        if best is None:
            return sorted(chosen, key=lambda i: (loss[i], names[i]))
        chosen = best


def gaps(E, keep, idx):
    """(gap, dropped index) for every preset in `idx` that `keep` did not keep, nearest survivor.

    The quantity #22 validated and the one this file never printed. `spread()` below is about the
    survivors' spacing -- a different set of presets, answering "do the slots that stayed sound
    alike" rather than "did anything go that will be missed". The two move independently, and in
    the shipped cut they moved in opposite directions.
    """
    kept = sorted(set(keep))
    drop = [i for i in idx if i not in set(kept)]
    if not drop or not kept:
        return np.array([]), []
    return (1.0 - E[drop] @ E[kept].T).min(axis=1), drop


def gap_report(E, keep, idx, names, label, show=0):
    """Print the gap distribution for one cut, both ways round. Returns the any-instrument gaps.

    The two rows are the whole of #45's arithmetic and they are NOT the same question:

      any        gap to the nearest survivor whatever it is called. "Is this sound still in the
                 bank?" -- the coverage objective, and what a re-cut can move.
      same-inst  gap to the surviving slot of the dropped preset's OWN instrument. "Is this sound
                 still where its name says it is?" -- and the pair #22 actually played, so it is
                 the row the 0.09 crossing was calibrated on.

    #45 quotes 41 of 64 over the crossing; that is the second row. The first is 16. Applying the
    crossing to the first row is an extrapolation off the pairs that were heard -- defensible,
    since the listener was shown no names, but not measured. Both are printed so the claim being
    made is always visible.
    """
    g, drop = gaps(E, keep, idx)
    if not len(g):
        print(f"  {label:9} nothing dropped")
        return g
    kept = sorted(set(keep))
    sib = []
    for i in drop:
        s = [j for j in kept if instrument(names[j]) == instrument(names[i])]
        sib.append(1.0 - max(float(E[i] @ E[j]) for j in s) if s else np.nan)
    sib = np.array(sib, dtype=float)
    lost = int(np.isnan(sib).sum())
    print(f"  {label:9} any        median {np.median(g):.3f}  worst {g.max():.3f}  "
          f"over {MISSED}: {int((g > MISSED).sum())} of {len(g)}   survivor spread "
          f"{spread(E, keep):.3f}")
    print(f"  {'':9} same-inst  median {np.nanmedian(sib):.3f}  "
          f"over {MISSED}: {int((sib[~np.isnan(sib)] > MISSED).sum())} of "
          f"{len(sib) - lost}" + (f"   ({lost} dropped presets have no surviving slot of their "
                                  f"own instrument at all)" if lost else ""))
    for j in np.argsort(-g)[:show]:
        print(f"            {g[j]:.3f}  {names[drop[j]]}")
    return g


def spread(E, idx):
    """Mean nearest-neighbour cosine distance: how far the closest pair of slots sits apart, which
    is the number a listener feels as "these two are the same patch"."""
    idx = list(idx)
    if len(idx) < 2:
        return float("nan")
    D = 1.0 - E[idx] @ E[idx].T
    np.fill_diagonal(D, np.inf)
    return float(D.min(axis=1).mean())


RULES = {"name": pick, "coverage": pick_coverage}
BY = {"name": "clap-embedding spread, instrument coverage first",
      "coverage": "clap-embedding coverage (k-centre + swaps), fewest presets left above 0.09"}


def bank_path(bank):
    """A stem under webui/ or a path to a bank file. The 128-slot bank is only reachable through
    git (`git show ccd17d7^:webui/presets_soundfont.json`), so a re-cut has to be able to read a
    file that is not in the tree."""
    return bank if bank.endswith(".json") else os.path.join(WEBUI, f"presets_{bank}.json")


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    all_inst = "--all-instruments" in argv
    argv = [a for a in argv if a not in ("--dry-run", "--all-instruments")]
    opt = {"--keep": None, "--rule": "name", "--compare": None, "--out": None, "--targets": None,
           "--cache": None}
    for flag in list(opt):
        if flag in argv:
            i = argv.index(flag)
            opt[flag] = argv[i + 1]
            del argv[i:i + 2]
    keep_n = int(opt["--keep"]) if opt["--keep"] else None
    rule, compare, out = opt["--rule"], opt["--compare"], opt["--out"]
    if rule not in RULES:
        sys.exit(f"--rule must be one of {', '.join(RULES)}")
    bank = argv[0] if argv else "soundfont"

    path = bank_path(bank)
    d = json.load(open(path))
    presets, meta = d["presets"], d.get("meta") or {}
    names = [p["name"] for p in presets]

    # The embedding is the expensive half -- 128 engine renders plus a CLAP forward pass, minutes --
    # and it does not depend on the rule. Cached so comparing rules costs seconds, which is what
    # #45 needs: the question there is which selection to make from a fixed set of sounds.
    cache = opt["--cache"]
    if cache and os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        if list(z["names"]) != names:
            sys.exit(f"{cache} was built from a different bank")
        E, L = z["E"], z["L"]
        print(f"embedding from {os.path.relpath(cache, os.path.dirname(HERE))}")
    else:
        loss_deep.select(os.environ.get("LOSS", "clap+stft"))
        stem = os.path.basename(path).removeprefix("presets_").removesuffix(".json")
        import importlib
        # A bank pulled out of git predates the `targets` key, so a path input may have to say
        # which corpus graded it. Only used for the fit losses that seed and demote picks; the
        # gaps below are preset-against-preset and need no target at all.
        corpus = importlib.import_module(opt["--targets"] or meta.get("targets") or stem)
        win = protocol.window(corpus)
        engine.render(engine._DEFAULTS, gate_s=win[0], tail_s=win[1])
        E, audio = embed(presets, win)
        L = fit_losses(presets, audio, corpus, win)
        if cache:
            np.savez(cache, E=E, L=L, names=np.array(names))
            print(f"cached embedding -> {cache}")

    cats = sorted({p["category"] for p in presets}, key=lambda c: [p["category"] for p in presets].index(c))
    keep = []
    for c in cats:
        idx = [i for i, p in enumerate(presets) if p["category"] == c]
        k = max(1, round(len(idx) * (keep_n / len(presets)))) if keep_n else max(1, len(idx) // 2)
        sel = (pick_coverage(idx, E, L, names, k, all_inst) if rule == "coverage"
               else RULES[rule](idx, E, L, names, k))
        keep += sel
        n_inst = len({instrument(names[i]) for i in idx})
        print(f"{c:8} {len(idx):3d} -> {len(sel):2d}   {n_inst} instruments, "
              f"{len({instrument(names[i]) for i in sel})} kept   "
              f"spread {spread(E, idx):.3f} -> {spread(E, sel):.3f}")
        for i in sorted(sel, key=lambda i: L[i]):
            print(f"           {names[i]:22} loss {L[i]:5.1f}")
        print(f"           dropped: {', '.join(names[i] for i in idx if i not in sel)}")

    keep_set = set(keep)
    allidx = list(range(len(presets)))
    print(f"\n{len(presets)} -> {len(keep)} presets, rule '{rule}'   "
          f"overall spread {spread(E, allidx):.3f} -> {spread(E, keep):.3f}   "
          f"instruments {len({instrument(n) for n in names})} -> "
          f"{len({instrument(names[i]) for i in keep})}")

    # #45: the number the cut has to answer for. Survivor spread above says whether the slots that
    # stayed sound alike; this says whether anything that went will be missed, which is the claim
    # halving a bank actually makes. Printed for every rule so the two are read side by side.
    print("\ngap distribution -- what the presets that GO are left with:")
    gap_report(E, keep, allidx, names, rule, show=6)
    if rule == "coverage":
        # `pick` refuses these outright; `pick_coverage` does not, so say how many it took. A slot
        # in the worst fit tail is a patch that missed its own target and still carries its name.
        tail = sum(1 for c in cats
                   for i in keep if presets[i]["category"] == c
                   and L[i] > np.percentile([L[j] for j, p in enumerate(presets)
                                             if p["category"] == c and np.isfinite(L[j])], WORST))
        print(f"  {'':9} {tail} kept slots are in their category's worst {100 - WORST}% of fits -- "
              f"patches that missed their own target and keep its name")
    if compare:
        other = {p["name"] for p in json.load(open(compare))["presets"]}
        alt = [i for i, n in enumerate(names) if n in other]
        if len(alt) != len(keep):
            print(f"  !! {os.path.basename(compare)} names {len(alt)} of these presets, "
                  f"not {len(keep)} -- the two cuts are not the same size")
        gap_report(E, alt, allidx, names, "shipped", show=6)

    if dry:
        return
    if not out and bank.endswith(".json"):
        # A bank read by path is usually a historical one pulled out of git. Overwriting it in
        # place would destroy the only copy in the working tree and is never what was meant.
        sys.exit("reading a bank by path would overwrite it; pass --out or --dry-run")
    dest = out or path
    d["presets"] = [p for i, p in enumerate(presets) if i in keep_set]
    meta["count"] = len(d["presets"])
    meta["consolidated"] = {"from": len(presets), "by": BY[rule]}
    d["meta"] = meta
    json.dump(d, open(dest, "w"))
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
