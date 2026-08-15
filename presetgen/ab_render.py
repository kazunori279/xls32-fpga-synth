"""Render the blind A/B listening set: the target, and the two fits of it, as WAVs.

`bank_compare.py` says the clap+stft bank is 29% closer under CLAP and 2% further under the FFT
loss. Neither number is an ear, and the whole reason this branch exists is that the FFT loss's own
numbers looked fine while the patches did not. So the two banks get compared the only way that
settles it, and the comparison is made blind because the person judging it also built one of them.

Per trial this writes three files -- the GM target, and both banks' fit of that same target -- and
records which bank is A and which is B behind a coin flip the page does not read until a vote is
in. Everything lands in webui/ab/, which is gitignored: ~30 MB of WAV regenerable in a minute.

Trials are drawn evenly across categories and, within a category, spread across the loss gap
between the banks rather than sampled at random: the pairs where the two distances disagree most
are the ones that carry information, and a trial where both banks landed on the same patch just
spends a listener's attention. `manifest.json` records `spread` per trial so a null result can be
told apart from a set that never asked a hard question.

    uv sync --extra deepfit
    uv run python presetgen/ab_render.py [trials-per-category] [source]
"""
import json
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine                                                              # noqa: E402
import loss_deep                                                           # noqa: E402
import search                                                              # noqa: E402
from name_audit import note_of                                             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "webui", "ab"))
CATS = ["Bass", "Lead", "Pad", "Pluck", "Keys", "Brass", "Strings", "FX"]
SEED = int(os.environ.get("SEED", 20260816))
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


def main():
    import importlib
    per_cat = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    source = sys.argv[2] if len(sys.argv) > 2 else "soundfont"
    # The two banks under test, by their $LOSS name. Left is the incumbent.
    banks_in = [("stft", f"presets_{source}_stft.json"),
                ("clapstft", f"presets_{source}_clapstft.json")]
    ns = importlib.import_module(source)
    os.makedirs(OUT, exist_ok=True)
    engine.render(engine._DEFAULTS, gate_s=search.GATE_S, tail_s=search.TAIL_S)

    webui = os.path.abspath(os.path.join(HERE, "..", "webui"))
    banks = {}
    for tag, fn in banks_in:
        d = json.load(open(os.path.join(webui, fn)))
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
        a0, a1 = (engine.render(p["values"], note=note, gate_s=search.GATE_S, tail_s=search.TAIL_S)
                  for p in (p0, p1))
        spread = float(loss_deep.loss(a0, a1, a_sr=engine.SR, b_sr=engine.SR))
        rows.append({"name": name, "category": p0["category"], "note": note, "spread": spread})
    print(f"{len(rows)} shared presets; pair spread under CLAP: "
          f"min {min(r['spread'] for r in rows):.3f} max {max(r['spread'] for r in rows):.3f}")

    rng = np.random.default_rng(SEED)
    trials = []
    for cat in CATS:
        pool = sorted([r for r in rows if r["category"] == cat], key=lambda r: -r["spread"])
        trials.extend(pool[:per_cat])                     # the pairs that actually differ
    rng.shuffle(trials)                                   # category order must not cue the listener

    manifest = []
    for i, t in enumerate(trials):
        flip = bool(rng.integers(2))                      # which bank plays as A
        first, second = (banks_in[1][0], banks_in[0][0]) if flip else (banks_in[0][0], banks_in[1][0])
        stem = f"t{i:02d}"
        audio, sr = ns.load(targets[t["name"]])
        write_wav(os.path.join(OUT, f"{stem}_target.wav"), audio, sr)
        for slot, tag in (("a", first), ("b", second)):
            w = engine.render(banks[tag][t["name"]]["values"], note=t["note"],
                              gate_s=search.GATE_S, tail_s=search.TAIL_S)
            write_wav(os.path.join(OUT, f"{stem}_{slot}.wav"), w, engine.SR)
        manifest.append({"id": stem, "name": t["name"], "category": t["category"],
                         "spread": round(t["spread"], 4), "a": first, "b": second})
        print(f"  {stem}  {t['category']:8} {t['name']:22} spread {t['spread']:.3f}")

    json.dump({"source": source, "banks": [b[0] for b in banks_in], "seed": SEED,
               "trials": manifest}, open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
    print(f"\nwrote {len(manifest)} trials -> {OUT}")
    print("  python3 -m http.server 8765 -d webui   # then open http://127.0.0.1:8765/ab_check.html")


if __name__ == "__main__":
    main()
