# M26 — chorus, ping-pong echo and 8-comb Freeverb for Tiliqua.
#
# A structural port of boards/basys3/rtl/top.v:159-400, not a reimplementation. The four graded
# cases (`echo`, `reverb`, `reverb_cathedral`, `stress_fx_tail`) were written against that exact
# topology, so the arithmetic stays bit-for-bit where it can and every departure is called out
# below with its reason.
#
# Why this is not built out of `tiliqua.dsp.delay_line` the way the port plan's M26 originally
# said: `DelayLine` is single-writer / multi-reader over one circular buffer, but each Freeverb
# comb has its *own* write pointer and writes its own feedback back into it. Expressing the tank
# that way needs 12 `DelayLine` instances per channel -- 24 in total, each with its own
# `wishbone.Arbiter` -- on a device that M25 left at 86% TRELLIS_COMB. The SDK itself does not do
# that either: `src/top/dsp/top.py:822` keeps a `sram_max_delay = 1024` heuristic that routes
# short taps to local memory and only long ones to PSRAM. Every Freeverb tap here is short
# (<=958 samples); only the echo is long. So the tank is one plain `Memory` per channel with
# region offsets -- exactly what the Basys 3 does -- and `DelayLine` is used for the one delay
# long enough to be worth its arbiter.
#
# Where the samples live
# ----------------------
#   reverb tank    2 x Memory(7,450 x 16)    BRAM, 8 DP16KD per channel
#   chorus history 2 x Memory(1,024 x 16)    BRAM, 1 DP16KD per channel
#   echo history   2 x dsp.DelayLine(16384)  BRAM, 16 DP16KD per channel
#
# All three were in BRAM only from M29. Before that the echo sat in PSRAM at 32,768 words and the
# tank ran at the Basys 3's full delay lengths, 15 DP16KD per channel. Video is what changed it:
# the beam-raced tiles need no memory at all but do need ~800 LUTs, and `psram_periph` plus its
# DDR physical layer was the only block of that size in this variant that could be given up. So
# the echo came inboard, the tank was halved to make room for it, and RVG rose to hold RT60.
#
# On Basys 3 the chorus and the echo read the *same* buffer (`dmemL`/`dmemR`), so the chorus
# hears the echo feedback and not the dry signal. That is preserved: the chorus ring is written
# with the identical word that goes into the echo line, it is just a second, much shorter copy of
# the recent past. Duplicating 1 kword per channel costs one BRAM tile and saves two variable
# PSRAM taps thrashing a 64-word direct-mapped cache against the write pointer.
#
# Timing
# ------
# `core.o` is already 48 kHz in `sync` at 60 MHz -- 1,250 cycles per sample. The Basys 3 FSM had
# to run at `ce8`; here the whole thing takes ~114 cycles, so there is room to spend cycles buying
# slack instead of saving them. Each tank step is therefore split into four phases (address /
# read+damp / feedback multiply / accumulate+write) rather than crammed into one, which keeps the
# memory output, the Q15 feedback multiply and the saturating adds off the same 16.6 ns path.
# M25 left `sync` placing at 48-51 MHz against 60 MHz required; this file is not going to be what
# makes that worse.
#
# The fourth phase is M35 and was bought with measurement, not caution. Three were not enough:
# `rsize -> rvg -> MULT18X18D -> fbm -> cbn -> acc -> csr` measured 21.49 ns, and the moment a
# skid buffer took luna's 22.11 ns USB cone out of the report, *this file* was the worst path on
# the die. See the `cbn_r` note below and ARCHITECTURE_tiliqua.md E4.
#
# Deliberate departures from the Verilog, each with a reason
# ---------------------------------------------------------
# 1. No +/-32768 offset arithmetic (top.v:342, :385-386). ASQ is signed Q1.15 and `core.o` is
#    already signed; the offsets exist only because the Basys 3 engine emitted offset binary and
#    the UART framing wanted it back.
# 2. No `clearing` FSM (top.v:295-302). Artix BRAM powers up as garbage; `amaranth.lib.memory`
#    zero-initialises DP16KD in the bitstream for free. (The PSRAM lines do still zero themselves
#    -- that is `DelayLine`'s own ZERO-MEMORY state, and the taps stall until it finishes, which
#    is why no audio passes for the first few ms after configuration.)
# 3. Three shared multipliers, not seven. The Verilog writes `fbm`, `echoW{L,R}`, `chW{L,R}` and
#    `rwet{L,R}` as combinational wires because that is what Verilog wires do; there are exactly
#    7 free MULT18X18D on this part, i.e. zero margin. The FSM already serialises L then R and
#    the echo/chorus wet never coincides with the reverb wet, so two channel multipliers plus one
#    for the comb feedback covers all seven sites.
# 4. CC90 (`dbg`, top.v:340-346) is not ported. It is a UART-era bypass probe; the Tiliqua
#    equivalent is the USB tee in top.py, which already exists.
#
# The multiply-width trap documented at top.v:271 does not exist in Amaranth -- `a * b` is
# evaluated at the full product width and only then shifted -- but the shifts are still written
# out explicitly so the two files read the same.

from amaranth import *
from amaranth.lib import data, memory, stream, wiring
from amaranth.lib.wiring import In, Out

from amaranth_soc import wishbone

