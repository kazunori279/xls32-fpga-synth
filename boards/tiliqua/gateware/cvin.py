# M28 — the Eurorack input jacks, turned into MIDI.
#
# The four input jacks have been arriving since M25 and going straight in the bin: top.py wires
# `pmod0.o_cal` to `core.i` and xls_core.py:219 ties `self.i.ready` high, so the engine consumes
# the stream and ignores every sample. This is what finally reads them.
#
# It emits MIDI rather than reaching into the engine, and that is the whole design decision. The
# DSLX core is shared with the Basys 3, so any new input port costs a 48-stage XLS build plus a
# Vivado run on a board this milestone does not otherwise touch. MIDI is a port the engine already
# has, on a channel nothing else uses, and it costs neither.
#
# Consequences of going through MIDI, both of which are deliberate:
#
#   * Pitch is a note number plus a bend, not a continuous frequency, so crossing a semitone
#     retriggers the envelope. That is what every CV-to-MIDI converter does and it is audible.
#     A true glide would need the core to take a pitch input, which is the thing above.
#   * The bend covers the residual only. synth.x:347 shifts the 14-bit bend right by 4 and
#     synth.x:364 clamps the result to +-2047, so the usable range is +-512 -- about +-2.1
#     semitones, against the +-0.5 a rounded note number leaves over. Comfortable.

from amaranth import *
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from tiliqua.dsp import ASQ

# BASE_NOTE / CC_RAMP / RAMP_STEP live apart because check_cv.py needs them and cannot import
# amaranth; see cv_proto.py.
from cv_proto import BASE_NOTE, CC_RAMP, RAMP_STEP  # noqa: F401  (re-exported for callers)

# 4000 counts per volt: ASQ full scale is 32768 = 8.192 V (see the class docstring in
# tiliqua/periph/eurorack_pmod.py, and macro_osc/fw/src/main.rs:138 which divides by 4000.0).
# One semitone of 1 V/oct is therefore 4000/12 = 333.333 counts.
#
# SEMI_MUL converts counts to Q8 semitones: 256/333.333 = 0.768, and 0.768 * 2^16 = 50331.6.
# The width is not incidental. The obvious 16-bit form, `cv * 197 >> 16`, is 0.003006 against a
# true 0.003 -- 0.2% -- which is 12 cents at the top of a 5-octave sweep, i.e. it would fail
# M28's exit criterion on arithmetic alone before any hardware was involved. At this width the
# error is 0.004 cents.
SEMI_MUL = 50332

# Q8 semitones of residual -> 14-bit MIDI bend. The core computes ratio = 1 + pmod/4096 with
# pmod = (bend14 - 8192) >> 4 (synth.x:202, :347), so pmod is linear in *ratio*, not in cents;
# d(pmod)/d(semitone) at the centre is 4096 * ln2/12 = 236.6. Hence 236.6/256 * 16 = 14.79 units
# of bend14 per Q8 count, times 2^8 for the shift below.
#
# Because the true curve is exponential and this is its tangent, the fit is exact at the centre
# and worst at the extremes: 0.72 cents at +-0.5 semitone. That is inside "a few cents" with room
# to spare. If it ever is not, a 64-entry LUT of the exact curve replaces this multiply.
BEND_MUL = 3786
BEND_MID = 8192
BEND_DEAD = 16                 # ~0.4 cents; below this the CV is jittering, not moving

AVG_LOG2 = 6                   # 64 frames = 1.33 ms at 48 kHz, and the emit rate
VEL = 100
CHAN = 3                       # MIDI channel 4. Parts are the channel's low 2 bits (synth.x:337),
                               # so CV drives part 4 and a keyboard on channel 1 still plays part 1.
GATE_HI, GATE_LO = 4000, 2000  # 1.00 V / 0.50 V, Schmitt
JACK_CV, JACK_GATE = 0, 1
JACK_CC = (2, 3)
CC_A, CC_B = 74, 71            # in2 -> cutoff, in3 -> resonance


