# M28 — the LED comet, re-homed from the Basys 3 onto the pmod's eight LEDs.
#
# `boards/basys3/rtl/top.v:127-147` drives 16 board LEDs from the engine's `viz_out` tap: the head
# advances when a voice is freshly struck, and every LED keeps tracking the live envelope of the
# voice that lit it, so the trail fades as notes release. This is the same thing on 8 LEDs.
#
# Two things are simpler here. The pmod takes a signed i8 per LED and does its own PWM
# (periph/eurorack_pmod.py:837), so the 16-way comparator chain at top.v:419-424 is not needed;
# and 8 LEDs instead of 16 makes the cursor 3 bits rather than 4.
#
# One thing is cheaper, and it is the reason this fits in a bitstream that has already spent 89%
# of the die -- see `bind` in elaborate().

from amaranth import *
from amaranth.lib import data, wiring
from amaranth.lib.wiring import In, Out

N_LED = 8
N_VOICE = 32
VIZ_NEW = 16                # synth.x:403 packs {env[15:0], is_new@16, last@17}


class LedComet(wiring.Component):

    """
    The engine's `viz_out` tap -> eight LED brightnesses.

    Single-domain and strobe-driven: instantiate it under `DomainRenamer("audio")` next to the
    engine, where the tuples are, and cross only the eight bytes. `i_strobe` is the tap's `valid`,
    which is every cycle in practice -- `viz_out` is drained with `ready` tied high.

    Brightness is `env[15:9]`, so 0..127, and positive is red on this pmod. Green is the negative
    half and is left unused: one colour is what the Basys 3 comet was and what it reads as.
    """

    i_viz:    In(32)
    i_strobe: In(1)
    o_led:    Out(data.ArrayLayout(signed(8), N_LED))

    def elaborate(self, platform):
        m = Module()

        cursor = Signal(range(N_LED))               # the comet head
        nxt    = Signal(range(N_LED))
        lvl    = Signal(signed(8))
        m.d.comb += [nxt.eq(cursor + 1), lvl.eq(self.i_viz[9:16])]

        # `bind` maps the voice currently on the wire to the LED it lit. The Basys 3 keeps it as a
        # 32-entry file indexed by a scan counter (top.v:135), but that index only ever walks
        # 0,1,...,31,0: `send(tok, viz_out, ...)` at synth.x:404 is *unconditional*, and `vidx` is
        # the same ring counter that raises `last`. So it is a rotation, not a lookup -- and a
        # rotation costs the flip-flops' own enable and their neighbour's Q, with no 32:1 mux and
        # no 32-way write decode. Same trade M26 made for the reverb region pointers.
        #
        # Being a rotation is also why bit 17 (`last`) is not read here. On the Basys 3 it resyncs
        # the scan index each pass; a ring that advances exactly once per tuple has no index to
        # resync, and cannot drift as long as the send stays unconditional. If it ever stops being
        # unconditional, this silently permutes which LED tracks which voice -- so that assumption
        # is named here rather than left to be rediscovered.
        # `bind` carries one value the Basys 3 does not have: UNBOUND, for a voice that has not
        # been struck since reset. The original initialises all 32 slots to LED 0 (top.v:138), and
        # on 16 LEDs that is nearly harmless -- but here 24 of the 32 voices are still pointing at
        # LED 0 once the head has been round once, and every one of them writes its silent envelope
        # there every scan. LED 0 would be held dark forever, and an eighth of the comet with it.
        # A ninth code point and one comparison buys it back.
        UNBOUND = N_LED
        bind = [Signal(range(N_LED + 1), init=UNBOUND, name=f"bind{i}") for i in range(N_VOICE)]
        head = Signal(range(N_LED + 1))
        m.d.comb += head.eq(Mux(self.i_viz[VIZ_NEW], nxt, bind[0]))

        bright = Array([Signal(signed(8), name=f"bright{i}") for i in range(N_LED)])
        with m.If(self.i_strobe):
            m.d.sync += [b.eq(n) for b, n in zip(bind, bind[1:] + [head])]
            # One write covers both cases: on `is_new` the head is already the advanced cursor,
            # which is the LED this voice is being bound to on the same edge.
            with m.If(head != UNBOUND):
                m.d.sync += bright[head].eq(lvl)
            with m.If(self.i_viz[VIZ_NEW]):
                m.d.sync += cursor.eq(nxt)

        for n in range(N_LED):
            m.d.comb += self.o_led[n].eq(bright[n])
        return m
