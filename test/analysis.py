"""Audio-analysis primitives for the e2e test suite. Operate on a list of *signed*
16-bit samples (`to_signed(samples_from_bytes(raw))`). Built on the project's DFT
(`host/analyze_fft.py`) plus envelope / band-energy / glitch / latch measures. Pure
stdlib (math), no numpy."""
import os, sys, math

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "host"))   # import host helpers
import analyze_fft                                                  # noqa: E402
from uartaudio import SR, glitches                                  # noqa: E402

# ---------- level ----------
def rms(s):
    if not s: return 0.0
    return math.sqrt(sum(x * x for x in s) / len(s))

def peak(s):
    return max((abs(x) for x in s), default=0)

def clip_ratio(s, near=32300):
    """Fraction of samples pinned at (near) full-scale — a rail/clip signature."""
    if not s: return 0.0
    return sum(1 for x in s if abs(x) >= near) / len(s)

# ---------- spectral ----------
def _window(s, W=4096):
    return analyze_fft.pick_window(s, W) if len(s) > W else s

def dft_mag(w, freqs):
    """Windowed (Hann) DFT magnitude at each freq in `freqs`. Same math as
    analyze_fft.spectrum but for an arbitrary freq list."""
    n = len(w)
    if n < 2: return [0.0] * len(freqs)
    mean = sum(w) / n
    xs = [(v - mean) * (0.5 - 0.5 * math.cos(2 * math.pi * k / (n - 1))) for k, v in enumerate(w)]
    out = []
    for f in freqs:
        wf = 2 * math.pi * f / SR; re = im = 0.0
        for k, x in enumerate(xs):
            re += x * math.cos(wf * k); im -= x * math.sin(wf * k)
        out.append(math.hypot(re, im))
    return out

def spectrum(s, fmin=60, fmax=6000, step=8):
    return analyze_fft.spectrum(_window(s), fmin=fmin, fmax=fmax, step=step)

def peaks(s, fmin=60, fmax=6000, step=8, rel=0.25):
    return analyze_fft.find_peaks(*spectrum(s, fmin, fmax, step), rel=rel)

def dominant(s, fmin=60, fmax=3000):
    p = peaks(s, fmin, fmax)
    return p[0][0] if p else 0    # find_peaks returns sorted-by-freq; re-pick strongest:

def strongest(s, fmin=60, fmax=3000, step=4):
    freqs, mags = spectrum(s, fmin, fmax, step)
    if not mags: return 0, 0.0
    i = max(range(len(mags)), key=lambda j: mags[j])
    return freqs[i], mags[i]

def harmonic_profile(s, f0, n=6):
    """Normalized magnitudes at f0, 2·f0 … n·f0 (harmonic[0] == 1.0)."""
    w = _window(s)
    mags = dft_mag(w, [f0 * k for k in range(1, n + 1)])
    base = mags[0] or 1.0
    return [m / base for m in mags]

def band_energy(s, lo, hi, step=8):
    """Per-bin mean power in [lo,hi] — bandwidth-normalized so bands of different
    widths are comparable (a raw sum would let a wide band win on bin count alone)."""
    freqs, mags = spectrum(s, fmin=max(20, lo), fmax=hi, step=step)
    if not mags: return 0.0
    return sum(m * m for m in mags) / len(mags)

def spectral_centroid(s, fmin=60, fmax=8000, step=16):
    freqs, mags = spectrum(s, fmin, fmax, step)
    tot = sum(mags)
    if tot <= 0: return 0.0
    return sum(f * m for f, m in zip(freqs, mags)) / tot

