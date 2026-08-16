"""Learned audio distances for preset fitting, swappable for the FFT loss in `loss.py`.

`loss.py` is a hand-weighted multi-resolution STFT distance. This module offers two learned
alternatives with the same call shape, so `search.py` can be pointed at either:

    LOSS=stft        presetgen/loss.py, verbatim (the default and the baseline)
    LOSS=cdpam       CDPAM -- a distance trained on crowdsourced human just-noticeable-difference
                     judgments of audio pairs. Native rate 22.05 kHz, which is already loss.ARATE.
    LOSS=clap        1 - cosine similarity of LAION CLAP audio embeddings. Native rate 48 kHz.
    LOSS=cdpam+stft  sum, with the deep term scaled by DEEP_W (see WEIGHTS)
    LOSS=clap+stft   likewise

CMA-ES is derivative-free, so none of this has to be differentiable: `search.py` only ever needs a
scalar, and any pretrained encoder can be a black-box feature extractor. That is the whole reason
these drop in without touching the optimizer.

**None of these is known to be better than the FFT loss.** A learned embedding's invariances are
learned and unpublished, and an AudioSet-lineage model is *rewarded* for treating two synth basses
as one thing -- exactly the distinction a preset fit lives on. Sustained single synth notes are also
out of distribution for these corpora. So a deep distance can come out flatter over our patch space
than the loss it replaces while looking more sophisticated. Pick between them with human ratings and
rank correlation, not by which prints a smaller number: the scales are not comparable across
definitions. See the tracking issue for the ranking harness this is waiting on.

Cost: models load once per process and are cached; `dists()` batches a whole CMA-ES generation
through the model in one call, which is where the time goes.
"""
import os
import numpy as np

import loss as _stft                      # the FFT baseline, unmodified

ARATE = _stft.ARATE                       # 22050 -- CDPAM's native rate too
CLAP_RATE = 48000
# `laion/clap-htsat-unfused` and NOT one of the `larger_clap_*` checkpoints, which sound like the
# better choice and are not: under transformers 5.15 the larger ones come back collapsed -- two
# unrelated captions score 0.9996 against each other and audio-text similarity sits at 0.003 with
# the wrong sign on the diagonal. The unfused checkpoint behaves (a sine scores 0.63 on "a pure sine
# tone" against 0.17 on "white noise hiss", and noise the other way round). Re-test before switching.
CLAP_MODEL = os.environ.get("CLAP_MODEL", "laion/clap-htsat-unfused")

# Deep-term weight in the hybrids. The scales are wildly different -- STFT lands ~7-40 on matched
# presets, CDPAM ~0.0-1.0, CLAP cosine distance 0.0-2.0 -- so these put the deep term at roughly
# the same order as the STFT term rather than encoding any belief about their relative worth.
# Provisional until the ranking harness says otherwise.
WEIGHTS = {"cdpam": float(os.environ.get("DEEP_W", 20.0)),
           "clap": float(os.environ.get("DEEP_W", 15.0))}

_BACKEND = os.environ.get("LOSS", "stft")
_models = {}


def select(name):
    """Choose the backend for the module-level prep()/loss()."""
    global _BACKEND
    if name not in ("stft", "cdpam", "clap", "cdpam+stft", "clap+stft"):
        raise ValueError(f"unknown LOSS={name}")
    _BACKEND = name


def backend():
    return _BACKEND


# --------------------------------------------------------------------------- resampling helpers

