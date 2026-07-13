#!/usr/bin/env python3
"""Play a chord and record the FPGA synth's 16-bit audio to a .wav.
Usage: record_wav.py [seconds] [out.wav] [--wave sine|saw|square|tri] [note ...]"""
import os, sys, time, struct, wave, termios
from uartaudio import open_port, read_bytes, samples_from_bytes, to_signed, normalize, note_on, note_off, set_wave, SR

WAVES = {"sine": 0, "saw": 1, "square": 2, "tri": 3}

def main():
    a = sys.argv[1:]
    secs = float(a[0]) if a and a[0].replace('.', '', 1).isdigit() else 6.0
    a = a[1:] if a and a[0].replace('.', '', 1).isdigit() else a
    out = a[0] if a and a[0].endswith(".wav") else "capture.wav"
    a = [x for x in a if not x.endswith(".wav")]
    wave_sel = None
    if "--wave" in a:
        j = a.index("--wave"); wave_sel = WAVES[a[j+1]]; a = a[:j] + a[j+2:]
    notes = [int(x) for x in a] or [69, 73, 76, 80]

    dev, fd = open_port(rw=True)
    if wave_sel is not None: os.write(fd, set_wave(wave_sel))
    for n in notes: os.write(fd, note_on(n, 100))
    time.sleep(0.1)
    s = normalize(to_signed(samples_from_bytes(read_bytes(fd, secs))))
    os.write(fd, b"".join(note_off(n) for n in notes)); termios.tcdrain(fd); time.sleep(0.2)
    os.close(fd)

    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz 16-bit")

if __name__ == "__main__":
    main()
