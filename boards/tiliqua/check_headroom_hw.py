#!/usr/bin/env python3
"""Issue #2 on the module: does a loud pulse passage clip on one rail only?

    XLS32_BOARD=tiliqua uv run python boards/tiliqua/check_headroom_hw.py
    uv run python boards/tiliqua/check_headroom_hw.py --self-test    # grading rule, no module

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

That A/B ran at ``CAP_S = 0.40``, which was later tripled -- see ``row``. Which rows clear ``RAIL``
shifts a little with the window, so a run today grades 10 rather than 9 or 11; the signs did not
change, and the sign is the verdict. From flash slot 6 the current build reads PASS, 10 of 10.

A row also has to out-measure its own repeat scatter before its sign is read at all (``grade``,
``MARGIN``), which is what finally stopped this check failing at random on good bitstreams (#42).
``--self-test`` exercises that rule against rows recorded off the module, so the logic can be
changed without booking ten minutes of board time to find out what it did.
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

CAP_S = 1.20            # capture length; see `row` -- 0.4 s was too short to hold the peak still
SETTLE = 0.40           # let the attack finish and the capture pipeline catch up
HOT = 127               # CC7 for the loud pass: whatever the engine does at full tilt
COLD = 40               # CC7 for the reference pass: ~0.31x, far below the clamp
BASE = 40               # lowest note of the stack, then every other semitone (as the sim does)
TOL = 0.06              # below this a row is called flat and carries no verdict either way
RAIL = 0.98             # a row is only graded once its loud pass actually reaches the clamp
REPEATS = 3             # pairs per row; the verdict is their median (see `row`)
MARGIN = 1.0            # and it must also beat its own repeat scatter -- see `grade`


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


def pair(tp, pw, nv):
    """One loud/quiet pair -> the asymmetry of each, and how far it moved between them."""
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


def row(tp, pw, nv):
    """The median of `REPEATS` pairs, and the spread across them.

    One pair is not enough on the rows with few voices. Two notes a tone apart beat at ~11 Hz, and
    both halves of the ratio move with the beat: the peak is an extreme value, so it depends on
    whether the window happened to contain a beat maximum, and the RMS is an average over however
    much of the beat cycle the window covered. At the original `CAP_S = 0.40` that was four beat
    periods, and `CC75 = 100` at 2 voices returned shifts of +0.056, -0.053 and -0.097 on three
    consecutive runs of a *known-good* bitstream -- straddling the threshold and flipping its
    verdict with it.

    The median of three stopped the verdict flapping but did not make it mean anything: the same
    row came back at -0.113 with the three repeats spanning 0.157, a scatter wider than the number
    it was reporting. The fix is the capture, not the statistic. At 1.2 s the window holds thirteen
    beat periods instead of four, and the scatter on every row falls with it.

    The median stays, and so does the spread column, because they are what makes the remaining
    instability visible: a row whose repeats disagree says so in the output rather than deciding on
    whichever run happened to be last. A check that intermittently fails on a good bitstream is one
    people learn to skip, which is worse than not having it.
    """
    rs = [pair(tp, pw, nv) for _ in range(REPEATS)]
    rs = [r for r in rs if r is not None]
    if not rs:
        return None
    ds = sorted(r[2] for r in rs)
    d = ds[len(ds) // 2]
    med = next(r for r in rs if r[2] == d)                  # report the run the verdict came from
    return med[0], med[1], d, med[3], med[4], ds[-1] - ds[0]


def grade(pw, d, hi, lo, sp):
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

    All of which reads the *sign* of ``d``, so the row has to have earned one. ``TOL`` is a fixed
    floor and it is not enough on its own: it says the shift is big in absolute terms, not that it
    is big compared to how much this particular row wanders between repeats. ``CC75 = 100`` at
    2 voices is where this was first caught -- it clears ``TOL`` on a bad draw while its three
    repeats span two or three times the number being reported, and a sign read off that is a coin
    toss. So a row must also beat its own scatter, which needs no per-row constant because each row
    measures its own.

    ``MARGIN = 1.0`` -- the shift at least as large as the range of the repeats it came from.
    Measured on a known-good build, the two populations do separate there and not by accident:

        genuine shape rows   |d| / spread   1.3 to 19    (12 rows)
        the flake            |d| / spread   0.35, 0.53, 0.72 across its recorded appearances

    It is not one bad row, which is the argument against ever fixing this with a constant. On the
    2026-08-21 run ``CC75 = 74`` at 4 voices -- 3.1x and comfortable the day before -- came back at
    0.42x, its spread having gone from 0.026 to 0.165 with nothing changed but the draw. It read
    ``shape`` under the old rule and passed, but its median was +0.069 inside a scatter of 0.165, so
    the sign it passed on was luck; a draw a little the other way is a ``DC <--`` and a red run on a
    good bitstream. Each row's scatter is a property of that run, not of the row, and only the row
    itself can report it.

    A row that fails it is ``marginal``: not evidence of the bug, and not evidence against it
    either, so it is neither failed nor quietly counted as a pass. The alternative -- raising
    ``TOL`` until the flake fits under it -- would have blinded the check to every genuine row
    below the new floor, and the 8-voice row sits at 0.12.

    1.3 is not a comfortable margin, and on a noisier run that row will go marginal. That is the
    direction to be wrong in: the cost is one row of evidence out of a dozen that all have to agree,
    and ``main`` says so and says how many. The cost of being wrong the other way is a red check on
    a good build, which is the thing that gets a check deleted.
    """
    if max(hi, -lo) < RAIL:
        return "no clip"
    if pw == 64:
        return "no DC term"
    if abs(d) <= TOL:
        return "flat"
    if abs(d) < MARGIN * sp:
        return "marginal"
    return "shape" if (d > 0) == (pw > 64) else "DC <--"


