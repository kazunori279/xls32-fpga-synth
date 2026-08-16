"""NSynth target corpus: real single-note instrument samples as ground truth for matching.

Maps our 8 categories to NSynth instrument families, prefers synth-like sources
(synthetic > electronic > acoustic), and picks samples at a fixed pitch/velocity so the
target and our engine render are the SAME note (fair spectrogram compare).
NSynth audio = 16 kHz mono, note held ~3 s then released.
"""
import os, json
from collections import defaultdict
import numpy as np
import soundfile as sf

ROOT = "/tmp/nsynthv/nsynth-valid"
SR = 16000
# NSynth holds the note for ~3 s of a 4 s clip, so its release is nowhere near the 1.9 s window a
# search can afford; stretching every candidate render to 3.4 s to catch it would cost 1.8x on
# every evaluation for one segment. tail_s = 0 says so: this corpus is compared over a held note,
# protocol.segments() emits no R phase for it, and the release of an NSynth-fitted preset is
# therefore unconstrained by the fit. See protocol.py.
WINDOW = (1.9, 0.0)

# our category -> NSynth instrument_family_str (synth_lead absent in valid -> guitar for Lead)
CAT_FAMILY = {
    "Bass": "bass", "Lead": "guitar", "Pad": "organ", "Pluck": "mallet",
    "Keys": "keyboard", "Brass": "brass", "Strings": "string", "FX": "reed",
}
NOTE = {"Bass": 48}                    # default 60 (C4); bass at C3
_PITCHES = (55, 64, 67, 52, 60, 48, 72, 43, 59, 62)   # extra pitches to fill a category
_SRC_RANK = {"synthetic": 0, "electronic": 1, "acoustic": 2}
_SRC_SHORT = {"synthetic": "Synth", "electronic": "Elec", "acoustic": "Aco"}
_NN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _examples():
    with open(os.path.join(ROOT, "examples.json")) as f:
        return json.load(f)


def _pname(p):
    return f"{_NN[p % 12]}{p // 12 - 1}"


def list_targets(per_cat=16):
    """Distinct instruments first (at the category's base note), then fill with extra pitches.
    Each target rendered at the sample's actual pitch. Returns (category, name, wav, note)."""
    ex = _examples()
    out = []
    for cat, fam in CAT_FAMILY.items():
        base = NOTE.get(cat, 60)
        byinst = defaultdict(list)
        for k, v in ex.items():
            if v["instrument_family_str"] == fam:
                byinst[k.rsplit("-", 2)[0]].append((k, v))   # group by instrument (strip -pitch-vel)
        insts = sorted(byinst, key=lambda i: min(_SRC_RANK.get(v["instrument_source_str"], 9)
                                                 for _, v in byinst[i]))
        picked, used = [], set()
        for rnd, pit in enumerate([base] + [p for p in _PITCHES if p != base]):
            for inst in insts:
                cs = [(k, v) for k, v in byinst[inst] if v["pitch"] == pit]
                if not cs:
                    continue
                k, v = min(cs, key=lambda kv: abs(kv[1]["velocity"] - 100))
                if k in used:
                    continue
                used.add(k)
                src = _SRC_SHORT.get(v["instrument_source_str"], "")
                num = int(inst.rsplit("_", 1)[-1])
                name = f"{cat} {src} {num}" + ("" if rnd == 0 else f" {_pname(pit)}")
                picked.append((cat, name, os.path.join(ROOT, "audio", k + ".wav"), pit))
                if len(picked) >= per_cat:
                    break
            if len(picked) >= per_cat:
                break
        out.extend(picked)
    return out


def load(path):
    a, sr = sf.read(path, dtype="float32")
    if a.ndim > 1:
        a = a.mean(axis=1)
    # Cropped to the window, from the onset: NSynth clips start at note-on, so this is a held note
    # of exactly the length the candidate is rendered at. Without the crop the last 0.3 s compared
    # our release against the target still sustaining.
    return a[:int(sum(WINDOW) * sr)], sr


if __name__ == "__main__":
    ts = list_targets(per_cat=16)
    from collections import Counter
    print("targets:", len(ts), dict(Counter(t[0] for t in ts)))
    for cat in CAT_FAMILY:
        ex = [t[1] for t in ts if t[0] == cat][:3]
        print(f"  {cat:8} {ex}")
