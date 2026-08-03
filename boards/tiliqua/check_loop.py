#!/usr/bin/env python3
"""M25 exit check: is the host↔board loop closed?

The 175-case suite is a bad first thing to point at new hardware -- when it fails you
cannot tell a broken transport from a broken synth. This does the smallest end-to-end
thing instead: send one note over USB-MIDI, record it back over USB audio, and check
the pitch. If this passes, ``run_tests.py --board tiliqua`` is worth starting.

It exercises exactly the two links M25 added and nothing else:

  * **MIDI down** -- the note has to survive the host's USB-MIDI driver, the bulk
    endpoint, ``UsbMidiUnpack``, M24's RT/SysEx/SysCommon filters, the usb→sync CDC and
    the engine's own DSLX parser. A wrong pitch or silence means one of those ate it.
  * **Audio up** -- the tee on ``core.o``, the UAC2 IN stream, and the host's scaling
    back into the ±32768 domain the test thresholds are written in.

It also prints two numbers worth watching.

The **frame gap rate**: on a correctly-clocked board this is ~0.001%, essentially zero.
An earlier 2.5-5% figure was withdrawn after re-measurement
(``docs/TILIQUA_USB_DROPOUTS.md``), so treat anything above a few hundredths of a
percent as a real signal -- cable, host, or clock -- and not as the expected baseline.

The **audio clock**, read off the counter the gateware puts on channel 2. It has to be
12.288 MHz, and it is checked before the pitch is, because a wrong clock detunes the
whole instrument by its own ratio and would otherwise present as an inexplicable pitch
error -- which is exactly how M25 lost most of a day to a stale ``clk0``.

    uv run boards/tiliqua/check_loop.py
    uv run boards/tiliqua/check_loop.py --note 60 --secs 2
"""

import argparse
import math
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "host"))
os.environ.setdefault("XLS32_BOARD", "tiliqua")

import numpy as np                                                    # noqa: E402

from boards import get_board                                          # noqa: E402
from synth import note_off, note_on                                   # noqa: E402
from transport.base import open_transport                             # noqa: E402

# Skip the ADSR attack and decay: during the first ~80 ms the envelope smears the
# spectrum enough to move the peak by several bins. Same reasoning as check_pitch.py.
SKIP_FRACTION = 0.35
# A semitone is 5.9%. Half of that is a comfortable bar for "the right note" while still
# catching an octave error, a resampler ratio slip, or a wrong-clock audio domain.
TOL_CENTS = 50.0


def dominant_hz(s, sr):
    """Peak of the windowed spectrum, refined by parabolic interpolation.

    Without the refinement the bin spacing alone (sr/N ≈ 0.5 Hz over 2 s) is fine, but
    a Hann window spreads the peak over three bins and the raw argmax can sit on the
    wrong side of it -- worth the three lines when the tolerance is in cents.
    """
    x = np.asarray(s, dtype=float)
    x = x - x.mean()
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    k = int(mag[1:].argmax()) + 1                    # never DC
    if 0 < k < len(mag) - 1:
        a, b, c = mag[k - 1], mag[k], mag[k + 1]
        denom = a - 2 * b + c
        k += 0.5 * (a - c) / denom if denom else 0.0
    return k * sr / len(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", type=int, default=69, help="MIDI note (default 69 = A4)")
    ap.add_argument("--vel", type=int, default=100)
    ap.add_argument("--secs", type=float, default=2.0, help="how long to hold the note")
    args = ap.parse_args()

    board = get_board()
    expect = 440.0 * 2 ** ((args.note - 69) / 12.0)

    t = open_transport(board).open()
    try:
        # The boot ROM opens the filter and sets the part volumes, so a bare note-on is
        # already audible -- no setup CCs, deliberately: fewer things to blame.
        t.send_midi(note_off(args.note))
        time.sleep(0.2)
        t.record_start()
        t.send_midi(note_on(args.note, args.vel))
        time.sleep(args.secs)
        t.send_midi(note_off(args.note))
        time.sleep(0.1)
        s = t.record_stop()
    finally:
        t.close()

    if len(s) < 4096:
        sys.exit(f"FAIL: captured only {len(s)} samples -- is the board enumerating "
                 f"as an audio device, and is the tee running?")

    peak = max(abs(v) for v in s)
    body = s[int(len(s) * SKIP_FRACTION):]
    hz = dominant_hz(body, board.sr)
    cents = 1200 * math.log2(hz / expect) if hz > 0 else float("-inf")

    gap = t.gap_rate
    print(f"captured {len(s)} samples @ {board.sr} Hz ({len(s)/board.sr:.2f} s), "
          f"peak {peak}")
    print(f"frame gaps {100*gap:.3f}%  (expect ~0.001%; longest {t.longest_gap} frames, "
          f"longest clean run {t.longest_clean})")

    clock = t.clock_note() if hasattr(t, "clock_note") else None
    if clock:
        text, clock_ok = clock
        print(text)
    else:
        clock_ok = True                      # nothing measured; say nothing about it

    print(f"note {args.note}: expected {expect:.2f} Hz, measured {hz:.2f} Hz "
          f"({cents:+.1f} cents)")

    # Before the pitch, because a wrong clock explains any pitch error and no amount of
    # rebuilding fixes it. Only the bootloader programs the SI5351, so a bitstream loaded
    # to SRAM inherits whatever rate the last-booted slot left behind.
    if not clock_ok:
        sys.exit("FAIL: the audio clock is wrong -- the module has to be sitting in the "
                 "bootloader\n  when the SRAM load happens. Power-cycle it and touch the "
                 "encoder within 5\n  seconds, or long-press the encoder from a running "
                 "slot; then load again.\n  clk0 comes from the SI5351, which only the "
                 "bootloader programs. A power cycle\n  alone is not enough: it autoboots "
                 "the last slot after a 5 s countdown and that\n  slot's manifest sets "
                 "clk0 (XBEAM leaves 49.152 MHz: 4x, +2400 cents).\n  See "
                 "boards/tiliqua/board.py.")

    if peak < 800:
        sys.exit("FAIL: silent -- the note-on never reached the engine, or the tee is "
                 "not being written (check the USB-MIDI destination name)")
    if abs(cents) > TOL_CENTS:
        octaves = cents / 1200
        hint = ("  (an exact octave: suspect the sample rate or a stereo de-interleave)"
                if abs(round(octaves) - octaves) < 0.05 and round(octaves) else "")
        sys.exit(f"FAIL: off by {cents:+.1f} cents, tolerance ±{TOL_CENTS:.0f}{hint}")
    print("PASS: MIDI down and audio up are both working.")


if __name__ == "__main__":
    main()
