#!/usr/bin/env python3
"""Render the blind substitution set: did halving the bank cost a sound, or only a slot? (#22)

`consolidate.py` halved `presets_soundfont.json` from 128 to 64 and defended it with two numbers:
mean nearest-neighbour CLAP distance 0.060 -> 0.083 (the survivors are better spread), and "all 46
instruments kept". #22's complaint is that nobody has heard either claim, and the second one turns
out to need hearing most, because it is true by name and questionable by sound:

  For 48 of the 64 dropped presets, the nearest-SOUNDING survivor is a different instrument than
  the one they share a name with. Dropping `Choir Pad` leaves `Choir Pad G4` at a CLAP distance of
  0.317, while `Rain FX G4` sits at 0.079. The instrument was kept; the sound was not.

  Median distance from a dropped preset to its own instrument's surviving slot is 0.114, against
  the 0.060-0.083 that `consolidate.py` quoted. Those measure different things -- that pair is the
  spacing among the slots that stayed, and this is the gap left by the ones that went -- and only
  the second one is about what was lost.

So this asks an ear the one question the metric is a proxy for: **if you had the survivor, would
you miss the one that went?** Two sounds per trial, no names, no distances, no indication of which
is which. Yes / no / can't tell.

The trials are stratified across the distance range rather than taken from the top, because the
useful result is not "the worst ones are bad" -- it is whether the answers track the distance at
all. If they do, `consolidate.py`'s metric is doing its job and the top of the list is a re-voicing
list. If a listener misses sounds at 0.05 as often as at 0.30, the metric is not measuring
substitutability and the halving was defended with the wrong number.

Every pair is asked twice, once in each playback order, and only a pair answered the same way both
times counts (#20). "Are these two interchangeable" is a property of the pair, so the order should
not enter into it -- which is exactly why a verdict that flips with the order is worth counting
rather than averaging away. The first run of this page asked once, and the only structure in its 24
answers pointed that way: `different` in 8 of 12 trials where the dropped preset played second
against 4 of 12 where it played first (Fisher p = 0.22 -- not a finding, and not something a single
hearing per pair can rule out).

    uv sync --extra deepfit
    uv run python presetgen/consolidate_check.py [trials] [old-bank.json] [--again]
    python3 -m http.server 8765 -d webui   # then http://127.0.0.1:8765/sub_check.html

The old 128-slot bank is `git show b9f77b7:webui/presets_soundfont.json`; pass it as a file.
Output lands in webui/sub/, which is gitignored alongside webui/ab/.

Re-running draws from the dropped presets no stored session has asked about yet, so a second
sitting extends the coverage instead of re-testing the listener's memory -- 64 dropped presets at
24 a sitting is between two and three sessions to see all of them. `--again` reuses them anyway.
"""
import glob
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import engine                                                              # noqa: E402
import loss_deep                                                           # noqa: E402
import protocol                                                            # noqa: E402
from ab_render import build_sequence, write_wav                            # noqa: E402
from consolidate import instrument                                         # noqa: E402
from name_audit import note_of                                             # noqa: E402

OUT = os.path.join(REPO, "webui", "sub")
NEW = os.path.join(REPO, "webui", "presets_soundfont.json")
STORE = os.path.join(HERE, "listening", "consolidate-128-to-64")
PREV_REV = "b9f77b7"                     # the 128-slot bank, before ccd17d7 refit + consolidated
SEED = int(os.environ.get("SEED", 20260821))


def already_heard():
    """Dropped presets some stored session already asked about.

    64 dropped presets and 24 trials a sitting means a second session should ask about the ones
    nobody has heard yet, not the same 24 again. Re-asking the same pairs would measure whether the
    listener repeats themselves -- and they have already been told the answers, since the page
    reveals the names and gaps at the end. Excluding them makes consecutive sessions independent
    samples that pool by band, which is what the band question needs.
    """
    seen = set()
    for p in sorted(glob.glob(os.path.join(STORE, "*.json"))):
        try:
            seen |= {r["dropped"] for r in json.load(open(p)).get("votes", []) if "dropped" in r}
        except (OSError, ValueError):
            continue
    return seen


def load_prev(path=None):
    if path:
        return json.load(open(path))["presets"]
    out = subprocess.run(["git", "show", f"{PREV_REV}:webui/presets_soundfont.json"],
                         cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"cannot read {PREV_REV}: {out.stderr.strip()}")
    return json.loads(out.stdout)["presets"]


def survey(old, new_names, win):
    """Every dropped preset, with the surviving slot of its own instrument and the CLAP gap."""
    engine.render(engine._DEFAULTS, gate_s=win[0], tail_s=win[1])
    waves = [np.asarray(engine.render(p["values"], note=note_of(p["name"], p["category"]),
                                      gate_s=win[0], tail_s=win[1]), dtype=np.float64)
             for p in old]
    A = loss_deep.clap_audio_emb(waves, engine.SR)
    kept = [i for i, p in enumerate(old) if p["name"] in new_names]
    drop = [i for i, p in enumerate(old) if p["name"] not in new_names]
    S = A[drop] @ A[kept].T
    rows = []
    for r, i in enumerate(drop):
        inst = instrument(old[i]["name"])
        sib = [k for k, j in enumerate(kept) if instrument(old[j]["name"]) == inst]
        if not sib:
            continue                     # cannot happen while consolidate.py keeps one per
            # instrument, but this reads the bank rather than trusting that it did
        best_sib = sib[int(np.argmax(S[r, sib]))]
        near = int(np.argmax(S[r]))
        rows.append({"dropped": old[i]["name"], "category": old[i]["category"],
                     "survivor": old[kept[best_sib]]["name"],
                     "dist": 1.0 - float(S[r, best_sib]),
                     "nearest": old[kept[near]]["name"],
                     "dist_nearest": 1.0 - float(S[r, near]),
                     "nearest_same_instrument": instrument(old[kept[near]]["name"]) == inst,
                     "_drop": i, "_keep": kept[best_sib]})
    return rows, waves


