"""Perceptual distance between two mono signals for preset matching.

Multi-resolution STFT (magnitude + log-magnitude) + a mel-ish band term + an amplitude-
envelope term. Both signals are resampled to a common analysis rate, loudness-normalized,
and compared magnitude-only (phase-invariant). Lower = closer.

**Segment weighting** (`SEG=1` plus a `window=`; OFF by default, and off it is bit-identical to
what this file always returned). Every spectral term here is a `.mean()` over frames, so a frame of
the decay tail counts exactly as much as a frame of the attack. Against the GM SoundFont that
looked badly wrong: Music Box puts 78% of its energy in the first 200 ms of a 1.9 s window and then
loops a 10 ms fragment, so the part a listener hears as "the sound" got ~10% of the loss's
attention, and fitted patches come out with an attack spectral centroid half the target's.

So the frames are grouped into the note's own phases (protocol.segments: AD / S / R) and each phase
is weighted by **sqrt of its share of the target's energy** -- equal-per-phase would be worse than
uniform (a decaying target has no sustain, and giving S a third of the vote spends a third of the
loss comparing two noise floors), proportional-to-energy is roughly what uniform already does. The
amplitude-envelope term stays GLOBAL and at full weight, so per-phase weighting cannot let a patch
match the timbre of a phase the target spends in near-silence while being far too loud there.

**It does not work.** The redistribution happens as designed -- on Music Box the AD phase goes from
13% of the frame weight to 65% -- but the attack does not get brighter. 24 presets, budget 800,
same seed, graded by attack_audit.py (a metric the loss never sees), geo-mean attack centroid
ours/target:

    objective     SEG=0    SEG=1
    clap+stft     0.706    0.642     <- the shipped objective: segment weighting is WORSE
    stft          0.576    0.594     <- +3%, inside the noise at n=24

Under SEG=1 the fits also drift inharmonic in the wrong places (Brass 46% vs a 7% target). The
reading is that the attack is not dull because the loss ignores it; it is dull because a single
oscillator with a static cross-mod index cannot make a struck sample's transient, and pointing more
of the loss at a phase the engine cannot reach just buys the error somewhere else. The code stays
because the measurement is worth being able to repeat -- see DEVELOPMENT.md.

What DID help was the other half of this change: aligning the target's window with the render's
(protocol.py). Same 24 presets, same objective, SEG off: 0.636 -> 0.706.
"""
import os

import numpy as np
from scipy.signal import resample_poly

import protocol

ARATE = 22050                    # common analysis sample rate
_FFTS = (256, 512, 1024, 2048)   # multi-resolution window sizes
# SEG=1 turns segment weighting on. Off by default because the A/B in the docstring says it does
# not help; it stays switchable so that measurement can be re-run rather than re-argued.
SEG = os.environ.get("SEG", "0") not in ("0", "false", "no")


