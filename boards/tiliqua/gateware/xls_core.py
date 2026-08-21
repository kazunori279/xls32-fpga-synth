# M24 — the XLS32 engine as a Tiliqua DSP core.
#
# The engine is the same `xls_engine` Verilog every board gets from core/codegen.sh; nothing
# board-specific leaks into the DSLX. This file is the whole Tiliqua-side shell: a boot patch,
# two clock-domain crossings, a sample-rate conversion, and the jack mapping.
#
# Four things are worth knowing before reading it.
#
# 1. The engine runs in the `audio` domain (12.288 MHz), not `sync` (60 MHz). M21/M22 measured
#    its Fmax at STAGES=12 as ~27.6 MHz, so 60 MHz is out of reach; 12.288 MHz has 2.2x margin.
#    That costs a CDC on each side, because the eurorack-pmod's user-facing streams (`i_cal` /
#    `o_cal`) are in `sync` -- I2SCalibrator's `stream_domain` defaults to "sync" and
#    EurorackPmod does not expose it -- and so is the MIDI UART. So: engine in `audio`, an
#    AsyncFIFO at each boundary, everything else in `sync`.
#
# 2. Nothing here generates a 32 kHz tick. The engine's sample rate is whatever its consumer
#    pulls at. `dsp.Resample` gates its input `ready` on the internal FIR, which stalls on
#    output backpressure (filters.py's FIR waits in WAIT-READY on `o.ready`), and `i_cal` is a
#    real FIFO whose `w_rdy` drops when the codec is behind. So the codec's demand propagates
#    backwards through 3/2 and lands on the engine as exactly 2/3 of the codec's frame rate --
#    phase-locked to the same mclk, with no divider to drift. The engine is always the one
#    waiting; the FIFO sits full and the pull sets the rate. M25 verified this end to end on
#    hardware with the frame counter on USB channel 3.
#
#    The corollary is that *the whole tuning of the instrument is the audio clock*. There is no
#    divider anywhere to notice a wrong one. `clk0` on the SI5351 has to be 12.288 MHz, giving
#    a 48 kHz codec and 32 kHz at the engine, which is what synth.x's pitch table assumes. No
#    bitstream sets it -- only the bootloader does, over I2C -- so an SRAM load made while the
#    module is running some other slot inherits that slot's rate silently and every note comes
#    out sharp by the ratio. That cost most of a day in M25 (49.152 MHz left behind by XBEAM:
#    4x). The module has to be in the bootloader when the load happens, which a power cycle
#    alone does not achieve; see boards/tiliqua/board.py. USB channel 2 now carries an
#    `audio`-cycle counter so check_loop.py can measure the clock and say so instead of
#    reporting a mysterious pitch error.
#
# 3. The engine's audio word is offset binary (`scale_mix` returns `(c + 32768) as u16`), while
#    ASQ is signed Q1.15. The conversion is an MSB inversion, and the 6 dB pad is one
#    arithmetic right shift.
#
# 4. MIDI bytes arrive raw, not decoded. The engine has its own parser in DSLX, so the SDK's
#    MidiDecodeSerial would be pure loss -- top.py feeds `i_midi_bytes` straight from
#    midi.SerialRx. What it does have to strip first is System messages; see midi_filter.py.

import os

from amaranth import *
from amaranth.lib import data, stream, wiring
from amaranth.lib.fifo import AsyncFIFO
from amaranth.lib.wiring import In, Out

from tiliqua import dsp
from tiliqua.build.types import BitstreamHelp
from tiliqua.dsp import ASQ

from voices import N_VOICE

# Engine sample rate. Set by the DSLX pitch tables, not by anything on this side: the phase
# increments in synth.x are computed for 32 kHz (Basys 3 divides 100 MHz by 3125 to match).
ENGINE_FS = 32000

# Boot patch, played into the engine's own MIDI parser at reset. Since M24 this is CCs only --
# no note-on -- so the module comes up silent and sounds when you play it. The CCs are still
# worth sending: they land every part somewhere more musical than the DSLX defaults, so a
# keyboard plugged into a fresh boot makes a reasonable noise without touching a knob.
#
# Broadcast on all four channels, because the engine takes the channel nibble's low 2 bits as
# the part (synth.x:337) and a CC on channel 1 alone would leave parts 2-4 at their defaults.
BOOT_CC = [
    (74, 100),   # cutoff     -> 3900 (open; default is 3000)
    (71,  40),   # resonance  -> 3000 damping, i.e. milder than the 2200 default
    ( 7, 110),   # part volume
]
BOOT_MIDI = [b for ch in range(4) for cc, v in BOOT_CC for b in (0xB0 | ch, cc, v)]


