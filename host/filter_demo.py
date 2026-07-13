#!/usr/bin/env python3
"""Verify the per-voice resonant filter (M6b): play a sawtooth (rich harmonics) and
sweep CC74 cutoff up while recording. The spectrogram should show the harmonics
roll off above a cutoff edge that sweeps upward.
Usage: filter_demo.py [out.wav] [note] [reso]
"""
import os, sys, time, struct, wave
from uartaudio import (open_port, read_bytes, samples_from_bytes, to_signed, normalize,
                       note_on, note_off, set_wave, set_cutoff, set_reso, SR)

def main():
    a = sys.argv[1:]
    out = a[0] if a and a[0].endswith(".wav") else "filter.wav"
    a = [x for x in a if not x.endswith(".wav")]
    note = int(a[0]) if len(a) > 0 else 45          # A2 ~110 Hz: many harmonics below Nyquist
    reso = int(a[1]) if len(a) > 1 else 90           # fairly resonant

    dev, fd = open_port(rw=True)
    os.write(fd, set_wave(1))                        # sawtooth
    os.write(fd, set_reso(reso))
    os.write(fd, set_cutoff(5))                      # start closed
    os.write(fd, note_on(note, 110))
    time.sleep(0.2)

    # single-threaded sweep: set each cutoff, then capture its slice (so the writes
    # aren't starved by a concurrent reader -> the rising filter edge is visible)
    buf = bytearray()
    for cutn in range(5, 128, 2):                    # sweep cutoff 5 -> 127
        os.write(fd, set_cutoff(cutn))
        buf.extend(read_bytes(fd, 0.045))
    os.write(fd, note_off(note)); time.sleep(0.1); os.close(fd)

    s = normalize(to_signed(samples_from_bytes(bytes(buf))))
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack('<h', max(-32768, min(32767, x))) for x in s))
    print(f"[{dev}] wrote {out}: {len(s)} samples, {len(s)/SR:.2f}s @ {SR} Hz "
          f"(saw note {note}, reso {reso}, cutoff swept 5->127)")

if __name__ == "__main__":
    main()
