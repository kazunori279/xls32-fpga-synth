"""Stress tests — push the synth to its boundaries and confirm it stays clean
(strict: glitches, clipping/railing, and stuck/latched output lower the score and
can FAIL). Primary metrics: glitch count, clip ratio, and post-release silence."""
import os, sys, time
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "host"))
import uartaudio as U                        # noqa: E402
from uartaudio import (note_on, note_off, cc,                          # noqa: E402
                       set_wave, set_cutoff, set_reso, set_fmode, set_unison, set_fx, set_room, set_reverb)
from harness import TestCase, mk            # noqa: E402
import harness as H
import analysis as A                         # noqa: E402

def w(fd, *msgs):
    for m in msgs: H.send(fd, m)

def stress_score(s, require_silence=True, audible=True):
    """Strict: start at 100, subtract for glitches / clipping, hard-cap on latch."""
    g = A.glitch_count(s, 9000)
    clip = A.clip_ratio(s)
    latched = require_silence and A.is_latched(s, 400, 600)
    score = 100.0
    score -= min(60.0, max(0.0, (g - 5)) * 0.5)     # broadband glitches
    score -= 400.0 * clip                            # sustained rail / clip
    if audible and A.peak(s) < 400:
        score -= 30                                  # produced (almost) nothing
    if latched:
        score = min(score, 40.0)                     # stuck/latched output = near-fail
    return score, g, clip, latched

def metric(g, clip, latched, extra=""):
    return f"{g} glitches, clip {clip*100:.1f}%{', LATCHED' if latched else ''}{extra}"

CASES = []
def add(**kw): CASES.append(TestCase(category="stress", **kw))

NOTES32 = list(range(40, 72))     # 32 distinct notes

# 1) 32-voice max polyphony
def _perf_32(fd):
    for n in NOTES32: H.send(fd, note_on(n, 45))   # moderate vel: 32 voices shouldn't clip the mix
    time.sleep(1.6)
    for n in NOTES32: H.send(fd, note_off(n))
    time.sleep(0.9)
def _chk_32(s):
    score, g, clip, latched = stress_score(s)
    return mk(score, metric(g, clip, latched, f", peak {A.peak(s)}"), "32 voices, no glitch/clip/latch")
def _setup_32(fd): w(fd, set_wave(1), set_cutoff(70), set_reso(20), set_fmode(0), cc(20, 2), cc(22, 100), cc(23, 30), set_fx(0))
add(id="stress_32voice", title="32-voice maximum polyphony", desc="All 32 physical voices at once, then released — must stay clean.",
    expected="no glitches, no clipping, silent after release", setup=_setup_32, perform=_perf_32, check=_chk_32, capture_s=3.0)

# 2) rapid retrigger (stuck-voice check)
def _perf_retrig(fd):
    seq = [45, 48, 52, 55, 57, 60, 64, 67]
    for _ in range(6):
        for n in seq:
            H.send(fd, note_on(n, 90)); time.sleep(0.025); H.send(fd, note_off(n))
    time.sleep(0.8)
def _chk_retrig(s):
    score, g, clip, latched = stress_score(s)
    return mk(score, metric(g, clip, latched), "fast retrigger, no stuck voices, silent after")
def _setup_retrig(fd): w(fd, set_wave(1), set_cutoff(90), set_reso(20), cc(20, 2), cc(22, 100), cc(23, 15), set_fx(0))
add(id="stress_retrigger", title="Rapid retrigger", desc="Machine-gun note on/off — voices must free correctly (no hang).",
    expected="clean, returns to silence", setup=_setup_retrig, perform=_perf_retrig, check=_chk_retrig, capture_s=3.0)

# 3) unison 4 × 8-note chord = 32 voices (allocation stress)
def _perf_unichord(fd):
    ch = [40, 43, 47, 50, 54, 57, 61, 64]
    for n in ch: H.send(fd, note_on(n, 70))
    time.sleep(1.6)
    for n in ch: H.send(fd, note_off(n))
    time.sleep(0.9)
def _chk_unichord(s):
    score, g, clip, latched = stress_score(s)
    return mk(score, metric(g, clip, latched), "unison×chord = 32 voices, clean")
def _setup_unichord(fd): w(fd, set_wave(1), set_unison(3), set_cutoff(80), set_reso(20), cc(20, 2), cc(22, 100), cc(23, 30), set_fx(0))
add(id="stress_unison_chord", title="Unison × chord (32 voices)", desc="4-voice unison on an 8-note chord saturates voice allocation.",
    expected="no glitch/clip/latch", setup=_setup_unichord, perform=_perf_unichord, check=_chk_unichord, capture_s=3.0)

