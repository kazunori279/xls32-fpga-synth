#!/usr/bin/env python3
"""M9 showcase: noise + multimode filter + sub-oscillator. Records to a .wav.
Part 1 sweeps a resonant filter over white noise (whoosh). Part 2 holds a saw and
cycles LP→HP→BP→notch. Part 3 A/Bs a bass note without vs with the sub-oscillator.
Usage: demo_m9.py [out.wav]"""
import os, sys, time, struct, wave, termios
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # put host/ on sys.path
from uartaudio import (open_port, samples_from_bytes, to_signed, normalize, note_on, note_off,
                       set_wave, set_cutoff, set_reso, set_fmode, set_sub, set_noise, cc, SR)

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_m9.wav"
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

    # --- Part 1: NOISE through a resonant filter sweep (whoosh) ---
    os.write(fd, set_noise()); os.write(fd, set_fmode(0)); os.write(fd, set_reso(105)); os.write(fd, set_sub(0))
    os.write(fd, note_on(60, 110))
    T, t0 = 4.0, time.time()
    while time.time() - t0 < T:
        f = (time.time() - t0) / T
        tri = abs(((f * 4.0) % 2.0) - 1.0)             # up/down twice
        os.write(fd, set_cutoff(int(6 + tri * 118)))
        pump(0.03)
    os.write(fd, note_off(60)); pump(0.3)

    # --- Part 2: MULTIMODE filter on a held saw: LP -> HP -> BP -> notch ---
    os.write(fd, set_wave(1)); os.write(fd, set_reso(70)); os.write(fd, set_cutoff(48))
    os.write(fd, note_on(45, 110))
    for mode in (0, 1, 2, 3):
        os.write(fd, set_fmode(mode)); pump(1.1)
    os.write(fd, note_off(45)); os.write(fd, set_fmode(0)); pump(0.3)

    # --- Part 3: SUB-OSCILLATOR A/B on a bass line (off, then full) ---
    os.write(fd, set_wave(1)); os.write(fd, set_reso(55)); os.write(fd, set_cutoff(70))
    for n in (33, 40):
        os.write(fd, set_sub(0)); os.write(fd, note_on(n, 110)); pump(0.9); os.write(fd, note_off(n)); pump(0.1)
        os.write(fd, set_sub(3)); os.write(fd, note_on(n, 110)); pump(0.9); os.write(fd, note_off(n)); pump(0.1)
    os.write(fd, set_sub(0))

    termios.tcdrain(fd); os.close(fd)
    s = normalize(to_signed(samples_from_bytes(bytes(buf))))
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz 16-bit")

if __name__ == "__main__":
    main()
