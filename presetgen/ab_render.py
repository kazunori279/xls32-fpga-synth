"""Render the blind A/B listening set: the target, and the two fits of it, as WAVs.

`bank_compare.py` says the clap+stft bank is 29% closer under CLAP and 2% further under the FFT
loss. Neither number is an ear, and the whole reason this branch exists is that the FFT loss's own
numbers looked fine while the patches did not. So the two banks get compared the only way that
settles it, and the comparison is made blind because the person judging it also built one of them.

Per trial this writes three files -- the GM target, and both banks' fit of that same target -- and
records which bank is A and which is B behind a flip the page does not read until a vote is in.
Everything lands in webui/ab/, which is gitignored: ~30 MB of WAV regenerable in a minute.

Each pair is then asked TWICE, in both playback orders, and only a pair that names the same bank
both ways counts as a preference. That is not caution, it is a repair: the first run of this rig
came back with the listener choosing the first-played clip in 19 of 22 decided trials, p = 0.001,
which is a larger and cleaner effect than anything the banks produced. Balancing A/B across trials
-- which this file already did -- keeps a position preference from favouring a bank while leaving
it free to decide every individual trial, so the bank split it reported was noise wearing a number.
`ab_tally.py` reads the pairs back and reports the order effect alongside the result.

Trials are drawn evenly across categories and, within a category, sampled across the CLAP spread in
bands rather than taken from the top of it. Taking the top was the original rule, on the assumption
that the pairs the metric separates most are the pairs an ear separates most; the counterbalanced
run measured that assumption and it does not hold. Among the pairs the listener decided, spread did
not predict which bank won (rho = +0.07), and the order-dependent pairs spanned 0.069-0.320. So the
set now samples the range and records the band, and the relationship can be estimated instead of
relied on.

The two questions are two SESSIONS, not two rows of one screen. Asked together they came back
identical on all 48 hearings, ties included, which is #20's first item and was reproduced exactly.
They also get different audio, because they are different questions: `closer` is a single note with
the target played immediately before each candidate, since one note is all the target exists as;
`better` is a short phrase and no target, since whether a patch is worth playing is the thing a
single held note answers worst.

The two banks are arguments, not constants. They were once the `_stft`/`_clapstft` pair spelled
into this file; those filenames no longer exist, and the question this rig answers is asked again
every time the fitting pipeline changes -- most recently by the $SPACE arms in issue #16, which are
four banks over the same 64 targets and need pairing on demand. The FIRST bank listed is the
incumbent.

    uv sync --extra deepfit
    uv run python presetgen/ab_render.py webui/presets_a.json webui/presets_b.json \
        [trials-per-category] [source]
"""
import json
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine                                                              # noqa: E402
import loss_deep                                                           # noqa: E402
import protocol                                                            # noqa: E402
from name_audit import note_of                                             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "webui", "ab"))
CATS = ["Bass", "Lead", "Pad", "Pluck", "Keys", "Brass", "Strings", "FX"]
SEED = int(os.environ.get("SEED", 20260816))
BLOCK = 6                # pairs per counterbalancing block; see build_sequence()
MIN_SEP = 4              # trials between the two hearings of one pair

# The phrase for the `better` pass, as semitone offsets from the preset's fitted note and onsets in
# seconds. Not a tune -- an ascending figure, a step back down, and a held last note, which is the
# smallest thing that exercises what a single held note cannot: how the attack behaves when notes
# arrive faster than the envelope finishes, whether the filter tracks pitch, and what the release
# sounds like under overlap. The last onset gets the corpus window so the tail is the same tail the
# fit was scored on.
#
# Deliberately short and deliberately dull. A phrase with any musical character of its own starts
# collecting votes about the phrase.
PHRASE = [(0, 0.00), (7, 0.30), (12, 0.60), (7, 0.90), (0, 1.20), (12, 1.50)]
# argv is read inside main(), not here: build_previews.py imports write_wav from this module and
# would otherwise inherit this file's idea of what argv[1] means.


def write_wav(path, x, sr):
    """16-bit mono. Peak-normalized to -3 dBFS: level is not what is being judged, and a bank that
    happened to fit louder would win on loudness alone -- the oldest way to rig a listening test."""
    x = np.asarray(x, dtype=np.float64)
    peak = float(np.max(np.abs(x)))
    if peak > 0:
        x = x / peak * 0.708
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def render_phrase(values, note, win):
    """PHRASE as one buffer: each note rendered on its own and summed at its onset.

    engine.render() is one note per call, so this is addition rather than the RTL's voice
    allocation -- overlapping notes here share an oscillator seed and an LFO reset, and a real
    polyphonic board would not. That is a fair approximation for a listening test, where both banks
    get exactly the same treatment and the comparison is between them, and it would NOT be fair as
    a target for fitting. Nothing in the search path calls this.

    Short notes get a short gate; the last one gets the corpus window so its release is the release
    the bank was fitted with.
    """
    last = max(t for _, t in PHRASE)
    step = min(t2 - t1 for (_, t1), (_, t2) in zip(PHRASE, PHRASE[1:]))
    out = np.zeros(int((last + win[0] + win[1]) * engine.SR) + 1, dtype=np.float64)
    for semis, at in PHRASE:
        gate = win[0] if at == last else step
        w = engine.render(values, note=note + semis, gate_s=gate, tail_s=win[1])
        k = int(at * engine.SR)
        out[k:k + len(w)] += np.asarray(w, dtype=np.float64)[:len(out) - k]
    return out