from tiliqua import dsp
from tiliqua.dsp import ASQ

# --- sample-rate scaling ---------------------------------------------------------------------
# Every constant below that is a number of *samples* comes from the 32 kHz Basys 3 build and is
# scaled by 3/2 so that the corresponding *time* is unchanged at 48 kHz. Gains are not scaled:
# `rvg` is a per-round-trip Q15 feedback coefficient and the round trip is the same duration.
FS = 48000


def _S(n):
    """32 kHz sample count -> 48 kHz, rounded half up (403 -> 605, not 604)."""
    return (n * 3 + 1) // 2


# Freeverb comb and all-pass delays, half of the Basys 3 figures (top.v:176-183) and still
# coprime-ish after the 3/2 scaling. M29 halved them to buy BRAM: the tank was 15 DP16KD per
# channel and the echo needed somewhere to live once PSRAM went away. See the tank note below.
CL     = [_S(n) for n in (405, 439, 470, 506, 533, 561, 588, 615)]
AL     = [_S(n) for n in (202, 160, 124, 82)]
SPREAD = _S(23)                          # R delay lengths = L + SPREAD; the Freeverb stereo image
DELAYS = CL + AL                         # 12 regions: 8 combs then 4 all-pass
NREG   = len(DELAYS)
NCOMB  = len(CL)

# Region map. The Basys 3 spaces the regions uniformly (RB0..RB7 every 1300, RA0..RA3 every 600)
# because it had 16 kwords of BRAM to spend and nothing else to spend them on. Here each region
# is exactly as long as its own R-channel delay, which is what turns 2 x 10 DP16KD into 2 x 8.
_LEN   = [d + SPREAD for d in DELAYS]
REGION = [sum(_LEN[:i]) for i in range(NREG)]
TANK_WORDS = sum(_LEN)                   # 7,450 -- 8 DP16KD per channel, was 15 at full length
assert TANK_WORDS <= 8 * 1024, "the tank has outgrown the 8 DP16KD the echo's move left it"

# Chorus: Q3 tap sweeping 450.0 .. 833.875 samples (9.375 .. 17.4 ms, same as Basys 3 at 32 kHz).
CH_BASE   = _S(2400)                     # 3600 in Q3 = 450.0 samples
CH_SWEEP  = _S(2047) + 1                 # 3072 in Q3 = 384.0 samples of sweep
CH_WORDS  = 1024                         # >= 834 + 1, one DP16KD
# LFO period in samples. The Basys 3 free-runs a 15-bit counter, giving 32768/32000 = 1.024 s;
# a free-running counter at 48 kHz would be 1.46 Hz instead, so this wraps explicitly and the
# LFO stays at 0.977 Hz. LFO_PERIOD/2 >> 3 == CH_SWEEP, which is what makes the fold exact.
LFO_PERIOD = CH_SWEEP * 16               # 49,152 samples

# Echo. `edly = (dtime << 7) | 128` is 4..512 ms at 32 kHz (top.v:167); x3/2 keeps the range.
ECHO_STEP = _S(128)                      # 192 samples per CC82 count
ECHO_MIN  = _S(128)                      # floor, so the tap is never == the write pointer
# M29: 16,384 words, not 32,768. The line moved from PSRAM to BRAM to free `psram_periph` and its
# DDR physical layer for the video block, and BRAM is what the halved reverb tank had to spare.
# `DelayLine` wants a power of two, so the reachable range is 192*(dtime+1) <= 16,384, i.e. CC82
# tops out at 84 and the echo at 340 ms instead of 512. The demo library's longest setting is
# Ivory Orbit at dtime 85 (344 ms); it clamps to 340, a 4 ms difference.
ECHO_MAX_DELAY = 16384
ECHO_DTIME_MAX = ECHO_MAX_DELAY // ECHO_STEP - 1     # 84; StereoFx clamps CC82 to this

# Room size -> comb feedback g (Q15).
#
# These are NOT the Basys 3 numbers, and the difference is the one thing about M29's tank cut that
# is audible if you get it wrong. RT60 = D * ln(0.001)/ln(g): the gain is per *round trip*, so
# halving the comb delay D halves the decay unless g rises to compensate. Same RT60 at half the
# delay wants ln(g') = ln(g)/2, i.e. g' = sqrt(g). Cathedral therefore climbs from 0.952 to 0.976
# of unity -- high, but still inside the range Freeverb's own roomsize control reaches, and the
# damping filter sits inside the same loop.
#
# The 3/2 rate scaling above is a different case and stays uncompensated: it preserves delay
# *time*, so the round trip is unchanged and g must not move.
RVG = [26850,   # 0  room      ~0.4 s   (was 22000 at full tank length)
       29188,   # 1  hall      ~0.8 s   (was 26000)
       30826,   # 2  large     ~1.5 s   (was 29000)
       31974]   # 3  cathedral ~3.5 s   (was 31200)

# PSRAM byte bases for the two echo lines. 32,768 samples x 2 bytes = 64 KiB each.
ECHO_BASE_L = 0x000000
ECHO_BASE_R = 0x010000


