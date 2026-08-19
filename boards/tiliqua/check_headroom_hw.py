#!/usr/bin/env python3
"""Issue #2 on the module: does a loud pulse passage clip on one rail only?

    XLS32_BOARD=tiliqua uv run python boards/tiliqua/check_headroom_hw.py

The hardware rung above ``core/sim/tb_headroom.v``. That one counts clamp hits inside the engine,
where the mix is a plain ``s32`` and the rails are literally +-32767. Getting at the same fact from
a USB capture is harder, because two things stand between the clamp and the host and both of them
destroy the obvious evidence:

* ``gateware/dc_block.py`` high-passes the tee at 7.5 Hz, and ``record_stop`` subtracts the mean
  again on the host side. The DC the sim reads straight off the mix is gone twice over. Reading
  the mean here proves nothing.
* ``xls_core.py``'s FIR resampler sits between the engine and ``dry``. It is linear, but it is not
  flat: a clamped sample is a corner, and the FIR rings on corners. So the flat top that a clipped
  mix has *inside* the engine does not arrive as a flat top -- it arrives as a plateau with
  overshoot on each end. Measured: captures that should be pinned to +-1.0 come back spanning
  2.17, which is 8 % past rail-to-rail, and the fraction of samples sitting on one identical value
  is 0.0 %. Counting flat samples through this path finds nothing, whatever the engine did.

What no linear stage can touch is a *ratio between two captures*. Take the same patch twice, once
at CC7 = 127 and once quiet enough to have headroom, and normalise each capture's two peaks by its
own RMS. Those normalised peaks are level-invariant for anything linear -- the high-pass, the mean
removal, the FIR, the codec's own scaling all cancel, because all of them apply equally to both
captures. The clamp does not cancel: it is the one stage whose behaviour depends on level. And a
clamp that a DC offset has pushed against one rail compresses that peak and not the other, so the
*asymmetry* of the normalised peaks moves between quiet and loud. That shift is the measurement.

The sweep is the one tb_headroom.v uses -- the demo patches' 78 % duty by polyphony, then 50 % as
the control that has no DC term either way, then the duty range the shipped bank actually spans.
CC75 is halved into pwthr, so the CC numbers here are the same ones the testbench sends.

Measured on the module, both bitstreams JTAG-loaded back to back:

    890d4be (before)   FAIL, 10 of 11 clipping rows carry the DC
    3aa0227 (after)    PASS, 0 of 9

and the 50 % control held to within 0.026 on *both*, which is what says the two runs differ by the
adder in ``voice_wave`` and not by anything else that moved between the two builds. The rawest
number in it is 78 % duty at 16 voices: before, the capture peaked at +0.07 against -1.88, the
positive half of the waveform essentially eaten whole by the clamp; after, +1.04 against -1.10.
"""
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "host"))
os.environ.setdefault("XLS32_BOARD", "tiliqua")
import synth as u                                                     # noqa: E402
from transport.base import open_transport                             # noqa: E402

CAP_S = 0.40            # capture length; ~19k samples, enough for a stable peak and RMS
SETTLE = 0.40           # let the attack finish and the capture pipeline catch up
HOT = 127               # CC7 for the loud pass: whatever the engine does at full tilt
COLD = 40               # CC7 for the reference pass: ~0.31x, far below the clamp
BASE = 40               # lowest note of the stack, then every other semitone (as the sim does)
TOL = 0.06              # below this a row is called flat and carries no verdict either way
RAIL = 0.98             # a row is only graded once its loud pass actually reaches the clamp


def patch(tp, ch, pw, vol):
    """Pulse, sustained, no sub-osc and no effects, at the given master volume.

    Every one of these mirrors ``load_all()`` in tb_headroom.v except CC7, which is the variable,
    and CC74, which is opened up because a closed filter would round off the very corners this is
    trying to find. The sub-osc is off because it would put a second waveform in the mix and this
    is a question about the pulse; the effects are off because the echo's ``>> 1`` mixers and the
    reverb tail both blur the peak the ratio is measured from.
    """
    for c, v in [(70, 2 << 4), (75, pw), (7, vol), (74, 127), (71, 0), (73, 0), (78, 0),
                 (80, 0), (20, 0), (21, 0), (22, 127), (23, 0),   # amp A D S R: hold, do not decay
                 (24, 0), (25, 0), (26, 127), (27, 0),            # filter env flat
                 (92, 0), (1, 0), (93, 0), (94, 0), (95, 0)]:     # no trem/vib/chorus/echo
        tp.send_midi(u.cc(c, v, ch))
        time.sleep(0.002)


def hush(tp):
    for ch in range(4):
        tp.send_midi(u.all_sound_off(ch))
    time.sleep(0.25)