def table(tp, title, header, rows):
    print(f"\n{title}")
    print(f"  {header} | asym quiet | asym loud |   shift | spread | peak+ | peak- | verdict")
    out = []
    for label, pw, nv in rows:
        r = row(tp, pw, nv)
        if r is None:
            print(f"  {label} |  -- capture failed --")
            continue
        ac, ah, d, hi, lo, sp = r
        g = grade(pw, d, hi, lo, sp)
        print(f"  {label} |    {ac:+7.4f} |   {ah:+7.4f} | {d:+7.4f} |  {sp:5.3f} | {hi:5.2f} "
              f"| {lo:5.2f} | {g}")
        out.append((label, pw, nv, ac, ah, d, g, sp))
    return out


# Real rows off the module, verdict written down by hand. Every one of these took ten minutes of
# board time to produce and none of it should have to be spent again to check an `if`.
#
# `flake-*` are the three recorded appearances of the row this check kept tripping over -- a
# known-good bitstream, so `DC <--` on any of them is the bug being guarded against. Their `hi`/`lo`
# come from the 2-voice row of the 2026-08-20 baseline run; only the shift and spread differ, which
# is the point of them.
CASES = [
    # pw,   d,      hi,    lo,     spread, expected
    (100, -0.0009, 0.33, -0.52, 0.003, "no clip"),      # 78% duty by polyphony, 2026-08-20
    (100, -0.0013, 0.63, -1.00, 0.038, "flat"),
    (100, +0.2292, 0.87, -1.07, 0.012, "shape"),
    (100, +0.2365, 1.04, -1.11, 0.181, "shape"),        # widest spread that still reads: 1.3x
    (100, +0.1223, 1.06, -1.11, 0.027, "shape"),
    (100, -0.0306, 1.06, -1.15, 0.003, "flat"),
    (64, +0.0001, 0.47, -0.47, 0.005, "no clip"),       # the 50% control
    (64, -0.0211, 1.11, -1.09, 0.087, "no DC term"),
    (5, -0.1129, 1.01, -0.48, 0.028, "shape"),          # by pulse width, four voices
    (19, -0.2424, 1.04, -0.70, 0.013, "shape"),
    (48, -0.1136, 1.10, -1.06, 0.069, "shape"),
    (74, +0.0813, 1.08, -1.09, 0.026, "shape"),         # smallest genuine shift, 3.1x its spread
    (88, +0.1716, 1.07, -1.09, 0.087, "shape"),
    (124, +0.1491, 0.49, -1.01, 0.099, "shape"),
    (74, +0.0690, 1.07, -1.10, 0.165, "marginal"),      # same row, 2026-08-21: 3.1x -> 0.42x
    (100, -0.0530, 0.63, -1.00, 0.153, "flat"),         # flake-1: under TOL as well, 0.35x
    (100, -0.0808, 0.63, -1.00, 0.153, "marginal"),     # flake-2: over TOL, 0.53x -- the FAIL
    (100, -0.1130, 0.63, -1.00, 0.157, "marginal"),     # flake-3: bigger still, 0.72x
    (100, -0.2000, 0.63, -1.00, 0.010, "DC <--"),       # what the bug actually looks like
    (48, +0.2000, 1.10, -1.06, 0.010, "DC <--"),        # and below 50% duty, the other way round
]


