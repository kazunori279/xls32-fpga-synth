# Unit sim for StereoFx: drive it through its real stream handshakes and compare every output
# sample against fx_model, which is a second, independent transcription of the Basys 3 FSM.
#
#   python boards/tiliqua/gateware/test_fx.py
#
# The point of this file is that a sign error, an off-by-one region offset or a mis-ordered
# read/write gets caught in seconds instead of in a 20-minute place-and-route followed by an
# afternoon on the bench. There was no such harness in this repo before M26; the Basys 3 effects
# were only ever verified on hardware.
#
# The echo delay line is PSRAM-backed on the real board. Standing up an emulated HyperRAM here
# would test the SDK's cache and PHY rather than this file, so instead the two `DelayLine`s are
# swapped for SRAM-backed ones with an identical interface -- same tap protocol, same
# wrpointer-minus-delay addressing, same handshake shape. What that does not cover is PSRAM
# latency, which is a throughput question, so `TestBench` also counts the cycles each sample
# takes and the run fails if any sample exceeds the 1,250-cycle budget.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from amaranth.sim import Simulator

import fx
from fx_model import FxModel

SAMPLE_BUDGET = 1250            # 60 MHz sync / 48 kHz
MAX_ECHO_SIM = fx.ECHO_MAX_DELAY
# Full length is free here: `_mem_zeroed` inits to 1 for an SRAM-backed line, so there is no
# zeroing pass to sit through. Keeping it full length means `edly` is never truncated, which is
# the difference between testing the echo and testing a one-sample feedback loop.


def cc_for(rsize, revwet, chdep, echodep, dtime):
    """The CC stream that puts the DUT's sniffer where the model's constructor already is."""
    return (0xB0, 91, rsize << 5, 93, revwet, 94, chdep, 95, echodep, 82, dtime)


def run(n_samples, stimulus, cc=None, rsize=3, revwet=0, chdep=64, echodep=64, dtime=63,
        max_echo=MAX_ECHO_SIM, verbose=True):
    # No assert on `dtime` fitting: since M29 the line is BRAM and shorter than CC82's range, so
    # oversized values are a case to *test*, not to reject. Both sides clamp; that they clamp
    # identically is the point.
    if cc is None:
        cc = cc_for(rsize, revwet, chdep, echodep, dtime)
    dut = fx.StereoFx(psram=False, max_echo=max_echo)
    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)

    got = []
    cycles = []

    async def midi(ctx):
        for b in cc:
            ctx.set(dut.i_midi_bytes.payload, b)
            ctx.set(dut.i_midi_bytes.valid, 1)
            await ctx.tick()
            ctx.set(dut.i_midi_bytes.valid, 0)
            await ctx.tick()

    async def feed(ctx):
        await midi(ctx)
        for s in stimulus:
            for c, v in enumerate((s, 0, s, s)):
                ctx.set(dut.i.payload[c].as_value(), v & 0xFFFF)
            ctx.set(dut.i.valid, 1)
            await ctx.tick().until(dut.i.ready)
            ctx.set(dut.i.valid, 0)
            # Let the FSM get out of IDLE before offering the next sample.
            await ctx.tick().until(~dut.i.ready)

    async def drain(ctx):
        ctx.set(dut.o.ready, 1)
        t = 0
        last = 0
        while len(got) < n_samples:
            await ctx.tick()
            t += 1
            if ctx.get(dut.o.valid):
                l = ctx.get(dut.o.payload[0].as_value().as_signed())
                r = ctx.get(dut.o.payload[1].as_value().as_signed())
                got.append((l, r))
                cycles.append(t - last)
                last = t
                await ctx.tick()          # consume the handshake
                t += 1

    sim.add_testbench(feed, background=True)
    sim.add_testbench(drain)
    sim.run()

    ref = FxModel(rsize=rsize, revwet=revwet, chdep=chdep, echodep=echodep, dtime=dtime,
                  max_echo=max_echo)
    exp = [tuple(ref.step(s)) for s in stimulus[:n_samples]]

    bad = [(i, g, e) for i, (g, e) in enumerate(zip(got, exp)) if g != e]
    worst = max(cycles[1:]) if len(cycles) > 1 else 0
    if verbose:
        print(f"  {len(got)} samples, worst {worst} cycles/sample "
              f"(budget {SAMPLE_BUDGET}), {len(bad)} mismatches")
        for i, g, e in bad[:8]:
            print(f"    [{i}] got {g}  want {e}")
    return bad, worst, got


