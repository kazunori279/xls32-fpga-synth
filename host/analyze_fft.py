#!/usr/bin/env python3
"""Detect pitches in the synth's 16-bit UART audio via a DFT.
  analyze_fft.py                      # read ints / "S N" lines from stdin (sim)
  analyze_fft.py --serial [sec]       # read from the UART (hardware)
"""
import sys, math, statistics
from transport.uart import open_port, read_bytes, frame_align
from synth import SR

def read_stdin():
    v = []
    for line in sys.stdin:
        t = line.split()
        if not t: continue
        tok = t[-1] if t[0] == "S" else t[0]
        try: v.append(int(tok))
        except ValueError: pass
    return v

def pick_window(s, W=2048, clean=False):
    """The W samples to transform. `clean=False` takes the loudest, which is the wrong
    tie-breaker whenever the loudest thing in the capture is not the note: a frame-phase shift
    decodes as full-scale hash and wins every time, and so does the tail of the *previous* take
    sitting at the front of the buffer. `clean=True` keeps only windows within 40 % of the
    loudest and then takes the one with the fewest sample-to-sample jumps over `synth.glitches`'
    threshold -- a tone of any pitch in range has none, misdecoded bytes have thousands.

    `host/play.py` passes `clean=True`. The default stays `False` for `test/analysis.py`, and
    since M37 that is a measured decision rather than a cautious one: `test/regrade.py` re-graded
    all 175 stored Tiliqua captures both ways. `clean=True` picks a different window on 68 of 350
    picks and moves exactly one score -- `filter_sweep`, 81.7 to 80.4, because `centroid_over_time`
    re-picks inside each of its eight slices and a quieter window flattens the rise it is looking
    for. Nothing improves. Only two of the 68 had the default landing on the glitchier window
    (`filter_notch`), and the take-level pathologies `clean=True` exists for are already rejected
    upstream by `harness._bad_take`. Unmeasured: the Basys 3 UART set, where those pathologies are
    the ones that actually occur -- no capture set for it is on disk. See #11, docs/TODO.md."""
    step = max(1, W // 8) if clean else 256
    offs = list(range(0, max(1, len(s)-W), step))
    pps = [max(s[i:i+W]) - min(s[i:i+W]) for i in offs]
    if not clean:
        return s[offs[pps.index(max(pps))]:][:W]
    from synth import glitches
    top = max(pps)
    loud = [i for i, pp in zip(offs, pps) if pp >= 0.6*top] or offs
    best = min(loud, key=lambda i: (glitches(s[i:i+W]), -(max(s[i:i+W])-min(s[i:i+W]))))
    return s[best:best+W]

def spectrum(w, fmin=60, fmax=3000, step=4):
    n = len(w); mean = sum(w)/n
    xs = [(v-mean) * (0.5 - 0.5*math.cos(2*math.pi*k/(n-1))) for k, v in enumerate(w)]
    freqs = list(range(fmin, fmax+1, step)); mags = []
    for f in freqs:
        wf = 2*math.pi*f/SR; re = im = 0.0
        for k, x in enumerate(xs):
            re += x*math.cos(wf*k); im -= x*math.sin(wf*k)
        mags.append(math.hypot(re, im))
    return freqs, mags

def find_peaks(freqs, mags, rel=0.25):
    mx = max(mags); th = rel*mx; cand = []
    for i in range(1, len(mags)-1):
        if mags[i] > th and mags[i] >= mags[i-1] and mags[i] >= mags[i+1]:
            cand.append((freqs[i], mags[i]))
    cand.sort(key=lambda x: -x[1]); kept = []
    for f, m in cand:
        if all(abs(f-g) > 25 for g, _ in kept): kept.append((f, m))
    return sorted(kept)

CHORD = {"A4": 440, "C#5": 554, "E5": 659, "G#5": 831}

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--serial":
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
        dev, fd = open_port(); import os
        s = frame_align(read_bytes(fd, secs)); os.close(fd)   # re-locks; see uart.py
        print(f"[{dev}] {len(s)} samples")
    else:
        s = read_stdin()
    if len(s) < 2048:
        print(f"too few samples ({len(s)})"); sys.exit(1)
    peaks = find_peaks(*spectrum(pick_window(s)))
    print(f"detected peaks (Hz): {[f for f, _ in peaks]}")
    hits = sum(any(abs(f-g) <= max(10, 0.03*f) for g, _ in peaks) for f in CHORD.values())
    ok = hits >= 3 and len(peaks) >= 3
    for name, f in CHORD.items():
        near = min((abs(f-g) for g, _ in peaks), default=9999)
        print(f"  {name:4} {f:4} Hz : {'FOUND' if near <= max(10,0.03*f) else 'missing'}")
    print(f"{'PASS' if ok else 'CHECK'}: {hits}/4 chord tones, {len(peaks)} peaks")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
