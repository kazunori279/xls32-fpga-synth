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

    uv sync --extra deepfit
    LOSS=clap+stft uv run python presetgen/consolidate.py [bank] [--keep N] [--dry-run]
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


def spread(E, idx):
    """Mean nearest-neighbour cosine distance: how far the closest pair of slots sits apart, which
    is the number a listener feels as "these two are the same patch"."""
    if len(idx) < 2:
        return float("nan")
    D = 1.0 - E[idx] @ E[idx].T
    np.fill_diagonal(D, np.inf)
    return float(D.min(axis=1).mean())


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    keep_n = None
    if "--keep" in argv:
        i = argv.index("--keep")
        keep_n = int(argv[i + 1])
        del argv[i:i + 2]
    bank = argv[0] if argv else "soundfont"

    loss_deep.select(os.environ.get("LOSS", "clap+stft"))
    path = os.path.join(WEBUI, f"presets_{bank}.json")
    d = json.load(open(path))
    presets, meta = d["presets"], d.get("meta") or {}
    import importlib
    corpus = importlib.import_module(meta.get("targets", bank))
    win = protocol.window(corpus)
    engine.render(engine._DEFAULTS, gate_s=win[0], tail_s=win[1])

    names = [p["name"] for p in presets]
    E, audio = embed(presets, win)
    L = fit_losses(presets, audio, corpus, win)

    cats = sorted({p["category"] for p in presets}, key=lambda c: [p["category"] for p in presets].index(c))
    keep = []
    for c in cats:
        idx = [i for i, p in enumerate(presets) if p["category"] == c]
        k = max(1, round(len(idx) * (keep_n / len(presets)))) if keep_n else max(1, len(idx) // 2)
        sel = pick(idx, E, L, names, k)
        keep += sel
        n_inst = len({instrument(names[i]) for i in idx})
        print(f"{c:8} {len(idx):3d} -> {len(sel):2d}   {n_inst} instruments, "
              f"{len({instrument(names[i]) for i in sel})} kept   "
              f"spread {spread(E, idx):.3f} -> {spread(E, sel):.3f}")
        for i in sorted(sel, key=lambda i: L[i]):
            print(f"           {names[i]:22} loss {L[i]:5.1f}")
        print(f"           dropped: {', '.join(names[i] for i in idx if i not in sel)}")

    keep_set = set(keep)
    print(f"\n{len(presets)} -> {len(keep)} presets   "
          f"overall spread {spread(E, range(len(presets))):.3f} -> {spread(E, keep):.3f}   "
          f"instruments {len({instrument(n) for n in names})} -> "
          f"{len({instrument(names[i]) for i in keep})}")
    if dry:
        return
    d["presets"] = [p for i, p in enumerate(presets) if i in keep_set]
    meta["count"] = len(d["presets"])
    meta["consolidated"] = {"from": len(presets), "by": "clap-embedding spread, "
                            "instrument coverage first"}
    d["meta"] = meta
    json.dump(d, open(path, "w"))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
