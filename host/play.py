#!/usr/bin/env python3
"""Play notes on the FPGA synth over USB and FFT-verify the pitches.
Usage: play.py [--wave sine|saw|square|tri] [note ...]   (default Amaj7)

The capture goes through `UartTransport.record_start/record_stop`, not through `read_bytes` +
`samples_from_bytes` as it used to. That is not tidying -- the old pair is where the flakiness
was, and it is two separate faults compounding:

  * `read_bytes` **lost bytes**, on 6 of 8 captures. It slept 50 ms between two flushes before
    reading, and any pause between the flush and the first read makes something below the tty
    discard a chunk that is not a whole number of frames. Fixed since, in `transport/uart.py`,
    which has the dose response; this tool does not go back because `record_stop` also trims the
    start-up backlog against the wall clock and says whether the phase held.
  * `samples_from_bytes` locks byte alignment **once**, from a smoothness score over samples
    [200:1200], so everything after the break decodes as (Lhi, Rlo) pairs -- uniform full-scale
    hash, the M28a signature. `frame_align` re-locks every 128 bytes, so a break costs about one
    sample instead of the rest of the take, and `record_stop` reports whether the phase held.

A capture with a break in it does not look broken, it looks like the wrong note: the hash is the
loudest thing in the buffer, so `pick_window` chose it, and the peak list came back as fifty-odd
frequencies none of the notes can account for. That is how this tool reported a 9-of-9 against
4-of-9 regression between two bitstreams that measure identical. See docs/TODO.md.
"""
import sys, time
from transport.uart import UartTransport
from synth import note_on, note_off, set_wave, note_to_hz, SR
from analyze_fft import spectrum, find_peaks, pick_window

WAVES = {"sine": 0, "saw": 1, "square": 2, "tri": 3}
SECS = 2.5

def tolerance(f):
    return max(10.0, 0.03*f)

def plan(freqs, n):
    """Window length and frequency band for these notes.

    A tolerance finer than the transform can resolve is luck rather than a measurement, and the
    fixed W=2048 was exactly that: a Hann window localises a peak to about 2*SR/W, which at 32 kHz
    is 31 Hz, while C2 was asked to land inside 10. So pick W from the tightest tolerance the notes
    ask for instead of loosening the tolerance to fit W. Band from the notes too -- the old fixed
    60 Hz floor sat 5 Hz under C2's 65.4, with no room for it to be a local maximum."""
    W = 2048
    need = 2*SR/min(tolerance(f) for f in freqs)
    while W < need and 2*W <= n:
        W *= 2
    fmin = max(20, int(min(freqs)*0.75))
    fmax = min(int(SR/2) - 1, int(max(freqs)*1.4) + 40)
    return W, fmin, fmax

def main():
    args = sys.argv[1:]; wave = None
    if args and args[0] == "--wave":
        wave = WAVES[args[1]]; args = args[2:]
    notes = [int(x) for x in args] or [69, 73, 76, 80]
    freqs = [note_to_hz(n) for n in notes]

    t = UartTransport().open()
    if wave is not None:
        t.send_midi(set_wave(wave))
        print(f"waveform -> {[k for k, v in WAVES.items() if v == wave][0]}")
    for n in notes:
        t.send_midi(note_on(n, 100))
    print(f"[{t.dev}] note-on {notes} ({[round(f) for f in freqs]} Hz)")
    time.sleep(0.15)                      # let the attack pass; it is backlog, and gets trimmed
    t.record_start(); time.sleep(SECS); s = t.record_stop()
    t.send_midi(b"".join(note_off(n) for n in notes))
    align = t.last_align
    t.close()

    if len(s) < 2048:
        print(f"too few samples ({len(s)})"); sys.exit(1)
    if align:
        nchg, first, nwin, worst = align
        phase = "held" if not nchg else f"MOVED {nchg}x, first at byte {first}"
        print(f"read {len(s)} samples; frame phase {phase} over {nwin} windows (worst {worst:.3f})")
    else:
        print(f"read {len(s)} samples")

    W, fmin, fmax = plan(freqs, len(s))
    print(f"DFT: {W} samples ({1000*W/SR:.0f} ms, {2*SR/W:.1f} Hz), band {fmin}-{fmax} Hz")
    peaks = find_peaks(*spectrum(pick_window(s, W, clean=True), fmin=fmin, fmax=fmax))
    print(f"detected peaks (Hz): {[f for f, _ in peaks]}")
    hits = 0
    for n, f in zip(notes, freqs):
        near = min((abs(f-g) for g, _ in peaks), default=9999)
        ok = near <= tolerance(f); hits += ok
        mark = "FOUND" if ok else "missing"
        print(f"  note {n} {f:6.1f} Hz : {mark} (nearest {near:5.1f} Hz off)")
    ok = hits >= max(1, len(notes)-1)
    print("PASS" if ok else "CHECK", f": {hits}/{len(notes)} notes")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
