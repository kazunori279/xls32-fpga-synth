#!/usr/bin/env python3
"""Play a chord and record the FPGA synth's 16-bit audio to a .wav.
Usage: record_wav.py [seconds] [out.wav] [--wave sine|saw|square|tri] [note ...]

Captures through `UartTransport`, not `read_bytes` + `samples_from_bytes`: that pair loses
frame phase on most captures and decodes everything after the break as full-scale hash (see
`read_bytes` in transport/uart.py). A take that had one was silently a take with a burst of
noise in it, which is a bad property for the file someone listens to."""
import sys, time, struct, wave
from transport.uart import UartTransport
from synth import normalize, note_on, note_off, set_wave, SR

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

    t = UartTransport().open()
    if wave_sel is not None: t.send_midi(set_wave(wave_sel))
    for n in notes: t.send_midi(note_on(n, 100))
    time.sleep(0.1)                       # attack; it predates record_start and gets trimmed
    t.record_start(); time.sleep(secs); s = normalize(t.record_stop())
    t.send_midi(b"".join(note_off(n) for n in notes))
    align, dev = t.last_align, t.dev
    t.close()
    if align and align[0]:
        print(f"[{dev}] WARNING: frame phase moved {align[0]}x, first at byte {align[1]}")

    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz 16-bit")

if __name__ == "__main__":
    main()
