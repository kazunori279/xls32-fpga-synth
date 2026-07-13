#!/usr/bin/env python3
"""M10 showcase: PWM + detuned dual oscillator. Records to a .wav.
Part 1 sweeps the pulse width on a held note (classic PWM morph). Part 2 A/Bs a saw
chord without vs with the detuned 2nd oscillator (thin -> fat/beating).
Usage: demo_m10.py [out.wav]"""
import os, sys, time, struct, wave, termios
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # put host/ on sys.path
from uartaudio import (open_port, samples_from_bytes, to_signed, normalize, note_on, note_off,
                       set_wave, set_cutoff, set_reso, set_pw, set_detune, cc, SR)

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_m10.wav"
    dev, fd = open_port(rw=True)
    for n in range(128): os.write(fd, note_off(n))
    time.sleep(0.05)
    os.write(fd, cc(79, 0)); os.write(fd, cc(77, 0))   # filter-env + LFO off for clean reads

    buf = bytearray()
    def pump(dur):
        t = time.time()
        while time.time() - t < dur:
            try:
                c = os.read(fd, 8192)
                if c: buf.extend(c)
                else: time.sleep(0.001)
            except BlockingIOError: time.sleep(0.001)

    # Naive saw/pulse alias hard at 32 kHz when the filter is wide open, so this demo
    # plays them MUSICALLY: a moderate low-pass (CC74) rolls off the aliased highs, and
    # the pulse width / detune stay in tasteful ranges.

    # --- Part 1: filtered PWM pad, width wobbling gently ---
    os.write(fd, set_wave(2)); os.write(fd, set_detune(0))
    os.write(fd, set_cutoff(58)); os.write(fd, set_reso(55)); os.write(fd, cc(79, 80))  # filter env pluck
    for n in (45, 52, 57): os.write(fd, note_on(n, 95))     # a chord pad
    T, t0 = 5.0, time.time()
    while time.time() - t0 < T:
        f = (time.time() - t0) / T
        tri = abs(((f * 4.0) % 2.0) - 1.0)             # 64 -> ~34 -> 64, twice (moderate)
        os.write(fd, set_pw(int(64 - tri * 30)))
        pump(0.03)
    for n in (45, 52, 57): os.write(fd, note_off(n))
    os.write(fd, set_pw(64)); pump(0.4)

    # --- Part 2: filtered detuned-saw A/B (thin -> fat) with a filter-envelope pluck ---
    os.write(fd, set_wave(1)); os.write(fd, set_cutoff(52)); os.write(fd, set_reso(50)); os.write(fd, cc(79, 110))
    for chord in ((45, 52), (40, 47)):                 # mid chords (less aliasing than very low)
        os.write(fd, set_detune(0))
        for n in chord: os.write(fd, note_on(n, 95))
        pump(1.3)
        for n in chord: os.write(fd, note_off(n))
        pump(0.2)
        os.write(fd, set_detune(2))                    # ~7 cents -> fat but smooth
        for n in chord: os.write(fd, note_on(n, 95))
        pump(1.7)
        for n in chord: os.write(fd, note_off(n))
        pump(0.25)
    os.write(fd, set_detune(0)); os.write(fd, cc(79, 0))

    termios.tcdrain(fd); os.close(fd)
    s = normalize(to_signed(samples_from_bytes(bytes(buf))))
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz 16-bit")

if __name__ == "__main__":
    main()
