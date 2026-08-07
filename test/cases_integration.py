"""Integration tests — typical combinations of features working together, plus the
5 factory presets (the canonical 'typical combinations' shipped in the web UI)."""
import os, sys, time
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "host"))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "webui"))
from synth import (note_on, note_off, cc, pitch_bend, set_wave, set_cutoff, set_reso, set_fmode, set_sub,  # noqa: E402
                   set_pw, set_detune, set_unison, set_porta, set_fx, set_trem, set_room, set_reverb,
                   set_echo_depth)
from harness import TestCase, mk            # noqa: E402
import harness as H
import analysis as A                         # noqa: E402
import synthspec                             # noqa: E402

def w(fd, *msgs):
    for m in msgs: H.send(fd, m)

def sc(x, good, bad):
    if good == bad: return 100.0 if x >= good else 0.0
    return max(0.0, min(100.0, 100.0 * (x - bad) / (good - bad)))

def clean_score(s, base, tail_ok=True):
    """Combine an audibility base score with glitch/latch penalties (integration =
    'this combination works cleanly')."""
    g = A.glitch_count(s, 9000)
    clip = A.clip_ratio(s)
    score = base
    score -= min(40, g / max(1, len(s)) * 4000)        # glitch penalty
    score -= 300 * clip                                  # rail/clip penalty
    latched = (not tail_ok) and A.is_latched(s, 500, 2000)
    if latched: score = min(score, 55)
    return max(0.0, score), g, clip

CASES = []
def add(**kw): CASES.append(TestCase(category="integration", **kw))

# ---- hand-built typical combinations ----
def _chk_lead(s):
    hits, p = A.found_pitches(s, [64])
    lo = A.band_energy(s, 200, 700); hi = A.band_energy(s, 2000, 6000)
    rolled = hi / max(1.0, lo) < 0.6
    base = 60 + 20 * hits + (20 if rolled else 0)
    score, g, clip = clean_score(s, base)
    return mk(score, f"pitch {'ok' if hits else 'x'}, rolloff {'ok' if rolled else 'x'}, {g} glitches", "clean filtered lead")
def _setup_lead(fd): w(fd, set_wave(1), set_cutoff(72), set_reso(45), set_fmode(0), cc(20, 6), cc(22, 105), cc(23, 45), set_fx(0))
add(id="combo_lead", title="Classic subtractive lead", desc="Saw → resonant LP → amp ADSR: the bread-and-butter patch.",
    expected="correct pitch, highs rolled off, clean", setup=_setup_lead, perform=lambda fd: (w(fd, note_on(64, 110)), time.sleep(1.6), w(fd, note_off(64)), time.sleep(0.3)),
    check=_chk_lead, capture_s=2.1)

def _chk_pad(s):
    cv = A.beating_cv(s); tail = A.tail_energy(s, 0.55)
    base = 45 + sc(cv, 0.12, 0.03) * 0.35 + sc(tail, 40, 5) * 0.2
    score, g, clip = clean_score(s, base)
    return mk(score, f"beating CV {cv:.2f}, reverb tail {tail:.0f}, {g} glitches", "thick detuned pad + reverb tail")
def _setup_pad(fd): w(fd, set_wave(1), set_unison(3), set_detune(2), set_cutoff(85), set_reso(20), cc(20, 90), cc(22, 120), cc(23, 90), set_fx(0), set_reverb(100), set_room(1))
add(id="combo_pad", title="Super-saw pad + reverb", desc="Unison + detune + slow attack + hall reverb → a lush pad.",
    expected="thick beating + reverb tail", setup=_setup_pad, perform=lambda fd: (w(fd, note_on(52, 90), note_on(59, 90), note_on(64, 90)), time.sleep(2.0), w(fd, note_off(52), note_off(59), note_off(64)), time.sleep(1.4)),
    check=_chk_pad, capture_s=3.6)

