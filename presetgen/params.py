"""Search space: maps a normalized vector in [0,1]^D <-> a raw-CC preset dict.

Continuous knobs (0..127) and bit-packed selects (discrete option sets) matching
webui/synthspec.py. CMA-ES optimizes the vector; selects are rounded to their nearest option.
"""
import numpy as np

def _w(v): return (v & 7) << 4       # 3-bit field @ bit4 (wave, fx)
def _s(v): return (v & 3) << 5       # 2-bit field @ bit5 (mode/sub/detune/unison/porta/trem/room)

KNOBS = ["cutoff", "reso", "pw", "fdepth", "aatt", "adec", "asus", "arel",
         "fatt", "fdec", "fsus", "frel", "lforate", "lfodep"]

SELECTS = {                          # id -> list of raw option values (index order)
    "wave":   [_w(i) for i in range(5)],
    "fmode":  [_s(i) for i in range(4)],
    "sub":    [_s(i) for i in range(4)],
    "detune": [_s(i) for i in range(4)],
    "unison": [_s(i) for i in range(4)],
    "porta":  [_s(i) for i in range(4)],
    "trem":   [_s(i) for i in range(4)],
    "fx":     [_w(i) for i in range(5)],
    "room":   [_s(i) for i in range(4)],
}
SEL_IDS = list(SELECTS.keys())
DIM = len(KNOBS) + len(SEL_IDS)

def preset_from_vec(vec):
    """[0,1]^DIM -> raw-CC dict (all control ids present)."""
    v = np.clip(np.asarray(vec, dtype=float), 0.0, 1.0)
    p = {}
    for i, k in enumerate(KNOBS):
        p[k] = int(round(v[i] * 127))
    for j, sid in enumerate(SEL_IDS):
        opts = SELECTS[sid]
        idx = min(len(opts) - 1, int(v[len(KNOBS) + j] * len(opts)))
        p[sid] = opts[idx]
    return p

def vec_from_preset(preset):
    v = np.zeros(DIM)
    for i, k in enumerate(KNOBS):
        v[i] = preset.get(k, 0) / 127.0
    for j, sid in enumerate(SEL_IDS):
        opts = SELECTS[sid]
        val = preset.get(sid, opts[0])
        idx = opts.index(val) if val in opts else 0
        v[len(KNOBS) + j] = (idx + 0.5) / len(opts)
    return v

# per-category starting point (raw CC) to seed the search near a musical region.
_SEED = {
    "Bass":    dict(wave=_w(1), sub=_s(2), cutoff=55, reso=30, aatt=2, adec=45, asus=85, arel=30),
    "Lead":    dict(wave=_w(1), detune=_s(1), cutoff=95, reso=40, aatt=4, adec=44, asus=105, arel=44),
    "Pad":     dict(wave=_w(1), unison=_s(2), detune=_s(2), cutoff=75, reso=22, aatt=96, adec=70,
                    asus=118, arel=110, fx=_w(4), room=_s(2)),
    "Pluck":   dict(wave=_w(1), cutoff=50, reso=55, fdepth=100, fatt=0, fdec=40, fsus=24, frel=36,
                    aatt=2, adec=40, asus=40, arel=34, fx=_w(2)),
    "Keys":    dict(wave=_w(3), detune=_s(1), cutoff=90, reso=22, aatt=4, adec=54, asus=96, arel=48),
    "Brass":   dict(wave=_w(1), unison=_s(1), cutoff=70, fdepth=60, fatt=30, fdec=50, fsus=80,
                    aatt=18, adec=50, asus=100, arel=44),
    "Strings": dict(wave=_w(1), unison=_s(3), detune=_s(2), cutoff=80, reso=20, aatt=90, adec=70,
                    asus=120, arel=100, fx=_w(4), room=_s(3)),
    "FX":      dict(wave=_w(4), cutoff=70, reso=90, fdepth=90, lforate=80, lfodep=80, fx=_w(4)),
}

def seed_vec(category):
    import engine  # reuse engine defaults for unset ids (same package)
    base = dict(engine._DEFAULTS)
    base.update(_SEED.get(category, {}))
    return vec_from_preset(base)


if __name__ == "__main__":
    print("DIM =", DIM, "(", len(KNOBS), "knobs +", len(SEL_IDS), "selects )")
    v = seed_vec("Bass")
    p = preset_from_vec(v)
    print("Bass seed preset:", {k: p[k] for k in ("wave", "cutoff", "sub", "aatt")})
    # round-trip
    v2 = vec_from_preset(p)
    print("round-trip max err:", float(np.max(np.abs(v - v2))))