def centroid_over_time(s, nseg=8, fmin=60, fmax=8000):
    seg = max(1, len(s) // nseg)
    return [spectral_centroid(s[i * seg:(i + 1) * seg], fmin, fmax) for i in range(nseg)]

def pitch_track(s, nseg=8, fmin=60, fmax=3000):
    seg = max(1, len(s) // nseg)
    out = []
    for i in range(nseg):
        f, m = strongest(s[i * seg:(i + 1) * seg], fmin, fmax)
        out.append(f if m > 0 else 0)
    return out

# ---------- envelope / timing ----------
def envelope(s, win=256):
    return [sum(abs(x) for x in s[i:i + win]) / win for i in range(0, max(0, len(s) - win), win)]

def env_stats(env):
    """onset index, peak, rise-to-50% (samples, onset-relative), sustain (median of
    the plateau), tail (last-10% mean). Returns a dict; times are in windows."""
    if not env or max(env) < 1:
        return {"peak": 0, "onset": 0, "rise50": None, "sustain": 0, "tail": 0}
    pk = max(env)
    onset = next((j for j, e in enumerate(env) if e >= 0.15 * pk), 0)
    rise50 = next((j - onset for j, e in enumerate(env) if j >= onset and e >= 0.5 * pk), None)
    mid = env[len(env) // 3: 2 * len(env) // 3] or env
    mid_sorted = sorted(mid)
    sustain = mid_sorted[len(mid_sorted) // 2]
    tail = sum(env[-max(1, len(env) // 10):]) / max(1, len(env) // 10)
    return {"peak": pk, "onset": onset, "rise50": rise50, "sustain": sustain, "tail": tail}

def beating_cv(s):
    """Coefficient of variation of the sustained envelope — detune/unison beating
    shows up as amplitude modulation. Uses the middle 60% (skip attack/release)."""
    env = envelope(s, win=256)
    if len(env) < 8: return 0.0
    core = env[len(env) // 5: 4 * len(env) // 5]
    m = sum(core) / len(core)
    if m <= 1: return 0.0
    sd = math.sqrt(sum((e - m) ** 2 for e in core) / len(core))
    return sd / m

def modulation_depth(s, win=128):
    """Peak-to-trough / mean of the smoothed envelope over the sustain — for tremolo."""
    env = envelope(s, win)
    if len(env) < 8: return 0.0
    core = env[len(env) // 5: 4 * len(env) // 5]
    m = sum(core) / len(core)
    if m <= 1: return 0.0
    return (max(core) - min(core)) / m

# ---------- glitch / latch ----------
def glitch_count(s, thresh=9000):
    return glitches(s, thresh)

def silence_tail(s, ms=300):
    n = int(SR * ms / 1000)
    return rms(s[-n:]) if len(s) > n else rms(s)

def tail_energy(s, skip_frac=0.2, win=4000):
    """Loudest ~0.12 s window after the initial excitation — for measuring a reverb/
    echo tail absolutely (the dry stab clips loud, so a ratio-to-peak is useless; the
    tail sits ~40-160 RMS for reverb vs ~5 dry)."""
    start = int(len(s) * skip_frac); seg = s[start:]
    return max((rms(seg[i:i + win]) for i in range(0, max(1, len(seg) - win), win)), default=0.0)

def is_latched(s, ms=300, floor=400):
    """After a note has been released and settled, the tail should be ~digital
    silence. A high tail RMS means a stuck/latched filter or reverb feedback."""
    return silence_tail(s, ms) > floor

# ---------- helpers for scoring ----------
def note_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)

def near(a, b, tol_frac=0.03, tol_abs=8):
    return abs(a - b) <= max(tol_abs, tol_frac * b)

def found_pitches(s, notes, fmax=4000, rel=0.15):
    """How many of `notes` (MIDI) carry real energy at their fundamental. Targeted DFT
    at each note freq vs the strongest *musical* bin (fmin=150 to ignore subsonic
    DC/rumble that could otherwise dwarf the ratio) — robust when resonance/filtering
    makes a harmonic the tallest peak."""
    w = _window(s)
    _, ref = strongest(s, 150, fmax)
    ref = ref or 1.0
    mags = dft_mag(w, [note_hz(n) for n in notes])
    hits = sum(1 for m in mags if m >= rel * ref)
    return hits, [round(note_hz(n)) for n in notes]
