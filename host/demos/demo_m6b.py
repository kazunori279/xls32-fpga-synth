#!/usr/bin/env python3
"""M6b showcase: per-voice resonant filter. Part 1 holds a spread sawtooth chord and
sweeps the cutoff up/down (resonant filter sweep — each voice filtered pre-mix).
Part 2 plays an ascending run at a *fixed* cutoff so key-tracking is audible/visible
(higher notes get brighter). Records to a .wav. Usage: demo_m6b.py [out.wav]"""
import os, sys, time, struct, wave, termios
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # put host/ on sys.path
from uartaudio import (open_port, samples_from_bytes, to_signed, normalize,
                       note_on, note_off, set_wave, set_cutoff, set_reso, cc, SR)

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_m6b.wav"
    dev, fd = open_port(rw=True)
    for n in range(128): os.write(fd, note_off(n))   # clear stuck voices
    time.sleep(0.05)
    os.write(fd, set_wave(1))                          # sawtooth (rich harmonics)

    buf = bytearray()
    def pump(dur):
        t1 = time.time()
        while time.time() - t1 < dur:
            try:
                c = os.read(fd, 8192)
                if c: buf.extend(c)
                else: time.sleep(0.001)
            except BlockingIOError: time.sleep(0.001)

    # --- Part 1: spread chord + resonant cutoff sweep (2 cycles up/down) ---
    os.write(fd, set_reso(95))                         # resonant
    os.write(fd, set_cutoff(8))
    for n in (33, 45, 52, 57, 64):                     # Am spread over octaves
        os.write(fd, note_on(n, 75))
    T, t0 = 7.0, time.time()
    while time.time() - t0 < T:
        f = (time.time() - t0) / T
        tri = abs(((f * 4.0) % 2.0) - 1.0)            # triangle 0->1->0, twice
        os.write(fd, set_cutoff(int(8 + tri * 110)))
        pump(0.03)
    for n in (33, 45, 52, 57, 64):
        os.write(fd, note_off(n))
    pump(0.4)

    # --- Part 2: ascending run at fixed cutoff -> key-tracking (brightness rises) ---
    os.write(fd, cc(79, 0)); os.write(fd, cc(77, 0))   # env + LFO off for a clean read
    os.write(fd, set_reso(60)); os.write(fd, set_cutoff(16))
    for n in (33, 40, 45, 52, 57, 64, 69, 76, 81, 88):
        os.write(fd, note_on(n, 105)); pump(0.30); os.write(fd, note_off(n)); pump(0.03)
    pump(0.3)

    # --- Part 3: filter-envelope pluck (low base cutoff, max env depth, repeated notes) ---
    os.write(fd, set_reso(70)); os.write(fd, set_cutoff(8)); os.write(fd, cc(79, 127))
    for _ in range(5):
        os.write(fd, note_on(45, 110)); pump(0.42); os.write(fd, note_off(45)); pump(0.12)
    pump(0.3)

    # --- Part 4: LFO auto-wah (hold a note, wobble the cutoff) ---
    os.write(fd, cc(79, 0)); os.write(fd, set_cutoff(45)); os.write(fd, cc(76, 22)); os.write(fd, cc(77, 120))
    os.write(fd, note_on(45, 110)); pump(3.0); os.write(fd, note_off(45)); pump(0.3)
    os.write(fd, cc(77, 0))                              # LFO off

    termios.tcdrain(fd); os.close(fd)
    s = normalize(to_signed(samples_from_bytes(bytes(buf))))
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz 16-bit")

if __name__ == "__main__":
    main()
