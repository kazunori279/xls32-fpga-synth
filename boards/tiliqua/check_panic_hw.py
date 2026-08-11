#!/usr/bin/env python3
"""M34 exit check, on the module: the channel mode messages over USB-MIDI, judged on USB audio.

    XLS32_BOARD=tiliqua uv run python boards/tiliqua/check_panic_hw.py

The rung above ``check_panic.py``. That one grades a Verilator capture of the whole shell; this
one grades the shipped bitstream on the board, with the bytes crossing a real USB link in both
directions. It is the only rung that can catch a slot flashed with the wrong build -- CC120 and
CC123 are exactly the messages a pre-M34 engine drops on the floor, so an old bitstream fails
the first assertion of every group rather than passing quietly.

Two things about measuring this from the host, both learned the hard way here.

**Silence is peak-to-peak, not peak.** Every voice that has sounded leaves a small DC constant
behind -- the SVF's ``low1 - (low1 >> 7)`` cannot move a value below 128, so the filter state
latches and stays. The mix does not return to zero; it stops *moving*. See docs/TODO.md.

**Do not measure straight after the message.** PortAudio delivers input frames with a latency
that varies from a few milliseconds to about 200, so a window opened 50 ms after a panic can be
filled entirely with audio from before it. Measured directly: at +50 ms the same panic reads
0.001 on one trial and 1.96 on the next; at +1.5 s it read silent on 8 of 8. Nothing was wrong
with the engine, and an afternoon went into proving that. Every assertion below is made after a
settle, and the CC120-versus-CC123 distinction is drawn by holding the release long enough
(CC23 = 127) that "still ringing at +0.6 s" is unambiguous rather than a race.
"""
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "host"))
import synth as u                                                     # noqa: E402
from transport.base import open_transport                             # noqa: E402

MOVING = 0.010      # normalised peak-to-peak: above this the instrument is audibly playing
STILL  = 0.004      # below this it has stopped moving (a DC offset does not count as sound)
SETTLE = 0.60       # seconds to let the capture pipeline catch up before believing it
RELEASE = 3.00      # seconds for a CC23=127 release to finish
CHORD = (57, 61, 64)

fails = []


def cap(tp, secs=0.20):
    tp.record_start()
    time.sleep(secs)
    s = np.asarray(tp.record_stop(), dtype=np.float32) / 32768.0
    return s if len(s) > 64 else np.zeros(2, dtype=np.float32)


def ptp(s):
    return float(np.max(s) - np.min(s))