class CvIn(wiring.Component):

    """
    Eurorack CV in -> a MIDI byte stream on channel 4.

    in0 is 1 V/oct pitch, in1 is the gate, in2 and in3 are assignable CCs. Samples arrive as a
    plain snapshot plus a strobe rather than as a stream, because `core.i` already consumes
    `pmod0.o_cal` with `ready` tied high -- a second *consumer* would fight it, so this observes,
    exactly as the effects sniff the MIDI bytes in top.py.

    Nothing is emitted unless in0 is patched. Without that check an unpatched board would read its
    own ~-100 mV of uncalibrated offset as a valid pitch and drone from power-up. If the gate jack
    is empty but in0 is not, the gate is held on, which is both the useful default for a pitch-only
    patch and what lets check_cv.py sweep pitch without a second cable.
    """

    i_cv:     In(data.ArrayLayout(ASQ, 4))
    i_strobe: In(1)
    jack:     In(8)
    o_midi:   Out(stream.Signature(unsigned(8)))

    def elaborate(self, platform):
        m = Module()

        # --- box average -------------------------------------------------------------------
        # The ADC noise floor is about -70 dBFS, ~10 counts, ~3 cents -- the entire error budget
        # of the exit criterion, spent on nothing. 64 frames takes it to well under a cent, and
        # doubles as the emit clock: everything downstream reconsiders itself on `tick`.
        #
        # The accumulator starts each window at half a divisor rather than at zero, which is the
        # rounding term pre-paid: `(HALF + sum) >> 6` is round-to-nearest, and it costs nothing.
        # Adding it at the divide instead would be four more 23-bit adders, and adders are 270 of
        # this module's 537 logic cells -- on a die with no room left, that is not a rounding
        # error in the figurative sense either.
        HALF   = 2**(AVG_LOG2 - 1)
        acc    = [Signal(signed(17 + AVG_LOG2), name=f"acc{c}", init=HALF) for c in range(4)]
        nxt    = [Signal(signed(17 + AVG_LOG2), name=f"nxt{c}")    for c in range(4)]
        smooth = [Signal(signed(16),            name=f"smooth{c}") for c in range(4)]
        cnt    = Signal(AVG_LOG2)
        tick   = Signal()

        # The sum has to be combinational because the closing frame belongs in its own window: the
        # obvious `smooth <= acc >> 6` divides a 63-sample sum by 64, which is a 1.6% gain error --
        # 27 cents at the top of the sweep. The rounding term is worth another 0.3 cents.
        for c in range(4):
            m.d.comb += nxt[c].eq(acc[c] + self.i_cv[c].as_value().as_signed())

        m.d.sync += tick.eq(0)
        with m.If(self.i_strobe):
            m.d.sync += cnt.eq(cnt + 1)
            for c in range(4):
                m.d.sync += acc[c].eq(nxt[c])
            with m.If(cnt == 2**AVG_LOG2 - 1):
                # `smooth` lands on the same edge as `tick`, so it is already the new value on
                # the cycle `tick` is high.
                for c in range(4):
                    m.d.sync += [smooth[c].eq(nxt[c] >> AVG_LOG2), acc[c].eq(HALF)]
                m.d.sync += tick.eq(1)

        # --- in0: volts -> note + bend -------------------------------------------------------
        # `semi_q8` is a register, not a wire, and that is a timing fix rather than a style. Both
        # multiplies here infer a MULT18X18D, and written combinationally they chain: the first
        # cv build put `smooth0 -> prod.MULT18X18D -> bprod.MULT18X18D` on the `sync` critical
        # path at 40.96 MHz, which is the first time since M25 that the failing path belonged to
        # this repo rather than to LUNA's control endpoint. Splitting it costs one cycle of
        # latency out of the 1.33 ms between ticks, and `smooth` is stable across all of it.
        prod    = Signal(signed(34))
        semi_q8 = Signal(signed(18))
        note_r  = Signal(signed(10))
        res_q8  = Signal(signed(9))
        m.d.comb += prod.eq(smooth[0] * C(SEMI_MUL, unsigned(17)))
        m.d.sync += semi_q8.eq(prod >> 16)
        m.d.comb += [
            note_r.eq((semi_q8 + 128) >> 8),          # round to nearest semitone...
            res_q8.eq(semi_q8 - (note_r << 8)),       # ...and the bend carries what is left
        ]

        note_n = Signal(signed(11))
        note_c = Signal(7)
        m.d.comb += note_n.eq(note_r + BASE_NOTE)
        with m.If(note_n < 0):
            m.d.comb += note_c.eq(0)
        with m.Elif(note_n > 127):
            m.d.comb += note_c.eq(127)
        with m.Else():
            m.d.comb += note_c.eq(note_n)

        bprod  = Signal(signed(22))
        bend14 = Signal(signed(16))
        m.d.comb += [
            bprod.eq(res_q8 * C(BEND_MUL, unsigned(12))),
            # +128 rounds this shift; +8 pre-rounds the core's own `>>4` at synth.x:347, whose
            # 16-unit quantum is 0.42 cents and is the floor on anything done through the bend.
            bend14.eq(BEND_MID + ((bprod + 128) >> 8) + 8),
        ]

        # --- in1: gate ------------------------------------------------------------------------
        gate_w = Signal()
        with m.If(smooth[1] > GATE_HI):
            m.d.sync += gate_w.eq(1)
        with m.Elif(smooth[1] < GATE_LO):
            m.d.sync += gate_w.eq(0)

        gate_on = Signal()
        m.d.comb += gate_on.eq(self.jack[JACK_CV] & Mux(self.jack[JACK_GATE], gate_w, 1))

        # --- in2/in3: CCs -----------------------------------------------------------------------
        # 0 to full scale over 0..8.192 V, so a 0..5 V modulation source uses the low 78 of 127.
        cc_w = [Signal(7, name=f"cc_w{k}") for k in range(2)]
        for k in range(2):
            v = Signal(signed(9), name=f"cc_raw{k}")
            m.d.comb += v.eq(smooth[2 + k] >> 8)
            with m.If(v < 0):
                m.d.comb += cc_w[k].eq(0)
            with m.Else():
                m.d.comb += cc_w[k].eq(v)

        # --- what we want to be playing ---------------------------------------------------------
        w_note = Signal(7)
        w_bend = Signal(14, init=BEND_MID)
        w_gate = Signal()
        w_cc   = [Signal(7, name=f"w_cc{k}") for k in range(2)]

        # One cycle behind `tick`, which is where the pitch pipeline above lands: `smooth` updates
        # on the same edge that raises `tick`, `semi_q8` registers it on the next, and `note_c` /
        # `bend14` are combinational from there.
        tick_d = Signal()
        m.d.sync += tick_d.eq(tick)

        bdiff = Signal(signed(16))
        m.d.comb += bdiff.eq(bend14 - w_bend)
        with m.If(tick_d):
            m.d.sync += [w_note.eq(note_c), w_gate.eq(gate_on)]
            for k in range(2):
                # Same reason the pitch is jack-gated, with sharper teeth: an unpatched in2 reads
                # roughly zero, and CC74 = 0 is a closed filter. Left ungated, plugging the board
                # in would silence it.
                with m.If(self.jack[JACK_CC[k]]):
                    m.d.sync += w_cc[k].eq(cc_w[k])
            # Jack-gated like the rest: an unpatched in0 wanders around its DC offset, and a bend
            # per tick would be a steady trickle of MIDI for a jack nobody has plugged into.
            with m.If(self.jack[JACK_CV] & ((bdiff > BEND_DEAD) | (bdiff < -BEND_DEAD))):
                m.d.sync += w_bend.eq(bend14)

        # --- and what we are playing --------------------------------------------------------------
        c_note = Signal(7)
        c_bend = Signal(14, init=BEND_MID)
        c_gate = Signal()
        c_cc   = [Signal(7, name=f"c_cc{k}") for k in range(2)]

        # --- the emitter --------------------------------------------------------------------------
        # One 3-byte message at a time, lowest priority in top.py's arbiter. At 750 emits/s worst
        # case this is 2.2 kB/s against a consumer that takes a byte per cycle, so it cannot back up.
        #
        # The order matters on a note change: release the old note, move the bend, then strike, so
        # the new note is already in tune on its first sample rather than sliding into it.
        msg = [Signal(8, name=f"msg{i}") for i in range(3)]
        idx = Signal(2)

        def emit(status, d1, d2, *updates):
            m.d.sync += [msg[0].eq(status | CHAN), msg[1].eq(d1), msg[2].eq(d2), idx.eq(0)]
            m.d.sync += updates

        with m.FSM():
            with m.State("IDLE"):
                with m.If(c_gate & ((w_note != c_note) | ~w_gate)):
                    emit(0x80, c_note, 0x40, c_gate.eq(0))
                    m.next = "SEND"
                with m.Elif(w_bend != c_bend):
                    emit(0xE0, w_bend[0:7], w_bend[7:14], c_bend.eq(w_bend))
                    m.next = "SEND"
                with m.Elif(w_gate & ~c_gate):
                    emit(0x90, w_note, VEL, c_note.eq(w_note), c_gate.eq(1))
                    m.next = "SEND"
                with m.Elif(w_cc[0] != c_cc[0]):
                    emit(0xB0, CC_A, w_cc[0], c_cc[0].eq(w_cc[0]))
                    m.next = "SEND"
                with m.Elif(w_cc[1] != c_cc[1]):
                    emit(0xB0, CC_B, w_cc[1], c_cc[1].eq(w_cc[1]))
                    m.next = "SEND"

            with m.State("SEND"):
                m.d.comb += [
                    self.o_midi.valid.eq(1),
                    self.o_midi.payload.eq(Array(msg)[idx]),
                ]
                with m.If(self.o_midi.ready):
                    with m.If(idx == 2):
                        m.next = "IDLE"
                    with m.Else():
                        m.d.sync += idx.eq(idx + 1)

        return m


