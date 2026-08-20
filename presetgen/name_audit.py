"""Does a preset sound like its name says? A CLAP audio<->text audit of the shipped banks.

The banks are fitted to a target sample and then inherit *that sample's* name, so a name makes
three claims that nothing has ever checked: that the target was what its corpus called it, that the
engine reached the target, and that the category label describes the result. Two of those are known
to be shaky -- `nsynth.py`'s CAT_FAMILY substitutes families it does not have (Lead <- guitar,
Pad <- organ, FX <- reed), and the fits stop at a median loss of 22.9-28.5, which is not zero.

This renders every preset in a bank on `engine.py` and scores it against text with CLAP:

  category    which of the 8 category prompts CLAP ranks first, against the label the bank carries.
              An accuracy and a confusion matrix -- the headline number. Each prompt is centred
              on its own mean before the ranking: the captions are not on a common scale, and
              uncentred the ranking largely reports which sentence CLAP likes rather than what
              the audio is. It is worth a lot -- on presets_soundfont.json, Pad, Strings and FX
              all scored a flat 0% uncentred and go to 25/50/25% once centred, and the bank's
              agreement rises 20.3% -> 29.7%. See category_report, and #19.
  own-name    where the preset's own name lands when every name in the bank is a candidate.
              Rank, not cosine: CLAP similarities are not calibrated and a bare 0.31 means nothing,
              while "your own name came 64th of 128" means exactly what it says.
  suggestion  the best-scoring descriptor from a small timbre vocabulary, as a rename candidate.

Chance is 1/8 for category and rank 64.5 for own-name in a 128-slot bank. Read the numbers against
that, and remember CLAP is judging a 1.6 s single note of an FPGA synth, which is not what it was
trained on -- a low score is a hypothesis about the bank, not a verdict on it.

Chance is the wrong yardstick on its own, though: a bank inherits its labels from a corpus whose own
labels CLAP may not agree with either. `--targets <source>` scores the corpus samples instead of our
fits, which is the ceiling -- a category the targets already fail is mislabelled upstream and no
amount of fitting will recover it, while a category the targets keep and the bank loses is the
engine or the search dropping it.

    uv sync --extra deepfit
    uv run python presetgen/name_audit.py [bank ...]        # default: every webui/presets_*.json
    uv run python presetgen/name_audit.py --targets soundfont        # the ceiling, not the bank
"""
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine                                                              # noqa: E402
import loss_deep                                                           # noqa: E402
import search                                                              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEBUI = os.path.join(HERE, "..", "webui")

# One prompt per bank category. Phrased as CLAP's training captions are -- a short descriptive
# sentence, not a bare noun -- because a bare noun scores erratically. Every prompt describes a
# SINGLE NOTE: "pad chord" or "violin section" would ask CLAP about polyphony the audit does not
# render, and would score the engine down for a confound in the question.
CATEGORY_PROMPT = {
    "Bass":    "a synthesizer bass note, low and deep",
    "Lead":    "a synthesizer lead melody note, cutting and bright",
    "Pad":     "a soft sustained synthesizer pad, slow and warm",
    "Pluck":   "a short plucked synthesizer note that decays quickly",
    "Keys":    "an electric piano or keyboard note",
    "Brass":   "a brass instrument note, like a trumpet",
    "Strings": "a bowed string instrument note, like a violin",
    "FX":      "a strange synthesizer sound effect, noisy and unmusical",
}

# The render window. Defaults to the one the fits used (search.GATE_S/TAIL_S) so the audit hears
# what the search heard; GATE=4 is the sensitivity check that asks how much of a slow category's
# score is the 1.6 s window cutting its attack off rather than the patch. Bank path only:
# --targets plays each corpus WAV at its own full length, so GATE does nothing there and the
# ceiling cannot be re-asked this way -- quote the sensitivity check about banks, not about #19.
GATE_S = float(os.environ.get("GATE", search.GATE_S))
TAIL_S = float(os.environ.get("TAIL", search.TAIL_S))

# Rename vocabulary: timbre first, then a body noun. Deliberately small -- a big vocabulary just
# finds whichever phrase CLAP happens to like.
ADJECTIVES = ["bright", "dark", "warm", "harsh", "hollow", "metallic", "buzzy", "noisy",
              "soft", "thin", "fat", "muffled", "glassy", "gritty", "clean", "detuned"]
