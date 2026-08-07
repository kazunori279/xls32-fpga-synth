# M28 — merging MIDI sources without corrupting either one.
#
# Until now top.py fed the engine from a two-way mux: USB won, TRS got the cycles USB left over,
# and the comment above it admitted the rest -- "playing both at once interleaves bytes mid-message
# and is not supported". That was survivable while both sources were human-scale and rarely used
# together. M28 adds a third that is neither: CvIn emits on its own schedule, forever, and cannot
# be asked to wait for the keyboard.
#
# What goes wrong without this is specific. core/synth.x:114 latches any byte >= 0x80 as running
# status, and there is exactly one such register for the whole engine. Interleave
#
#     source A:  90 3C 64          source B:  B0 4A 20
#
# a byte apart and the engine sees 90 B0 3C 4A 64 20: one CC to a controller that does not exist,
# with a note number for a value. Nothing detects this; the note simply never sounds.
#
# So the rule is that a source, once granted, keeps the bus until its message is finished. Two
# things make that harder than a mux with a hold:
#
#   * Running status. A keyboard that has already sent 0x90 may send bare note pairs from then on.
#     Those are only meaningful next to that source's own last status, which is precisely what
#     gets destroyed by another source going first. The arbiter therefore remembers a running
#     status *per source* and re-inserts it when that source is granted, which turns every source
#     into one that always sends complete, self-describing messages.
#   * System Real-Time (0xF8-0xFF) is one byte and is legal *inside* another message. It takes no
#     grant and does not disturb running status. It is dropped downstream by MidiRTFilter, but
#     dropping it here instead would mean the arbiter deciding what is worth forwarding, which is
#     not its job.

from amaranth import *
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out


def _data_len(m, byte, out):
    """Data bytes owed after `byte`, per the MIDI spec. 0 for anything with no data or unbounded."""
    with m.Switch(byte[4:8]):
        with m.Case(0x8, 0x9, 0xA, 0xB, 0xE):   # note off/on, aftertouch, CC, pitch bend
            m.d.comb += out.eq(2)
        with m.Case(0xC, 0xD):                  # program change, channel pressure
            m.d.comb += out.eq(1)
        with m.Case(0xF):
            with m.Switch(byte[0:4]):
                with m.Case(1, 3):              # MTC quarter frame, song select
                    m.d.comb += out.eq(1)
                with m.Case(2):                 # song position pointer
                    m.d.comb += out.eq(2)


class MidiArbiter(wiring.Component):

    """
    Merge N MIDI byte streams into one, a whole message at a time.

    Arbitration is round-robin: whoever was granted last goes to the back of the queue. Fixed
    priority would be simpler and is wrong here, because these sources are not equally patient.
    CvIn can have something to say on every one of its ticks, indefinitely; give it -- or the USB
    host running a preset census -- a permanent claim on index 0 and the TRS jack never plays
    another note. Round-robin bounds the wait at one message per other source, so worst-case
    latency for the keyboard is a few microseconds rather than unbounded.

    A status byte arriving where a data byte was expected ends the message early and re-arbitrates
    without consuming it. That is the same choice SysCommonFilter makes: a truncated message should
    cost its own bytes and not the next one's.
    """

    def __init__(self, n_sources):
        self.n = n_sources
        super().__init__({
            "i": In(stream.Signature(unsigned(8))).array(n_sources),
            "o": Out(stream.Signature(unsigned(8))),
        })

    def elaborate(self, platform):
        m = Module()

        pay = Array([s.payload for s in self.i])
        val = Array([s.valid   for s in self.i])
        rdy = Array([s.ready   for s in self.i])
        # Running status per source, 0 for "this source has not sent one yet", which is
        # unambiguous because a status byte always has bit 7 set.
        run = Array([Signal(8, name=f"run{k}") for k in range(self.n)])

        sel  = Signal(range(self.n))
        cur  = Signal(range(self.n))
        last = Signal(range(self.n))
        rem  = Signal(2)

        # Round-robin pick: scan from `last + 1` and keep the first hit, which falls out of writing
        # the chain backwards so the nearest candidate is assigned last and overrides the rest.
        pick = Signal(range(self.n))
        with m.Switch(last):
            for base in range(self.n):
                with m.Case(base):
                    for off in reversed(range(1, self.n + 1)):
                        k = (base + off) % self.n
                        with m.If(val[k]):
                            m.d.comb += pick.eq(k)

        b         = Signal(8)
        is_status = Signal()
        is_sys    = Signal()
        is_rt     = Signal()
        m.d.comb += [
            b.eq(pay[sel]),
            is_status.eq(b[7]),
            is_sys.eq(b[4:8] == 0xF),
            is_rt.eq((b[4:8] == 0xF) & b[3]),
        ]

        len_new, len_run = Signal(2), Signal(2)
        _data_len(m, b, len_new)
        _data_len(m, run[sel], len_run)

        with m.FSM():
            with m.State("ARB"):
                m.d.comb += sel.eq(pick)
                with m.If(val[sel]):
                    # Whatever comes of this cycle, this source has had its turn.
                    with m.If(is_status | (run[sel] != 0)):
                        with m.If(self.o.ready):
                            m.d.sync += last.eq(sel)
                    with m.Else():
                        m.d.sync += last.eq(sel)

                    with m.If(is_status):
                        m.d.comb += [self.o.valid.eq(1), self.o.payload.eq(b),
                                     rdy[sel].eq(self.o.ready)]
                        with m.If(self.o.ready & ~is_rt):
                            m.d.sync += [cur.eq(sel), rem.eq(len_new),
                                         # System Common cancels running status; a channel status
                                         # becomes it. Real-Time, handled above, does neither.
                                         run[sel].eq(Mux(is_sys, 0, b))]
                            with m.If(is_sys & (b[0:4] == 0)):
                                m.next = "SYSEX"
                            with m.Elif(len_new != 0):
                                m.next = "DATA"
                    with m.Elif(run[sel] != 0):
                        # Running status. Send the remembered status *without* consuming this data
                        # byte, so the message that reaches the engine carries its own status even
                        # though the source did not resend one.
                        m.d.comb += [self.o.valid.eq(1), self.o.payload.eq(run[sel])]
                        with m.If(self.o.ready):
                            m.d.sync += [cur.eq(sel), rem.eq(len_run)]
                            m.next = "DATA"
                    with m.Else():
                        m.d.comb += rdy[sel].eq(1)      # data byte with no status: nothing to do

            with m.State("DATA"):
                m.d.comb += sel.eq(cur)
                with m.If(val[sel] & is_status):
                    m.next = "ARB"                      # truncated; leave the byte for ARB
                with m.Else():
                    m.d.comb += [self.o.valid.eq(val[sel]), self.o.payload.eq(b),
                                 rdy[sel].eq(self.o.ready)]
                    with m.If(val[sel] & self.o.ready):
                        m.d.sync += rem.eq(rem - 1)
                        with m.If(rem == 1):
                            m.next = "ARB"

            with m.State("SYSEX"):
                # Unbounded by definition, so the grant runs until 0xF7 -- or until any other
                # non-Real-Time status byte, which means the dump was cut short.
                m.d.comb += sel.eq(cur)
                m.d.comb += [self.o.valid.eq(val[sel]), self.o.payload.eq(b),
                             rdy[sel].eq(self.o.ready)]
                with m.If(val[sel] & self.o.ready & is_status & ~is_rt):
                    m.next = "ARB"

        return m
