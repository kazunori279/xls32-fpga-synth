"""Per-parameter model-vs-board diff: sweep ONE CC at a time and compare `engine.render()` against
what the board actually plays.

Why this exists. `validate_hw.py` scores the model over a bank of presets, which is the right
headline number but a terrible localizer: a preset moves 24 parameters at once, so a disagreement
says "somewhere in here". The M27 follow-up burned a session on preset-level parameter correlation
and got nowhere -- the match/mismatch split was cut the wrong way by every parameter that looked
promising. What settled it in minutes was dropping the presets and sweeping one explicit patch.
That found two real model bugs in the first three parameters tried (the detune oscillator was a
hardcoded saw; `wave` indices 5-7 returned noise instead of sine), worth 59% -> 72% identification.
This is that method, generalized to the whole CC_MAP so the rest cannot hide.

The failure mode it guards against is structural, not incidental: `presetgen/engine.py` and
`core/synth.x` independently write out the same DSP. Every such pair is a drift waiting to happen,
and both bugs above were exactly that -- in one case the RTL carried a comment saying it had fixed
the very line the model still had wrong. Nothing but hardware sees these.

    uv run python presetgen/param_diff.py              # all parameters
    uv run python presetgen/param_diff.py cutoff reso  # just these

Stop webui/server.py first. Costs no build and ~15 min of board time for the full sweep.
"""
import os, sys, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "host")))
import numpy as np
from synth import BOARD, open_transport
import synth as u
import engine
import loss as lossmod
from validate_hw import recover
from calibrate import NOTE, CC_MAP

SR = lossmod.ARATE                       # analysis rate; prep() resamples both sides to this
GATE, TAIL = 1.30, 0.70                  # note held 1.30 s, then 0.70 s of tail so release and the
                                         # effect decays are inside the window (the census uses
                                         # 1.55/0.10 and cannot see them at all)
F0 = 261.63                              # NOTE = 60
SUS = (0.70, 1.25)                       # steady state, before note-off
ATT = (0.00, 0.40)
FLAG = 5.0                               # above this, re-capture and score the board against
                                         # itself (a matched parameter sits at 2-3)
REPEATS = 3                              # extra board takes on a flagged row; `self` is the worst
                                         # of the six pairs. See the comment at the call site.

# A deliberately plain patch: one saw voice, filter wide open, envelope flat and instant, no
# effects. Every sweep below moves exactly one key of this.
BASE = dict(wave=16, pw=64, detune=0, sub=0, cutoff=127, reso=0, fmode=0,
            fatt=0, fdec=0, fsus=127, frel=0, fdepth=0,
            aatt=0, adec=0, asus=127, arel=0, lforate=0, lfodep=0, trem=0,
            unison=0, porta=0, dtime=0, room=0, reverb=0, chorusd=0, echod=0)

FULL = [0, 42, 85, 127]
# Per-parameter sweep points and base overrides. An override is needed wherever the plain patch
# gives the parameter nothing to act on -- `pw` only shapes a pulse, a filter envelope needs the
# cutoff off its rail to have anywhere to travel, and `detune` has to be heard over a SINE or a
# wrong second-oscillator waveform hides inside the main saw's own harmonics (which is precisely
# how the hardcoded-saw bug survived).
SWEEPS = {
    "wave":    ([0, 16, 32, 48, 64, 80, 112], {}),
    "pw":      ([8, 40, 64, 100], {"wave": 32}),
    "detune":  (FULL, {"wave": 0}),
    "sub":     (FULL, {}),
    "cutoff":  ([10, 40, 80, 127], {}),
    "reso":    (FULL, {"cutoff": 45}),
    "fmode":   (FULL, {"cutoff": 60}),
    "fatt":    ([0, 30, 60, 90], {"cutoff": 20, "fdepth": 127}),
    "fdec":    (FULL, {"cutoff": 20, "fdepth": 127, "fsus": 0}),
    "fsus":    (FULL, {"cutoff": 20, "fdepth": 127, "fdec": 40}),
    "frel":    (FULL, {"cutoff": 20, "fdepth": 127}),
    "fdepth":  (FULL, {"cutoff": 20}),
    "aatt":    ([0, 30, 60, 90], {}),
    "adec":    (FULL, {"asus": 0}),
    "asus":    (FULL, {"adec": 50}),
    "arel":    (FULL, {}),
    "lforate": (FULL, {"lfodep": 127, "cutoff": 50}),
    "lfodep":  (FULL, {"lforate": 60, "cutoff": 50}),
    "trem":    (FULL, {"lforate": 60}),
    "unison":  (FULL, {}),
    "dtime":   (FULL, {"echod": 100}),
    "room":    (FULL, {"reverb": 100}),
    "reverb":  (FULL, {}),
    "chorusd": (FULL, {}),
    "echod":   (FULL, {"dtime": 60}),
}
# `porta` glides between two notes, so a single note-on measures nothing. Reported as skipped
# rather than swept and silently passed.
SKIP = {"porta": "needs a two-note stimulus; one note-on cannot show a glide"}