class XlsSynth(wiring.Component):

    """
    XLS32: a subtractive synthesizer compiled from DSLX by Google XLS. 24 voices as shipped,
    or 32 in the experimental build; `$VOICES` picks, and the engine's sample rate does not
    change with it because the codec's backpressure sets the pace, not the ring's length.

    The name is the project's, not the voice count -- it has meant "32-bit synthesizer in XLS"
    since M1, and the count has been 8, 16, 24 and 32 under it. `voices.N_VOICE` is the count,
    and the `brief` below prints it so the bootloader's slot list says which build this is.

    Multitimbral over MIDI channels 1-4, played through `i_midi_bytes` -- a raw byte stream,
    not decoded messages. The engine is mono, so out0 and out1 carry the same signal; the
    chorus, echo and reverb that make them a stereo pair live downstream in `fx.StereoFx`,
    outside this component, exactly as they do on the Basys 3.

    `i_midi_bytes`, `i` and `o` are all in `sync`; the engine's own domain stays inside.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    i_midi_bytes: In(stream.Signature(unsigned(8)))
    # M29: the raw `viz_out` tuple and its strobe, in the engine's own `audio` domain rather than
    # `sync`. Exposed rather than consumed here because its reader -- viz.VizStore -- ends in the
    # *pixel* clock, and the crossing it wants is a dual-port BRAM in the video block, not a
    # synchroniser here.
    o_viz:       Out(32)
    o_viz_valid: Out(1)

    # What the bootloader prints beside the module when you highlight this slot. `io_left` is the
    # eight eurorack jacks in port order -- in0..in3 then out0..out3 -- and `io_right` is the rest
    # of the panel: encoder, `usb2`, video, then the TRS MIDI jack last. The SDK's own
    # src/top/usb_audio/top.py is the reference for both orderings. An empty string means the jack
    # is unused, which is why in0..in3 and out2/out3 are blank: nothing reads the ADC (M28's CV
    # variant that did was deleted in M31) and out2/out3 have carried silence since M26.
    bitstream_help = BitstreamHelp(
        # 64 bytes is a hard cap -- BitstreamHelp raises above it, which is why the channel list
        # lost its parentheses to make room for the voice count. 62 here, 63 if a future ladder
        # rung is three digits.
        brief=f"XLS32 synth, {N_VOICE} voices: MIDI ch 1-4 over TRS or USB, audio out",
        io_left=['', '', '', '', 'out L', 'out R', '', ''],
        io_right=['', 'USB MIDI + audio', 'video out', '', '', 'TRS MIDI in'],
    )

    def __init__(self, engine_path=None, viz=False):
        # `viz` exports the `viz_out` tap for M29's tile renderer. A flag rather than an
        # always-driven output because the tuple is 32 bits carried the length of a 12-stage
        # pipeline: left dangling it is dead logic that yosys is *expected* to prune, and relying
        # on that is how a build with 181 spare cells acquires a mysterious regression. Tied off,
        # there is nothing to prune. The Verilator flow builds without video and takes that path.
        self.viz = viz
        # Default to what boards/tiliqua/build.sh stages; XLS_ENGINE_V overrides for one-offs.
        if engine_path is None:
            repo = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))))
            engine_path = os.environ.get(
                "XLS_ENGINE_V", os.path.join(repo, "build", "tiliqua", "engine.v"))
        self.engine_path = engine_path
        # Observation points for the Verilator harness. Created here rather than in elaborate()
        # because top_level_cli asks for the simulation ports before the design is elaborated.
        # The counters localise a stall in one run: no MIDI bytes means the UART or its filters
        # ate them, no engine samples means the boot ROM or the proc is stuck, engine samples but
        # no resampler output means the CDC or the FIR is stuck, resampler output but no codec
        # writes means the pmod side is.
        self.dbg_rom   = Signal(8)
        self.dbg_midi  = Signal(32)
        self.dbg_eng   = Signal(32)
        self.dbg_res   = Signal(32)
        self.dbg_out   = Signal(32)
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        # The engine is passed by *contents*, not path: yowasp yosys runs under WASI and can
        # only see files beneath its working directory, and the Verilator flow copies added
        # files into its own build dir. Both platforms take contents (see src/top/sid/top.py).
        with open(self.engine_path) as f:
            platform.add_file("xls_engine.v", f.read())

        # --- MIDI in: sync domain -> audio domain ------------------------------------------
        # Byte-wide twin of the audio CDC below. The UART lives in `sync` because 60 MHz /
        # 31250 = 1920 exactly -- zero baud error, where the audio domain would carry +0.055%.
        # Depth 4 is generous: a MIDI byte takes 320 us, the engine drains one per cycle.
        m.submodules.midi_cdc = midi_cdc = AsyncFIFO(
            width=8, depth=4, w_domain="sync", r_domain="audio")
        m.d.comb += [
            midi_cdc.w_data.eq(self.i_midi_bytes.payload),
            midi_cdc.w_en.eq(self.i_midi_bytes.valid),
            self.i_midi_bytes.ready.eq(midi_cdc.w_rdy),
        ]

        # --- MIDI boot ROM (audio domain), then the wire ------------------------------------
        # The ROM has absolute priority until it is empty, which takes ~36 audio cycles -- 3 us,
        # long finished before the first start bit of anything a player could send.
        rom      = Array([Const(b, unsigned(8)) for b in BOOT_MIDI])
        rom_idx  = Signal(range(len(BOOT_MIDI) + 1))
        rom_busy = Signal()
        midi_b   = Signal(8)
        midi_v   = Signal()
        midi_r   = Signal()
        m.d.comb += [
            rom_busy.eq(rom_idx < len(BOOT_MIDI)),
            midi_b.eq(Mux(rom_busy, rom[rom_idx], midi_cdc.r_data)),
            midi_v.eq(Mux(rom_busy, 1, midi_cdc.r_rdy)),
            midi_cdc.r_en.eq(~rom_busy & midi_cdc.r_rdy & midi_r),
        ]
        with m.If(rom_busy & midi_r):
            m.d.audio += rom_idx.eq(rom_idx + 1)

        # --- the engine ------------------------------------------------------------------
        audio_o   = Signal(16)      # offset binary
        audio_v   = Signal()
        audio_r   = Signal()
        viz_o     = Signal(32)
        viz_v     = Signal()

        m.submodules.engine = Instance(
            "xls_engine",
            i_clk = ClockSignal("audio"),
            i_rst = ResetSignal("audio"),
            i_ce  = C(1),
            i__midi_in     = midi_b,
            i__midi_in_vld = midi_v,
            o__midi_in_rdy = midi_r,
            o__audio_out     = audio_o,
            o__audio_out_vld = audio_v,
            i__audio_out_rdy = audio_r,
            o__viz_out     = viz_o,
            o__viz_out_vld = viz_v,
            i__viz_out_rdy = C(1),   # unused, but the proc deadlocks if it is never drained
        )

        if self.viz:
            m.d.comb += [self.o_viz.eq(viz_o), self.o_viz_valid.eq(viz_v)]

        # Offset binary -> signed, then >>1 for the 6 dB pad. ASQ full scale is +-8.192 V, so
        # halving lands the output at +-4.1 V, inside normal Eurorack audio range.
        signed_o = Signal(signed(16))
        padded   = Signal(signed(16))
        m.d.comb += [
            signed_o.eq(Cat(audio_o[:15], ~audio_o[15]).as_signed()),
            padded.eq(signed_o >> 1),
        ]

        # --- audio domain -> sync domain -------------------------------------------------
        # Depth 8: the FIR consumes 2 inputs per 3 outputs in bursts rather than evenly, and a
        # deeper queue keeps the engine free-running through them instead of restarting.
        m.submodules.cdc = cdc = AsyncFIFO(
            width=16, depth=8, w_domain="audio", r_domain="sync")
        m.d.comb += [
            cdc.w_data.eq(padded),
            cdc.w_en.eq(audio_v),
            audio_r.eq(cdc.w_rdy),
        ]

        # --- 32 kHz -> 48 kHz -------------------------------------------------------------
        m.submodules.resample = resample = dsp.Resample(
            fs_in=ENGINE_FS, n_up=3, m_down=2)
        m.d.comb += [
            resample.i.payload.as_value().eq(cdc.r_data),
            resample.i.valid.eq(cdc.r_rdy),
            cdc.r_en.eq(cdc.r_rdy & resample.i.ready),
        ]

        # --- jacks --------------------------------------------------------------------------
        # The engine is mono (one `_audio_out`); the Basys 3 stereo pair comes from its effects
        # FSM, which M26 ports into `fx.StereoFx` downstream of here. Both channels get the same
        # signal at this point and the effects split them; out2/out3 stay at 0.
        m.d.comb += [
            self.o.payload[0].as_value().eq(resample.o.payload.as_value()),
            self.o.payload[1].as_value().eq(resample.o.payload.as_value()),
            self.o.valid.eq(resample.o.valid),
            resample.o.ready.eq(self.o.ready),
            # Inputs are unused, but the pmod's ADC FIFO must not be allowed to back up.
            self.i.ready.eq(1),
        ]

        # --- observation ------------------------------------------------------------------
        m.d.comb += self.dbg_rom.eq(rom_idx)
        with m.If(self.i_midi_bytes.valid & self.i_midi_bytes.ready):
            m.d.sync += self.dbg_midi.eq(self.dbg_midi + 1)
        with m.If(audio_v & audio_r):
            m.d.audio += self.dbg_eng.eq(self.dbg_eng + 1)
        with m.If(resample.i.valid & resample.i.ready):
            m.d.sync += self.dbg_res.eq(self.dbg_res + 1)
        with m.If(self.o.valid & self.o.ready):
            m.d.sync += self.dbg_out.eq(self.dbg_out + 1)

        return m
