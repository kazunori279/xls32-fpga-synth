# Unit sim for CvIn: sweep in0 across five octaves and check what the *engine* would play.
#
#   python boards/tiliqua/gateware/test_cvin.py
#
# This is M28's exit criterion run in a simulator. check_cv.py measures the same thing on hardware
# with an FFT, but hardware cannot separate a converter that computes the wrong number from a
# converter that computes the right number into a miscalibrated ADC. This can: it feeds exact
# counts in and reads exact MIDI out, so anything it finds is arithmetic and nothing else.
#
# The pitch check is deliberately end-to-end rather than per-stage. It does not assert that the
# note number is N or that the bend is B -- those are an implementation detail of how CvIn splits
# a pitch in two. It reconstructs the pitch the core would sound, `note + 12*log2(1 + pmod/4096)`
# with the core's own formula from synth.x:202, and compares that against the volts that went in.
# Splitting the pitch differently is free; landing on a different pitch is the bug.

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from amaranth.sim import Simulator

from cvin import AVG_LOG2, BASE_NOTE, CC_A, CC_RAMP, CHAN, RAMP_STEP, CvIn, CvTestRamp

COUNTS_PER_SEMI = 4000 / 12
TOL_CENTS = 2.0                 # "within a few cents", with the hardware's share still to come
SETTLE = 2 * 2**AVG_LOG2 + 32   # one box window to flush the old level, one to average the new


def parse(stream):
    """MIDI bytes -> a list of (status, d1, d2). Running status is never emitted here."""
    out, i = [], 0
    while i + 3 <= len(stream):
        st, d1, d2 = stream[i:i + 3]
        assert st & 0x80, f"expected a status byte at {i}, got {st:#04x}"
        assert st & 0x0f == CHAN, f"wrong channel at {i}: {st:#04x}"
        assert not (d1 & 0x80) and not (d2 & 0x80), f"data byte with bit 7 set at {i}"
        out.append((st & 0xf0, d1, d2))
        i += 3
    assert i == len(stream), f"trailing {len(stream) - i} byte(s): a message was cut short"
    return out


class Player:

    """The engine's view of the stream: what is sounding, and at what bend.

    State has to carry across batches. CvIn only speaks when something changes, so a window in
    which the pitch moved by less than a semitone contains a bend and no note-on at all -- and a
    decoder that starts from silence every time would read that as nothing playing.
    """

    def __init__(self):
        self.note, self.bend = None, 8192

    def apply(self, stream):
        for st, d1, d2 in parse(stream):
            if st == 0x90:
                self.note = d1
            elif st == 0x80:
                self.note = None
            elif st == 0xE0:
                self.bend = (d2 << 7) | d1
        return self.note, self.bend


def sounded_semitones(note, bend):
    """What the engine plays, in semitones above BASE_NOTE, using synth.x's own pitch maths."""
    pmod = (bend - 8192) >> 4                       # synth.x:347
    assert -2047 <= pmod <= 2047, f"bend {pmod} would clamp at synth.x:364"
    return (note - BASE_NOTE) + 12 * math.log2(1 + pmod / 4096)   # synth.x:202


def bench(body):
    """Run `body(ctx, dut, feed)` under the simulator. `feed` sets the jacks and runs N frames,
    returning every MIDI byte emitted while it did."""
    dut = CvIn()

    async def tb(ctx):
        ctx.set(dut.o_midi.ready, 1)
        seen = []

        async def step(strobe):
            """One cycle. `ready` is tied high, so every cycle `valid` holds is a transfer."""
            ctx.set(dut.i_strobe, strobe)
            if ctx.get(dut.o_midi.valid):
                seen.append(ctx.get(dut.o_midi.payload))
            await ctx.tick()

        async def feed(cv=(0, 0, 0, 0), jack=0b0001, frames=SETTLE):
            seen.clear()
            ctx.set(dut.jack, jack)
            for c, v in enumerate(cv):
                ctx.set(dut.i_cv[c].as_value(), v & 0xffff)
            for _ in range(frames):
                await step(1)
                for _ in range(4):      # slack for the emitter to drain its 3-byte messages
                    await step(0)
            # Run on until the emitter has been quiet for a while. Cutting the capture on a frame
            # boundary would split a 3-byte message across two `feed` calls, and note-off/bend/
            # note-on go out back to back with a single idle cycle between them.
            idle = 0
            while idle < 8:
                idle = 0 if ctx.get(dut.o_midi.valid) else idle + 1
                await step(0)
            return list(seen)

        await body(ctx, dut, feed)

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()


