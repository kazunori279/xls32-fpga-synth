"""Search space: maps a normalized vector in [0,1]^D <-> a raw-CC preset dict.

Continuous knobs (0..127) and bit-packed selects (discrete option sets) matching
webui/synthspec.py. CMA-ES optimizes the vector; selects are rounded to their nearest option.

**How wide the space is, is a $SPACE choice.** The engine exposes 30 CCs and the fits have only
ever reached 22 of them; widening that is a claim about the bank, and a claim has to be measured
against the width it replaces rather than merged as an improvement. So all four widths run from
one tree:

    SPACE=base   22   what every shipped bank was fitted under, and the default. A bank fitted at
                      a different width is a different bank, so nothing downstream changes width
                      by accident.
    SPACE=xmod  +3    cross-mod: CC85 xmode, CC86 xdepth, CC87 xratio. M19 left these out because
                      a magnitude-STFT loss never chose them; the shipped bank is fitted under
                      clap+stft, which is not a per-bin distance, and xmod_probe.py says the
                      verdict flips (6/10 on a same-budget re-fit, attack centroid 0.521 -> 0.591).
    SPACE=fx    +4    effect depths CC93 reverb / CC94 chorusd / CC95 echod, plus CC82 dtime,
                      which is inert until echod is nonzero and so was never even considered.
    SPACE=full  29    both. The thirtieth CC is `volume`, correctly excluded for good: the loss
                      RMS-normalizes, so gain is unobservable and the dimension would be free.

`room` (CC91) has been in the space all along and is DEAD at base and xmod width: engine.py:480
skips the whole effects chain unless one of the three depths is nonzero, so `rsize` -- the only
thing room feeds -- never reaches a render during a fit. SPACE=fx is what brings it to life. If
the fx arm loses, room should come out rather than stay as a coordinate CMA-ES cannot spend.

A vector is only meaningful alongside the width that produced it -- adding a knob shifts every
select's index -- so the interchange format between widths is the raw-CC preset dict, which is
keyed by name. preset_from_vec()/vec_from_preset() are the only places that need to agree.

See issue #16 (M27).
"""
import os

import numpy as np

def _w(v): return (v & 7) << 4       # 3-bit field @ bit4 (wave/xratio)
def _s(v): return (v & 3) << 5       # 2-bit field @ bit5 (mode/sub/detune/unison/porta/trem/room)

SPACE = os.environ.get("SPACE", "base")
if SPACE not in ("base", "xmod", "fx", "full"):
    raise ValueError(f"unknown SPACE={SPACE}: want base | xmod | fx | full")
_XMOD = SPACE in ("xmod", "full")
_FX = SPACE in ("fx", "full")

KNOBS = ["cutoff", "reso", "pw", "fdepth", "aatt", "adec", "asus", "arel",
         "fatt", "fdec", "fsus", "frel", "lforate", "lfodep"]
if _XMOD:
    KNOBS += ["xdepth"]                                  # 0..127 continuous, like every other depth
if _FX:
    # dtime rides with the depths and not on its own: echo_delay() only reaches the render when
    # echodep is nonzero, so searching it at base width would be a second dead coordinate.
    KNOBS += ["reverb", "chorusd", "echod", "dtime"]

SELECTS = {                          # id -> list of raw option values (index order)
    "wave":   [_w(i) for i in range(5)],
    "fmode":  [_s(i) for i in range(4)],
    "sub":    [_s(i) for i in range(4)],
    "detune": [_s(i) for i in range(4)],
    "unison": [_s(i) for i in range(4)],
    "porta":  [_s(i) for i in range(4)],
    "trem":   [_s(i) for i in range(4)],
    "room":   [_s(i) for i in range(4)],
}
if _XMOD:
    SELECTS["xmode"] = [_s(i) for i in range(4)]         # off / ring / FM / FM+
    SELECTS["xratio"] = [_w(i) for i in range(8)]        # engine.py:194-201, a 3-bit index
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

# Per-category starting point (raw CC) to seed the search near a musical region. Pad, Pluck,
# Strings and FX used to seed a CC83 fx mode too; that control is dead, and its live
# replacements are outside the search space (see SELECTS), so those entries are gone rather
# than translated -- a seed for a dimension the vector does not carry is silently dropped.
# `room` stays: it is live (CC91), and it is what a future reverb fit would size the tank with.
#
# Nothing here seeds the $SPACE additions, and that is the point: seed_vec() fills them from
# engine._DEFAULTS, which is xmode=0 and every depth 0 -- dry, cross-mod off, exactly where the
# base-width fits start. A wider run therefore begins at its own control's starting point, so
# whatever it wins it won by searching, not by being handed a wetter or more metallic seed.
_SEED = {
    "Bass":    dict(wave=_w(1), sub=_s(2), cutoff=55, reso=30, aatt=2, adec=45, asus=85, arel=30),
    "Lead":    dict(wave=_w(1), detune=_s(1), cutoff=95, reso=40, aatt=4, adec=44, asus=105, arel=44),
    "Pad":     dict(wave=_w(1), unison=_s(2), detune=_s(2), cutoff=75, reso=22, aatt=96, adec=70,
                    asus=118, arel=110, room=_s(2)),
    "Pluck":   dict(wave=_w(1), cutoff=50, reso=55, fdepth=100, fatt=0, fdec=40, fsus=24, frel=36,
                    aatt=2, adec=40, asus=40, arel=34),
    "Keys":    dict(wave=_w(3), detune=_s(1), cutoff=90, reso=22, aatt=4, adec=54, asus=96, arel=48),
    "Brass":   dict(wave=_w(1), unison=_s(1), cutoff=70, fdepth=60, fatt=30, fdec=50, fsus=80,
                    aatt=18, adec=50, asus=100, arel=44),
    "Strings": dict(wave=_w(1), unison=_s(3), detune=_s(2), cutoff=80, reso=20, aatt=90, adec=70,
                    asus=120, arel=100, room=_s(3)),
    "FX":      dict(wave=_w(4), cutoff=70, reso=90, fdepth=90, lforate=80, lfodep=80),
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
