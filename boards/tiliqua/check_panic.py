#!/usr/bin/env python3
"""M34 exit check: do the channel mode messages stop the synth from the TRS jack?

Reads the ``panic`` capture -- two identical three-note chords, the first stopped by CC123 All
Notes Off, the second by CC120 All Sound Off::

    SIM=1 XLS_SIM_MIDI=panic XLS_SIM_MS=1150 \\
        XLS_SIM_OUT="$PWD/build/tiliqua/out0_panic.txt" bash boards/tiliqua/build.sh
    uv run boards/tiliqua/check_panic.py

``core/sim/tb_panic.v`` already grades the same messages against the engine's own visualiser tap,
one voice at a time and in a few seconds. This is the rung above it: the bytes arrive at
``midi_rx`` as a bit-banged serial stream and pass the UART, the three System-message filters,
the byte CDC and the effects before anything is measured. Nothing here is true unless the whole
shell carries them.

**They stop it at all.** No note-off is ever sent -- the chords are ended only by the mode
message. Before M34 ``apply_cc``'s catch-all dropped 120-127 on the floor, so both chords would
sit at their ADSR sustain level to the end of the capture. That is the regression, and the late
window is what catches it.

**They stop it differently.** CC123 falls through RELEASE, so 5-30 ms later the chord is still
audible; CC120 zeroes the envelope, so the same window is silent. A build that mapped both to the
same path would pass the paragraph above and fail this one.

The windows are computed from the four constants ``script_panic()`` uses in
``gateware/sim_xls_core.cpp``, the same way check_midi.py does it, so a drift between the two
shows up as a clear failure rather than as nonsense numbers.
"""

import argparse
import sys

import numpy as np

# --- mirrors script_panic() in gateware/sim_xls_core.cpp -------------------------------------
LEAD_MS, HOLD_MS, TAIL_MS = 100, 200, 300
FX_OFF_MS = 1                   # delay before the first CC95 byte
MODES = [123, 120]              # group 0, group 1
RESET_MS = 0.002                # reset_ns in the harness

# Both derived exactly as check_midi.py derives them; see the comments there for why the
# simulated clocks are not the nominal ones.
BYTE_MS = 10 * 1920 * 16 / 1e6
FS_CAPTURE = 12.5e6 / 256

# Measured relative to the marks below. PLAY skips the attack and stops short of the mode
# message. EARLY is the release window -- the default release runs about 60 ms, so 5-30 ms in is
# comfortably inside it. LATE is far enough past both that nothing legitimate is left.
PLAY_FROM, PLAY_TO = 90, HOLD_MS - 5
EARLY_FROM, EARLY_TO = 5, 30
LATE_FROM, LATE_TO = 200, 295


def marks_ms():
    """``(chord start, mode message end)`` per group, in the harness's milliseconds."""
    t = RESET_MS + FX_OFF_MS + 6 * BYTE_MS      # CC95 then CC94, the echo and chorus switch-off
    out = []
    for g in range(2):
        t += LEAD_MS if g == 0 else TAIL_MS
        t += 9 * BYTE_MS                        # three note-ons
        start = t
        t += HOLD_MS + 3 * BYTE_MS              # hold, then the channel mode message
        out.append((start, t))
    return out


def rms(x):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return float("nan")
    # The mean goes first for a reason beyond centring: each voice's SVF leaves a few hundred
    # counts of DC behind once its envelope dies (the >>7 leak cannot shift a value below 128),
    # so "stopped" is a signal that has stopped *moving*, not one that has reached zero.
    return float(np.sqrt(np.mean((x - x.mean()) ** 2)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build/tiliqua/out0_panic.txt")
    ap.add_argument("--quiet-ratio", type=float, default=0.02,
                    help="late window, as a fraction of the chord it followed (default 2%%)")
    ap.add_argument("--release-ratio", type=float, default=0.10,
                    help="CC123's early window, as a fraction of its chord (default 10%%)")
    ap.add_argument("--cut-ratio", type=float, default=0.25,
                    help="CC120's early window, as a fraction of CC123's (default 25%%)")
    args = ap.parse_args()

    try:
        data = np.loadtxt(args.out)
    except OSError as exc:
        sys.exit(f"{exc}\nrun the panic simulation first (see the module docstring)")

    marks = marks_ms()
    need = int((marks[-1][1] + LATE_TO) / 1000 * FS_CAPTURE)
    if data.size < need:
        sys.exit(f"{args.out}: {data.size} samples, need at least {need} "
                 f"-- raise XLS_SIM_MS (1150 is enough)")

    def window(t0_ms, t1_ms):
        return data[int(t0_ms / 1000 * FS_CAPTURE):int(t1_ms / 1000 * FS_CAPTURE)]

    play, early, late = [], [], []
    for start, mode in marks:
        play.append(rms(window(start + PLAY_FROM, start + PLAY_TO)))
        early.append(rms(window(mode + EARLY_FROM, mode + EARLY_TO)))
        late.append(rms(window(mode + LATE_FROM, mode + LATE_TO)))

    print(f"capture          : {data.size} samples at {FS_CAPTURE:.1f} Hz "
          f"({data.size / FS_CAPTURE * 1000:.0f} ms)")
    print()
    print("  cc   ch  chord (ms)         playing    +5-30ms     +200-295ms")
    for g, (start, mode) in enumerate(marks):
        print(f"  {MODES[g]}  {g + 1}   {start:7.1f}-{mode:7.1f}   "
              f"{play[g]:8.1f}   {early[g]:8.1f}     {late[g]:8.1f}")

    # How the sound goes away, in 20 ms slices. Nothing is graded off this -- it is here so that a
    # failure above can be read as a shape rather than as three numbers.
    print()
    print("  decay after the message, rms per 20 ms slice")
    for g, (_, mode) in enumerate(marks):
        slices = [rms(window(mode + k * 20, mode + k * 20 + 20)) for k in range(10)]
        print(f"    CC{MODES[g]}: " + " ".join(f"{v:7.1f}" for v in slices))

    ok = True
    print()
    for g in range(2):
        if play[g] < 100:
            print(f"FAIL: the CC{MODES[g]} chord never sounded (rms {play[g]:.1f})")
            ok = False
        elif late[g] > args.quiet_ratio * play[g]:
            print(f"FAIL: CC{MODES[g]} did not stop the chord -- {LATE_FROM}-{LATE_TO} ms later "
                  f"it is still at rms {late[g]:.1f} against {play[g]:.1f} while playing")
            ok = False

    if early[0] < args.release_ratio * play[0]:
        print(f"FAIL: CC123 cut the chord instead of releasing it -- rms {early[0]:.1f} at "
              f"{EARLY_FROM}-{EARLY_TO} ms, against {play[0]:.1f} while playing")
        ok = False
    if early[1] > args.cut_ratio * early[0]:
        print(f"FAIL: CC120 did not cut -- rms {early[1]:.1f} at {EARLY_FROM}-{EARLY_TO} ms, "
              f"no quieter than CC123's {early[0]:.1f} at the same offset")
        ok = False

    if not ok:
        print("\nFAIL: the channel mode messages do not survive the shell")
        return 1
    print(f"PASS: CC123 releases the chord and CC120 cuts it, both from the TRS jack "
          f"({early[1] / early[0] * 100:.1f}% of CC123's level at +{EARLY_FROM}-{EARLY_TO} ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
