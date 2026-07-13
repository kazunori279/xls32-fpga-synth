#!/usr/bin/env python3
# LED "comet" showcase: drives note-ons so the 16 board LEDs show the sliding-cursor
# light show. Each note advances the head one LED; brightness follows the ADSR envelope
# (so each LED swells then fades). Watch the Basys 3 LEDs, not the terminal.
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import uartaudio as u

def main():
    dev, fd = u.open_port(rw=True)
    w = lambda b: (os.write(fd, b), time.sleep(0.01))
    # envelope tuned for a visible swell + trailing fade
    w(u.cc(70, 32))    # saw
    w(u.cc(20, 46))    # amp attack  (~medium swell)
    w(u.cc(22, 120))   # amp sustain (stays bright while held)
    w(u.cc(23, 40))    # amp release (visible fade after note-off)
    w(u.cc(80, 0))     # unison off
    time.sleep(0.2)

    print("1) single notes — watch the head step one LED at a time, each fading behind it")
    for n in [60, 62, 64, 65, 67, 69, 71, 72]:
        w(u.note_on(n, 110)); time.sleep(0.9)
        w(u.note_off(n));      time.sleep(0.5)

    time.sleep(0.6)
    print("2) chords — several new voices at once jump the head multiple LEDs fast")
    for chord in ([48, 55, 64], [50, 57, 65], [52, 59, 67, 71]):
        for n in chord: w(u.note_on(n, 110))
        time.sleep(1.2)
        for n in chord: w(u.note_off(n))
        time.sleep(0.7)

    time.sleep(0.4)
    print("3) unison (CC80=3 -> 4 stacked voices) — one keypress lights a burst of LEDs")
    w(u.cc(80, 3)); time.sleep(0.1)
    for n in [55, 60, 64]:
        w(u.note_on(n, 110)); time.sleep(1.3)
        w(u.note_off(n));      time.sleep(0.8)
    w(u.cc(80, 0))
    os.close(fd)
    print("done")

if __name__ == "__main__":
    main()