def stratified(rows, per_cat, rng):
    """Per category, one pair from each band of the CLAP spread rather than the widest `per_cat`.

    Taking the top of the spread was the original rule and it selected for pairs the METRIC finds
    hard, which is not the same as pairs an ear can separate -- #20 flagged the assumption and the
    counterbalanced run measured it: among the pairs the listener decided, spread did not predict
    which bank won (rho = +0.07), and the pairs that came back order-dependent spanned 0.069-0.320,
    nearly the whole range. Sampling the range instead lets the relationship be estimated rather
    than assumed, and stops the set consisting entirely of pairs that turn out to be ties.
    """
    out = []
    for cat in CATS:
        pool = sorted([r for r in rows if r["category"] == cat], key=lambda r: r["spread"])
        if not pool:
            continue
        n = min(per_cat, len(pool))
        edges = [round(k * len(pool) / n) for k in range(n + 1)]
        for k in range(n):
            band = pool[edges[k]:edges[k + 1]] or [pool[min(edges[k], len(pool) - 1)]]
            pick = dict(band[int(rng.integers(len(band)))])
            pick["band"] = k                       # 0 = narrowest spread in its category
            out.append(pick)
    return out


def build_sequence(ids, rng):
    """Each pair asked twice, once in each playback order, as [{id, rev}, ...].

    The first run of this rig balanced which bank was A and thought that was enough; it is not.
    Balancing stops a position preference from favouring one bank and leaves it free to decide
    individual trials -- and it did, hard: the listener picked the first-played clip in 19 of 22
    decided trials, p = 0.001, against a bank split at p = 0.52. A balanced design converts that
    bias into noise rather than removing it, so the split it produced was 22 votes about playback
    order and 3 about the banks.

    Asking each pair both ways makes the order effect measurable instead of invisible: a pair where
    the same bank wins both ways is a preference, a pair that follows the order is not, and the
    count of each says how much of the result was ever about audio.

    Interleaved in blocks rather than as two passes, so that a listener who stops early still
    leaves whole pairs behind. MIN_SEP is not decoration: concordance is evidence only if the second
    hearing is an independent judgement, and a pair heard again two trials later can be answered
    from memory of the first answer, which manufactures agreement and reads as a preference.
    """
    order = list(range(len(ids)))
    rng.shuffle(order)
    seq = []
    for s in range(0, len(order), BLOCK):
        blk = order[s:s + BLOCK]
        rev = [j % 2 == 1 for j in range(len(blk))]          # balanced within the block too
        rng.shuffle(rev)
        by_pair = dict(zip(blk, rev))
        first = list(blk)
        rng.shuffle(first)
        for _ in range(2000):
            second = list(blk)
            rng.shuffle(second)
            if min(len(blk) + second.index(k) - j for j, k in enumerate(first)) >= MIN_SEP:
                break
        else:                                                # only for a tiny final block
            second = first[MIN_SEP // 2:] + first[:MIN_SEP // 2]
        seq += [{"id": ids[k], "rev": by_pair[k]} for k in first]
        seq += [{"id": ids[k], "rev": not by_pair[k]} for k in second]
    return seq


def main():
    import importlib
    argv = sys.argv[1:]
    paths = [a for a in argv if a.endswith(".json")]
    rest = [a for a in argv if not a.endswith(".json")]
    if len(paths) != 2:
        sys.exit("usage: ab_render.py bank_a.json bank_b.json [trials-per-category] [source]")
    per_cat = int(rest[0]) if rest else 3
    source = rest[1] if len(rest) > 1 else "soundfont"
    # Tag = the filename stem minus "presets_", which is what the manifest records as the answer
    # key. Left is the incumbent.
    banks_in = [(os.path.basename(p)[8:-5] if os.path.basename(p).startswith("presets_")
                 else os.path.basename(p)[:-5], p) for p in paths]
    ns = importlib.import_module(source)
    os.makedirs(OUT, exist_ok=True)
    win = protocol.window(ns)         # the corpus's window, not the global one -- see protocol.py
    engine.render(engine._DEFAULTS, gate_s=win[0], tail_s=win[1])

    banks = {}
    for tag, fn in banks_in:
        d = json.load(open(fn))
        banks[tag] = {p["name"]: p for p in d["presets"]}
    shared = [n for n in banks[banks_in[0][0]] if n in banks[banks_in[1][0]]]
    targets = {name: path for _, name, path, _ in ns.list_targets(per_cat=16) if name in shared}

    # How far apart are the two fits of the same target? Measured under CLAP, because that is the
    # axis the two banks are known to differ on -- a pair the FFT loss separates but CLAP does not
    # is not a test of the change that was made.
    loss_deep.select("clap")
    rows = []
    for name in shared:
        p0, p1 = banks[banks_in[0][0]][name], banks[banks_in[1][0]][name]
        note = note_of(name, p0["category"])
        a0, a1 = (engine.render(p["values"], note=note, gate_s=win[0], tail_s=win[1])
                  for p in (p0, p1))
        spread = float(loss_deep.loss(a0, a1, a_sr=engine.SR, b_sr=engine.SR))
        rows.append({"name": name, "category": p0["category"], "note": note, "spread": spread})
    print(f"{len(rows)} shared presets; pair spread under CLAP: "
          f"min {min(r['spread'] for r in rows):.3f} max {max(r['spread'] for r in rows):.3f}")

    rng = np.random.default_rng(SEED)
    trials = stratified(rows, per_cat, rng)
    rng.shuffle(trials)                                   # category order must not cue the listener

    # Which bank plays as A. Balanced and then shuffled, NOT an independent coin per trial: an
    # independent flip is unbiased in expectation and lopsided in any one draw, and this seed gave
    # 16/8. Position is not neutral in a forced choice -- a listener with any first-item preference
    # would hand that to whichever bank drew the long straw, and with n=24 a 16/8 split is enough
    # of a thumb to matter. An odd trial count leaves one extra, which goes to the incumbent.
    flips = [i % 2 == 1 for i in range(len(trials))]
    rng.shuffle(flips)

    manifest = []
    for i, t in enumerate(trials):
        flip = flips[i]
        first, second = (banks_in[1][0], banks_in[0][0]) if flip else (banks_in[0][0], banks_in[1][0])
        stem = f"t{i:02d}"
        audio, sr = ns.load(targets[t["name"]])
        write_wav(os.path.join(OUT, f"{stem}_target.wav"), audio, sr)
        for slot, tag in (("a", first), ("b", second)):
            vals = banks[tag][t["name"]]["values"]
            # Single note for the `closer` pass -- it is the only thing the target exists as -- and
            # the phrase for `better`, which does not use the target and is the question a single
            # held note answers worst.
            write_wav(os.path.join(OUT, f"{stem}_{slot}.wav"),
                      engine.render(vals, note=t["note"], gate_s=win[0], tail_s=win[1]), engine.SR)
            write_wav(os.path.join(OUT, f"{stem}_{slot}P.wav"),
                      render_phrase(vals, t["note"], win), engine.SR)
        manifest.append({"id": stem, "name": t["name"], "category": t["category"],
                         "spread": round(t["spread"], 4), "band": t["band"],
                         "a": first, "b": second})
        print(f"  {stem}  {t['category']:8} {t['name']:22} spread {t['spread']:.3f}  band {t['band']}")

    # One sequence per question, drawn from separate shuffles, because the two questions are now
    # separate sessions. Asked on one screen they came back identical on all 48 hearings, ties
    # included -- two rows of the same buttons, and the cheapest way to answer the second is to
    # repeat the first, which is #20's first item. Splitting them means the `better` judgement
    # cannot see the `closer` one: different session, different order, different audio.
    ids = [t["id"] for t in manifest]
    sequences = {q: build_sequence(ids, rng) for q in ("closer", "better")}

    comparison = "-vs-".join(b[0] for b in banks_in)
    json.dump({"source": source, "banks": [b[0] for b in banks_in], "seed": SEED,
               "comparison": comparison, "block": BLOCK, "phrase": PHRASE,
               "trials": manifest, "sequences": sequences},
              open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
    n = len(manifest)
    print(f"\nwrote {n} pairs -> {OUT}")
    print(f"  each question is its own session of {2 * n} trials: {n} pairs x 2 playback orders")
    print("  closer  single note, target played immediately before each candidate")
    print(f"  better  a {len(PHRASE)}-note phrase, no target")
    print("\n  python3 -m http.server 8765 -d webui")
    print("  http://127.0.0.1:8765/ab_check.html      # pick the question and enter a listener id")
    print(f"  uv run python presetgen/ab_tally.py presetgen/listening/{comparison}")


if __name__ == "__main__":
    main()