def sat16(m, x, name="sat"):
    """top.v:215 `sat18`: clamp a wide signed value into 16 bits."""
    y = Signal(signed(16), name=name)
    with m.If(x > 32767):
        m.d.comb += y.eq(32767)
    with m.Elif(x < -32768):
        m.d.comb += y.eq(-32768)
    with m.Else():
        m.d.comb += y.eq(x)
    return y


class FxControl(wiring.Component):

    """
    The effect-control CC sniffer (top.v:76-104), reading the same byte stream the engine parses.

    It mirrors the engine's running-status handling rather than decoding MIDI properly, because
    that is what the Basys 3 does and the test harness was written against its quirks: any byte
    >= 0x80 restarts the state machine, and a completed CC leaves the counter at 1 so the next
    pair of data bytes is taken as another controller/value.

    CC83 (`fxmode`) is not decoded. It was superseded by depth gating on Basys 3 and is dead
    there too; the cases still send it and it is still correctly ignored.
    """

    i: In(stream.Signature(unsigned(8)))

    def __init__(self):
        # Defaults are the Basys 3 power-on values (top.v:78-83). `revwet` is 0, i.e. the reverb
        # is off until a CC turns it on -- which is what keeps the path through StereoFx close to
        # dry at the settings the test harness resets to.
        self.rsize   = Signal(2, init=3)     # CC91 room size    -- cathedral
        self.revwet  = Signal(7, init=0)     # CC93 reverb wet   -- off
        self.chdep   = Signal(7, init=64)    # CC94 chorus depth -- 0.5
        self.echodep = Signal(7, init=64)    # CC95 echo depth   -- 0.5
        self.dtime   = Signal(7, init=63)    # CC82 delay time   -- ~252 ms
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        ecnt  = Signal(2)
        ectrl = Signal(8)
        b     = self.i.payload

        m.d.comb += self.i.ready.eq(1)       # never backpressure the MIDI path
        with m.If(self.i.valid):
            with m.If(b >= 0x80):
                m.d.sync += ecnt.eq(Mux((b & 0xF0) == 0xB0, 1, 0))
            with m.Elif(ecnt == 1):
                m.d.sync += [ectrl.eq(b), ecnt.eq(2)]
            with m.Elif(ecnt == 2):
                with m.Switch(ectrl):
                    with m.Case(82):
                        m.d.sync += self.dtime.eq(b[0:7])
                    with m.Case(91):
                        m.d.sync += self.rsize.eq(b[5:7])
                    with m.Case(93):
                        m.d.sync += self.revwet.eq(b[0:7])
                    with m.Case(94):
                        m.d.sync += self.chdep.eq(b[0:7])
                    with m.Case(95):
                        m.d.sync += self.echodep.eq(b[0:7])
                m.d.sync += ecnt.eq(1)       # running status

        return m