BODIES = ["bass", "lead", "pad", "pluck", "electric piano", "brass", "strings", "bell",
          "organ", "sound effect", "drone", "noise"]

_NOTE_RE = re.compile(r"\s+[A-G]#?-?\d$")            # trailing "G3" / "C4" pitch tag
_ID_RE = re.compile(r"\s+\d+$")                      # trailing corpus id, e.g. "Bass Synth 135"
_NN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_of(name, category):
    """The note the preset was fitted at: the name's own pitch tag if it has one, else the
    category base note nsynth.py used (Bass at C3, everything else C4)."""
    m = _NOTE_RE.search(name)
    if m:
        tag = m.group(0).strip()
        octv = int(tag[-1])
        return _NN.index(tag[:-1]) + (octv + 1) * 12
    return 48 if category == "Bass" else 60


def clean_name(name):
    """The descriptive part of a preset name -- the corpus id and pitch tag claim nothing."""
    return _ID_RE.sub("", _NOTE_RE.sub("", name)).strip() or name


def _confusion(cats, truth, pred, title):
    acc = float((pred == truth).mean())
    print(f"{title}: {acc*100:5.1f}%  (chance {100/len(cats):.1f}%)")
    conf = np.zeros((len(cats), len(cats)), dtype=int)
    for t, q in zip(truth, pred):
        conf[t, q] += 1
    print("            " + "".join(f"{c[:4]:>6}" for c in cats) + "   <- CLAP heard")
    for i, c in enumerate(cats):
        n = conf[i].sum()
        hit = conf[i, i] / n * 100 if n else 0
        print(f"  {c:9} " + "".join(f"{v:6d}" for v in conf[i]) + f"   {hit:5.1f}% kept")
    return acc


def category_report(A, truth):
    """Which category prompt does CLAP rank first, against the label the corpus/bank carries?

    Ranks the prompts per sample, but only after subtracting each prompt's own mean, because a
    bare argmax over CLAP cosines compares numbers that are not on the same scale. Each caption
    carries a baseline affinity of its own, and it swamps the audio: on --targets soundfont the
    baselines run 0.02 (FX) to 0.33 (Keys), a 0.31 spread against a within-prompt sd of 0.11
    across all 128 samples, so the ranking largely reports which sentence CLAP likes in the
    abstract. Uncorrected, "a strange synthesizer sound effect" peaks below five other prompts'
    *averages* -- no audio can make it win -- and the columns for the low-baseline prompts come
    out empty, which reads as a category the corpus got wrong rather than a question this audit
    asked badly. The printed spread is the tell: while it stays well above the within-prompt sd,
    the uncentred matrix is mostly measuring the prompts. See #19 for what that cost.

    Centring assumes the categories are balanced -- true of every bank and of --targets, both
    8 or 16 per category by construction, but LIMIT slices the head off a bank and breaks it,
    so that case says so and falls back. The uncentred number is printed either way: it is what
    every earlier reading of this audit reported, and dropping it would silently rebase them.
    """
    cats = list(CATEGORY_PROMPT)
    C = loss_deep.clap_text_emb([CATEGORY_PROMPT[c] for c in cats])
    S = A @ C.T                                                            # [N, 8], uncalibrated
    counts = np.bincount(truth, minlength=len(cats))
    balanced = len(set(counts.tolist())) == 1

    raw_pred = S.argmax(axis=1)
    if not balanced:
        print("category prompts left uncentred: the sample is unbalanced "
              f"({', '.join(f'{c}={n}' for c, n in zip(cats, counts))}), so a per-prompt mean "
              "would absorb the class skew along with the prompt bias")
        return _confusion(cats, truth, raw_pred, "category agreement"), raw_pred

    bias = S.mean(axis=0)
    print(f"prompt bias: {' '.join(f'{c[:4]} {m:.2f}' for c, m in zip(cats, bias))}  "
          f"(spread {bias.max()-bias.min():.2f} vs within-prompt sd {S.std(axis=0).mean():.2f})")
    _confusion(cats, truth, raw_pred, "category agreement, uncentred")
    print()
    pred = (S - bias).argmax(axis=1)
    acc = _confusion(cats, truth, pred, "category agreement")
    return acc, pred


