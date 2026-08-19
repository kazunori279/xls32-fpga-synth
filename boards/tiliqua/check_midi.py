#!/usr/bin/env python3
"""M24 exit check: does MIDI arriving at the TRS jack play the right part at the right pitch?

Reads the ``parts`` capture -- four notes, one per MIDI channel, each preceded by its own CC7
volume::

    SIM=1 XLS_SIM_MIDI=parts XLS_SIM_MS=1650 \\
        XLS_SIM_OUT="$PWD/build/tiliqua/out0_parts.txt" bash boards/tiliqua/build.sh
    uv run boards/tiliqua/check_midi.py

and asserts two independent things about it.

**Pitch.** Each segment's peak frequency, as a ratio to segment 0's, must match the
equal-temperament ratio for its note. Ratios rather than hertz, for the same reason
``check_pitch.py`` compares cycles per sample: the harness advances time in whole nanoseconds,
so neither simulated clock is physically exact, and hertz would fold that error in. A ratio is
immune to it. This is what proves the note *number* survived the UART, the three System-message
filters and the byte CDC -- a dropped or corrupted data byte moves the pitch.

**Per-part routing.** Segment amplitude must be strictly decreasing, matching the descending
CC7 values 110 / 80 / 55 / 30. This is the part that four notes alone cannot test:
``core/synth.x:337`` takes the channel nibble's low two bits as the part index, but a part is
polyphonic, so if routing collapsed and all four channels landed on part 0 the notes would still
sound correct one after another. They would not have four different volumes -- with one part,
the last CC7 wins and every segment comes out the same.

Segment boundaries are computed from the constants the harness's ``script_parts()`` uses, then
sanity-checked against the envelope, so a drift between the two shows up as a clear failure
rather than as nonsense numbers. That includes the CC95/CC94 pair the script now sends first:
``out0`` is the wet side of StereoFx and ``echodep`` boots at 64, so until M35 every gap held an
echo of channel 1 -- 327 rms still ringing at A4 800 ms after its note-off -- and the isolation
check below failed on the effects rather than on anything about MIDI.
"""

import argparse
import sys

import numpy as np

# --- mirrors script_parts() in gateware/sim_xls_core.cpp -------------------------------------
LEAD_MS, HOLD_MS, GAP_MS = 100, 250, 150
NOTES = [69, 63, 78, 60]        # A4, D#4, F#5, C4
VOLS  = [110, 80, 55, 30]       # CC7 per channel, strictly descending
RESET_MS = 0.002                # reset_ns in the harness
FX_OFF_MS = 1                   # delay before the first CC95 byte

# The harness derives its bit period from the receiver's divisor rather than from 31250 baud
# (see the comment there), so a byte on the simulated wire lasts 10 * 1920 sync cycles. In the
# harness's nanosecond timebase a sync cycle is 16 ns, not 16.667.
BYTE_MS = 10 * 1920 * 16 / 1e6

# Capture rate, in the same timebase: the codec runs off the audio clock, which that same
# truncation makes 12.5 MHz rather than 12.288, at 256 clocks per frame.
FS_CAPTURE = 12.5e6 / 256

# Skip the ADSR attack and decay before measuring, and stop short of the note-off so the release
# never enters the window. Same reasoning as check_pitch.py's SKIP_FRACTION.
ANALYSE_FROM_MS, ANALYSE_TO_MS = 90, HOLD_MS - 5


def segment_starts_ms():
    """Time at which each channel's note-on finishes transmitting, in the harness's ms."""
    t = RESET_MS + FX_OFF_MS + 6 * BYTE_MS      # CC95 then CC94, the echo and chorus switch-off
    starts = []
    for ch in range(4):
        t += LEAD_MS if ch == 0 else GAP_MS
        t += 6 * BYTE_MS            # CC7 status/number/value, then note-on status/note/velocity
        starts.append(t)
        t += HOLD_MS + 3 * BYTE_MS  # hold, then the note-off
    return starts


def peak_norm_freq(x):
    """Peak frequency of ``x`` in cycles per sample, parabolically interpolated."""
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    n = len(x)
    spec = np.abs(np.fft.rfft(x * np.hanning(n)))
    k = int(spec.argmax())
    if k == 0 or k == len(spec) - 1:
        return k / n
    a, b, c = spec[k - 1], spec[k], spec[k + 1]
    denom = a - 2 * b + c
    delta = 0.0 if denom == 0 else 0.5 * (a - c) / denom
    return (k + delta) / n