class StereoFx(wiring.Component):

    """
    Chorus + ping-pong echo + 8-comb Freeverb, in `sync`, between `core.o` and `pmod0.i_cal`.

    The engine is mono; the effects create the stereo image. The dry signal sits centred
    (identical on both channels) and only the wet is decorrelated: the reverb uses the Freeverb
    stereo spread (R delay lengths = L + SPREAD), the echo ping-pongs L<->R, and the chorus L/R
    LFO taps run in anti-phase.

    Members
    -------
    i : stream of 4 x ASQ
        From the engine. Channel 0 is the mono voice sum; 2 and 3 pass through untouched.
    o : stream of 4 x ASQ
        Channels 0/1 are the wet stereo pair, 2/3 the passed-through dry.
    i_midi_bytes : stream of unsigned(8)
        A copy of the byte stream the engine parses, for the CC sniffer.
    bus : wishbone master
        The two echo delay lines' shared PSRAM port. Present only when `psram=True`.
    """

    def __init__(self, psram=True, psram_addr_width=22, max_echo=ECHO_MAX_DELAY):
        """
        psram : bool
            True on hardware. False swaps the two echo lines for SRAM-backed ones with an
            identical tap interface and drops the `bus` port -- that is what test_fx.py builds,
            so the unit sim does not need an emulated HyperRAM to say anything useful.
        max_echo : int
            Echo line length in samples, a power of two. Only shortened for simulation, where
            the line's own zeroing pass would otherwise dominate the run.
        """
        self.psram = psram
        self.max_echo = max_echo
        self.ctrl = FxControl()
        if psram:
            self.delay = [dsp.DelayLine(max_delay=max_echo, psram_backed=True,
                                        addr_width_o=psram_addr_width, base=base,
                                        write_triggers_read=False)
                          for base in (ECHO_BASE_L, ECHO_BASE_R)]
        else:
            self.delay = [dsp.DelayLine(max_delay=max_echo, write_triggers_read=False)
                          for _ in range(2)]
        self.tap = [d.add_tap() for d in self.delay]
        ports = {
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "i_midi_bytes": In(stream.Signature(unsigned(8))),
        }
        if psram:
            ports["bus"] = Out(wishbone.Signature(addr_width=psram_addr_width, data_width=32,
                                                  granularity=8, features={"bte", "cti"}))
        super().__init__(ports)

    def elaborate(self, platform):
        m = Module()

        m.submodules.ctrl = ctrl = self.ctrl
        wiring.connect(m, wiring.flipped(self.i_midi_bytes), ctrl.i)

        delay, tap = self.delay, self.tap
        m.submodules.delay_l = delay[0]
        m.submodules.delay_r = delay[1]

        if self.psram:
            # Both echo lines share the one PSRAM port. Their windows do not overlap (64 KiB
            # apart) and each carries its own L2 cache, so the arbiter only interleaves bursts.
            m.submodules.psram_arb = arb = wishbone.Arbiter(
                addr_width=self.bus.addr_width, data_width=32, granularity=8,
                features={"bte", "cti"})
            for d in delay:
                arb.add(d.bus)
            wiring.connect(m, arb.bus, wiring.flipped(self.bus))

        # --- memories -----------------------------------------------------------------------
        m.submodules.tank_l = tank_l = memory.Memory(shape=signed(16), depth=TANK_WORDS, init=[])
        m.submodules.tank_r = tank_r = memory.Memory(shape=signed(16), depth=TANK_WORDS, init=[])
        m.submodules.chor_l = chor_l = memory.Memory(shape=signed(16), depth=CH_WORDS, init=[])
        m.submodules.chor_r = chor_r = memory.Memory(shape=signed(16), depth=CH_WORDS, init=[])
        tank_rd = [tank_l.read_port(), tank_r.read_port()]
        tank_wr = [tank_l.write_port(), tank_r.write_port()]
        chor_rd = [chor_l.read_port(), chor_r.read_port()]
        chor_wr = [chor_l.write_port(), chor_r.write_port()]

        # --- knobs --------------------------------------------------------------------------
        echo_on    = Signal()
        chorus_on  = Signal()
        rvg        = Signal(15)
        edly       = Signal(range(self.max_echo))
        dtime_c    = Signal(7)
        wetgn      = Signal(signed(16))
        chdep_q15  = Signal(signed(16))
        echdep_q15 = Signal(signed(16))
        m.d.comb += [
            echo_on.eq(ctrl.echodep != 0),         # top.v:210 -- depth-gated, no mode selector
            chorus_on.eq(ctrl.chdep != 0),
            edly.eq((dtime_c << 7) + (dtime_c << 6) + ECHO_MIN),   # * ECHO_STEP, see below
            wetgn.eq(ctrl.revwet << 8),            # CC 0..127 -> Q15 0..~0.99
            chdep_q15.eq(ctrl.chdep << 8),
            echdep_q15.eq(ctrl.echodep << 8),
        ]
        # `edly` is a shift-add rather than `dtime_c * ECHO_STEP` for the same reason, one step
        # further along. A constant multiply of a 7-bit value ought to be a couple of LUTs, but
        # yosys hands it to a MULT18X18D, and this die has none to give: all 28 are in use (#6).
        # It also costs what every unregistered MULT18X18D here costs, 3.93 ns in a single cell,
        # and it sits mid-path between the CC82 clamp below and the echo line's write pointer.
        # On the seed sweep's best placement that whole path measured 17.76 ns and was the
        # longest in the design -- the critical path had left luna and landed here. ECHO_STEP is
        # 192 = 128 + 64, so two shifts and an add give the identical value with no DSP and no
        # extra cycle. The assert is the maintenance cost: change ECHO_STEP and the decomposition
        # above stops being that constant.
        assert ECHO_STEP == 128 + 64, "edly decomposes ECHO_STEP as (x<<7)+(x<<6); re-derive it"

        # `rvg` is registered, not wired, and that is a timing fix rather than a style choice.
        # `Array(RVG)[ctrl.rsize]` is a 4:1 mux over 15 bits sitting directly on the A input of
        # the comb-feedback MULT18X18D, so it lands at the *front* of the longest path in this
        # module and drags a LUT plus its inter-tile hop -- 1.18 ns measured -- in front of a
        # 3.93 ns multiplier. `rsize` is CC91: it changes at MIDI rate, and its effect does not
        # reach the output until a comb pointer has wrapped 1,215 samples later (see
        # test_fx.py's note on why the CC test cannot go through the DSP). One cycle of extra
        # latency on a knob like that is not observable by any means we have.
        m.d.sync += rvg.eq(Array(RVG)[ctrl.rsize])

        # CC82 still accepts its full 0..127 -- presets in the wild carry values the BRAM line
        # cannot reach, and rejecting them would mean editing every one. The tap is clamped here
        # instead, at the single point of use, so the init value cannot slip past it either. Left
        # unclamped, `dtime` 85..127 would compute past `max_echo` and `DelayLine`'s address mask
        # would fold it back to a *short* delay -- the one failure mode that sounds like a bug
        # rather than a limit.
        dtime_max = min(self.max_echo // ECHO_STEP - 1, 127)
        m.d.comb += dtime_c.eq(Mux(ctrl.dtime > dtime_max, dtime_max, ctrl.dtime))

        # --- chorus LFO ---------------------------------------------------------------------
        # Triangle in Q3 (1/8 sample) so the read can be linearly interpolated; an integer-only
        # tap jumps a whole sample as it sweeps and every jump is a click.
        lfo   = Signal(range(LFO_PERIOD))
        fold  = Signal(range(LFO_PERIOD))
        m.d.comb += fold.eq(Mux(lfo < LFO_PERIOD // 2, lfo, LFO_PERIOD - 1 - lfo))
        ctri  = [Signal(range(CH_SWEEP), name=f"ctri{c}") for c in range(2)]
        m.d.comb += [
            ctri[0].eq(fold >> 3),
            ctri[1].eq(CH_SWEEP - 1 - ctri[0]),    # anti-phase, for width
        ]
        ctap = [Signal(range(CH_BASE + CH_SWEEP), name=f"ctap{c}") for c in range(2)]
        m.d.comb += [ctap[c].eq(CH_BASE + ctri[c]) for c in range(2)]
        cti = [q[3:] for q in ctap]                # integer tap
        cfr = [q[0:3] for q in ctap]               # fraction, 0..7

        # --- per-sample state ---------------------------------------------------------------
        def pair(shape, name):
            return [Signal(shape, name=f"{name}{c}") for c in range(2)]

        raws  = Signal(signed(16))                 # mono dry
        echod = pair(signed(16), "echod")
        chs0  = pair(signed(16), "chs0")
        chint = pair(signed(16), "chint")
        echow = pair(signed(16), "echow")
        chw   = pair(signed(16), "chw")
        ecw   = pair(signed(16), "ecw")
        rwet  = pair(signed(16), "rwet")
        samp  = pair(signed(16), "samp")
        dry23 = pair(signed(16), "dry23")
        rin_r = Signal(signed(16))                 # reverb comb input (echo/chorus wet, /64)
        cwaddr = Signal(range(CH_WORDS))           # chorus ring write pointer

        # --- reverb tank state ----------------------------------------------------------------
        # 12 pointers and 8 damping registers per channel.
        #
        # These are rings, not arrays. Indexing 24 pointer registers by `ridx`/`chan` is a 24:1
        # mux over 11 bits plus a 24-way write decode, and the damping registers cost another
        # 16:1 over 16 bits -- together the largest combinational structure left in the design,
        # and on an ECP5 already at 86% before any of this existed, unaffordable. But the FSM
        # walks the regions in a fixed order, 0..11 for L then 0..11 for R, so the value it wants
        # is always the head of the ring: rotate by one as each region retires and after a full
        # pass every entry is back where it started, updated. A rotate is the flip-flops' own
        # clock enable and their neighbour's Q -- no mux, no decode, no LUT at all.
        cp   = [[Signal(11, name=f"cp{i}_{c}") for i in range(NREG)] for c in range(2)]
        dlp  = [[Signal(signed(16), name=f"dlp{i}_{c}") for i in range(NCOMB)] for c in range(2)]
        acc  = Signal(signed(20))                  # 8-comb running sum
        csr  = Signal(signed(16))                  # comb sum / 4 = all-pass chain input
        apy  = Signal(signed(16))                  # running all-pass carry
        revw = pair(signed(16), "revw")            # per-channel reverb wet, after 4 all-pass
        chan = Signal()                            # 0 = L, 1 = R
        ridx = Signal(range(NREG))
        drd2 = Signal(signed(16))
        nlp_r = Signal(signed(16))

        # Region base + rotating pointer for the region the FSM is on.
        cur_cp   = Signal(11)
        cur_base = Signal(range(TANK_WORDS))
        tank_addr = Signal(range(TANK_WORDS))
        m.d.comb += [
            cur_cp.eq(Mux(chan, cp[1][0], cp[0][0])),
            cur_base.eq(Array(REGION)[ridx]),
            tank_addr.eq(cur_base + cur_cp),
        ]
        cur_dlp = Signal(signed(16))
        m.d.comb += cur_dlp.eq(Mux(chan, dlp[1][0], dlp[0][0]))

        # Length of the region the FSM is on, and the pointer it will hold next sample. The Verilog
        # advances all 24 pointers together at the end of the sample (top.v:390-397); doing that
        # here costs 24 parallel 11-bit compare-and-increments, about 500 LUT4, which is the
        # difference between fitting on this die and not. Since the FSM already visits every region
        # exactly once per sample, one shared compare-and-increment written back through the region
        # decode is the same computation spread over the 96 cycles the tank was using anyway.
        cur_len = Signal(11)
        cp_next = Signal(11)
        m.d.comb += [
            cur_len.eq(Array(DELAYS)[ridx] + Mux(chan, SPREAD, 0)),
            cp_next.eq(Mux(cur_cp == cur_len - 1, 0, cur_cp + 1)),
        ]

        # --- three shared multipliers ----------------------------------------------------------
        # mul_a / mul_b are the L and R channel wet multipliers, time-shared between the echo
        # wet, the chorus wet and the reverb wet -- those three never fall in the same cycle.
        # mul_g is the comb feedback. Seven multiply sites in the Verilog, three here.
        mul_a_x, mul_a_y = Signal(signed(17)), Signal(signed(17))
        mul_b_x, mul_b_y = Signal(signed(17)), Signal(signed(17))
        mul_a = Signal(signed(34))
        mul_b = Signal(signed(34))
        mul_g = Signal(signed(34))
        m.d.comb += [
            mul_a.eq(mul_a_x * mul_a_y),
            mul_b.eq(mul_b_x * mul_b_y),
            mul_g.eq(Cat(rvg, C(0, 1)).as_signed() * nlp_r),
        ]

        # Damping: 0.5*old + 0.5*new (top.v:249). Feedback: g*y in Q15 (top.v:250).
        #
        # The Verilog rounds half up -- `(mul_g + 16384) >> 15` -- and that is what this line did
        # until the tank stopped reaching digital silence. Round-half-up gives the comb recurrence
        # a dead band: it has a fixed point wherever the rounding pulls the product back up to the
        # state it came from, i.e. wherever |v| * (32768 - g) <= 16384. At M29's cathedral g of
        # 31974 that is every |v| <= 20, so each of the eight combs parks its whole delay line on a
        # non-zero constant and never leaves. The sum came out of the wet multiplier as a steady
        # +206 DC, about -44 dBFS: not a ringing tail, not railing, just a floor the tank could not
        # get below. `stress_fx_tail` measured the same DC in both its windows and read the ratio
        # as "the tail is not decaying"; `stress_silence_recovery` saw it as a tail RMS of 92
        # against the 0 the Basys 3 returns. The Basys 3 has the same structure but g = 31200, so
        # its dead band is |v| <= 10 and it stays under both checkers' floors -- the halved tank
        # that pushed g from 0.952 to 0.976 is what made a latent artefact audible to the suite.
        #
        # Magnitude truncation -- round toward zero rather than to nearest -- removes it outright.
        # |g*v| < |v| for every g < 1, so truncating the magnitude guarantees each round trip
        # strictly shrinks the state and the only fixed point left is zero. It is the textbook
        # cure for fixed-point limit cycles, and the bias is at most 1 LSB per round trip against
        # a 16-bit state, so RT60 does not move.
        #
        # `>>` is already floor, which is truncation for positives; negatives want ceil, which is
        # `(x + 32767) >> 15`. So the whole correction is a sign-selected addend into the adder
        # that was there anyway -- the same shape as the +16384 it replaces, not extra depth.
        #
        # The select is driven from `nlp_r`, not from the product. `rvg` is always positive, so
        # sign(mul_g) == sign(nlp_r), and `nlp_r` is a register output that is stable long before
        # the multiplier settles. Deriving it from `mul_g` instead costs 5 MHz on a `sync` domain
        # that is already short (see the M25 note above): it serialises a 15-bit OR reduce behind
        # the multiply and drags the whole comb feedback onto the critical path.
        nlp = Signal(signed(16))
        fbm = Signal(signed(16))
        m.d.comb += [
            nlp.eq(cur_dlp + ((drd2 - cur_dlp + 1) >> 1)),
            fbm.eq((mul_g + Mux(nlp_r < 0, 32767, 0)) >> 15),
        ]
        cbn = sat16(m, rin_r + fbm, "cbn")

        # `cbn_r` buys the fourth tank phase, and it is the reason this module stopped being what
        # caps the board. Until M35 the comb feedback was one combinational cycle: the multiply,
        # the round-toward-zero shift, the `rin_r + fbm` saturating add and the 20-bit `acc`
        # chain all landed between the same pair of flops. nextpnr measured that at 21.49 ns --
        # `rsize -> rvg -> MULT18X18D -> fbm -> cbn -> acc -> csr`, 9.96 ns of it logic -- which
        # is 46.5 MHz, and once the USB skid buffer took luna's 22.11 ns cone out of the report
        # it was the worst path in the design. Splitting it at `cbn` leaves the multiply and its
        # two adders in one cycle and the accumulator in the next; neither half is close.
        #
        # The cost is 24 cycles per sample -- one per region per channel, ~90 -> ~114 of the
        # 1,250 the sample period has -- which is the same trade the three-phase split already
        # made at the top of this file. Nothing about the arithmetic changes: `nlp_r` is
        # registered in RVB-READ and `rin_r` once per sample, so `cbn` is settled and constant
        # for the whole of the new state, and RVB-WRITE sees the identical value one cycle later.
        cbn_r = Signal(signed(16))

        # All-pass stage input: the comb sum for stage 0, the previous stage's output after that.
        apin = Signal(signed(16))
        m.d.comb += apin.eq(Mux(ridx == NCOMB, csr, apy))
        apo  = sat16(m, apin + (drd2 >> 1), "apo")     # what goes back into the tank
        apnx = sat16(m, drd2 - (apin >> 1), "apnx")    # this stage's output

        # Chorus interpolation, top.v:257-266: chint = s0 + (s1-s0)*frac at quarter-sample
        # resolution via a shift-mux. Deliberately not a multiply -- it keeps the chorus off the
        # timing budget and out of the three DSPs.
        cdif  = pair(signed(17), "cdif")
        cble  = pair(signed(17), "cble")
        for c in range(2):
            m.d.comb += [
                cdif[c].eq(chor_rd[c].data - chs0[c]),
                cble[c].eq(Mux(cfr[c][1:3] == 0, 0,
                           Mux(cfr[c][1:3] == 1, cdif[c] >> 2,
                           Mux(cfr[c][1:3] == 2, cdif[c] >> 1,
                               (cdif[c] >> 1) + (cdif[c] >> 2))))),
            ]

        # Ping-pong echo write: L stores the dry plus half of what R just read (top.v:337-338).
        wr = [sat16(m, raws + Mux(echo_on, echod[1 - c] >> 1, 0), f"wr{c}") for c in range(2)]
        ecw_c = [sat16(m, raws + Mux(echo_on, echow[c], 0) + Mux(chorus_on, chw[c], 0),
                       f"ecw_c{c}") for c in range(2)]

        # --- output / passthrough -------------------------------------------------------------
        m.d.comb += [
            self.o.payload[0].as_value().eq(samp[0]),
            self.o.payload[1].as_value().eq(samp[1]),
            self.o.payload[2].as_value().eq(dry23[0]),
            self.o.payload[3].as_value().eq(dry23[1]),
        ]

        # Tank read/write addresses are driven combinationally from the region counter: the
        # address is presented in RVB-ADDR and the memory answers in RVB-READ -- the same "set
        # the address, then read" shape as the Basys 3 FSM, where the six `ce8` cycles between
        # steps hid the BRAM latency.
        for c in range(2):
            m.d.comb += [tank_rd[c].addr.eq(tank_addr), tank_wr[c].addr.eq(tank_addr)]
        m.d.comb += drd2.eq(Mux(chan, tank_rd[1].data, tank_rd[0].data))

        # Sticky per-line handshake flags: the two echo lines are independent wishbone masters
        # behind an arbiter, so they do not accept or answer on the same cycle. Every one of the
        # three stream rendezvous below waits for both before advancing.
        done = [Signal(name="done_l"), Signal(name="done_r")]

        with m.FSM(domain="sync"):

            with m.State("IDLE"):
                m.d.comb += self.i.ready.eq(1)
                with m.If(self.i.valid):
                    m.d.sync += [
                        raws.eq(self.i.payload[0].as_value().as_signed()),
                        dry23[0].eq(self.i.payload[2].as_value().as_signed()),
                        dry23[1].eq(self.i.payload[3].as_value().as_signed()),
                        lfo.eq(Mux(lfo == LFO_PERIOD - 1, 0, lfo + 1)),
                        done[0].eq(0), done[1].eq(0),
                    ]
                    m.next = "ECHO-REQ"

            # --- echo tap: PSRAM, variable delay ----------------------------------------------
            with m.State("ECHO-REQ"):
                fire = []
                for c in range(2):
                    m.d.comb += [tap[c].i.payload.eq(edly), tap[c].i.valid.eq(~done[c])]
                    ok = Signal(name=f"req_ok{c}")
                    m.d.comb += ok.eq(done[c] | tap[c].i.ready)
                    with m.If(tap[c].i.valid & tap[c].i.ready):
                        m.d.sync += done[c].eq(1)
                    fire.append(ok)
                with m.If(fire[0] & fire[1]):
                    m.d.sync += [done[0].eq(0), done[1].eq(0)]
                    m.next = "ECHO-RD"

            with m.State("ECHO-RD"):
                fire = []
                for c in range(2):
                    m.d.comb += tap[c].o.ready.eq(~done[c])
                    ok = Signal(name=f"rd_ok{c}")
                    m.d.comb += ok.eq(done[c] | tap[c].o.valid)
                    with m.If(tap[c].o.valid & tap[c].o.ready):
                        m.d.sync += [echod[c].eq(tap[c].o.payload.as_value().as_signed()),
                                     done[c].eq(1)]
                    fire.append(ok)
                with m.If(fire[0] & fire[1]):
                    m.d.sync += [done[0].eq(0), done[1].eq(0)]
                    m.next = "CHOR-A"

            # --- chorus: two adjacent taps out of the short BRAM ring -------------------------
            with m.State("CHOR-A"):
                for c in range(2):
                    m.d.comb += chor_rd[c].addr.eq(cwaddr - cti[c])
                m.next = "CHOR-B"

            with m.State("CHOR-B"):
                for c in range(2):
                    m.d.comb += chor_rd[c].addr.eq(cwaddr - cti[c] - 1)
                    m.d.sync += chs0[c].eq(chor_rd[c].data)          # s0, the nearer tap
                m.next = "CHOR-C"

            with m.State("CHOR-C"):
                # chor_rd[c].data is now s1; cdif/cble close over it.
                for c in range(2):
                    m.d.sync += chint[c].eq(chs0[c] + cble[c])
                m.next = "WET-ECHO"

            # --- wet levels: two shared multipliers, one pass each -----------------------------
            with m.State("WET-ECHO"):
                m.d.comb += [
                    mul_a_x.eq(echdep_q15), mul_a_y.eq(echod[0]),
                    mul_b_x.eq(echdep_q15), mul_b_y.eq(echod[1]),
                ]
                m.d.sync += [echow[0].eq(mul_a >> 15), echow[1].eq(mul_b >> 15)]
                m.next = "WET-CHOR"

            with m.State("WET-CHOR"):
                m.d.comb += [
                    mul_a_x.eq(chdep_q15), mul_a_y.eq(chint[0]),
                    mul_b_x.eq(chdep_q15), mul_b_y.eq(chint[1]),
                ]
                m.d.sync += [chw[0].eq(mul_a >> 15), chw[1].eq(mul_b >> 15)]
                m.next = "WRITE"

            # --- commit the history write, then branch to the reverb send ----------------------
            with m.State("WRITE"):
                fire = []
                for c in range(2):
                    m.d.comb += [delay[c].i.payload.as_value().eq(wr[c]),
                                 delay[c].i.valid.eq(~done[c])]
                    ok = Signal(name=f"wr_ok{c}")
                    m.d.comb += ok.eq(done[c] | delay[c].i.ready)
                    with m.If(delay[c].i.valid & delay[c].i.ready):
                        m.d.sync += done[c].eq(1)
                    fire.append(ok)
                with m.If(fire[0] & fire[1]):
                    for c in range(2):
                        m.d.comb += [chor_wr[c].addr.eq(cwaddr),
                                     chor_wr[c].data.eq(wr[c]),
                                     chor_wr[c].en.eq(1)]
                        m.d.sync += [ecw[c].eq(ecw_c[c]), samp[c].eq(ecw_c[c]),
                                     done[c].eq(0)]
                    # /64 so eight combs summing with g up to 0.95 cannot saturate.
                    m.d.sync += [cwaddr.eq(Mux(cwaddr == CH_WORDS - 1, 0, cwaddr + 1)),
                                 rin_r.eq((ecw_c[0] + ecw_c[1]) >> 6),
                                 chan.eq(0), ridx.eq(0), acc.eq(0)]
                    # The tank runs even when `revwet` is 0. It costs 96 of the 1,250 cycles in
                    # a sample period and `rwet` comes out as zero anyway, but it means the tank
                    # is primed when the wet knob comes up, instead of starting from whatever
                    # was frozen in it -- which is also what the Basys 3 does, since its FSM has
                    # no bypass branch either.
                    m.next = "RVB-ADDR"

            # --- the tank: 12 regions x 2 channels, four cycles each ----------------------------
            with m.State("RVB-ADDR"):
                # tank_addr is driven combinationally; this cycle exists only so the memory
                # output is settled by RVB-READ.
                m.next = "RVB-READ"

            with m.State("RVB-READ"):
                m.d.sync += nlp_r.eq(nlp)
                m.next = "RVB-FB"

            with m.State("RVB-FB"):
                # The comb feedback, alone in its own cycle: MULT18X18D, the round-toward-zero
                # shift and the saturating add. The all-pass regions pass through here too --
                # `cbn_r` is simply unused for them -- because a conditional transition would put
                # `ridx < NCOMB` on the FSM's next-state logic to save 4 idle cycles out of 1,250.
                m.d.sync += cbn_r.eq(cbn)
                m.next = "RVB-WRITE"

            with m.State("RVB-WRITE"):
                with m.If(ridx < NCOMB):
                    # comb: y = damp(tank); tank <= in + g*y
                    m.d.comb += [tank_wr[0].data.eq(cbn_r), tank_wr[1].data.eq(cbn_r),
                                 tank_wr[0].en.eq(~chan), tank_wr[1].en.eq(chan)]
                    # Retire this comb's damping register into the tail of its channel's ring.
                    for c in range(2):
                        with m.If(chan == c):
                            for i in range(NCOMB - 1):
                                m.d.sync += dlp[c][i].eq(dlp[c][i + 1])
                            m.d.sync += dlp[c][NCOMB - 1].eq(nlp_r)
                    m.d.sync += acc.eq(acc + cbn_r)
                    with m.If(ridx == NCOMB - 1):
                        m.d.sync += csr.eq(sat16(m, (acc + cbn_r) >> 2, "csr_n"))
                with m.Else():
                    # all-pass: y = tank - 0.5*in; tank <= in + 0.5*tank
                    m.d.comb += [tank_wr[0].data.eq(apo), tank_wr[1].data.eq(apo),
                                 tank_wr[0].en.eq(~chan), tank_wr[1].en.eq(chan)]
                    m.d.sync += apy.eq(apnx)
                    with m.If(ridx == NREG - 1):
                        m.d.sync += Array(revw)[chan].eq(apnx)

                # This region's pointer advances now, not at the end of the sample (top.v:390-397
                # does all 24 at once, which is 24 parallel 11-bit compare-and-increments). The
                # read and the write above both used `cur_cp`, so rotating it out here is the same
                # one-advance-per-region-per-sample -- computed once instead of 24 times.
                for c in range(2):
                    with m.If(chan == c):
                        for i in range(NREG - 1):
                            m.d.sync += cp[c][i].eq(cp[c][i + 1])
                        m.d.sync += cp[c][NREG - 1].eq(cp_next)

                with m.If(ridx == NREG - 1):
                    with m.If(chan == 0):
                        m.d.sync += [chan.eq(1), ridx.eq(0), acc.eq(0)]
                        m.next = "RVB-ADDR"
                    with m.Else():
                        m.next = "RVB-WET"
                with m.Else():
                    m.d.sync += ridx.eq(ridx + 1)
                    m.next = "RVB-ADDR"

            with m.State("RVB-WET"):
                m.d.comb += [
                    mul_a_x.eq(wetgn), mul_a_y.eq(revw[0]),
                    mul_b_x.eq(wetgn), mul_b_y.eq(revw[1]),
                ]
                m.d.sync += [rwet[0].eq(mul_a >> 15), rwet[1].eq(mul_b >> 15)]
                m.next = "RVB-MIX"

            with m.State("RVB-MIX"):
                for c in range(2):
                    m.d.sync += samp[c].eq(sat16(m, ecw[c] + rwet[c], f"mix{c}"))
                m.next = "OUT"

            with m.State("OUT"):
                m.d.comb += self.o.valid.eq(1)
                with m.If(self.o.ready):
                    m.next = "IDLE"

        return m