def grab(tp, vals, gate=GATE, tail=TAIL):
    recover(tp)
    for n in range(128):
        tp.send_midi(u.note_off(n))
    for cid, cc in CC_MAP:
        tp.send_midi(u.cc(cc, vals.get(cid, 0) & 0x7f)); time.sleep(0.003)
    time.sleep(0.04)
    tp.record_start(); tp.send_midi(u.note_on(NOTE, 100)); time.sleep(gate)
    tp.send_midi(u.note_off(NOTE)); time.sleep(tail)
    return np.asarray(tp.record_stop(), dtype=np.float32) / 32768.0


def _seg(x, t0, t1):
    return np.asarray(x, np.float64)[int(t0 * SR):int(t1 * SR)]


def ladder(x, k=6, n=8192):
    """Harmonics 2..k relative to the fundamental, dB, over the sustain window."""
    s = _seg(x, *SUS)[:n]
    if len(s) < n // 2:
        return None
    S = np.abs(np.fft.rfft(s * np.hanning(len(s)), n))
    f = np.fft.rfftfreq(n, 1.0 / SR)
    def at(hz):
        m = (f > hz - 12) & (f < hz + 12)
        return S[m].max() if m.any() else 1e-12
    h1 = at(F0) + 1e-12
    return np.array([20 * np.log10(at(F0 * h) / h1 + 1e-12) for h in range(2, k + 1)])


def centroid(x):
    s = _seg(x, *SUS)
    if len(s) < 2048:
        return float("nan")
    S = np.abs(np.fft.rfft(s * np.hanning(len(s))))
    f = np.fft.rfftfreq(len(s), 1.0 / SR)
    m = (f > 40) & (f < 7000)
    return float(np.sum(f[m] * S[m]) / (np.sum(S[m]) + 1e-12))


def envelope(x, ms=5.0):
    w = max(1, int(ms * SR / 1000))
    return np.convolve(np.abs(np.asarray(x, np.float64)), np.ones(w) / w, mode="same")


def t50(x):
    """ms from note-on to half the peak reached in the attack window."""
    e = envelope(_seg(x, *ATT))
    if not len(e) or e.max() <= 0:
        return float("nan")
    i = np.argmax(e >= 0.5 * e.max())
    return 1000.0 * i / SR


def tail_db(x, drop=20.0):
    """ms from note-off to `drop` dB below the level at note-off (release + effect decay)."""
    e = envelope(np.asarray(x, np.float64)[int(GATE * SR):])
    if len(e) < 64 or e[:int(0.01 * SR)].max() <= 0:
        return float("nan")
    ref = e[:int(0.01 * SR)].max()
    below = np.nonzero(e < ref * 10 ** (-drop / 20))[0]
    return 1000.0 * below[0] / SR if len(below) else float("nan")


def moddepth(x):
    """Envelope AC/DC ratio over the sustain -- how much the LFO/tremolo is moving the level.

    Smoothed over 15 ms: that is ~4 cycles of the 261 Hz carrier, so the waveform's own shape
    averages out to a few percent of ripple, while an LFO stays intact up to ~30 Hz. At 2 ms the
    metric just reported the carrier (0.357 for a saw, LFO on or off) and measured nothing.
    """
    e = envelope(_seg(x, *SUS), ms=15.0)
    return float(np.std(e) / (np.mean(e) + 1e-12)) if len(e) else float("nan")


def centmod(x, hop=0.02, win=0.04):
    """Spread of the short-time spectral centroid over the sustain, as a fraction of its mean.

    The amplitude metric above is blind to the two LFO parameters and to `fmode`, because CC77
    modulates the *filter*, not the level -- `lfodep` shows a rising loss with a completely flat
    `amod`. This is the column that localizes it.
    """
    s = _seg(x, *SUS)
    n, h = int(win * SR), int(hop * SR)
    if len(s) < n + h:
        return float("nan")
    f = np.fft.rfftfreq(n, 1.0 / SR)
    m = (f > 40) & (f < 7000)
    cs = []
    for i in range(0, len(s) - n, h):
        S = np.abs(np.fft.rfft(s[i:i + n] * np.hanning(n)))[m]
        cs.append(np.sum(f[m] * S) / (np.sum(S) + 1e-12))
    cs = np.array(cs)
    return float(np.std(cs) / (np.mean(cs) + 1e-12))


def row(a, b):
    la, lb = ladder(a), ladder(b)
    hd = float("nan") if la is None or lb is None else float(np.mean(np.abs(la - lb)))
    ca, cb = centroid(a), centroid(b)
    return dict(loss=lossmod.loss(a, b, a_prepped=True, b_prepped=True),
                cent=cb / ca if ca else float("nan"), hd=hd,
                t50a=t50(a), t50b=t50(b), tla=tail_db(a), tlb=tail_db(b),
                mda=moddepth(a), mdb=moddepth(b), cma=centmod(a), cmb=centmod(b))