def _chk_bass(s):
    lo = A.band_energy(s, 60, 250); hi = A.band_energy(s, 1200, 6000)
    strong_low = lo / max(1.0, lo + hi)
    base = 55 + sc(strong_low, 0.7, 0.3) * 0.45
    score, g, clip = clean_score(s, base, tail_ok=False)
    return mk(score, f"low-band fraction {strong_low:.2f}, {g} glitches", "strong clean low end")
def _setup_bass(fd): w(fd, set_wave(2), set_sub(3), set_cutoff(48), set_reso(30), set_fmode(0), cc(20, 2), cc(21, 45), cc(22, 70), cc(23, 25), set_fx(0))
add(id="combo_bass", title="Sub bass", desc="Square + full sub-osc + low cutoff + snappy env → deep bass.",
    expected="strong low end, clean, decays", setup=_setup_bass, perform=lambda fd: (w(fd, note_on(33, 115)), time.sleep(1.0), w(fd, note_off(33)), time.sleep(0.6)),
    check=_chk_bass, capture_s=1.8)

# Audibility + echo, and the echo carries the weight. This case used to spend 0.3 of its base on a
# spectral-centroid "pluck drop", which never measured the wah: on note 45 (110 Hz) through a
# resonant lowpass the centroid moves ~60 Hz and is done inside 30 ms, so nothing survives the
# 220 ms grading windows. What the term actually scored was the *last window being silence*
# (centroid 0), which is why it read ~1000 Hz dry and went NEGATIVE once the echo repeats filled
# the tail -- and swung 80 -> -12 between two consecutive runs, i.e. no longer reproducible.
# Brightness belongs to `basic/filter_env` and `basic/lfo_autowah`, which measure it on patches
# where the centroid does move and both score 100. Integration's contract is "this combination
# works cleanly", so what is left is: audible, echoing, glitch-free. The tail term at 0.4 is the
# regression guard this case was missing -- if the echo ever goes dead again it reads ~7, not ~420,
# and the case drops to 60 instead of quietly holding a passing-looking 80.
def _chk_wah(s):
    pk = A.peak(s)
    tail = A.tail_energy(s, 0.4)
    base = 40 + sc(pk, 6000, 500) * 0.2 + sc(tail, 150, 25) * 0.4
    score, g, clip = clean_score(s, base)
    return mk(score, f"peak {pk:.0f}, echo tail {tail:.0f}, {g} glitches", "filter pluck + echo repeats")
# set_echo_depth, not set_fx(2): the only thing this case sent to turn the echo on was CC83, which
# the shell stopped reading when effects went depth-gated, so it had been plucking dry. 64 is the
# shell's own default depth and matches the wet/2 the mode used to hardcode; dtime stays at the
# default 63 (~256 ms), which is what the old fixed tap was.
def _setup_wah(fd): w(fd, set_wave(1), set_cutoff(38), set_reso(85), set_fmode(0), cc(79, 110), cc(24, 0), cc(25, 55), cc(26, 30), cc(20, 2), cc(23, 15), set_echo_depth(64))
add(id="combo_wah", title="Auto-wah pluck + echo", desc="High reso + deep filter-env + echo → a plucky, repeating wah.",
    expected="bright→dark pluck with echoes", setup=_setup_wah, perform=lambda fd: (w(fd, note_on(45, 110)), time.sleep(0.25), w(fd, note_off(45)), time.sleep(1.8)),
    check=_chk_wah, capture_s=2.2)

def _chk_expr(s):
    p = [x for x in A.pitch_track(s, 14) if x > 0]
    span = (max(p) - min(p)) / max(1.0, min(p)) if len(p) >= 4 else 0
    base = 50 + sc(span, 0.5, 0.1) * 0.45
    score, g, clip = clean_score(s, base)
    return mk(score, f"pitch span {span*100:.0f}%, {g} glitches", "glide + bend + vibrato all move pitch")
