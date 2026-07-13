#!/usr/bin/env python3
"""M11 showcase: pitch expression - vibrato, pitch bend, portamento. Records to a .wav.
Filtered saw throughout. Uses a background Recorder (drain thread) so the many rapid
bend writes don't overflow the FTDI buffer (a dropped byte misaligns the whole 16-bit
stream -> noise). Usage: demo_m11.py [out.wav]"""
import os, sys, time, struct, wave, termios
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # put host/ on sys.path
from uartaudio import (open_port, samples_from_bytes, to_signed, normalize, glitches, Recorder,
                       note_on, note_off, set_wave, set_cutoff, set_reso, set_vib, pitch_bend,
                       set_porta, cc, SR)

def perform(fd):
    for n in range(128): os.write(fd, note_off(n))
    time.sleep(0.05)
    os.write(fd, set_wave(1)); os.write(fd, set_cutoff(62)); os.write(fd, set_reso(60))
    os.write(fd, cc(79, 50)); os.write(fd, cc(77, 0))          # gentle filter-env pluck; no cutoff LFO
    rec = Recorder(fd)
    # Part 1: vibrato deepening on a held note (mod wheel 0 -> 3)
    os.write(fd, set_vib(0)); os.write(fd, set_porta(0)); os.write(fd, note_on(57, 95))
    for d in (0, 1, 2, 3):
        os.write(fd, set_vib(d)); time.sleep(0.9)
    os.write(fd, note_off(57)); os.write(fd, set_vib(0)); time.sleep(0.3)
    # Part 2: pitch bend up then down then back on a held note
    os.write(fd, note_on(57, 95)); time.sleep(0.3)
    for k in range(25):            os.write(fd, pitch_bend(k / 24)); time.sleep(0.03)
    for k in range(24, -25, -1):   os.write(fd, pitch_bend(k / 24)); time.sleep(0.02)
    for k in range(-24, 1):        os.write(fd, pitch_bend(k / 24)); time.sleep(0.03)
    os.write(fd, note_off(57)); os.write(fd, pitch_bend(0)); time.sleep(0.3)
    # Part 3: legato melody with portamento (notes glide into each other)
    os.write(fd, set_porta(3))
    for n in (45, 52, 48, 57, 52, 60, 57, 45):
        os.write(fd, note_on(n, 95)); time.sleep(0.42); os.write(fd, note_off(n)); time.sleep(0.08)
    os.write(fd, set_porta(0)); time.sleep(0.2)
    termios.tcdrain(fd)
    return rec.stop()

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_m11.wav"
    for attempt in range(4):
        dev, fd = open_port(rw=True)
        raw = perform(fd); os.close(fd)
        s = to_signed(samples_from_bytes(raw)); g = glitches(s)
        print(f"attempt {attempt+1}: {len(s)} samples, {g} glitches")
        if g == 0:
            break
    s = normalize(s)
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz 16-bit")

if __name__ == "__main__":
    main()
