#!/usr/bin/env python3
"""M12 tremolo showcase (LFO -> amplitude, CC92). A held filtered-saw chord with the
tremolo depth ramped off -> light -> med -> deep: the amplitude pulses deeper each step.
Recorded via the drain Recorder. Usage: demo_tremolo.py [out.wav]"""
import os, sys, time, struct, wave, termios
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # put host/ on sys.path
from uartaudio import (open_port, samples_from_bytes, to_signed, normalize, glitches, Recorder,
                       note_on, note_off, set_wave, set_cutoff, set_reso, set_fx, set_trem, cc, SR)

WAVE = int(sys.argv[2]) if len(sys.argv) > 2 else 1     # 0=sine 1=saw 2=square 3=tri

def perform(fd):
    for n in range(128): os.write(fd, note_off(n))
    time.sleep(0.05)
    os.write(fd, set_fx(0)); os.write(fd, set_wave(WAVE)); os.write(fd, set_cutoff(60)); os.write(fd, set_reso(55))
    os.write(fd, cc(79, 0)); os.write(fd, cc(77, 0)); os.write(fd, cc(76, 40))   # LFO rate ~5-6 Hz
    vel = 78 if WAVE == 0 else 95                            # sine chord: a touch quieter (headroom)
    rec = Recorder(fd)
    for n in (45, 52, 57, 64): os.write(fd, note_on(n, vel))  # sustained pad chord
    for d in (0, 1, 2, 3):                                     # ramp tremolo depth
        os.write(fd, set_trem(d)); time.sleep(2.2)
    for n in (45, 52, 57, 64): os.write(fd, note_off(n))
    os.write(fd, set_trem(0)); time.sleep(0.4)
    termios.tcdrain(fd)
    return rec.stop()

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_tremolo.wav"
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