def check_sniffer():
    """
    Drive the CC sniffer directly and read its five outputs.

    Doing this through the DSP would not work: `rsize` only reaches the output once a comb
    pointer has wrapped, which is 1,215 samples away, and `rsize` is the only knob whose effect
    is invisible before then. Reading the registers is both faster and a stronger check. (608
    samples since M29 halved the tank, but the argument is unchanged.)

    The sequence deliberately includes the running-status quirk -- `ecnt` lands on 1 rather than
    0 after a completed CC -- so `91, 1` following `93, 100` is taken as another controller/value
    pair with no status byte between them. That is how the Basys 3 behaves and how the test
    harness's `set_fx()` output is actually parsed.
    """
    dut = fx.FxControl()
    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    seen = {}

    async def tb(ctx):
        for b in (0xB0, 93, 100, 91, 3 << 5, 94, 20, 95, 0,
                  0x90, 60, 100,            # a note-on must reset the CC state machine
                  82, 7,                    # ... so this pair is data, not a controller
                  0xB0, 82, 7):
            ctx.set(dut.i.payload, b)
            ctx.set(dut.i.valid, 1)
            await ctx.tick()
        ctx.set(dut.i.valid, 0)
        await ctx.tick()
        for k in ("rsize", "revwet", "chdep", "echodep", "dtime"):
            seen[k] = ctx.get(getattr(dut, k))

    sim.add_testbench(tb)
    sim.run()

    want = dict(rsize=3, revwet=100, chdep=20, echodep=0, dtime=7)
    ok = seen == want
    print(f"\nCC sniffer registers\n  got  {seen}\n  want {want}  {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


def main():
    fails = 0

    # Region map sanity, before any simulation: the whole port rests on these numbers.
    print("region map")
    print(f"  comb   {fx.CL}")
    print(f"  allpass{fx.AL}  spread {fx.SPREAD}")
    print(f"  tank   {fx.TANK_WORDS} words/channel  ({(fx.TANK_WORDS + 1023) // 1024} DP16KD)")
    top = fx.ECHO_DTIME_MAX * fx.ECHO_STEP + fx.ECHO_MIN
    print(f"  echo   {fx.ECHO_MIN} .. {top} samples "
          f"({1000 * fx.ECHO_MIN / fx.FS:.1f} .. {1000 * top / fx.FS:.1f} ms), "
          f"CC82 clamped at {fx.ECHO_DTIME_MAX} ({(fx.ECHO_MAX_DELAY + 1023) // 1024} DP16KD/ch)")
    for i in range(fx.NREG):
        assert fx.REGION[i] + fx.DELAYS[i] + fx.SPREAD <= fx.TANK_WORDS, f"region {i} overruns"
    assert fx.CH_BASE + fx.CH_SWEEP >> 3 < fx.CH_WORDS
    # The clamp is what stops CC82 folding round the address mask into a *short* delay.
    assert top <= fx.ECHO_MAX_DELAY, "the longest reachable tap overruns the line"

    # 1. Impulse, everything off but the echo. Catches the tap addressing and the ping-pong.
    print("\nimpulse, echo only (dtime=1, revwet=0, chdep=0)")
    stim = [20000] + [0] * 1200
    bad, _, _ = run(600, stim, chdep=0, echodep=100, dtime=1, revwet=0)
    fails += len(bad) > 0

    # 2. Impulse with the chorus on. Catches the interpolator and the LFO fold.
    print("\nimpulse, chorus only")
    bad, _, _ = run(600, stim, chdep=100, echodep=0, revwet=0)
    fails += len(bad) > 0

    # 3. Impulse into the tank at cathedral, run past the longest region.
    #
    #    Length matters more than it looks. The shortest comb is 608 samples, so for the first
    #    608 samples every tank read returns zero -- which means the damping register, the
    #    feedback multiply, `rvg` and the comb saturation are all untested no matter what the
    #    output looks like. 4,000 samples wraps every comb at least twice and every all-pass
    #    region many times over, so the feedback path is genuinely in the loop.
    n = 4000
    assert n > 2 * (max(fx.DELAYS) + fx.SPREAD), "too short to wrap the longest region twice"
    print(f"\nimpulse, reverb cathedral (rsize=3, revwet=110), {n} samples")
    bad, worst, _ = run(n, [20000] + [0] * n, chdep=0, echodep=0, revwet=110, rsize=3, dtime=1)
    fails += len(bad) > 0
    fails += worst > SAMPLE_BUDGET

    # 4. Everything at once on a loud sine, long enough that the reverb has built up. This is
    #    the one that exercises saturation: 28,000 dry plus echo plus chorus plus a hall tail
    #    clips `sat16` repeatedly, and clipping is where a width or sign error surfaces.
    print("\nsine, all effects on, 4000 samples")
    import math
    sine = [int(28000 * math.sin(2 * math.pi * 440 * k / fx.FS)) for k in range(n)]
    bad, worst, _ = run(n, sine, chdep=100, echodep=100, revwet=90, rsize=1, dtime=2)
    fails += len(bad) > 0
    fails += worst > SAMPLE_BUDGET

    # 5. CC82 past the end of the BRAM line. M29 shortened the echo from 32,768 words in PSRAM to
    #    16,384 in BRAM, which puts dtime 85..127 out of reach. Unclamped, `DelayLine`'s address
    #    mask folds those back to a short delay -- Ivory Orbit's 344 ms would come out as 4 ms,
    #    and it would sound like a broken preset rather than a shortened one. So drive the top of
    #    the CC range and require the delay actually heard to be the clamp, not the fold.
    #
    #    The impulse has to run past the tap for this to mean anything: 1,000 samples would agree
    #    with a folded model just as well, because neither has produced an echo yet.
    print(f"\nimpulse, CC82 = 127 (past the line; clamps to {fx.ECHO_DTIME_MAX})")
    edly = fx.ECHO_DTIME_MAX * fx.ECHO_STEP + fx.ECHO_MIN
    n5 = edly + 500
    bad, _, got = run(n5, [20000] + [0] * n5, chdep=0, echodep=100, revwet=0, dtime=127)
    fails += len(bad) > 0

    #    Agreeing with the model is not enough on its own -- the model could be folding too. So
    #    also require the echo to land near the clamped tap and nowhere near where a fold would
    #    have put it.
    peak = max(range(1, len(got)), key=lambda k: abs(got[k][0]) + abs(got[k][1]))
    print(f"  first echo at sample {peak}, tap is {edly}")
    if not edly <= peak <= edly + 8:
        print(f"  FAIL: expected the echo at ~{edly}, folded would be {fx.ECHO_MIN}")
        fails += 1

    # 5. The CC sniffer, read at its registers rather than through the DSP.
    fails += check_sniffer()

    print("\nFAIL" if fails else "\nPASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
