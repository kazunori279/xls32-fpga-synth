# Unit sim for LedComet: does the head advance, and does each LED keep tracking its own voice?
#
#   python boards/tiliqua/gateware/test_led.py
#
# The comet is a light show and nothing downstream reads it, so what is worth testing is not how
# bright any particular LED is -- it is the binding. `bind` is a rotation rather than an indexed
# file (see led.py), which is only equivalent because the engine emits exactly one tuple per voice
# slot forever. These tests are what that equivalence looks like from outside: a voice struck at
# slot 7 must still be the voice slot 7 updates a hundred scans later, and no other slot may move
# the LED it took.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from amaranth.sim import Simulator

from led import N_LED, N_VOICE, LedComet


def viz(env, is_new=0, last=0):
    """Pack one tuple the way synth.x:403 does."""
    return (env & 0xffff) | (is_new << 16) | (last << 17)


def bench(body):
    dut = LedComet()

    async def tb(ctx):
        async def scan(tuples):
            """One full 32-slot pass. Returns the LED brightnesses after it."""
            assert len(tuples) == N_VOICE
            for i, t in enumerate(tuples):
                ctx.set(dut.i_viz, t | ((i == N_VOICE - 1) << 17))
                ctx.set(dut.i_strobe, 1)
                await ctx.tick()
            ctx.set(dut.i_strobe, 0)
            await ctx.tick()
            return [ctx.get(dut.o_led[n]) for n in range(N_LED)]

        await body(ctx, scan)

    sim = Simulator(dut)
    sim.add_clock(1 / 12.288e6)
    sim.add_testbench(tb)
    sim.run()


def silence():
    return [viz(0) for _ in range(N_VOICE)]


def test_head_advances():
    """Each freshly struck voice takes the next LED, and it wraps after eight."""
    seen = []

    async def body(ctx, scan):
        await scan(silence())
        # Ten voices struck one scan after another, each at slot 0. Ten strikes on eight LEDs, so
        # the head must wrap: 1,2,...,7,0,1,2.
        #
        # Each strike carries its own envelope, and the LED is found by that level rather than by
        # "the one that is lit". Earlier LEDs stay lit -- that is the trail, and the whole point --
        # so looking for the first non-zero would report LED 1 ten times over.
        for k in range(10):
            t = silence()
            t[0] = viz((k + 1) << 9, is_new=1)
            leds = await scan(t)
            seen.append(next((n for n in range(N_LED) if leds[n] == k + 1), None))

    bench(body)
    want = [(k + 1) % N_LED for k in range(10)]
    assert seen == want, f"\n  head went {seen}\n  want      {want}"
    print(f"  head:    advances per strike and wraps at {N_LED}          PASS")


def test_binding_persists():
    """A voice keeps its LED across scans, and its neighbours cannot move it."""
    rows = []

    async def body(ctx, scan):
        await scan(silence())
        # Strike one voice at slot 7. It should take LED 1 (the head starts at 0 and advances).
        t = silence()
        t[7] = viz(0xFF00, is_new=1)
        leds = await scan(t)
        lit = [n for n in range(N_LED) if leds[n]]
        assert lit == [1], f"expected only LED 1 lit after the strike, got {lit} ({leds})"
        rows.append(("struck", leds[1]))

        # Now let it decay for three scans while *every other slot* reports zero. If the ring had
        # drifted by even one slot, slot 7's envelope would land somewhere else and LED 1 would be
        # overwritten with a neighbour's zero.
        for env in (0xC000, 0x8000, 0x2000):
            t = silence()
            t[7] = viz(env)
            leds = await scan(t)
            rows.append((f"env={env:#06x}", leds[1]))
            others = [n for n in range(N_LED) if n != 1 and leds[n]]
            assert not others, f"env={env:#06x}: LEDs {others} lit but nothing struck them"

    bench(body)
    got = [v for _, v in rows]
    want = [0xFF00 >> 9, 0xC000 >> 9, 0x8000 >> 9, 0x2000 >> 9]
    assert got == want, f"\n  got  {rows}\n  want {want}"
    print("  binding: one voice holds one LED and decays on it       PASS")


def test_no_drift():
    """A hundred scans of traffic, then the same voice must still own the same LED."""

    async def body(ctx, scan):
        t = silence()
        t[3] = viz(0x8000, is_new=1)
        await scan(t)                            # slot 3 takes LED 1
        for k in range(100):                     # noise on every *other* slot
            t = silence()
            for i in range(N_VOICE):
                if i != 3:
                    t[i] = viz(0x1000 + k)
            await scan(t)
        t = silence()
        t[3] = viz(0xFE00)
        leds = await scan(t)
        assert leds[1] == 0xFE00 >> 9, (
            f"after 100 scans slot 3 wrote {leds} -- the ring drifted off LED 1")

    bench(body)
    print("  drift:   stable over 100 scans of interleaved traffic   PASS")


def test_unstruck_voices_write_nowhere():
    """LED 0 must survive a scan of silent voices -- the reason `bind` has an UNBOUND state.

    24 of the 32 voices are still unstruck once the head has been round once. If those pointed at
    LED 0 the way the Basys 3 initialises them to, each would write its silent envelope there every
    scan and LED 0 would be held dark for good, taking an eighth of the comet with it.
    """

    async def body(ctx, scan):
        # Eight strikes to walk the head all the way round to LED 0, each on its own slot so no
        # LED is handed on to a second voice.
        for k in range(8):
            t = silence()
            t[k] = viz(0xFE00, is_new=1)
            leds = await scan(t)
        assert leds[0] == 0xFE00 >> 9, f"the head never reached LED 0: {leds}"

        # One quiet scan. Slots 0-7 report a decayed envelope; slots 8-31 have never been struck
        # and report silence. Every LED should show the decay, LED 0 included.
        t = silence()
        for k in range(8):
            t[k] = viz(0x4000)
        leds = await scan(t)
        want = [0x4000 >> 9] * 8
        assert leds == want, f"\n  got  {leds}\n  want {want}  (LED 0 clobbered by silent voices?)"

    bench(body)
    print("  unbound: silent voices cannot hold LED 0 dark           PASS")


if __name__ == "__main__":
    test_head_advances()
    test_binding_persists()
    test_no_drift()
    test_unstruck_voices_write_nowhere()
