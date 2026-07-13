#!/usr/bin/env python3
"""M14 reverb showcase (CC83=4 reverb, Schroeder 4-comb + 2-all-pass, CC91 room size).
Part 1: a plucky phrase DRY. Part 2: the same phrase with CATHEDRAL reverb (each note
rings into a long diffuse tail). Part 3: a single chord stab swept through the four room
sizes (room -> hall -> large -> cathedral) so the tail audibly lengthens. Recorded via
the drain Recorder; the first (startup-artifact) capture is discarded and the take is
validated by requiring the final tail to fall to near-silence. Usage: demo_reverb.py [out.wav]
"""
import os, sys, time, struct, wave, termios, math
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # put host/ on sys.path
from uartaudio import (open_port, samples_from_bytes, to_signed, normalize, glitches, Recorder,
                       note_on, note_off, set_wave, set_cutoff, set_reso, set_fx, set_room, cc, SR)

PHRASE = (57, 60, 64, 67, 64, 60, 62, 69)
CHORD  = (45, 52, 57, 64)

def rms(seg):
    return math.sqrt(sum(x*x for x in seg) / max(1, len(seg)))

def perform(fd):
    for n in range(128): os.write(fd, note_off(n))
    time.sleep(0.05)
    os.write(fd, set_wave(1)); os.write(fd, set_cutoff(56)); os.write(fd, set_reso(50))
    os.write(fd, cc(79, 100)); os.write(fd, cc(77, 0))         # snappy pluck envelope
    os.write(fd, set_room(3))                                   # cathedral for parts 1-2
    rec = Recorder(fd)
    # Part 1: phrase dry
    os.write(fd, set_fx(0))
    for n in PHRASE:
        os.write(fd, note_on(n, 105)); time.sleep(0.16); os.write(fd, note_off(n)); time.sleep(0.20)
    time.sleep(0.6)
    # Part 2: same phrase with cathedral reverb (notes ring out)
    os.write(fd, set_fx(4))
    for n in PHRASE:
        os.write(fd, note_on(n, 105)); time.sleep(0.16); os.write(fd, note_off(n)); time.sleep(0.24)
    time.sleep(2.5)                                             # let the tail ring
    # Part 3: one chord stab per room size -> tail lengthens room..cathedral
    for room in (0, 1, 2, 3):
        os.write(fd, set_room(room)); time.sleep(0.02)
        for n in CHORD: os.write(fd, note_on(n, 95))
        time.sleep(0.18)
        for n in CHORD: os.write(fd, note_off(n))
        time.sleep(2.2 if room < 3 else 4.0)                    # room short ... cathedral long
    os.write(fd, set_fx(0))
    termios.tcdrain(fd)
    return rec.stop()

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_reverb.wav"
    # warm up: discard the first capture (startup DC artifact)
    dev, fd = open_port(rw=True)
    os.write(fd, set_fx(4)); os.write(fd, set_room(3))
    os.write(fd, set_wave(1)); os.write(fd, set_cutoff(56))
    os.write(fd, note_on(57, 90)); time.sleep(0.3); os.write(fd, note_off(57)); time.sleep(1.0)
    os.close(fd)
    for attempt in range(5):
        dev, fd = open_port(rw=True)
        raw = perform(fd); os.close(fd)
        s = to_signed(samples_from_bytes(raw))
        tail = rms(s[-int(0.4 * SR):])              # a real take ends in near-silence;
        g = glitches(s, thresh=9000)               # a byte-misaligned take stays ~30000
        print(f"attempt {attempt+1}: {len(s)} samples, tail RMS={tail:.0f}, glitches={g}")
        if tail < 400:                             # aligned + fully decayed
            break
    s = normalize(s)
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz 16-bit")

if __name__ == "__main__":
    main()
