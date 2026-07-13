#!/usr/bin/env python3
"""Detect pitches in the synth's 16-bit UART audio via a DFT.
  analyze_fft.py                      # read ints / "S N" lines from stdin (sim)
  analyze_fft.py --serial [sec]       # read from the UART (hardware)
"""
import sys, math, statistics
from uartaudio import SR, open_port, read_bytes, samples_from_bytes

def read_stdin():
    v = []
    for line in sys.stdin:
        t = line.split()
        if not t: continue
        tok = t[-1] if t[0] == "S" else t[0]
        try: v.append(int(tok))
        except ValueError: pass
    return v

def pick_window(s, W=2048):
    best_i, best_pp = 0, -1
    for i in range(0, max(1, len(s)-W), 256):
        seg = s[i:i+W]
        pp = max(seg) - min(seg)
        if pp > best_pp: best_pp, best_i = pp, i
    return s[best_i:best_i+W]

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
        s = samples_from_bytes(read_bytes(fd, secs)); os.close(fd)
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
