"""Perceptual distance between two mono signals for preset matching.

Multi-resolution STFT (magnitude + log-magnitude) + a mel-ish band term + an amplitude-
envelope term. Both signals are resampled to a common analysis rate, loudness-normalized,
and compared magnitude-only (phase-invariant). Lower = closer.
"""
import numpy as np
from scipy.signal import resample_poly

ARATE = 22050                    # common analysis sample rate
_FFTS = (256, 512, 1024, 2048)   # multi-resolution window sizes


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


def prep(x, sr):
    """Resample to ARATE + RMS-normalize once (cache targets with this)."""
    return _norm(_resample(np.asarray(x, dtype=np.float64).flatten(), sr))


def loss(a, b, a_sr=ARATE, b_sr=ARATE, a_prepped=False, b_prepped=False):
    """Distance between signals a and b. Pass *_prepped=True if already prep()'d at ARATE."""
    A = a if a_prepped else prep(a, a_sr)
    B = b if b_prepped else prep(b, b_sr)
    n = min(len(A), len(B))
    A, B = A[:n], B[:n]
    total = 0.0
    for nf in _FFTS:                              # multi-resolution spectral loss
        MA, MB = _stft_mag(A, nf), _stft_mag(B, nf)
        f = min(len(MA), len(MB))
        MA, MB = MA[:f], MB[:f]
        total += np.abs(MA - MB).mean()
        total += np.abs(np.log(MA + 1e-4) - np.log(MB + 1e-4)).mean() * 0.5
    # mel-band term (perceptual timbre)
    ea, eb = _mel_env(A, ARATE), _mel_env(B, ARATE)
    f = min(len(ea), len(eb))
    total += np.abs(ea[:f] - eb[:f]).mean() * 2.0
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
