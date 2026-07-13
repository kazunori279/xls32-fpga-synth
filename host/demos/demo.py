#!/usr/bin/env python3
"""Filter demo: two saw notes with the resonant low-pass cutoff swept up and down
(classic filter sweep), recorded to a .wav. Usage: demo.py [out.wav]"""
import os, sys, time, struct, wave, termios
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # put host/ on sys.path
from uartaudio import open_port, samples_from_bytes, to_signed, normalize, note_on, note_off, set_wave, SR

def cc(n, v): return bytes([0xB0, n & 0x7F, v & 0x7F])

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "demo.wav"
    dev, fd = open_port(rw=True)

    for n in range(128):                          # reset any stuck voices
        os.write(fd, note_off(n))
    time.sleep(0.05)
    os.write(fd, set_wave(1))                     # saw (rich harmonics)
    os.write(fd, cc(71, 30))                      # resonance (sweep-safe)
    os.write(fd, cc(74, 20))                      # start low
    os.write(fd, note_on(45, 60)); os.write(fd, note_on(57, 60))

    buf = bytearray(); t0 = time.time(); DUR = 5.0
    while time.time() - t0 < DUR:
        frac = (time.time() - t0) / DUR
        tri = frac * 2 if frac < 0.5 else (1 - frac) * 2      # 0->1->0
        os.write(fd, cc(74, int(20 + tri * 90)))             # cutoff 20..110..20
        t1 = time.time()
        while time.time() - t1 < 0.03:
            try:
                c = os.read(fd, 8192); buf += c if c else b""
                if not c: time.sleep(0.001)
            except BlockingIOError: time.sleep(0.001)
    os.write(fd, note_off(45)); os.write(fd, note_off(57))
    termios.tcdrain(fd); os.close(fd)

    s = normalize(to_signed(samples_from_bytes(bytes(buf))))
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz 16-bit")

if __name__ == "__main__":
    main()
