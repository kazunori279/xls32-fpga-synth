#!/usr/bin/env python3
"""Play notes on the FPGA synth over USB and FFT-verify the pitches.
Usage: play.py [--wave sine|saw|square|tri] [note ...]   (default Amaj7)
"""
import os, sys, time, termios
from uartaudio import open_port, read_bytes, samples_from_bytes, note_on, note_off, set_wave, note_to_hz
from analyze_fft import spectrum, find_peaks, pick_window

WAVES = {"sine": 0, "saw": 1, "square": 2, "tri": 3}

def main():
    args = sys.argv[1:]; wave = None
    if args and args[0] == "--wave":
        wave = WAVES[args[1]]; args = args[2:]
    notes = [int(x) for x in args] or [69, 73, 76, 80]

    dev, fd = open_port(rw=True)
    if wave is not None:
        os.write(fd, set_wave(wave)); print(f"waveform -> {[k for k,v in WAVES.items() if v==wave][0]}")
    for n in notes:
        os.write(fd, note_on(n, 100))
    print(f"[{dev}] note-on {notes} ({[round(note_to_hz(n)) for n in notes]} Hz)")

    time.sleep(0.15)
    s = samples_from_bytes(read_bytes(fd, 2.5))
    os.write(fd, b"".join(note_off(n) for n in notes)); termios.tcdrain(fd); time.sleep(0.2)
    os.close(fd)

    print(f"read {len(s)} samples")
    peaks = find_peaks(*spectrum(pick_window(s)))
    print(f"detected peaks (Hz): {[f for f, _ in peaks]}")
    hits = 0
    for n in notes:
        f = note_to_hz(n); near = min((abs(f-g) for g, _ in peaks), default=9999)
        ok = near <= max(10, 0.03*f); hits += ok
        print(f"  note {n} {f:6.1f} Hz : {'FOUND' if ok else 'missing'}")
    ok = hits >= max(1, len(notes)-1)
    print("PASS" if ok else "CHECK", f": {hits}/{len(notes)} notes")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
