# M24 — the one MIDI filter the SDK does not ship.
#
# The XLS engine parses MIDI itself, in DSLX, and its parser treats *any* byte >= 0x80 as a new
# running status (core/synth.x:114). That is fine for channel messages and fine for the hand-fed
# byte streams the Basys 3 host transport sends, but a real cable also carries System messages,
# and each one that reaches the engine costs the next two bytes: the engine latches it as a
# status, then consumes two data bytes against a 0xFn status that matches none of its cases.
#
# Two of the three System families already have filters in the SDK -- midi.MidiRTFilter drops
# System Real-Time (0xF8-0xFF) and midi.MidiSysexFilter drops SysEx (0xF0 .. 0xF7). System
# Common does not, because the SDK's own decoder handles it inline instead, in the SKIP-1 /
# SKIP-2 states of MidiDecodeSerial. This is that logic as a standalone stream filter.

from amaranth import *
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out


class SysCommonFilter(wiring.Component):

    """
    Drop System Common messages (0xF1-0xF7) and their data bytes from a MIDI byte stream.

    Expects System Real-Time and SysEx to have been removed upstream, so any 0xFn arriving here
    is System Common. Data-byte counts follow the spec: MTC quarter frame (0xF1) and song select
    (0xF3) carry one, song position pointer (0xF2) carries two, the rest carry none.

    A status byte appearing while data bytes are still expected cancels the skip rather than
    being swallowed by it -- a truncated message should cost its own bytes, not the next one's.
    """

    i: In(stream.Signature(unsigned(8)))
    o: Out(stream.Signature(unsigned(8)))

    def elaborate(self, platform):
        m = Module()

        b         = self.i.payload
        is_status = b[7]
        is_common = Signal()
        m.d.comb += is_common.eq(b[4:8] == 0xF)

        # Data bytes owed by the System Common message currently being dropped.
        n_skip = Signal(2)
        with m.Switch(b[0:4]):
            with m.Case(1, 3):
                m.d.comb += n_skip.eq(1)
            with m.Case(2):
                m.d.comb += n_skip.eq(2)

        skip = Signal(2)
        drop = Signal()
        m.d.comb += drop.eq(is_common | ((skip != 0) & ~is_status))

        m.d.comb += [
            self.o.payload.eq(b),
            self.o.valid.eq(self.i.valid & ~drop),
            self.i.ready.eq(self.o.ready | drop),
        ]

        with m.If(self.i.valid & self.i.ready):
            with m.If(is_common):
                m.d.sync += skip.eq(n_skip)
            with m.Elif(is_status):
                m.d.sync += skip.eq(0)
            with m.Elif(skip != 0):
                m.d.sync += skip.eq(skip - 1)

        return m