def main():
    want = sys.argv[1:] or [c for c, _ in CC_MAP]
    tp = open_transport().open()
    print(f"board: {BOARD.name} over {BOARD.transport} at {tp.sr} Hz   analysis {SR} Hz   "
          f"note {NOTE} ({F0} Hz)   gate {GATE}s tail {TAIL}s\n")
    engine.render(BASE, note=NOTE, gate_s=GATE, tail_s=TAIL)          # warm the JIT
    summary, worstpair = [], []
    for cid in want:
        if cid in SKIP:
            print(f"## {cid}: SKIPPED -- {SKIP[cid]}\n")
            summary.append((float("nan"), cid, "skipped"))
            continue
        if cid not in SWEEPS:
            continue
        values, over = SWEEPS[cid]
        base = dict(BASE); base.update(over)
        note = f"   base: {' '.join(f'{k}={v}' for k, v in over.items())}" if over else ""
        print(f"## {cid} (CC{dict(CC_MAP)[cid]}){note}")
        print(f"{'val':>5s} {'loss':>6s} {'cent':>5s} {'H2-6':>5s} "
              f"{'t50 m/b ms':>13s} {'tail m/b ms':>13s} {'amod m/b':>15s} {'cmod m/b':>15s}"
              f" {'self':>6s}")
        worst = 0.0
        for v in values:
            vals = dict(base); vals[cid] = v
            sim = engine.render(vals, note=NOTE, gate_s=GATE, tail_s=TAIL)
            brd = grab(tp, vals)
            a = lossmod.prep(sim, engine.SR)
            b = lossmod.prep(brd, tp.sr)
            r = row(a, b)
            # A high loss is only a model bug if the BOARD is repeatable. It often is not: voice
            # start phases come from the noise LFSR (core/synth.x:140), which free-runs from
            # power-up, so a detuned or stacked patch begins its beat at an arbitrary point every
            # note-on while `engine.render()` always seeds 0xACE1. That is irreducible, not a
            # divergence to chase. So score the board against ITSELF: if `self` is as large as
            # `loss`, the number is nondeterminism. Only paid on flagged rows.
            #
            # REPEATS is 3, not 1, and `self` is the WORST pair, because one repeat is not a
            # control for this. The quantity that wanders is the PHASE of a ~1 Hz beat, so two
            # takes land at the same point often enough to look repeatable: the first run of this
            # sweep called `unison 42` and `detune 127` model bugs on a single low `self`, and a
            # direct envelope print then showed both beating at the right rate from a different
            # start every take. Four takes, six pairs, worst one wins.
            self_loss = float("nan")
            if r["loss"] > FLAG:
                reps = [b] + [lossmod.prep(grab(tp, vals), tp.sr) for _ in range(REPEATS)]
                self_loss = max(lossmod.loss(reps[i], reps[j], a_prepped=True, b_prepped=True)
                                for i in range(len(reps)) for j in range(i + 1, len(reps)))
            worst = max(worst, r["loss"])
            flag = "" if np.isnan(self_loss) else ("  nondet" if self_loss > 0.6 * r["loss"]
                                                   else "  <-- MODEL")
            print(f"{v:5d} {r['loss']:6.2f} {r['cent']:5.2f} {r['hd']:5.1f} "
                  f"{r['t50a']:6.0f} /{r['t50b']:6.0f} {r['tla']:6.0f} /{r['tlb']:6.0f} "
                  f"{r['mda']:7.3f} /{r['mdb']:7.3f} {r['cma']:7.3f} /{r['cmb']:7.3f}"
                  f" {self_loss:6.2f}{flag}", flush=True)
            if not np.isnan(self_loss):
                worstpair.append((r["loss"], self_loss, cid, v))
        summary.append((worst, cid, ""))
        print()
    tp.close()
    print("=== worst loss per parameter (a well-matched parameter sits at 2-3) ===")
    for w, cid, tag in sorted(summary, key=lambda s: (np.isnan(s[0]), -s[0])):
        print(f"  {cid:9s} {tag if tag else f'{w:6.2f}'}")
    if worstpair:
        print(f"\n=== flagged rows (loss > {FLAG}), model error vs board nondeterminism ===")
        print(f"{'param':>9s} {'val':>5s} {'loss':>6s} {'self':>6s}  verdict")
        for ls, sf, cid, v in sorted(worstpair, reverse=True):
            print(f"{cid:>9s} {v:5d} {ls:6.2f} {sf:6.2f}  "
                  f"{'nondeterministic (board disagrees with itself)' if sf > 0.6 * ls else 'MODEL'}")


if __name__ == "__main__":
    main()
