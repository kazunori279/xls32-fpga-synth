"""GM SoundFont targets via FluidSynth: render named synth presets at known pitches.

A General-MIDI soundfont has synth presets that map cleanly to our categories (Synth Bass,
Saw/Square Lead, Pad 1-8, Synth Strings/Brass, etc.). FluidSynth renders any (program, note)
deterministically, so targets are named + pitched. Reliable and fully offline.

Interface matches nsynth.py: list_targets(per_cat) -> [(category, name, wav, note)], load(wav).
"""
import os
import numpy as np
import soundfile as sf

# help ctypes find brew's libfluidsynth before importing the wrapper
os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib:/usr/local/lib")
import fluidsynth  # noqa: E402

SF2 = "/tmp/sf/MuseScore_General.sf3"
SR = 44100
CACHE = os.path.join(os.path.dirname(__file__), "targets_soundfont")
GATE_S, TAIL_S = 1.5, 0.5

# GM program (0-indexed) -> display name, grouped into our categories.
_GM = {
    38: "Synth Bass 1", 39: "Synth Bass 2", 87: "Bass Lead",
    80: "Square Lead", 81: "Saw Lead", 82: "Calliope", 83: "Chiff",
    84: "Charang", 85: "Voice Lead", 86: "Fifths",
    88: "New Age Pad", 89: "Warm Pad", 90: "Polysynth", 91: "Choir Pad",
    92: "Bowed Pad", 93: "Metallic Pad", 94: "Halo Pad", 95: "Sweep Pad",
    8: "Celesta", 9: "Glockenspiel", 10: "Music Box", 11: "Vibraphone",
    12: "Marimba", 13: "Xylophone", 45: "Pizzicato", 108: "Kalimba",
    4: "E-Piano 1", 5: "E-Piano 2", 6: "Harpsichord", 7: "Clavinet",
    56: "Trumpet", 61: "Brass Section", 62: "Synth Brass 1", 63: "Synth Brass 2",
    48: "Strings 1", 49: "Strings 2", 50: "Synth Strings 1", 51: "Synth Strings 2",
    96: "Rain FX", 97: "Soundtrack", 98: "Crystal", 99: "Atmosphere",
    100: "Brightness", 101: "Goblins", 102: "Echoes", 103: "Sci-Fi",
}
CAT_PROGRAMS = {
    "Bass":    [38, 39, 87],
    "Lead":    [81, 80, 84, 82, 83, 86, 85],
    "Pad":     [89, 88, 90, 95, 92, 94, 91, 93],
    "Pluck":   [10, 108, 45, 8, 9, 11, 12, 13],
    "Keys":    [4, 5, 6, 7],
    "Brass":   [62, 63, 61, 56],
    "Strings": [50, 51, 48, 49],
    "FX":      [98, 99, 102, 100, 97, 103, 96, 101],
}
NOTE = {"Bass": 48}
_OFFS = (0, 7, -5, 12, -12, 4, -8, 9, -3, 5)      # note offsets to fan out to per_cat
_NN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _pname(p):
    return f"{_NN[p % 12]}{p // 12 - 1}"


_synth = None
_sfid = None


def _render(prog, note):
    global _synth, _sfid
    if _synth is None:
        _synth = fluidsynth.Synth(samplerate=float(SR))
        _sfid = _synth.sfload(SF2)
    _synth.program_select(0, _sfid, 0, prog)
    _synth.all_notes_off(0)
    _synth.get_samples(int(0.05 * SR))                # flush any tail
    _synth.noteon(0, note, 110)
    a = _synth.get_samples(int(GATE_S * SR))
    _synth.noteoff(0, note)
    a = np.append(a, _synth.get_samples(int(TAIL_S * SR)))
    a = a.reshape(-1, 2).mean(axis=1).astype(np.float32) / 32768.0   # interleaved int16 -> mono float
    return a


def list_targets(per_cat=16):
    out = []
    for cat, progs in CAT_PROGRAMS.items():
        base = NOTE.get(cat, 60)
        cdir = os.path.join(CACHE, cat); os.makedirs(cdir, exist_ok=True)
        picked = []
        for off in _OFFS:
            note = max(24, min(96, base + off))
            for prog in progs:
                name = _GM.get(prog, str(prog)) + ("" if off == 0 else f" {_pname(note)}")
                wav = os.path.join(cdir, f"{prog}_{note}.wav")
                if not os.path.exists(wav):
                    a = _render(prog, note)
                    sf.write(wav, a, SR, subtype="PCM_16")
                picked.append((cat, name, wav, note))
                if len(picked) >= per_cat:
                    break
            if len(picked) >= per_cat:
                break
        out.extend(picked)
        print(f"  {cat:8} {len(picked)} targets")
    return out


def load(path):
    a, sr = sf.read(path, dtype="float32")
    if a.ndim > 1:
        a = a.mean(axis=1)
    return a, sr


if __name__ == "__main__":
    ts = list_targets(per_cat=int(os.environ.get("PER", "3")))
    from collections import Counter
    print("total:", len(ts), dict(Counter(t[0] for t in ts)))
    for t in ts[:6]:
        print("  ", t[0], t[1], "note", t[3])