def _perf_expr(fd):
    w(fd, note_on(45, 110)); time.sleep(0.4)
    w(fd, note_on(57, 110)); time.sleep(0.5)                 # glide up
    for i in range(6): w(fd, pitch_bend(i / 5.0)); time.sleep(0.12)   # bend up
    w(fd, pitch_bend(0.0)); time.sleep(0.5)                  # vibrato continues
    w(fd, note_off(57), note_off(45)); time.sleep(0.2)
def _setup_expr(fd): w(fd, set_wave(1), set_cutoff(100), set_reso(20), set_porta(2), cc(76, 80), cc(1, 96), cc(20, 2), cc(22, 120), set_fx(0))
add(id="combo_expression", title="Expression stack", desc="Portamento + pitch bend + vibrato together on one lead.",
    expected="rich, continuous pitch movement", setup=_setup_expr, perform=_perf_expr, check=_chk_expr, capture_s=2.6)

def _chk_polyfx(s):
    hits, p = A.found_pitches(s, [48, 55, 60, 64]); tail = A.silence_tail(s, 500)
    base = 40 + 12 * hits + sc(tail, 400, 80) * 0.12
    score, g, clip = clean_score(s, base)
    return mk(score, f"{hits}/4 tones, reverb tail {tail:.0f}, {g} glitches", "polyphonic chord + reverb, clean")
def _setup_polyfx(fd): w(fd, set_wave(1), set_cutoff(80), set_reso(25), set_fmode(0), cc(20, 6), cc(22, 110), cc(23, 60), set_fx(0), set_reverb(100), set_room(2))
add(id="combo_poly_reverb", title="Poly chord through filter + reverb", desc="A four-note chord filtered and sent to reverb.",
    expected="4 tones, reverb tail, no glitches", setup=_setup_polyfx, perform=lambda fd: (w(fd, note_on(48, 95), note_on(55, 95), note_on(60, 95), note_on(64, 95)), time.sleep(1.8), w(fd, note_off(48), note_off(55), note_off(60), note_off(64)), time.sleep(1.2)),
    check=_chk_polyfx, capture_s=3.2)

# ---- the 5 factory presets ----
_CC_OF = {c["id"]: c["cc"] for c in synthspec.CONTROLS}
# Presets outlive the controls they were saved with. "fx" is the one survivor: it held a CC83
# effect-mode number, the shell ignores CC83 now (effects are depth-gated via CC93/94/95) and
# synthspec dropped the control, so sending it would be a no-op even if we could. M27 rewrote it
# out of the shipped banks and out of FACTORY, so nothing reaching this file carries it any more
# -- but a patch saved to localStorage before then still does, and must load rather than raise.
# Anything else missing is a real drift between the preset bank and the control list: be loud.
_RETIRED = {"fx"}

def _apply_preset(values):
    unknown = sorted(set(values) - set(_CC_OF) - _RETIRED)
    if unknown:
        raise KeyError(f"preset uses controls synthspec does not define: {unknown}")
    def setup(fd):
        for cid, val in values.items():
            if cid in _CC_OF:
                H.send(fd, cc(_CC_OF[cid], val))
    return setup

def _chk_preset(name):
    def check(s):
        pk = A.peak(s); g = A.glitch_count(s, 9000); clip = A.clip_ratio(s)
        base = sc(pk, 6000, 500)                          # audible?
        score, g, clip = clean_score(s, base)
        return mk(score, f"peak {pk}, {g} glitches, clip {clip*100:.1f}%", f"'{name}' sounds & runs clean")
    return check

for p in synthspec.FACTORY:
    add(id=f"preset_{p['name'].lower().replace(' ', '_')}", title=f"Factory preset — {p['name']}",
        desc=f"The '{p['name']}' factory patch played as a chord.", expected="audible, characterful, glitch-free",
        setup=_apply_preset(p["values"]),
        perform=lambda fd: (w(fd, note_on(48, 100), note_on(55, 100), note_on(60, 100)), time.sleep(1.8),
                            w(fd, note_off(48), note_off(55), note_off(60)), time.sleep(1.2)),
        check=_chk_preset(p["name"]), capture_s=3.2)