def self_test():
    """Check `grade` against recorded rows, without a module.

    Two claims, and the second is the one that matters: the rule still calls every genuine row on a
    known-good build, and it no longer calls the flake. A rule that only did the second could be had
    by returning "marginal" always.
    """
    bad = 0
    for pw, d, hi, lo, sp, want in CASES:
        got = grade(pw, d, hi, lo, sp)
        ratio = abs(d) / sp if sp else float("inf")
        flag = "   " if got == want else "!! "
        bad += got != want
        print(f"  {flag}CC75={pw:3d}  shift {d:+7.4f}  spread {sp:5.3f}  ({ratio:5.1f}x)  "
              f"-> {got}" + ("" if got == want else f", expected {want}"))
    kept = sum(1 for c in CASES if c[5] == "shape")
    print(f"\n{len(CASES)} recorded rows, {bad} disagree; {kept} genuine shape rows still read, "
          "3 of 3 flake draws no longer do")
    return 1 if bad else 0


def main():
    if "--self-test" in sys.argv:
        return self_test()
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
        #
        # Only the control rows that actually *reach* the clamp can say that, though. The claim
        # being checked is that a clipping row with no DC term does not move with level, and a row
        # that never clips is not evidence for or against it -- it is two notes beating inside a
        # 0.4 s window, which is the noisiest thing this script measures (spread 0.10 at 2 voices,
        # against 0.02 once four voices are sounding). Counting those rows made the whole run come
        # back INVALID on a bitstream whose nine clipping rows were unanimous.
        rows = poly + sweep
        graded = [r for r in rows if r[6] in ("shape", "DC <--")]
        bad = [r for r in rows if r[6] == "DC <--"]
        weak = [r for r in rows if r[6] == "marginal"]
        moved = [r for r in ctrl if r[6] != "no clip" and abs(r[5]) > TOL]
        print("\nverdict")
        if moved:
            print(f"  INVALID: {len(moved)} of the 50% control rows moved by more than {TOL}; "
                  "the shift is not measuring the clamp")
            return 1
        print(f"  control holds: every 50% row within {TOL} between quiet and loud")
        # Say what was thrown out and why. A row silently dropped is indistinguishable in the
        # output from a row that was never run, and this check exists precisely because a number
        # nobody could see was deciding the verdict.
        for label, pw, nv, _, _, d, _, sp in weak:
            print(f"  set aside: CC75={pw} at {nv} voices, shift {d:+.4f} against a repeat spread "
                  f"of {sp:.3f} -- too close to its own scatter to carry a sign")
        if not graded:
            print(f"  no row reached the clamp with a readable sign ({len(weak)} marginal) -- "
                  "nothing to grade")
            return 1
        if len(weak) > len(graded):
            # Not a failure of the engine, a failure of the run: most of what clipped came back
            # unreadable. Reporting PASS off the minority that survived would be the same mistake
            # the marginal verdict was added to stop, one level up.
            print(f"  INCONCLUSIVE: {len(weak)} marginal rows against {len(graded)} readable ones; "
                  "the captures were too unstable to grade this build")
            return 1
        if bad:
            for label, pw, nv, _, _, d, _, sp in bad:
                print(f"  CC75={pw} at {nv} voices: shift {d:+.4f} (spread {sp:.3f}), clamped on "
                      "the side the duty offset pushes into")
            print(f"  FAIL: {len(bad)} of {len(graded)} clipping rows still carry the pulse DC")
            return 1
        print(f"  PASS: all {len(graded)} clipping rows clamp on the shape's own side, "
              "not the duty offset's"
              + (f" ({len(weak)} marginal, not counted either way)" if weak else ""))
        return 0
    finally:
        # `hush` stops the notes and leaves every CC where this script put them, and the last row
        # it runs is CC75 = 124 -- a 2 % duty pulse with almost no fundamental. Whatever runs next
        # inherits that: `check_loop.py` measured A440 as 2639.97 Hz, its sixth harmonic, and
        # reported a 3102-cent pitch failure on a board that was working perfectly. Hand the next
        # script something neutral instead.
        hush(tp)
        for ch in range(4):
            patch(tp, ch, 64, 100)
        tp.close()


if __name__ == "__main__":
    sys.exit(main())
