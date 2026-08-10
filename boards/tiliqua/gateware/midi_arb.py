# M28 — merging MIDI sources without corrupting either one.
#
# Until now top.py fed the engine from a two-way mux: USB won, TRS got the cycles USB left over,
# and the comment above it admitted the rest -- "playing both at once interleaves bytes mid-message
# and is not supported". That was survivable while both sources were human-scale and rarely used
# together. M28 added a third that was neither -- CvIn, emitting on its own schedule forever -- and
# that is what forced the issue. CvIn went away again in M31; the arbiter did not, because the
# hazard it fixes was never CvIn's. Two sources are enough to hit it.
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
    A USB host running a preset census has something to say on every cycle, indefinitely; give it
    a permanent claim and the TRS jack never plays another note. Round-robin bounds the wait at
    one message per other source, so worst-case latency for the keyboard is a few microseconds
    rather than unbounded.

    A status byte arriving where a data byte was expected ends the message early and re-arbitrates
    without consuming it. That is the same choice SysCommonFilter makes: a truncated message should
    cost its own bytes and not the next one's.

    Each source can also be re-addressed to a fixed channel: raise `chan_en[k]` and every
    channel-voice status byte from source k leaves with `chan[k]` in its low nibble. See
    MidiPartSelect below for why that lives here rather than in a block of its own.
    """

    def __init__(self, n_sources):
        self.n = n_sources
        super().__init__({
            "i": In(stream.Signature(unsigned(8))).array(n_sources),
            "o": Out(stream.Signature(unsigned(8))),
            # Per-source channel override. Off at reset, so a source nobody drives these for
            # behaves exactly as it did before they existed.
            "chan":    In(4).array(n_sources),
            "chan_en": In(1).array(n_sources),
        })

    def elaborate(self, platform):
        m = Module()

        pay = Array([s.payload for s in self.i])
        val = Array([s.valid   for s in self.i])
        rdy = Array([s.ready   for s in self.i])
        ch  = Array(self.chan)
        cen = Array(self.chan_en)
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

        def rechan(byte):
            """`byte` re-addressed to the granted source's channel, if that source has one set.

            Only channel-voice status survives the guard: 0xF0-0xFF has no channel nibble, and a
            data byte's bit 7 is clear. Note this rewrites at the *output*, not on the way into
            `run[]` -- which is what makes a change of target take effect on the very next message
            even from a keyboard that has stopped resending its status byte.
            """
            return Mux(cen[sel] & byte[7] & (byte[4:8] != 0xF), Cat(ch[sel], byte[4:8]), byte)

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
                        m.d.comb += [self.o.valid.eq(1), self.o.payload.eq(rechan(b)),
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
                        m.d.comb += [self.o.valid.eq(1), self.o.payload.eq(rechan(run[sel]))]
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


CC_PART = 103                                   # undefined in the MIDI spec; unused by synth.x


class MidiPartSelect(wiring.Component):

    """
    CC103 -> which of the four parts the TRS keyboard plays.

    A hardware keyboard transmits on the channel it was configured with -- in practice channel 1,
    always -- so it reaches part 1 and nothing the web UI does moves it. The on-screen keys have no
    such problem because the browser re-addresses them before they leave (app.js `noteChans`), and
    so does the bridge for a host-side keyboard in LOCAL play. TRS is the one path where the bytes
    arrive already addressed, past every piece of software involved, which is why the fix has to be
    here at all: by the time anything can see them they are on the FPGA.

    So the PART chips send CC103 over USB, and the arbiter re-addresses source 0 to match. Reading
    the target from the USB side rather than from the merged stream is deliberate -- it means a
    keyboard cannot retarget itself by sending CC103, and that the two directions cannot fight.

    Value 0-15 selects a channel; anything above (the UI sends 127) turns the override off and the
    keyboard plays on its own channel again. Off is also the reset state, so check_midi.py and every
    bitstream built before this one behave identically until something asks otherwise.

    Like the other sniffers this ties `ready` high: an observer that can stall the path it observes
    is a deadlock waiting for the one day both sources are busy.

    `i_clear` drops the override again. M34 wires it to MidiChanWatch, so a player who reaches for
    the keyboard's own channel knob takes the decision back from the panel -- otherwise the panel's
    last click outranks the instrument in the player's hands for as long as the board is powered.
    """

    i_midi:  In(stream.Signature(unsigned(8)))
    i_clear: In(1)
    o_chan:  Out(4)
    o_en:    Out(1)

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.i_midi.ready.eq(1)

        b   = self.i_midi.payload
        st  = Signal(8)
        idx = Signal()
        num = Signal(7)

        # Written before the sniffer below, so a CC103 arriving on the same cycle wins: the panel
        # asking for a part is a later decision than the keyboard's, whatever order they land in.
        with m.If(self.i_clear):
            m.d.sync += self.o_en.eq(0)

        # This sits *upstream* of the arbiter's running-status expansion, on the raw USB stream, so
        # latching `st` here is load-bearing rather than defensive: the bridge sends CC103 with no
        # status byte of its own if the last thing it sent was also a CC.
        with m.If(self.i_midi.valid):
            with m.If(b[7]):
                m.d.sync += [st.eq(b), idx.eq(0)]
            with m.Elif(st[4:8] == 0xB):
                m.d.sync += idx.eq(~idx)
                with m.If(~idx):
                    m.d.sync += num.eq(b)
                with m.Elif(num == CC_PART):
                    m.d.sync += [self.o_chan.eq(b[0:4]), self.o_en.eq(b < 16)]

        return m


class MidiChanWatch(wiring.Component):

    """
    Which channel is the TRS keyboard speaking on right now?

    Nothing downstream can answer this. The arbiter rewrites the channel nibble at its *output*
    (see `rechan`), so by the time bytes reach the engine the keyboard's own choice is gone; and
    when the override is off there is no record of the choice at all. Both of the things M34 adds
    need one: releasing the panel's override when the player turns the channel knob, and knowing
    which part to silence when the target moves.

    Running status is not a problem. A keyboard that changes transmit channel emits a fresh status
    byte -- it has to, the nibble is *in* the status byte -- so every change is visible here even
    from an instrument that otherwise never resends one. 0xF0-0xFF is excluded because it carries
    no channel; without that guard Active Sensing would read as channel 14 twice a second.

    The one thing that fools it is a split or layered keyboard alternating two channels. That looks
    like a change on every note, so the override never sticks and the board falls back to letting
    the keyboard's channel choose the part -- which is the sane behaviour for a split anyway.
    """

    i_midi:   In(stream.Signature(unsigned(8)))
    o_chan:   Out(4)
    o_change: Out(1)                            # one cycle, when o_chan takes a new value

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.i_midi.ready.eq(1)
        b = self.i_midi.payload
        m.d.sync += self.o_change.eq(0)
        with m.If(self.i_midi.valid & b[7] & (b[4:8] != 0xF)):
            m.d.sync += self.o_chan.eq(b[0:4])
            with m.If(b[0:4] != self.o_chan):
                m.d.sync += self.o_change.eq(1)
        return m


class TrsPanicInject(wiring.Component):

    """
    When the TRS jack starts playing a different part, silence the one it left.

    This is the hardware half of a bug the browser could only paper over. A key held across a PART
    change gets its note-off re-addressed by `rechan` to the part the player just moved *to*, so the
    note strands on the part they moved *from* and sounds until the power goes off. app.js sweeps
    128 note-offs at every part change to cover it, but only for changes it knows about -- and it
    knows about none of the ones the keyboard makes on its own, because TRS bytes never reach the
    browser.

    So the board cleans up after itself: feed it the *effective* target (the override when the panel
    has set one, the keyboard's own channel otherwise) and it emits `Bn 7B 00` -- All Notes Off --
    addressed to the target being abandoned, as a fourth MIDI source. The arbiter is message-atomic,
    so this waits for whatever the keyboard is mid-way through rather than interleaving with it.

    One CC123 per change, and both are zero at reset, so it stays quiet until something moves.
    """

    i_chan: In(4)                               # the part the TRS jack's notes are landing on
    o:      Out(stream.Signature(unsigned(8)))

    def elaborate(self, platform):
        m = Module()
        prev    = Signal(4)
        leaving = Signal(4)
        idx     = Signal(2)
        sending = Signal()

        m.d.comb += [
            self.o.valid.eq(sending),
            self.o.payload.eq(Mux(idx == 0, Cat(leaving, C(0xB, 4)),
                              Mux(idx == 1, C(0x7B, 8), C(0x00, 8)))),
        ]
        with m.If(sending):
            with m.If(self.o.ready):
                m.d.sync += idx.eq(idx + 1)
                with m.If(idx == 2):
                    m.d.sync += sending.eq(0)
        with m.Elif(self.i_chan != prev):
            # `prev` advances here rather than when the message finishes: a second change during
            # a send then queues its own CC123 for the channel this one is moving to, instead of
            # re-sending this one and losing the intermediate part entirely.
            m.d.sync += [leaving.eq(prev), prev.eq(self.i_chan), sending.eq(1), idx.eq(0)]

        return m
