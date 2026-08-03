# M23 — the XLS32 engine as a Tiliqua DSP core.
#
# The engine is the same `xls_engine` Verilog every board gets from core/codegen.sh; nothing
# board-specific leaks into the DSLX. This file is the whole Tiliqua-side shell: a boot patch,
# a clock-domain crossing, a sample-rate conversion, and the jack mapping.
#
# Three things are worth knowing before reading it.
#
# 1. The engine runs in the `audio` domain (12.288 MHz), not `sync` (60 MHz). M21/M22 measured
#    its Fmax at STAGES=12 as ~27.6 MHz, so 60 MHz is out of reach; 12.288 MHz has 2.2x margin.
#    That costs one CDC, because the eurorack-pmod's user-facing streams (`i_cal`/`o_cal`) are
#    in `sync` -- I2SCalibrator's `stream_domain` defaults to "sync" and EurorackPmod does not
#    expose it. So: engine in `audio`, AsyncFIFO, everything downstream in `sync`.
#
# 2. Nothing here generates a 32 kHz tick. The engine's sample rate is whatever its consumer
#    pulls at. `dsp.Resample` gates its input `ready` on the internal FIR, which stalls on
#    output backpressure, so the codec's 48 kHz demand propagates backwards through 3/2 and
#    lands on the engine as exactly 32 kHz on average -- phase-locked to the same mclk, with no
#    divider to drift. Free-running, the engine would emit 12.288e6/224 = 54.9 kHz, so it is
#    always the one waiting; the FIFO sits full and the pull sets the rate.
#
# 3. The engine's audio word is offset binary (`scale_mix` returns `(c + 32768) as u16`), while
#    ASQ is signed Q1.15. The conversion is an MSB inversion, and the 6 dB pad is one
#    arithmetic right shift.

import os

from amaranth import *
from amaranth.lib import data, stream, wiring
from amaranth.lib.fifo import AsyncFIFO
from amaranth.lib.wiring import In, Out

from tiliqua import dsp
from tiliqua.build.types import BitstreamHelp
from tiliqua.dsp import ASQ

# Engine sample rate. Set by the DSLX pitch tables, not by anything on this side: the phase
# increments in synth.x are computed for 32 kHz (Basys 3 divides 100 MHz by 3125 to match).
ENGINE_FS = 32000

# Boot patch, played into the engine's own MIDI parser at reset. M24 replaces this with the TRS
# and USB inputs; until then it is the only way to make the bitstream audible, and it doubles as
# the stimulus the pitch check compares against.
BOOT_MIDI = [
    0xB0,  74, 100,   # CC74 cutoff      -> 3900 (open; default is 3000)
    0xB0,  71,  40,   # CC71 resonance   -> 3000 damping, i.e. milder than the 2200 default
    0xB0,   7, 110,   # CC7  part volume
    0x90,  69, 100,   # note on, A4 -- 440 Hz is unambiguous in an FFT
]


class XlsSynth(wiring.Component):

    """
    XLS32: a 32-voice subtractive synthesizer compiled from DSLX by Google XLS.

    M23 is audio-only. A fixed boot patch plays one note; there is no MIDI input and no
    effects yet. The engine is mono, so out0 and out1 carry the same signal.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    bitstream_help = BitstreamHelp(
        brief="XLS32 synth (audio only, fixed note)",
        io_left=['', '', '', '', 'synth out', 'synth out', '', ''],
        io_right=['', '', '', '', '', ''],
    )

    def __init__(self, engine_path=None):
        # Default to what boards/tiliqua/build.sh stages; XLS_ENGINE_V overrides for one-offs.
        if engine_path is None:
            repo = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))))
            engine_path = os.environ.get(
                "XLS_ENGINE_V", os.path.join(repo, "build", "tiliqua", "engine.v"))
        self.engine_path = engine_path
        # Observation points for the Verilator harness. Created here rather than in elaborate()
        # because top_level_cli asks for the simulation ports before the design is elaborated.
        # Three counters localise a stall immediately: no engine samples means the boot ROM or
        # the proc is stuck, engine samples but no resampler output means the CDC or the FIR is
        # stuck, resampler output but no codec writes means the pmod side is.
        self.dbg_rom   = Signal(8)
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

        # --- MIDI boot ROM (audio domain) ------------------------------------------------
        rom     = Array([Const(b, unsigned(8)) for b in BOOT_MIDI])
        rom_idx = Signal(range(len(BOOT_MIDI) + 1))
        midi_b  = Signal(8)
        midi_v  = Signal()
        midi_r  = Signal()
        m.d.comb += [
            midi_b.eq(rom[rom_idx]),
            midi_v.eq(rom_idx < len(BOOT_MIDI)),
        ]
        with m.If(midi_v & midi_r):
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
        # FSM, which M23 does not port. out0 and out1 get the same signal; out2/out3 stay at 0.
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
        with m.If(audio_v & audio_r):
            m.d.audio += self.dbg_eng.eq(self.dbg_eng + 1)
        with m.If(resample.i.valid & resample.i.ready):
            m.d.sync += self.dbg_res.eq(self.dbg_res + 1)
        with m.If(self.o.valid & self.o.ready):
            m.d.sync += self.dbg_out.eq(self.dbg_out + 1)

        return m
