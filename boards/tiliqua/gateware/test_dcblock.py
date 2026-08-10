# Unit sim for TeeDcBlock: does it actually remove DC, and does it leave the music alone.
#
#   python boards/tiliqua/gateware/test_dcblock.py
#
# Half of it is not breaking what it filters. A DC blocker is a high-pass, and a high-pass set
# too high eats the bass of a synthesiser -- which nothing downstream would report, because the
# graded suite scores this path on RMS and spectral peaks that a few dB of tilt at 40 Hz does not
# move. `test_passband` pins the corner by measurement instead.
#
# The other half is that a subtraction which is *nearly* right looks exactly like one that is
# right. `test_settles` therefore checks the residual and not the trend, and `test_no_wrap` exists
# because the failure mode of getting the arithmetic wrong here is not a bit of leftover DC, it is
# a full-scale sign flip on every loud edge.
#
# `test_extra_bits` is a negative result kept on purpose: the plausible story about `extra_bits`
# is wrong, and the test says so rather than leaving the next reader to re-derive it.

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from amaranth.sim import Simulator

from dc_block import DEFAULT_EXTRA_BITS, DEFAULT_SHIFT, TeeDcBlock

FS = 48000
ASQ_MAX = 2 ** 15                     # ASQ is Q1.15: 1.0 is 32768 in raw counts


def run(samples, shift=DEFAULT_SHIFT, extra_bits=None):
    """Push `samples` (floats in [-1, 1)) through channel 0 and return the output as floats.

    Channel 1 is driven with the negated signal, so every test also checks that the two channels
    have genuinely separate state -- a shared accumulator would show up as channel 1 mirroring
    channel 0's *error* rather than its input.
    """
    kw = {"shift": shift}
    if extra_bits is not None:
        kw["extra_bits"] = extra_bits
    dut = TeeDcBlock(**kw)
    out0, out1 = [], []

    async def tb(ctx):
        ctx.set(dut.en, 1)
        for x in samples:
            raw = max(-ASQ_MAX, min(ASQ_MAX - 1, int(round(x * ASQ_MAX))))
            ctx.set(dut.i[0].as_value(), raw)
            ctx.set(dut.i[1].as_value(), -raw)
            await ctx.delay(1e-9)                       # let the comb path settle
            out0.append(ctx.get(dut.o[0].as_value()) / ASQ_MAX)
            out1.append(ctx.get(dut.o[1].as_value()) / ASQ_MAX)
            await ctx.tick()

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()

    # Separate state, checked on every run rather than in a test of its own.
    for n, (a, b) in enumerate(zip(out0, out1)):
        assert abs(a + b) <= 2 / ASQ_MAX, f"channels are coupled at sample {n}: {a} vs {b}"
    return out0