def rms(x):
    x = np.asarray(x, dtype=float)
    return float(np.sqrt(np.mean((x - x.mean()) ** 2)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build/tiliqua/out0_parts.txt")
    ap.add_argument("--tol", type=float, default=0.01,
                    help="allowed relative error on each pitch ratio (default 1%%)")
    ap.add_argument("--gap-max", type=float, default=0.05,
                    help="silence between notes, as a fraction of the quieter neighbour")
    args = ap.parse_args()

    try:
        data = np.loadtxt(args.out)
    except OSError as exc:
        sys.exit(f"{exc}\nrun the parts simulation first (see the module docstring)")

    starts = segment_starts_ms()
    need = int((starts[-1] + ANALYSE_TO_MS) / 1000 * FS_CAPTURE)
    if data.size < need:
        sys.exit(f"{args.out}: {data.size} samples, need at least {need} "
                 f"-- raise XLS_SIM_MS (1650 is enough)")

    def window(t0_ms, t1_ms):
        return data[int(t0_ms / 1000 * FS_CAPTURE):int(t1_ms / 1000 * FS_CAPTURE)]

    segs = [window(t + ANALYSE_FROM_MS, t + ANALYSE_TO_MS) for t in starts]
    # The last 35 ms before each note-on. The release runs about 60 ms and the effects are off,
    # so by here the previous note must have reached silence; anything else means the notes
    # overlap and the amplitudes below are not measuring one part each.
    gaps = [window(t - 45, t - 10) for t in starts[1:]]

    freqs = [peak_norm_freq(s) for s in segs]
    levels = [rms(s) for s in segs]
    gap_levels = [rms(g) for g in gaps]

    print(f"capture          : {data.size} samples at {FS_CAPTURE:.1f} Hz "
          f"({data.size / FS_CAPTURE * 1000:.0f} ms)")
    print()
    print("  ch  note  cc7   window (ms)      peak (cyc/sample)   ratio    expected     err"
          "     rms")
    ok = True
    for i, (t, f, lv) in enumerate(zip(starts, freqs, levels)):
        ratio = f / freqs[0]
        expect = 2 ** ((NOTES[i] - NOTES[0]) / 12)
        err = abs(ratio / expect - 1.0)
        flag = "" if err <= args.tol else "  <-- FAIL"
        ok &= err <= args.tol
        print(f"  {i + 1}   {NOTES[i]:3d}  {VOLS[i]:3d}   "
              f"{t + ANALYSE_FROM_MS:7.1f}-{t + ANALYSE_TO_MS:7.1f}   "
              f"{f:.6f}          {ratio:7.4f}  {expect:7.4f}  {err * 100:6.3f}%  "
              f"{lv:7.1f}{flag}")

    print()
    print(f"silence before   : {', '.join(f'{g:.1f}' for g in gap_levels)}  "
          f"(must be under {args.gap_max * 100:g}% of the quieter neighbour)")

    # Segment isolation: if the windows had drifted off the notes, or a release tail were still
    # ringing, the amplitude comparison below would be measuring the wrong thing.
    for i, g in enumerate(gap_levels):
        quietest = min(levels[i], levels[i + 1])
        if g > args.gap_max * quietest:
            print(f"FAIL: the 35 ms before note {i + 2} has rms {g:.1f}, not silence against "
                  f"segment rms {quietest:.1f} -- the notes overlap")
            ok = False

    # The routing test proper.
    print(f"segment rms      : {', '.join(f'{v:.1f}' for v in levels)}  "
          f"(must be strictly decreasing, as CC7 is)")
    for i in range(3):
        if levels[i + 1] >= levels[i]:
            print(f"FAIL: segment {i + 2} is not quieter than segment {i + 1} -- "
                  f"CC7 did not reach separate parts")
            ok = False
    if max(levels) < 100:
        print(f"FAIL: nothing sounded (loudest segment rms {max(levels):.1f})")
        ok = False

    print()
    if not ok:
        print("FAIL: MIDI in does not drive the four parts correctly")
        return 1
    print("PASS: each MIDI channel plays its own note on its own part")
    return 0


if __name__ == "__main__":
    sys.exit(main())