class CvTestRamp(wiring.Component):

    """
    CC102 -> a DC level on out2, so the board can grade its own CV input with one patch cable.

    out2 and out3 have carried silence since M26 (fx.py:426-427 passes through the engine's empty
    channels), so channel 2 is free for the host to drive. Patch out2 -> in0 and check_cv.py can
    step a 1 V/oct sweep and FFT the result over the USB tee without a signal generator, a voltmeter
    or a second module -- and re-run it any time, which is what makes it a regression rather than a
    bring-up measurement.

    The level arrives as MIDI rather than through a register file because there is no SoC in this
    bitstream and no CSR bus to hang one off; the host already has a MIDI path to the board, and
    CC102 is undefined in the spec and unused by synth.x. Like the other sniffers this observes the
    filtered byte stream with `ready` tied high, so it cannot stall the engine's MIDI.

    The ramp is *not* calibrated and does not need to be. Both converters carry -86..-116 mV of
    uncalibrated DC (docs/TILIQUA_PORT.md:140), which is a constant transposition of the whole
    sweep and cancels out of a slope fit. Only gain error would show up as tracking error, and the
    per-revision defaults compiled in at periph/eurorack_pmod.py:302 cover that.
    """

    i_midi:  In(stream.Signature(unsigned(8)))
    o_level: Out(signed(16))

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.i_midi.ready.eq(1)         # observer; must never stall the MIDI path

        b   = self.i_midi.payload
        st  = Signal(8)
        idx = Signal()
        num = Signal(7)
        val = Signal(7)

        # Running status is expanded by the arbiter before this point, so `st` would survive on the
        # status byte alone -- but latching it here costs two LUTs and keeps the sniffer correct if
        # it is ever moved upstream of the arbiter.
        with m.If(self.i_midi.valid):
            with m.If(b[7]):
                m.d.sync += [st.eq(b), idx.eq(0)]
            with m.Elif(st[4:8] == 0xB):
                m.d.sync += idx.eq(~idx)
                with m.If(~idx):
                    m.d.sync += num.eq(b)
                with m.Elif(num == CC_RAMP):
                    m.d.sync += val.eq(b)

        # value * RAMP_STEP, as a shift pair rather than a multiply: the die has one MULT18X18D
        # left and a DC level is not what to spend it on. 160 = 128 + 32 is why that constant is
        # 160 and not a rounder number; check_cv.py reads it back from here for its volts axis.
        assert RAMP_STEP == 160
        m.d.comb += self.o_level.eq((val << 7) + (val << 5))
        return m