def _resample(x, sr):
    if sr == ARATE:
        return x.astype(np.float64)
    from math import gcd
    g = gcd(int(sr), ARATE)
    return resample_poly(x.astype(np.float64), ARATE // g, sr // g)


def _norm(x):
    p = np.sqrt(np.mean(x * x)) + 1e-9
    return x / p                                  # RMS-normalize (loudness-invariant)


def _stft_mag(x, n):
    hop = n // 4
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    win = np.hanning(n)
    frames = 1 + (len(x) - n) // hop
    m = np.empty((frames, n // 2 + 1))
    for i in range(frames):
        m[i] = np.abs(np.fft.rfft(x[i * hop:i * hop + n] * win))
    return m


def _mel_env(x, sr):
    # coarse log-frequency band energies over time (perceptual weighting)
    m = _stft_mag(x, 1024)
    freqs = np.fft.rfftfreq(1024, 1 / sr)
    edges = np.logspace(np.log10(50), np.log10(sr / 2), 25)
    bands = np.zeros((m.shape[0], len(edges) - 1))
    for b in range(len(edges) - 1):
        sel = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if sel.any():
            bands[:, b] = m[:, sel].mean(axis=1)
    return bands


def _frame_weights(B, window, frames, nf, hop):
    """Per-frame weights summing to 1: sqrt of each phase's share of the TARGET's energy, spread
    evenly over that phase's frames. Uniform (i.e. a plain mean) is what you get back when the
    window has one phase or the target is silent, so this can only redistribute attention."""
    centres = (np.arange(frames) * hop + nf / 2) / ARATE
    E = B * B
    tot = float(E.sum()) + 1e-12
    w = np.zeros(frames)
    for _, t0, t1 in protocol.segments(*window):
        m = (centres >= t0) & (centres < t1)
        k = int(m.sum())
        if k:
            e = float(E[int(t0 * ARATE):int(t1 * ARATE)].sum())
            w[m] = np.sqrt(e / tot) / k
    if w.sum() <= 0:                       # every frame fell outside the window: nothing to weight
        return np.full(frames, 1.0 / frames)
    lost = frames - int((w > 0).sum())     # frames past the declared window (rounding at the edge)
    if lost:
        w[w == 0] = w[w > 0].min() * 1e-3  # not dropped outright: silence there is still an error
    return w / w.sum()


def _wmean(D, w):
    """mean over (frames, bins), with the frames axis weighted. w=None is the plain mean, bit for
    bit -- the shipped banks were fitted with that and their numbers have to stay reproducible."""
    return D.mean() if w is None else float(w @ D.mean(axis=1))


def prep(x, sr):
    """Resample to ARATE + RMS-normalize once (cache targets with this)."""
    return _norm(_resample(np.asarray(x, dtype=np.float64).flatten(), sr))


def loss(a, b, a_sr=ARATE, b_sr=ARATE, a_prepped=False, b_prepped=False, window=None):
    """Distance between signals a and b. Pass *_prepped=True if already prep()'d at ARATE.

    `window` is the corpus's (gate_s, tail_s); b is the target and its energy sets the weights."""
    A = a if a_prepped else prep(a, a_sr)
    B = b if b_prepped else prep(b, b_sr)
    n = min(len(A), len(B))
    A, B = A[:n], B[:n]
    seg = window if (window and SEG) else None
    total = 0.0
    for nf in _FFTS:                              # multi-resolution spectral loss
        MA, MB = _stft_mag(A, nf), _stft_mag(B, nf)
        f = min(len(MA), len(MB))
        MA, MB = MA[:f], MB[:f]
        w = None if seg is None else _frame_weights(B, seg, f, nf, nf // 4)
        total += _wmean(np.abs(MA - MB), w)
        total += _wmean(np.abs(np.log(MA + 1e-4) - np.log(MB + 1e-4)), w) * 0.5
    # mel-band term (perceptual timbre)
    ea, eb = _mel_env(A, ARATE), _mel_env(B, ARATE)
    f = min(len(ea), len(eb))
    w = None if seg is None else _frame_weights(B, seg, f, 1024, 256)
    total += _wmean(np.abs(ea[:f] - eb[:f]), w) * 2.0
    # amplitude-envelope term (attack/decay shape)
    def env(x):
        w = ARATE // 100
        e = np.sqrt(np.convolve(x * x, np.ones(w) / w, 'same'))
        return e / (e.max() + 1e-9)
    ea2, eb2 = env(A), env(B)
    total += np.abs(ea2 - eb2).mean() * 3.0
    return float(total)


if __name__ == "__main__":
    # sanity: identical < shifted-timbre < noise
    import numpy as np
    t = np.arange(ARATE) / ARATE
    a = np.sin(2 * np.pi * 220 * t).astype(np.float32)
    b = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    c = np.random.randn(ARATE).astype(np.float32) * 0.3
    print("loss(a,a)  =", round(loss(a, a), 4), "(expect ~0)")
    print("loss(a,220')=", round(loss(a, (a * 0.5)), 4), "(same tone, quieter -> ~0)")
    print("loss(a,440)=", round(loss(a, b), 4), "(octave off)")
    print("loss(a,noise)=", round(loss(a, c), 4), "(should be largest)")