# 4) all-effects + cathedral tail (long feedback, must not rail)
def _perf_fxtail(fd):
    for n in (48, 55, 60): H.send(fd, note_on(n, 90))
    time.sleep(0.4)
    for n in (48, 55, 60): H.send(fd, note_off(n))
    time.sleep(3.4)     # let the cathedral tail ring out
def _chk_fxtail(s):
    # tail should DECAY, not sustain/rail. compare late vs mid energy + no clip.
    n = len(s); mid = A.rms(s[n // 4:n // 2]); late = A.rms(s[-n // 6:])
    decayed = late < 0.7 * max(1.0, mid)
    clip = A.clip_ratio(s); g = A.glitch_count(s, 9000)
    score = 100.0 - min(60.0, max(0.0, (g - 5)) * 0.5) - 400.0 * clip
    if not decayed: score = min(score, 45)      # stuck/growing tail = latch-like
    return mk(score, f"{g} glitches, clip {clip*100:.1f}%, late/mid {late/max(1.0,mid):.2f}", "reverb tail decays, no railing")
def _setup_fxtail(fd): w(fd, set_wave(1), set_cutoff(90), set_reso(20), cc(20, 2), cc(23, 10), set_fx(0), set_reverb(115), set_room(3))
add(id="stress_fx_tail", title="All-effects cathedral tail", desc="Cathedral reverb (longest feedback) — the tail must decay, never rail.",
    expected="decaying tail, no clip/railing", setup=_setup_fxtail, perform=_perf_fxtail, check=_chk_fxtail, capture_s=4.0)

# 5) extreme resonance + cutoff sweep (self-oscillation edge; must recover)
def _perf_selfosc(fd):
    H.send(fd, note_on(45, 100))
    for i in range(10): H.send(fd, set_cutoff(10 + i * 12)); time.sleep(0.22)
    H.send(fd, note_off(45)); time.sleep(1.0)     # must return to silence (no latch)
def _chk_selfosc(s):
    score, g, clip, latched = stress_score(s)
    return mk(score, metric(g, clip, latched), "max-reso sweep recovers to silence")
def _setup_selfosc(fd): w(fd, set_wave(1), set_reso(127), set_fmode(0), cc(20, 2), cc(22, 110), cc(23, 20), set_fx(0))
add(id="stress_self_osc", title="Extreme resonance sweep", desc="Max resonance while sweeping cutoff — the filter must not latch/stick.",
    expected="no latch; silent after note-off", setup=_setup_selfosc, perform=_perf_selfosc, check=_chk_selfosc, capture_s=3.4)

# 6) rapid CC automation during held notes
def _perf_rapidcc(fd):
    for n in (48, 55, 60): H.send(fd, note_on(n, 85))
    for i in range(60):
        H.send(fd, set_cutoff(30 + (i * 13) % 90))
        H.send(fd, cc(75, 10 + (i * 7) % 110))     # pulse width
        H.send(fd, set_reso((i * 11) % 120))
        time.sleep(0.03)
    for n in (48, 55, 60): H.send(fd, note_off(n))
    time.sleep(0.6)
def _chk_rapidcc(s):
    score, g, clip, latched = stress_score(s)
    return mk(score, metric(g, clip, latched), "fast CC automation, no glitches/latch")
def _setup_rapidcc(fd): w(fd, set_wave(2), set_cutoff(70), set_reso(30), cc(20, 2), cc(22, 100), cc(23, 20), set_fx(0))
add(id="stress_rapid_cc", title="Rapid CC automation", desc="Flooding cutoff/PW/reso changes while three notes are held.",
    expected="clean under CC flood", setup=_setup_rapidcc, perform=_perf_rapidcc, check=_chk_rapidcc, capture_s=3.0)

# 7) dense sustain -> silence recovery (latch check)
def _perf_recovery(fd):
    ch = [36, 40, 43, 47, 50, 54, 57, 60, 64, 67]
    for n in ch: H.send(fd, note_on(n, 75))
    time.sleep(3.0)
    for n in ch: H.send(fd, note_off(n))
    time.sleep(1.0)
def _chk_recovery(s):
    score, g, clip, latched = stress_score(s)
    tail = A.silence_tail(s, 300)
    return mk(score, metric(g, clip, latched, f", tail RMS {tail:.0f}"), "dense chord → digital silence")
def _setup_recovery(fd): w(fd, set_wave(1), set_cutoff(75), set_reso(25), cc(20, 2), cc(22, 95), cc(23, 25), set_fx(0))
add(id="stress_silence_recovery", title="Sustain → silence recovery", desc="A dense 10-note chord held for 3 s must return to true silence.",
    expected="tail returns to digital silence", setup=_setup_recovery, perform=_perf_recovery, check=_chk_recovery, capture_s=4.4)
