"""Does a preset sound like its name says? A CLAP audio<->text audit of the shipped banks.

The banks are fitted to a target sample and then inherit *that sample's* name, so a name makes
three claims that nothing has ever checked: that the target was what its corpus called it, that the
engine reached the target, and that the category label describes the result. Two of those are known
to be shaky -- `nsynth.py`'s CAT_FAMILY substitutes families it does not have (Lead <- guitar,
Pad <- organ, FX <- reed), and the fits stop at a median loss of 22.9-28.5, which is not zero.

This renders every preset in a bank on `engine.py` and scores it against text with CLAP:

  category    which of the 8 category prompts CLAP ranks first, against the label the bank carries.
              An accuracy and a confusion matrix -- the headline number.
  own-name    where the preset's own name lands when every name in the bank is a candidate.
              Rank, not cosine: CLAP similarities are not calibrated and a bare 0.31 means nothing,
              while "your own name came 64th of 128" means exactly what it says.
  suggestion  the best-scoring descriptor from a small timbre vocabulary, as a rename candidate.

Chance is 1/8 for category and rank 64.5 for own-name in a 128-slot bank. Read the numbers against
that, and remember CLAP is judging a 1.6 s single note of an FPGA synth, which is not what it was
trained on -- a low score is a hypothesis about the bank, not a verdict on it.

    uv sync --extra deepfit
    uv run python presetgen/name_audit.py [bank ...]        # default: every webui/presets_*.json
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
# score is the 1.6 s window cutting its attack off rather than the patch.
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
    C = loss_deep.clap_text_emb([CATEGORY_PROMPT[c] for c in cats])
    pred = (A @ C.T).argmax(axis=1)
    truth = np.array([cats.index(p["category"]) for p in presets])
    acc = float((pred == truth).mean())
    print(f"category agreement: {acc*100:5.1f}%  (chance {100/len(cats):.1f}%)")
    conf = np.zeros((len(cats), len(cats)), dtype=int)
    for t, q in zip(truth, pred):
        conf[t, q] += 1
    print("            " + "".join(f"{c[:4]:>6}" for c in cats) + "   <- CLAP heard")
    for i, c in enumerate(cats):
        n = conf[i].sum()
        hit = conf[i, i] / n * 100 if n else 0
        print(f"  {c:9} " + "".join(f"{v:6d}" for v in conf[i]) + f"   {hit:5.1f}% kept")

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
    banks = args or sorted(f for f in
                           [os.path.join(WEBUI, x) for x in os.listdir(WEBUI)]
                           if re.search(r"presets_\w+\.json$", f))
    # warm the JIT once so the first render is not the slow one
    engine.render(engine._DEFAULTS, gate_s=GATE_S, tail_s=TAIL_S)
    out = [audit(b, limit) for b in banks]
    dest = os.path.join(HERE, os.environ.get("OUT", "name_audit.json"))
    json.dump(out, open(dest, "w"), indent=1)
    print(f"\nwrote {dest}")