def audit_targets(source):
    """The ceiling: score the corpus samples our banks are fitted to, not the fits."""
    import importlib
    ns = importlib.import_module(source)
    targets = ns.list_targets(per_cat=int(os.environ.get("PER_CAT", 16)))
    cats = list(CATEGORY_PROMPT)
    print(f"\n=== {source} targets: {len(targets)} samples " + "=" * 20)
    waves, sr = [], None
    for _, _, path, _ in targets:
        a, sr = ns.load(path)
        waves.append(np.asarray(a, dtype=np.float64))
    A = loss_deep.clap_audio_emb(waves, sr)
    acc, _ = category_report(A, np.array([cats.index(t[0]) for t in targets]))
    return {"bank": f"{source} targets", "n": len(targets), "category_agreement": acc}


def audit(path, limit=None):
    bank = json.load(open(path))
    presets = bank["presets"][:limit]
    label = os.path.basename(path)
    print(f"\n=== {label}: {len(presets)} presets, gate {GATE_S}s " + "=" * 20)

    waves = []
    for p in presets:
        w = engine.render(p["values"], note=note_of(p["name"], p["category"]),
                          gate_s=GATE_S, tail_s=TAIL_S)
        waves.append(np.asarray(w, dtype=np.float64))
    A = loss_deep.clap_audio_emb(waves, engine.SR)                        # [N, D], L2-normalized

    # ---- category: does the sound land in the box the bank filed it under?
    cats = list(CATEGORY_PROMPT)
    acc, pred = category_report(A, np.array([cats.index(p["category"]) for p in presets]))

    # ---- own name: rank among every name in the bank
    names = [clean_name(p["name"]) for p in presets]
    uniq = sorted(set(names))
    T = loss_deep.clap_text_emb([f"the sound of a {n}" for n in uniq])
    S = A @ T.T                                                            # [N, U]
    idx = {n: i for i, n in enumerate(uniq)}
    ranks = []
    for i, n in enumerate(names):
        own = S[i, idx[n]]
        ranks.append(int((S[i] > own).sum()) + 1)
    ranks = np.array(ranks)
    print(f"own-name rank among {len(uniq)} names: median {np.median(ranks):.0f}, "
          f"chance {(len(uniq)+1)/2:.0f}; top-1 {float((ranks==1).mean())*100:.0f}%, "
          f"top-10 {float((ranks<=10).mean())*100:.0f}%")

    # ---- rename candidates for the worst offenders
    V = [f"a {a} {b} sound" for a in ADJECTIVES for b in BODIES]
    VE = loss_deep.clap_text_emb(V)
    best = (A @ VE.T).argmax(axis=1)
    order = np.argsort(-ranks)
    print("  worst name/sound mismatches:")
    for i in order[:8]:
        print(f"    {presets[i]['category']:8} {presets[i]['name'][:26]:26} "
              f"rank {ranks[i]:4d}   CLAP hears: {V[best[i]][2:-6]}")
    return {"bank": label, "n": len(presets), "category_agreement": acc,
            "median_own_name_rank": float(np.median(ranks)), "n_names": len(uniq),
            "presets": [{"name": p["name"], "category": p["category"],
                         "clap_category": cats[q], "own_name_rank": int(r),
                         "clap_hears": V[b]}
                        for p, q, r, b in zip(presets, pred, ranks, best)]}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    limit = int(os.environ.get("LIMIT", "0")) or None
    if "--targets" in sys.argv:
        out = [audit_targets(s) for s in (args or ["soundfont"])]
        json.dump(out, open(os.path.join(HERE, "name_audit_targets.json"), "w"), indent=1)
        sys.exit(0)
    banks = args or sorted(f for f in
                           [os.path.join(WEBUI, x) for x in os.listdir(WEBUI)]
                           if re.search(r"presets_\w+\.json$", f))
    # warm the JIT once so the first render is not the slow one
    engine.render(engine._DEFAULTS, gate_s=GATE_S, tail_s=TAIL_S)
    out = [audit(b, limit) for b in banks]
    dest = os.path.join(HERE, os.environ.get("OUT", "name_audit.json"))
    json.dump(out, open(dest, "w"), indent=1)
    print(f"\nwrote {dest}")
