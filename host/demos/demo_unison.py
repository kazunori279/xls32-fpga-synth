#!/usr/bin/env python3
"""M15 unison showcase (CC80 voice-stacking: assign N of 32 voices to one note, each
detuned + phase-decorrelated). Part 1: a saw lead phrase with unison OFF. Part 2: the
same phrase with 4-voice unison (thick, detuned super-saw). Part 3: a held power chord
swept off -> 2 -> 3 -> 4 voices so it audibly fattens. Recorded via the drain Recorder;
the first (startup-artifact) take is discarded and each take is validated by rejecting
byte-misaligned garbage (a real take peaks modestly and ends near-silent). Usage:
demo_unison.py [out.wav]
"""
import os, sys, time, struct, wave, termios, math
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # put host/ on sys.path
from uartaudio import (open_port, samples_from_bytes, to_signed, normalize, glitches, Recorder,
                       note_on, note_off, set_wave, set_cutoff, set_reso, set_fx, set_unison, cc, SR)

LEAD  = (57, 64, 62, 60, 62, 64, 67, 64)       # a simple saw lead line

def rms(seg):
    return math.sqrt(sum(x*x for x in seg) / max(1, len(seg)))

def perform(fd):
    for n in range(128): os.write(fd, note_off(n))
    time.sleep(0.05)
    os.write(fd, set_fx(0)); os.write(fd, set_wave(1))          # saw
    os.write(fd, set_cutoff(64)); os.write(fd, set_reso(40)); os.write(fd, cc(79, 60)); os.write(fd, cc(77, 0))
    rec = Recorder(fd)
    # Part 1: lead phrase, unison OFF
    os.write(fd, set_unison(0))
    for n in LEAD:
        os.write(fd, note_on(n, 100)); time.sleep(0.24); os.write(fd, note_off(n)); time.sleep(0.04)
    time.sleep(0.4)
    # Part 2: same phrase, 4-voice unison (thick detuned super-saw)
    os.write(fd, set_unison(3))
    for n in LEAD:
        os.write(fd, note_on(n, 100)); time.sleep(0.24); os.write(fd, note_off(n)); time.sleep(0.04)
    time.sleep(0.6)
    # Part 3: one sustained note swept off -> 2 -> 3 -> 4. The gain comp holds the level
    # constant, so what you hear grow is the detune beating/thickness itself.
    os.write(fd, note_on(45, 100))
    for uni in (0, 1, 2, 3):
        os.write(fd, set_unison(uni)); time.sleep(1.7)
    os.write(fd, note_off(45))
    os.write(fd, set_unison(0))
    time.sleep(0.5)
    termios.tcdrain(fd)
    return rec.stop()

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_unison.wav"
    dev, fd = open_port(rw=True)                 # warm up: discard first (startup DC) capture
    os.write(fd, set_wave(1)); os.write(fd, set_cutoff(64))
    os.write(fd, note_on(57, 90)); time.sleep(0.3); os.write(fd, note_off(57)); time.sleep(0.8)
    os.close(fd)
    for attempt in range(6):
        dev, fd = open_port(rw=True)
        raw = perform(fd); os.close(fd)
        s = to_signed(samples_from_bytes(raw))
        pk = max((abs(x) for x in s), default=0)
        tail = rms(s[-int(0.4 * SR):])           # real take ends near-silent; misaligned stays ~30000
        g = glitches(s, thresh=9000)
        print(f"attempt {attempt+1}: {len(s)} samples, peak={pk}, tail RMS={tail:.0f}, glitches={g}")
        if pk < 26000 and tail < 500:            # aligned (not railed garbage) + decayed
            break
    s = normalize(s)
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz 16-bit")

if __name__ == "__main__":
    main()