def check(cond, msg, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {msg}{('  -- ' + detail) if detail else ''}")
    if not cond:
        fails.append(msg)


def patch(tp, ch):
    """Full sustain, the longest release, no effects. The release has to outlast the settle for
    "it releases" and "it cuts" to be different measurements, and a reverb tail would keep
    singing after the voices are gone and read as a failed panic."""
    for c, v in [(70, 1 << 4), (74, 100), (71, 20), (72, 0), (78, 0), (80, 0), (73, 0),
                 (20, 0), (21, 0), (22, 127), (23, 127),         # amp A D S R
                 (24, 0), (25, 0), (26, 127), (27, 20),          # filter env
                 (92, 0), (1, 0), (93, 0), (94, 0), (95, 0)]:    # no trem, no vib, no fx
        tp.send_midi(u.cc(c, v, ch))
        time.sleep(0.002)


def hush(tp):
    for ch in range(4):
        for n in range(128):
            tp.send_midi(u.note_off(n, ch))
    time.sleep(RELEASE)


def chord(tp, ch, on=True):
    for n in CHORD:
        tp.send_midi(u.note_on(n, 100, ch) if on else u.note_off(n, ch))


def main():
    tp = open_transport().open()
    try:
        hush(tp)
        for ch in range(4):
            patch(tp, ch)
        time.sleep(0.2)

        print("\nCC123 All Notes Off (part 0)")
        chord(tp, 0)
        time.sleep(0.35)
        a = ptp(cap(tp))
        check(a > MOVING, "three notes are sounding", f"ptp {a:.3f}")
        tp.send_midi(u.all_notes_off(0))
        time.sleep(SETTLE)
        b = ptp(cap(tp))
        check(b > MOVING, "still ringing after the settle -- it releases, it does not cut",
              f"ptp {b:.3f}")
        time.sleep(RELEASE)
        c = ptp(cap(tp))
        check(c < STILL, "gone once the release has run", f"ptp {c:.3f}")

        print("\nCC120 All Sound Off (part 1)")
        chord(tp, 1)
        time.sleep(0.35)
        a = ptp(cap(tp))
        check(a > MOVING, "three notes are sounding", f"ptp {a:.3f}")
        tp.send_midi(u.all_sound_off(1))
        time.sleep(SETTLE)
        b = ptp(cap(tp))
        check(b < STILL, "silent where CC123 was still ringing -- it cuts", f"ptp {b:.3f} (was {a:.3f})")

        print("\nCC64 is ignored (part 2)")
        chord(tp, 2)
        time.sleep(0.35)
        a = ptp(cap(tp))
        check(a > MOVING, "the chord sounds", f"ptp {a:.3f}")
        tp.send_midi(u.cc(64, 127, 2))          # pedal down, if there were a pedal
        time.sleep(0.05)
        chord(tp, 2, on=False)
        time.sleep(RELEASE)
        b = ptp(cap(tp))
        check(b < STILL, "note-off releases it anyway -- no pedal to defer it", f"ptp {b:.3f}")

        print("\nCC120 cuts a voice mid-release (part 3)")
        chord(tp, 3)
        time.sleep(0.35)
        chord(tp, 3, on=False)
        time.sleep(SETTLE)
        a = ptp(cap(tp))
        check(a > MOVING, "still falling through the release", f"ptp {a:.3f}")
        tp.send_midi(u.all_sound_off(3))
        time.sleep(SETTLE)
        b = ptp(cap(tp))
        check(b < STILL, "All Sound Off takes it anyway", f"ptp {b:.3f}")

        print("\nTrsPanicInject: a target change silences the part it leaves")
        # The injector watches the TRS jack's *effective* target, and the panel drives that over
        # USB CC103 -- so the whole path (MidiPartSelect, the eff/eff_q compare, the injector, the
        # third arbiter source) can be exercised with no keyboard in the jack. What it emits is a
        # real CC123 on the channel being left, which reaches the engine like any other message
        # and therefore silences that part whether its notes arrived over TRS or USB.
        tp.send_midi(u.cc(103, 127))            # override off: the target is part 0
        time.sleep(0.1)
        tp.send_midi(u.panic())
        time.sleep(RELEASE)
        chord(tp, 0)
        time.sleep(0.35)
        a = ptp(cap(tp))
        check(a > MOVING, "part 0 is holding a chord", f"ptp {a:.3f}")
        tp.send_midi(u.cc(103, 1))              # target 0 -> 1, so part 0 is the one being left
        time.sleep(SETTLE + RELEASE)
        b = ptp(cap(tp))
        check(b < STILL, "moving the target off part 0 silenced it", f"ptp {b:.3f}")
        chord(tp, 0)
        time.sleep(0.35)
        tp.send_midi(u.cc(103, 2))              # target 1 -> 2: part 0 is not involved
        time.sleep(SETTLE + RELEASE)
        c = ptp(cap(tp))
        check(c > MOVING, "a move that does not leave part 0 leaves it playing", f"ptp {c:.3f}")
        tp.send_midi(u.cc(103, 127))
        time.sleep(0.1)

        print("\npanic() across all four parts")
        for ch in range(4):
            chord(tp, ch)
        time.sleep(0.35)
        a = ptp(cap(tp))
        check(a > MOVING, "twelve notes on four parts", f"ptp {a:.3f}")
        tp.send_midi(u.panic())
        time.sleep(SETTLE)
        b = ptp(cap(tp))
        check(b < STILL, "one panic() silences all of them", f"ptp {b:.3f}")
    finally:
        try:
            tp.send_midi(u.panic())
            tp.close()
        except Exception:
            pass

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s) -- " + "; ".join(fails))
        sys.exit(1)
    print("PASS: the channel mode messages behave on hardware")


main()
