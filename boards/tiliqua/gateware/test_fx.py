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
    assert dtime * fx.ECHO_STEP + fx.ECHO_MIN < max_echo, "edly would truncate"
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
    return bad, worst


def check_sniffer():
    """
    Drive the CC sniffer directly and read its five outputs.

    Doing this through the DSP would not work: `rsize` only reaches the output once a comb
    pointer has wrapped, which is 1,215 samples away, and `rsize` is the only knob whose effect
    is invisible before then. Reading the registers is both faster and a stronger check.

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
    print(f"  echo   {fx.ECHO_MIN} .. {127 * fx.ECHO_STEP + fx.ECHO_MIN} samples "
          f"({1000 * fx.ECHO_MIN / fx.FS:.1f} .. "
          f"{1000 * (127 * fx.ECHO_STEP + fx.ECHO_MIN) / fx.FS:.1f} ms)")
    for i in range(fx.NREG):
        assert fx.REGION[i] + fx.DELAYS[i] + fx.SPREAD <= fx.TANK_WORDS, f"region {i} overruns"
    assert fx.CH_BASE + fx.CH_SWEEP >> 3 < fx.CH_WORDS

    # 1. Impulse, everything off but the echo. Catches the tap addressing and the ping-pong.
    print("\nimpulse, echo only (dtime=1, revwet=0, chdep=0)")
    stim = [20000] + [0] * 1200
    bad, _ = run(600, stim, chdep=0, echodep=100, dtime=1, revwet=0)
    fails += len(bad) > 0

    # 2. Impulse with the chorus on. Catches the interpolator and the LFO fold.
    print("\nimpulse, chorus only")
    bad, _ = run(600, stim, chdep=100, echodep=0, revwet=0)
    fails += len(bad) > 0

    # 3. Impulse into the tank at cathedral, run past the longest region.
    #
    #    Length matters more than it looks. The shortest comb is 1,215 samples, so for the first
    #    1,215 samples every tank read returns zero -- which means the damping register, the
    #    feedback multiply, `rvg` and the comb saturation are all untested no matter what the
    #    output looks like. 4,000 samples wraps every comb at least twice and every all-pass
    #    region many times over, so the feedback path is genuinely in the loop.
    n = 4000
    assert n > 2 * (max(fx.DELAYS) + fx.SPREAD), "too short to wrap the longest region twice"
    print(f"\nimpulse, reverb cathedral (rsize=3, revwet=110), {n} samples")
    bad, worst = run(n, [20000] + [0] * n, chdep=0, echodep=0, revwet=110, rsize=3, dtime=1)
    fails += len(bad) > 0
    fails += worst > SAMPLE_BUDGET

    # 4. Everything at once on a loud sine, long enough that the reverb has built up. This is
    #    the one that exercises saturation: 28,000 dry plus echo plus chorus plus a hall tail
    #    clips `sat16` repeatedly, and clipping is where a width or sign error surfaces.
    print("\nsine, all effects on, 4000 samples")
    import math
    sine = [int(28000 * math.sin(2 * math.pi * 440 * k / fx.FS)) for k in range(n)]
    bad, worst = run(n, sine, chdep=100, echodep=100, revwet=90, rsize=1, dtime=2)
    fails += len(bad) > 0
    fails += worst > SAMPLE_BUDGET

    # 5. The CC sniffer, read at its registers rather than through the DSP.
    fails += check_sniffer()

    print("\nFAIL" if fails else "\nPASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