def test_settles():
    """A DC step must decay to nothing, not to nearly nothing."""
    n = 20 * FS // 1000 * 50                            # 1 s
    out = run([0.5] * n)
    # One time constant is 2**shift samples = 21.3 ms at shift=10. After 1 s the step should be
    # gone into the noise floor, and "noise floor" here means the quantiser: 1 LSB.
    tail = out[-FS // 10:]
    worst = max(abs(v) for v in tail)
    assert worst <= 2 / ASQ_MAX, f"residual DC after 1 s is {worst * ASQ_MAX:.1f} LSB, want <= 2"
    # And it has to have started from the full step: a filter that outputs zero always would
    # pass the line above.
    assert out[0] >= 0.49, f"step was not passed through at t=0: {out[0]}"
    print(f"  settles:      0.5 step -> {worst * ASQ_MAX:.1f} LSB after 1 s             PASS")


def test_passband():
    """The corner is where it was designed to be, and the audio band is not being eaten."""
    # Magnitude, not the in-phase projection: a one-pole is 45 degrees out at its own corner, so
    # correlating against sin alone reads -6 dB there and would move the pass/fail line by 3 dB.
    # Both quadratures, over a whole number of cycles to keep leakage out of it.
    def gain(f, amp=0.4, settle=20000):
        period = FS / f
        cycles = max(4, int(math.ceil(0.5 * FS / period)))
        n = settle + int(round(cycles * period))
        sig = [amp * math.sin(2 * math.pi * f * k / FS) for k in range(n)]
        out = run(sig)
        win = range(settle, n)
        a = sum(out[k] * math.sin(2 * math.pi * f * k / FS) for k in win)
        b = sum(out[k] * math.cos(2 * math.pi * f * k / FS) for k in win)
        return 2 * math.hypot(a, b) / len(win) / amp

    g_dc_corner = gain(7.5)
    g_low = gain(40.0)
    g_mid = gain(440.0)

    # -3 dB at the design corner, within the slop a 1-LSB quantiser and a 3 s window leave.
    db = 20 * math.log10(g_dc_corner)
    assert -5.0 <= db <= -1.5, f"corner gain is {db:.2f} dB at 7.5 Hz, want about -3"
    # 40 Hz is E1, below the bottom of a five-octave keyboard. Losing more than a dB there
    # would be audible on bass patches.
    db40 = 20 * math.log10(g_low)
    assert db40 >= -1.0, f"40 Hz is down {-db40:.2f} dB, want <= 1.0"
    # And the rest of the band is untouched.
    db440 = 20 * math.log10(g_mid)
    assert abs(db440) <= 0.1, f"440 Hz is off by {db440:.3f} dB, want flat"
    print(f"  passband:     7.5 Hz {db:+.2f} dB · 40 Hz {db40:+.2f} dB · 440 Hz "
          f"{db440:+.2f} dB   PASS")


def test_extra_bits():
    """What the accumulator's spare fractional bits actually buy.

    The intuitive answer -- that too few of them park the filter in a dead band and leave a
    permanent offset -- is wrong, and this test says so out loud so nobody re-derives it. At the
    SDK default of 10 the residual is the same 1 LSB as at 16. What changes is the noise the
    filter injects while tracking, which is why `DEFAULT_EXTRA_BITS` is 16 anyway.
    """
    # No dead band: the DC residual is the same either way, and small enough to be the quantiser.
    coarse = max(abs(v) for v in run([0.5] * (2 * FS), extra_bits=DEFAULT_SHIFT)[-FS // 10:])
    fine = max(abs(v) for v in run([0.5] * (2 * FS))[-FS // 10:])
    assert coarse <= 2 / ASQ_MAX and fine <= 2 / ASQ_MAX, \
        f"DC residual: {coarse * ASQ_MAX:.1f} LSB coarse, {fine * ASQ_MAX:.1f} fine"
    # Including for an offset that starts inside one LSB of the increment, which is the case a
    # real dead band could not remove.
    tiny = max(abs(v) for v in run([4 / ASQ_MAX] * (2 * FS))[-FS // 10:])
    assert tiny <= 2 / ASQ_MAX, f"a 4 LSB offset left {tiny * ASQ_MAX:.1f} LSB behind"

    # The real difference: quantisation noise on a quiet signal, measured as what is left of the
    # output after the fundamental is subtracted out.
    def noise(extra_bits):
        f, amp, settle = 220.0, 0.01, 20000
        n = settle + 4800
        out = run([0.02 + amp * math.sin(2 * math.pi * f * k / FS) for k in range(n)],
                  extra_bits=extra_bits)
        win = range(settle, n)
        a = 2 / len(win) * sum(out[k] * math.sin(2 * math.pi * f * k / FS) for k in win)
        b = 2 / len(win) * sum(out[k] * math.cos(2 * math.pi * f * k / FS) for k in win)
        res = [out[k] - a * math.sin(2 * math.pi * f * k / FS)
                      - b * math.cos(2 * math.pi * f * k / FS) for k in win]
        return math.sqrt(sum(r * r for r in res) / len(res)) * ASQ_MAX, math.hypot(a, b) / amp

    n_coarse, g_coarse = noise(DEFAULT_SHIFT)
    n_fine, g_fine = noise(DEFAULT_EXTRA_BITS)
    assert n_fine < n_coarse, \
        f"extra_bits buys nothing: {n_coarse:.3f} LSB at {DEFAULT_SHIFT}, {n_fine:.3f} at " \
        f"{DEFAULT_EXTRA_BITS} -- if the SDK's OnePole changed, revisit DEFAULT_EXTRA_BITS"
    assert abs(g_fine - g_coarse) < 1e-3, "passband gain should not depend on extra_bits"
    print(f"  extra_bits:   residual 1 LSB either way; noise {n_coarse:.3f} -> {n_fine:.3f} LSB "
          f"  PASS")


def test_no_wrap():
    """A full-scale square on top of DC must saturate, not wrap.

    Unsaturated, `x - lowpass(x)` overflows on every edge of a loud offset waveform and the
    16-bit result flips sign -- which is a click per sample, an infinitely worse artefact than
    the offset it replaced.
    """
    # 100 Hz square between 0.0 and 0.98: mean +0.49, peaks that leave no room for the subtraction.
    period = FS // 100
    sig = [(0.98 if (k % period) < period // 2 else 0.0) for k in range(FS)]
    out = run(sig)
    tail = out[FS // 2:]
    # Saturation clamps; wrapping would put samples of the opposite sign next to the peaks.
    for k in range(1, len(tail)):
        assert abs(tail[k] - tail[k - 1]) < 1.6, \
            f"sample {k} jumped by {abs(tail[k] - tail[k-1]):.3f} -- looks like a wrap"
    assert max(tail) <= 1.0 and min(tail) >= -1.0
    # The point of the exercise: the mean is gone.
    mean = sum(tail) / len(tail)
    assert abs(mean) <= 2 / ASQ_MAX, f"mean of a +0.49 square is still {mean:.5f}"
    print(f"  no_wrap:      +0.49 square -> mean {mean * ASQ_MAX:+.1f} LSB, no sign flips  PASS")


def test_en_gates():
    """With `en` low the filter must not integrate, so a stalled stream cannot skew the corner."""
    dut = TeeDcBlock()
    seen = []

    async def tb(ctx):
        ctx.set(dut.i[0].as_value(), int(0.5 * ASQ_MAX))
        ctx.set(dut.en, 0)
        for _ in range(5000):                           # far longer than a time constant
            await ctx.tick()
        await ctx.delay(1e-9)
        seen.append(ctx.get(dut.o[0].as_value()) / ASQ_MAX)

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()
    assert seen[0] >= 0.49, f"filter integrated with en low: output fell to {seen[0]}"
    print("  en_gates:     5000 idle cycles change nothing                    PASS")


if __name__ == "__main__":
    test_settles()
    test_passband()
    test_extra_bits()
    test_no_wrap()
    test_en_gates()