def _resample(x, sr, to):
    x = np.asarray(x, dtype=np.float64).flatten()
    if sr == to:
        return x
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(sr), int(to))
    return resample_poly(x, to // g, sr // g)


def _rms_norm(x, level=0.1):
    """RMS-normalize to a fixed level. Both learned models were trained on real recordings and are
    not loudness-invariant, while a preset fit must be (targets and renders differ in gain by an
    arbitrary amount) -- so equalize first and let them compare timbre."""
    r = np.sqrt(np.mean(x * x)) + 1e-12
    return x * (level / r)


# --------------------------------------------------------------------------------------- CDPAM

def _cdpam():
    if "cdpam" not in _models:
        import torch
        import cdpam as _c
        # cdpam's checkpoint is a pickled dict; torch >= 2.6 defaults weights_only=True and refuses
        # it. The file ships inside the installed package, so trusting it is trusting the wheel.
        _orig = torch.load
        torch.load = lambda *a, **k: _orig(*a, **{**k, "weights_only": False})
        try:
            _models["cdpam"] = _c.CDPAM(dev="cpu")
        finally:
            torch.load = _orig
    return _models["cdpam"]


def _cdpam_wav(x, sr):
    """-> float32 [L] at 22.05 kHz in int16 units, which is what CDPAM.forward expects.
    (cdpam.load_audio does the same thing from a path, but calls np.float, removed in numpy 2.)"""
    return np.round(_rms_norm(_resample(x, sr, ARATE)) * 32768).astype(np.float32)


def _cdpam_dists(refs, outs):
    import torch
    n = min(min(len(a) for a in refs), min(len(b) for b in outs))
    A = np.stack([a[:n] for a in refs])
    B = np.stack([b[:n] for b in outs])
    with torch.no_grad():
        d = _cdpam().forward(A, B)
    return np.atleast_1d(d.detach().cpu().numpy().flatten()).astype(np.float64)


# ----------------------------------------------------------------------------------------- CLAP

def _clap():
    if "clap" not in _models:
        import torch
        from transformers import ClapModel, ClapProcessor
        m = ClapModel.from_pretrained(CLAP_MODEL).eval()
        p = ClapProcessor.from_pretrained(CLAP_MODEL)
        _models["clap"] = (m, p, torch)
    return _models["clap"]


def clap_audio_emb(waves, sr, chunk=16):
    """L2-normalized CLAP audio embeddings for a list of mono signals. [N, D] float64.
    Chunked: the feature extractor pads every clip to CLAP's 10 s window, so a whole bank in one
    call is a lot of spectrogram to hold at once for no speed gain on CPU."""
    m, p, torch = _clap()
    out = []
    for i in range(0, len(waves), chunk):
        xs = [_rms_norm(_resample(w, sr, CLAP_RATE), level=0.1).astype(np.float32)
              for w in waves[i:i + chunk]]
        inp = p(audio=xs, sampling_rate=CLAP_RATE, return_tensors="pt", padding=True)
        with torch.no_grad():
            # transformers >= 5 returns the tower output; pooler_output IS the projected embedding
            # (verified identical to ClapOutput.audio_embeds), so do not project it again.
            e = m.get_audio_features(**inp).pooler_output
        out.append((e / e.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float64))
    return np.concatenate(out) if out else np.zeros((0, 512))


def clap_text_emb(texts):
    """L2-normalized CLAP text embeddings. [N, D] float64."""
    m, p, torch = _clap()
    inp = p(text=list(texts), return_tensors="pt", padding=True)
    with torch.no_grad():
        e = m.get_text_features(**inp).pooler_output
    e = e / e.norm(dim=-1, keepdim=True)
    return e.cpu().numpy().astype(np.float64)


# ------------------------------------------------------------------------------- public interface

def prep(x, sr, window=None):
    """Cache a target in whatever form the active backend consumes (mirrors loss.prep).

    `window` is the corpus's (gate_s, tail_s), carried along so the STFT term can weight the
    note's phases by the target's own energy. Only a TARGET needs it -- a candidate is prepped
    without one and the weights come from the target it is being compared to."""
    x = np.asarray(x, dtype=np.float64).flatten()
    out = {"raw": x, "sr": sr, "window": window}
    b = _BACKEND
    if "stft" in b:
        out["stft"] = _stft.prep(x, sr)
    if b.startswith("cdpam"):
        out["cdpam"] = _cdpam_wav(x, sr)
    if b.startswith("clap"):
        out["clap"] = clap_audio_emb([x], sr)[0]
    return out


def _as_prepped(x, sr, prepped, window=None):
    return x if prepped else prep(x, sr, window)


def loss(a, b, a_sr=ARATE, b_sr=ARATE, a_prepped=False, b_prepped=False, window=None):
    """Distance between a and b under the active backend. Same signature as loss.loss."""
    return dists([a], b, a_sr=a_sr, b_sr=b_sr, a_prepped=a_prepped, b_prepped=b_prepped,
                 window=window)[0]


def dists(cands, target, a_sr=ARATE, b_sr=ARATE, a_prepped=False, b_prepped=False, window=None):
    """Distances from each of `cands` to `target`. Batched: this is the call a CMA-ES generation
    should make, so the model runs once for the whole population instead of once per candidate."""
    T = _as_prepped(target, b_sr, b_prepped, window)
    win = window or T.get("window")
    b = _BACKEND
    n = len(cands)
    total = np.zeros(n)

    if b == "stft" or b.endswith("+stft"):
        for i, c in enumerate(cands):
            C = c["stft"] if a_prepped else _stft.prep(c, a_sr)
            total[i] += _stft.loss(C, T["stft"], a_prepped=True, b_prepped=True, window=win)

    if b.startswith("cdpam"):
        cw = [c["cdpam"] if a_prepped else _cdpam_wav(c, a_sr) for c in cands]
        total += _cdpam_dists(cw, [T["cdpam"]] * n) * (WEIGHTS["cdpam"] if "+" in b else 1.0)

    if b.startswith("clap"):
        ce = (np.stack([c["clap"] for c in cands]) if a_prepped
              else clap_audio_emb([np.asarray(c, dtype=np.float64).flatten() for c in cands], a_sr))
        cos = ce @ T["clap"]
        total += (1.0 - cos) * (WEIGHTS["clap"] if "+" in b else 1.0)

    return total


if __name__ == "__main__":
    # Reference scale, the same four signals loss.py checks itself against: a distance that cannot
    # order these cannot order presets either. Prints every backend side by side.
    import sys
    t = np.arange(int(1.5 * ARATE)) / ARATE
    sig = {
        "same":      np.sin(2 * np.pi * 220 * t),
        "quieter":   np.sin(2 * np.pi * 220 * t) * 0.5,
        "detuned":   np.sin(2 * np.pi * 223 * t),
        "square220": np.sign(np.sin(2 * np.pi * 220 * t)),
        "octave":    np.sin(2 * np.pi * 440 * t),
        "noise":     np.random.default_rng(0).standard_normal(len(t)) * 0.3,
    }
    ref = sig["same"]
    names = [n for n in sys.argv[1:]] or ["stft", "cdpam", "clap"]
    print(f"{'':10}" + "".join(f"{n:>12}" for n in names))
    rows = {}
    for n in names:
        select(n)
        T = prep(ref, ARATE)
        rows[n] = {k: loss(v, T, a_sr=ARATE, b_prepped=True) for k, v in sig.items()}
    for k in sig:
        print(f"{k:10}" + "".join(f"{rows[n][k]:12.4f}" for n in names))