def main():
    argv = sys.argv[1:]
    again = "--again" in argv                 # ask about the stored pairs again anyway
    argv = [a for a in argv if a != "--again"]
    paths = [a for a in argv if a.endswith(".json")]
    rest = [a for a in argv if not a.endswith(".json")]
    n_trials = int(rest[0]) if rest else 24

    old = load_prev(paths[0] if paths else None)
    new_names = {p["name"] for p in json.load(open(NEW))["presets"]}
    win = protocol.window("soundfont")
    rows, waves = survey(old, new_names, win)
    d = np.array([r["dist"] for r in rows])
    diff = sum(1 for r in rows if not r["nearest_same_instrument"])
    print(f"{len(rows)} dropped presets, all with a surviving slot of the same instrument")
    print(f"  gap to that slot: median {np.median(d):.3f}, worst {d.max():.3f}, best {d.min():.3f}")
    print(f"  for {diff} of {len(rows)}, some OTHER instrument's slot sounds nearer")

    pool_rows = rows
    heard = set() if again else already_heard()
    if heard:
        fresh = [r for r in rows if r["dropped"] not in heard]
        print(f"  {len(heard)} already asked about in {os.path.relpath(STORE, REPO)}; "
              f"drawing from the remaining {len(fresh)}   (--again to reuse them)")
        if len(fresh) >= n_trials:
            pool_rows = fresh
        else:
            print(f"  !! only {len(fresh)} unheard left, fewer than {n_trials} -- using all "
                  f"{len(rows)}, so this set repeats pairs")

    # Stratified across the gap, in thirds, so the answers can be read against the distance. Taking
    # the top n would only ask whether the worst cases are bad, which is not in doubt and is not
    # what validates the metric.
    order = sorted(pool_rows, key=lambda r: -r["dist"])
    band = max(1, len(order) // 3)
    per = [n_trials // 3 + (1 if k < n_trials % 3 else 0) for k in range(3)]
    rng = np.random.default_rng(SEED)
    trials = []
    for k, lo in enumerate((0, band, 2 * band)):
        pool = order[lo:lo + band] if k < 2 else order[lo:]
        pick = rng.choice(len(pool), size=min(per[k], len(pool)), replace=False)
        trials.extend(pool[int(j)] for j in sorted(pick))
    rng.shuffle(trials)

    # Which of the two plays first, balanced then shuffled -- an independent coin per trial is
    # lopsided in any one draw, and position is not neutral in a forced choice (see ab_render.py).
    flips = [i % 2 == 1 for i in range(len(trials))]
    rng.shuffle(flips)

    os.makedirs(OUT, exist_ok=True)
    manifest = []
    for i, (t, flip) in enumerate(zip(trials, flips)):
        stem = f"s{i:02d}"
        # Rendered from the same array the CLAP gap was measured on, so the number in the manifest
        # is about the audio in the file and not about a second render of the same preset.
        pair = [("dropped", waves[t["_drop"]]), ("survivor", waves[t["_keep"]])]
        if flip:
            pair.reverse()
        for slot, (role, w) in zip(("1", "2"), pair):
            write_wav(os.path.join(OUT, f"{stem}_{slot}.wav"), w, engine.SR)
        manifest.append({"id": stem, "category": t["category"],
                         "dropped": t["dropped"], "survivor": t["survivor"],
                         "dist": round(t["dist"], 4),
                         "nearest": t["nearest"], "dist_nearest": round(t["dist_nearest"], 4),
                         "nearest_same_instrument": t["nearest_same_instrument"],
                         # which numbered slot holds the one that was DROPPED; the page must not
                         # read this until the last vote is in
                         "gone": "2" if flip else "1"})
        print(f"  {stem}  {t['category']:8} {t['dropped']:20} -> {t['survivor']:20} "
              f"gap {t['dist']:.3f}")

    # Each pair asked TWICE, once in each playback order, using ab_render's block builder. The
    # verdict here is a property of the pair -- "are these two interchangeable" is the same question
    # whichever plays first -- so a verdict that changes with the order is not a tie, it is the
    # protocol failing to ask, exactly as in the A/B rig. The first run of this page asked once and
    # the only structure in it was in that direction: the dropped preset was called `different` in
    # 8 of 12 trials when it played second and 4 of 12 when it played first. Fisher p = 0.22, so not
    # a finding -- but not something 24 single hearings can rule out either.
    sequence = build_sequence([t["id"] for t in manifest], rng)

    json.dump({"banks": ["prev128", "soundfont"], "comparison": "consolidate-128-to-64",
               "seed": SEED, "window": list(win),
               "survey": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
               "trials": manifest, "sequence": sequence},
              open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
    print(f"\nwrote {len(manifest)} pairs -> {OUT}")
    print(f"  {len(sequence)} trials: each pair heard twice, once in each playback order")
    print("  python3 -m http.server 8765 -d webui   # then http://127.0.0.1:8765/sub_check.html")
    print("  !! hard-reload the page (cmd-shift-R). A re-render reuses the filenames, and a cached")
    print("  !! sub_check.html will happily re-ask the pairs the last session already answered.")
    print("  uv run python presetgen/sub_tally.py presetgen/listening/consolidate-128-to-64")


if __name__ == "__main__":
    main()
