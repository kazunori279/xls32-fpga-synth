#!/usr/bin/env python3
"""M13 showcase: effects (chorus + delay). Records to a .wav via the drain Recorder.
Part 1 A/Bs a filtered-saw chord dry -> chorus (shimmer). Part 2 plays a plucky melody
with echo (decaying repeats). Part 3 = both. CC83 selects dry/chorus/echo/both.
Usage: demo_m13.py [out.wav]"""
import os, sys, time, struct, wave, termios
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # put host/ on sys.path
from uartaudio import (open_port, samples_from_bytes, to_signed, normalize, glitches, Recorder,
                       note_on, note_off, set_wave, set_cutoff, set_reso, set_fx, cc, SR)

def perform(fd):
    for n in range(128): os.write(fd, note_off(n))
    time.sleep(0.05)
    os.write(fd, set_wave(1)); os.write(fd, set_cutoff(58)); os.write(fd, set_reso(55)); os.write(fd, cc(77, 0))
    rec = Recorder(fd)
    # Part 1: chord dry -> chorus (thickening / shimmer)
    os.write(fd, cc(79, 30))
    for mode in (0, 1):
        os.write(fd, set_fx(mode))
        for n in (45, 52, 57): os.write(fd, note_on(n, 90))
        time.sleep(2.2)
        for n in (45, 52, 57): os.write(fd, note_off(n))
        time.sleep(0.4)
    # Part 2: plucky melody with echo (decaying repeats)
    os.write(fd, set_fx(2)); os.write(fd, cc(79, 110)); os.write(fd, set_cutoff(50))  # snappy filter pluck
    for n in (57, 64, 60, 69, 64, 72):
        os.write(fd, note_on(n, 110)); time.sleep(0.14); os.write(fd, note_off(n)); time.sleep(0.46)
    time.sleep(1.0)                                   # let the last echoes ring
    # Part 3: both (chorus + echo) on a short phrase
    os.write(fd, set_fx(3)); os.write(fd, set_cutoff(56)); os.write(fd, cc(79, 60))
    for n in (52, 59, 57):
        os.write(fd, note_on(n, 100)); time.sleep(0.2); os.write(fd, note_off(n)); time.sleep(0.5)
    time.sleep(1.0)
    os.write(fd, set_fx(0))
    termios.tcdrain(fd)
    return rec.stop()

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_m13.wav"
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