def shape(tp, pw, nv, vol):
    """One capture -> its two peaks, each divided by the capture's own RMS.

    Normalising by RMS is what makes two captures at different volumes comparable. It also makes
    the pair immune to everything linear between the clamp and here, which is the whole point --
    see the header.
    """
    hush(tp)
    for ch in range(4):
        patch(tp, ch, pw, vol)
    for n in range(nv):
        tp.send_midi(u.note_on(BASE + 2 * n, 127, n % 4))
        time.sleep(0.002)
    time.sleep(SETTLE)
    tp.record_start()
    time.sleep(CAP_S)
    s = np.asarray(tp.record_stop(), dtype=np.float64) / 32768.0
    hush(tp)
    if len(s) < 1024:
        return None
    s = s - s.mean()
    rms = float(np.sqrt((s * s).mean()))
    if rms < 1e-6:
        return None
    p, n = float(s.max()) / rms, float(-s.min()) / rms
    return p, n, float(s.max()), float(s.min())


def row(tp, pw, nv):
    """Loud against quiet, and how far the peak asymmetry moved between them."""
    cold = shape(tp, pw, nv, COLD)
    hot = shape(tp, pw, nv, HOT)
    if cold is None or hot is None:
        return None
    (pc, nc, _, _), (ph, nh, hi, lo) = cold, hot
    # Asymmetry in [-1, 1]: positive means the top peak is the taller one. Reading the *shift*
    # rather than the absolute value is deliberate -- a DC-carrying pulse is lopsided before it
    # ever clips, and only the clamp makes that lopsidedness depend on how loud it is played.
    ac = (pc - nc) / (pc + nc)
    ah = (ph - nh) / (ph + nh)
    return ac, ah, ah - ac, hi, lo


def grade(pw, d, hi, lo):
    """Which rail did the clamp take, and is that the rail the fixed engine should be taking?

    Once the DC term is gone the pulse is still lopsided -- that is the *shape*, not an offset. At
    duty above 50 % what is left is a short deep trough, so the mix reaches the negative rail
    first; below 50 % it is a short tall spike and the positive rail goes first. Clipping squashes
    whichever peak got there, so the asymmetry moves *away* from it and the shift comes out with
    the sign of ``pw - 64``.

    A pulse still carrying its DC does the opposite on every row. The offset has the sign of
    ``pw - 64`` and it rides the whole mix that way, so the clamp catches the side the offset
    pushed into and the shift lands on the other sign. Fourteen rows, and the fix flips all of
    them: that is a far stronger signature than any single number.
    """
    if max(hi, -lo) < RAIL:
        return "no clip"
    if pw == 64:
        return "no DC term"
    if abs(d) <= TOL:
        return "flat"
    return "shape" if (d > 0) == (pw > 64) else "DC <--"


def table(tp, title, header, rows):
    print(f"\n{title}")
    print(f"  {header} | asym quiet | asym loud |   shift | peak+ | peak- | verdict")
    out = []
    for label, pw, nv in rows:
        r = row(tp, pw, nv)
        if r is None:
            print(f"  {label} |  -- capture failed --")
            continue
        ac, ah, d, hi, lo = r
        g = grade(pw, d, hi, lo)
        print(f"  {label} |    {ac:+7.4f} |   {ah:+7.4f} | {d:+7.4f} | {hi:5.2f} | {lo:5.2f} | {g}")
        out.append((label, pw, nv, ac, ah, d, g))
    return out


def main():
    tp = open_transport().open()
    try:
        hush(tp)
        poly = table(tp, "pulse at 78% duty (CC75 = 100), by polyphony",
                     "voices", [(f"{n:6d}", 100, n) for n in (1, 2, 4, 8, 16, 32)])
        ctrl = table(tp, "the same at 50% duty (CC75 = 64), which has no DC term",
                     "voices", [(f"{n:6d}", 64, n) for n in (1, 2, 4, 8, 16, 32)])
        sweep = table(tp, "four voices, by pulse width (the range the shipped bank spans)",
                      "  CC75", [(f"{pw:6d}", pw, 4) for pw in (5, 19, 48, 64, 74, 88, 117, 124)])

        # Two things have to hold, and they check each other. The 50 % rows are the method's own
        # control: pwthr 128 has no DC term at any volume, so if those rows move, the measurement
        # is picking up something other than the clamp and none of the rest can be trusted.
        rows = poly + sweep
        graded = [r for r in rows if r[6] in ("shape", "DC <--")]
        bad = [r for r in rows if r[6] == "DC <--"]
        moved = [r for r in ctrl if abs(r[5]) > TOL]
        print("\nverdict")
        if moved:
            print(f"  INVALID: {len(moved)} of the 50% control rows moved by more than {TOL}; "
                  "the shift is not measuring the clamp")
            return 1
        print(f"  control holds: every 50% row within {TOL} between quiet and loud")
        if not graded:
            print("  no row reached the clamp -- nothing to grade")
            return 1
        if bad:
            for label, pw, nv, _, _, d, _ in bad:
                print(f"  CC75={pw} at {nv} voices: shift {d:+.4f}, clamped on the side the duty "
                      "offset pushes into")
            print(f"  FAIL: {len(bad)} of {len(graded)} clipping rows still carry the pulse DC")
            return 1
        print(f"  PASS: all {len(graded)} clipping rows clamp on the shape's own side, "
              "not the duty offset's")
        return 0
    finally:
        hush(tp)
        tp.close()


if __name__ == "__main__":
    sys.exit(main())