def test_pitch():
    """1 V/oct tracking over the five octaves M28 has to hold."""
    rows, worst = [], 0.0

    async def body(ctx, dut, feed):
        nonlocal worst
        p = Player()
        # 61 steps over 5 V, coprime with the 60 semitones in it, so the residual sweeps the whole
        # +-0.5 semitone the bend has to cover. A round 0.25 V grid lands on exact semitones every
        # time and never exercises the bend at all -- it passes at 0.00 cents while saying nothing.
        for i in range(62):
            counts = round(i * 20000 / 61)
            note, bend = p.apply(await feed(cv=(counts, 0, 0, 0)))
            assert note is not None, f"{counts / 4000:.2f} V: no note sounding"
            err = (sounded_semitones(note, bend) - counts / COUNTS_PER_SEMI) * 100
            worst = max(worst, abs(err))
            rows.append((counts / 4000, note, bend, err))

    bench(body)
    print(f"  {'volts':>6s} {'note':>5s} {'bend':>6s} {'cents':>7s}")
    for volts, note, bend, err in rows:
        print(f"  {volts:6.2f} {note:5d} {bend:6d} {err:+7.2f}")
    assert worst < TOL_CENTS, f"tracking error {worst:.2f} cents exceeds {TOL_CENTS}"
    print(f"\n  pitch:   worst {worst:.2f} of {TOL_CENTS} cents over 60 semitones   PASS")


def test_jacks():
    """Jack detect and the gate. Both defaults exist to stop the board misbehaving unpatched."""

    async def body(ctx, dut, feed):
        p = Player()
        one_volt = 4000

        # Nothing patched: silence. An unpatched in0 reads the converter's own DC offset, and
        # without this check the board would drone from power-up.
        assert await feed(cv=(-400, 0, 0, 0), jack=0) == [], "emitted MIDI with no jack patched"

        # in0 only: the gate is held on, which is what lets check_cv.py sweep with one cable.
        note, _ = p.apply(await feed(cv=(one_volt, 0, 0, 0), jack=0b0001))
        assert note == BASE_NOTE + 12, f"pitch-only patch: expected a drone, got note {note}"

        # Gate jack patched and low: silence, even though in0 still has a pitch on it.
        note, _ = p.apply(await feed(cv=(one_volt, 0, 0, 0), jack=0b0011))
        assert note is None, f"gate low but note {note} sounding"

        # ...and high: it plays.
        note, _ = p.apply(await feed(cv=(one_volt, 5 * 4000, 0, 0), jack=0b0011))
        assert note == BASE_NOTE + 12, f"gate high but note is {note}"

        # Schmitt: 0.75 V is between the two thresholds, so a gate already high stays high.
        note, _ = p.apply(await feed(cv=(one_volt, 3000, 0, 0), jack=0b0011))
        assert note is not None, "gate fell inside the hysteresis band"
        # 0.25 V is below the lower threshold, so it releases.
        note, _ = p.apply(await feed(cv=(one_volt, 1000, 0, 0), jack=0b0011))
        assert note is None, f"gate held at 0.25 V, note {note} still sounding"

        # in2 unpatched reads ~0, and CC74 = 0 is a closed filter -- so it must stay quiet.
        ccs = [m for m in parse(await feed(cv=(one_volt, 0, 0, 0), jack=0b0001)) if m[0] == 0xB0]
        assert ccs == [], f"emitted {ccs} for an unpatched in2"

        # Patched, it tracks: 4 V of 8.192 V full scale is 62 of 127. A step arrives as a ramp --
        # the box window straddles it for one period -- so what matters is where it lands.
        ccs = [m for m in parse(await feed(cv=(one_volt, 0, 16000, 0), jack=0b0101)) if m[0] == 0xB0]
        assert ccs and ccs[-1] == (0xB0, CC_A, 62), f"expected CC{CC_A} = 62, got {ccs}"

    bench(body)
    print("  jacks:   detect, gate hysteresis, CC gating                PASS")


def test_ramp():
    """CC102 -> out2 level. A two-byte CC decoder with a toggle in it is worth one sim."""
    dut = CvTestRamp()
    seen = []

    async def tb(ctx):
        async def send(*bs):
            ctx.set(dut.i_midi.valid, 1)
            for b in bs:
                ctx.set(dut.i_midi.payload, b)
                await ctx.tick()
            ctx.set(dut.i_midi.valid, 0)
            await ctx.tick()
            return ctx.get(dut.o_level)

        seen.append(("idle", await send()))
        seen.append(("cc102=64", await send(0xB0, CC_RAMP, 64)))
        # CC74 must not move it, and must not leave the decoder off by one either -- the CC102
        # that follows is the real check, not the level right here.
        seen.append(("cc74=0", await send(0xB0, 74, 0)))
        seen.append(("running 127", await send(CC_RAMP, 127)))     # running status, no 0xB0
        seen.append(("cc102=0", await send(0xB0, CC_RAMP, 0)))

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()

    want = [("idle", 0), ("cc102=64", 64 * RAMP_STEP), ("cc74=0", 64 * RAMP_STEP),
            ("running 127", 127 * RAMP_STEP), ("cc102=0", 0)]
    assert seen == want, f"\n  got  {seen}\n  want {want}"
    top = 127 * RAMP_STEP / 4000
    print(f"  ramp:    CC{CC_RAMP} 0-127 spans 0.00-{top:.2f} V on out2       PASS")


if __name__ == "__main__":
    test_pitch()
    test_jacks()
    test_ramp()
