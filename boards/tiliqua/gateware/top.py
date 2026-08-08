#!/usr/bin/env python3
# M25 — Tiliqua top level for the XLS32 engine.
#
# Modelled on the SDK's src/top/dsp/top.py CoreTop, minus PSRAM and video: this bitstream needs
# the codec, the clocks, the TRS MIDI-In jack, a reboot button, and — since M25 — a USB device on
# `usb2` carrying MIDI down and audio up. Build it through boards/tiliqua/build.sh, which stages
# the engine Verilog and points the SDK's toolchain variables at yowasp.
#
# The MIDI chain here is deliberately *not* the SDK's. src/top/dsp/top.py:1122 auto-wires
# SerialRx -> MidiDecodeSerial -> core.i_midi for any core exposing `i_midi`, handing the core
# decoded MidiMessage structs. The XLS engine parses MIDI itself, in DSLX, so it takes raw bytes
# and this file names the port `i_midi_bytes` precisely so that auto-wiring does not fire.
#
# Everything USB is behind `sim.is_hw`. The Verilator flow has no ULPI to talk to, and keeping it
# out of simulation means M23's check_pitch.py and M24's check_midi.py stay exactly the regression
# guards they were.

import os
import sys

from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.fifo import AsyncFIFO, SyncFIFO

from tiliqua import midi
from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.periph import eurorack_pmod
from tiliqua.platform import RebootProvider
from tiliqua.video import dvi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cvin import CvIn, CvTestRamp
from fx import StereoFx
from midi_arb import MidiArbiter, MidiPartSelect
from midi_filter import SysCommonFilter
from usb_iface import XlsUsbInterface
from viz import VizStore, VoiceTiles
from xls_core import XlsSynth

# The panel's own EDID asks for this one, and the bootloader reads it and programs the SI5351
# accordingly on every cold boot -- so a bitstream built against it inherits a live pixel clock
# without writing anything to flash. See docs/TILIQUA_PORT.md.
MODELINE = "720x720p60r2"


class CoreTop(Elaboratable):

    # M28 · two bitstreams, because the effects and the jacks do not fit in one.
    #
    # This was measured, not assumed. M26 closed at 23,800 of 24,288 TRELLIS_COMB -- 488 cells of
    # headroom on the whole die -- and CvIn plus the arbiter cost 639, so the first full build
    # after M28's steps 1-2 landed at **24,848 (102%)** and nextpnr refused to place it. A
    # per-block census of `top.json` says where the die actually went:
    #
    #     core 17,675 (70.5%) | usbif 2,440 | fx 2,398 | pmod0 1,000 | cvin 537 | arb 102
    #
    # and it rules out shrinking our way out: even deleting all of M28 only just clears the
    # overrun, and the engine has no soft area (§2.6 -- XLS unrolls the voice loop into a flat
    # register file, so there is no BRAM win hiding in it). Dropping `usbif` would free plenty and
    # is exactly wrong, because M28's exit criterion is graded by FFT over the USB tee.
    #
    # So the split is along `fx`, and it is the fallback docs/TILIQUA_PORT.md:1042 already named:
    #
    #     variant "fx" (default) -- effects + USB, no jacks.  What M26/M27 shipped.
    #     variant "cv"           -- CV/gate in + USB, effects bypassed.  M28's instrument.
    #
    # Dropping the effects for the CV bitstream costs nothing the measurement wants: an FFT of a
    # 1 V/oct sweep grades a dry oscillator, and reverb on the graded signal would be noise in the
    # literal sense. The Tiliqua bootloader holds eight user slots (§1.1) and this spends a second
    # one, which is what they are for.
    def __init__(self, clock_settings):
        self.cv = os.environ.get("XLS32_VARIANT", "fx") == "cv"
        # M29. Both bitstreams get video. It began as CV-only, because fx was at 98.9% of the die
        # and could not afford the ~800 LUTs; moving the echo out of PSRAM into BRAM freed
        # `psram_periph` and its DDR physical layer, which is what paid for it. The user-visible
        # consequence is that effects and picture are no longer mutually exclusive, which is the
        # whole reason the tank was halved -- see the effects block in elaborate().
        #
        # Still guarded on the modeline rather than assumed, because the video block is
        # unbuildable without one: it is where the timings come from, and DVIPHY needs a `dvi5x`
        # domain that the clock generator only creates when `clock_settings` names a resolution
        # (pll.py:353).
        self.video = clock_settings.modeline is not None
        self.core = XlsSynth(led=self.cv, viz=self.video)
        self.clock_settings = clock_settings
        self.pmod0 = eurorack_pmod.EurorackPmod(clock_settings.audio_clock)
        self.bitstream_help = self.core.bitstream_help
        # In `sync`, where 60 MHz / 31250 baud = 1920 exactly -- no baud error at all. Built here
        # rather than in elaborate() so simulation_ports() can reach `.phy.i` before elaboration.
        # pins=None leaves that input free: on hardware it is synchronised from the jack below,
        # in simulation it becomes a top-level port the C++ harness bit-bangs.
        #
        # rx_depth is 8, not the SDK default of 64. The engine drains a byte in one audio cycle
        # and the wire delivers one every 320 us, so 64 bytes of elasticity buys nothing; see the
        # area note in DEVELOPMENT.md for what it was costing.
        self.serialrx = midi.SerialRx(
            system_clk_hz=clock_settings.frequencies.sync, pins=None, rx_depth=8)
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        m.submodules.pmod0 = pmod0 = self.pmod0
        if sim.is_hw(platform):
            m.submodules.car = platform.clock_domain_generator(self.clock_settings)
            m.submodules.provider = provider = eurorack_pmod.FFCProvider()
            wiring.connect(m, pmod0.pins, provider.pins)
            m.submodules.reboot = reboot = RebootProvider(
                    self.clock_settings.frequencies.sync)
            m.submodules.btn = FFSynchronizer(
                    platform.request("encoder").s.i, reboot.button)
            m.d.comb += pmod0.codec_mute.eq(reboot.mute)
        else:
            m.submodules.car = sim.FakeTiliquaDomainGenerator()

        m.submodules.core = self.core

        # --- M26 effects ------------------------------------------------------------------
        # Chorus, ping-pong echo and Freeverb sit between the engine and the jacks, which is the
        # same place they occupy on Basys 3 -- the XLS engine has no effects code in it, on
        # either board. This is also what finally makes out0/out1 a stereo pair; the engine is
        # mono and until now both jacks carried the same signal.
        #
        # Every delay line here is BRAM, including the echo, and that is M29's doing. Through M28
        # the echo was a 32,768-word PSRAM line; video needed ~800 LUTs that this variant did not
        # have, and `psram_periph` plus its DDR physical layer was the only block big enough to
        # give up. So the echo moved inboard to 16,384 words, the reverb tank was halved to make
        # the BRAM for it, and RVG rose to hold RT60 against the shorter round trip. What that
        # costs is the top of CC82: 340 ms instead of 512. See the header of fx.py.
        #
        # A side effect worth having: hardware and simulation now run the *same* delay line.
        # sim_xls_core.cpp never had a HyperRAM model, so until now test_fx.py's sample-for-sample
        # agreement with fx_model was proving an SRAM build that hardware did not quite run.
        #
        # `dry` is whatever ends up feeding the jacks -- the effects output, or the engine itself
        # in the CV variant. Everything downstream (the codec, the USB tee) reads it through this
        # name so the two bitstreams differ in one place rather than four.
        wiring.connect(m, pmod0.o_cal, self.core.i)
        fx = ramp = None
        if self.cv:
            dry = self.core.o
            # Built here so `pmod0.i_cal` can be assigned below; its MIDI input is a sniffer and
            # gets attached further down, once the filter chain that feeds it exists.
            m.submodules.ramp = ramp = CvTestRamp()
        else:
            m.submodules.fx = fx = StereoFx(psram=False)
            wiring.connect(m, self.core.o, fx.i)
            dry = fx.o
        if ramp is None:
            wiring.connect(m, dry, pmod0.i_cal)
        else:
            # The same connection, by hand, so channel 2 can carry the self-test level instead of
            # the engine's empty third channel. out3 is left as it was: an unused output is a
            # useful thing to still have when the next measurement needs one.
            m.d.comb += [
                pmod0.i_cal.valid.eq(dry.valid),
                dry.ready.eq(pmod0.i_cal.ready),
                pmod0.i_cal.payload[0].eq(dry.payload[0]),
                pmod0.i_cal.payload[1].eq(dry.payload[1]),
                pmod0.i_cal.payload[2].as_value().eq(ramp.o_level),
                pmod0.i_cal.payload[3].eq(dry.payload[3]),
            ]

        # --- M28: the eight LEDs -------------------------------------------------------------
        # Taking all eight for the comet costs the pmod's automatic mode, which shows the four
        # input levels on 0-3 and the four output levels on 4-7. That is a fair trade *in this
        # variant and only here*: the comet head advances on every note CvIn strikes, so during a
        # check_cv.py sweep it is a live readout of the CV path working, where the automatic mode
        # would show a DC level sitting still. The fx variant keeps the automatic mode, and gets
        # no comet, because it has no room for one.
        if self.cv:
            m.d.comb += pmod0.led_mode.eq(0)
            for n in range(8):
                m.d.comb += pmod0.led[n].eq(self.core.o_led[n])

        # --- TRS MIDI in ------------------------------------------------------------------
        # The jack is optoisolated and idles high, so the synchroniser resets to 1: a reset that
        # released the line low would look like a start bit and cost one framing error.
        m.submodules.serialrx = serialrx = self.serialrx
        if sim.is_hw(platform):
            m.submodules.midi_sync = FFSynchronizer(
                platform.request("midi").rx.i, serialrx.phy.i, init=1)

        # Everything the engine's DSLX parser cannot survive, removed before it gets there:
        # synth.x:114 latches *any* byte >= 0x80 as running status, so one Active Sensing or
        # MIDI Clock byte corrupts the message in flight and then eats up to two more.
        m.submodules.rt_filter = rt_filter = midi.MidiRTFilter()
        m.submodules.sysex_filter = sysex_filter = midi.MidiSysexFilter()
        m.submodules.common_filter = common_filter = SysCommonFilter()
        wiring.connect(m, rt_filter.o, sysex_filter.i)
        wiring.connect(m, sysex_filter.o, common_filter.i)
        wiring.connect(m, common_filter.o, self.core.i_midi_bytes)

        # The effects sniff the same bytes the engine parses, for CC82/91/93/94/95. This is a
        # pure observation, not a second consumer: `FxControl.i.ready` is tied high, so the
        # sniffer can never stall the MIDI path -- which rules out the failure the SDK's usual
        # answer (a SyncFIFO that drops on full) exists to prevent, and costs nothing to do.
        # A byte is taken on the cycle the engine accepts it, so the two see the same stream.
        for snoop in (fx.i_midi_bytes if fx is not None else None,
                      ramp.i_midi if ramp is not None else None):
            if snoop is not None:
                m.d.comb += [
                    snoop.payload.eq(common_filter.o.payload),
                    snoop.valid.eq(common_filter.o.valid & common_filter.o.ready),
                ]

        # --- M28: the input jacks become another MIDI source ------------------------------
        # Sniffed off `pmod0.o_cal` rather than consumed from it. `core.i` is already the consumer
        # and ties `ready` high (xls_core.py:219), so a second handshake would be a second claim on
        # the same bytes; this takes a copy on the cycle the engine takes one, like the fx sniffer.
        n_src = 1 + int(self.cv) + int(sim.is_hw(platform))
        # The arbiter is in both variants even though only the CV one has three sources, because
        # the two-way mux it replaces was already wrong: `top.py` used to admit that playing USB
        # and TRS at once "interleaves bytes mid-message and is not supported". It costs 102 cells
        # to stop being true. See midi_arb.py -- round-robin, message-atomic, and it expands each
        # source's running status so what reaches the engine is always self-describing.
        m.submodules.arb = arb = MidiArbiter(n_src)
        wiring.connect(m, arb.o, rt_filter.i)
        wiring.connect(m, serialrx.o, arb.i[0])
        if self.cv:
            m.submodules.cvin = cvin = CvIn()
            m.d.comb += [
                cvin.i_cv.eq(pmod0.o_cal.payload),
                cvin.i_strobe.eq(pmod0.o_cal.valid & pmod0.o_cal.ready),
                cvin.jack.eq(pmod0.jack),
            ]
            wiring.connect(m, cvin.o_midi, arb.i[1])

        if not sim.is_hw(platform):
            return m
        usb_src = arb.i[n_src - 1]

        # --- M29: the 32 voices as 32 tiles ------------------------------------------------
        # Hardware only, and deliberately: the Verilator harness has no display and no `dvi5x`,
        # and sim_xls_core.cpp is still the M23/M24 regression guard. Keeping video out of it
        # leaves check_pitch.py and check_midi.py measuring exactly what they measured before.
        #
        # The pixel clock comes from outside the FPGA -- si5351 clk1 -> the second ECP5 PLL
        # (pll.py:275) -- and *the bootloader has already programmed it* by the time a JTAG-loaded
        # bitstream runs, from the panel's EDID. That is why there is no manifest work here and no
        # flash write: this variant inherits a 39.07 MHz clk1 the same way it already inherits the
        # 12.288 MHz clk0 that clocks the codec.
        if self.video:
            m.submodules.dvi_tgen = dvi_tgen = dvi.DVITimingGen()
            for member in dvi_tgen.timings.signature.members:
                m.d.comb += getattr(dvi_tgen.timings, member).eq(
                    getattr(self.clock_settings.modeline, member))

            # The store spans the two clocks; the renderer is entirely inside the pixel one.
            m.submodules.viz_store = store = VizStore()
            m.submodules.tiles = tiles = DomainRenamer("dvi")(VoiceTiles())
            m.d.comb += [
                store.i_viz.eq(self.core.o_viz),
                store.i_strobe.eq(self.core.o_viz_valid),
                store.i_addr.eq(tiles.o_addr),
                tiles.i_level.eq(store.o_level),
                tiles.i_note.eq(store.o_note),
                tiles.i_x.eq(dvi_tgen.x),
                tiles.i_y.eq(dvi_tgen.y),
                # The inverted copies, because these go straight out of the connector.
                tiles.i_de.eq(dvi_tgen.ctrl_phy.de),
                tiles.i_hsync.eq(dvi_tgen.ctrl_phy.hsync),
                tiles.i_vsync.eq(dvi_tgen.ctrl_phy.vsync),
            ]

            # One register between the renderer and the TMDS encoders, on all six signals at once
            # so nothing skews. This is what top/beamrace/top.py:413 does, and the colour path
            # arrives here out of a mux, which is the one worth registering.
            m.submodules.dvi_gen = dvi_gen = dvi.DVIPHY()
            m.d.dvi += [
                dvi_gen.i.r.eq(tiles.o_r),
                dvi_gen.i.g.eq(tiles.o_g),
                dvi_gen.i.b.eq(tiles.o_b),
                dvi_gen.i.de.eq(tiles.o_de),
                dvi_gen.i.hsync.eq(tiles.o_hsync),
                dvi_gen.i.vsync.eq(tiles.o_vsync),
            ]

        # --- USB: MIDI down, audio up -----------------------------------------------------
        m.submodules.usbif = usbif = XlsUsbInterface(
            audio_clock=self.clock_settings.audio_clock, nr_channels=4)

        # `usb` and `sync` are both 60 MHz but they are separate domains -- `usb` is recovered
        # from the ULPI's own 60 MHz clock -- so the bytes need a real crossing. Depth 4 matches
        # the engine-side CDC in xls_core.py, and for the same reason: MIDI is slow and the
        # consumer drains a byte per cycle.
        m.submodules.usb_midi_cdc = usb_midi_cdc = AsyncFIFO(
            width=8, depth=4, w_domain="usb", r_domain="sync")
        m.d.comb += [
            usb_midi_cdc.w_data.eq(usbif.o_midi.payload),
            usb_midi_cdc.w_en.eq(usbif.o_midi.valid),
            usbif.o_midi.ready.eq(usb_midi_cdc.w_rdy),
        ]

        # The FIFO's read side is not a stream, so this is the handshake by hand. USB takes the
        # last index and carries no less weight for it -- the arbiter rotates -- which is the
        # point: under the old mux a preset census streaming over USB held the bus for its
        # whole run.
        m.d.comb += [
            usb_src.payload.eq(usb_midi_cdc.r_data),
            usb_src.valid.eq(usb_midi_cdc.r_rdy),
            usb_midi_cdc.r_en.eq(usb_midi_cdc.r_rdy & usb_src.ready),
        ]

        # --- The TRS keyboard follows the web UI's PART selection ---------------------------
        # Sniffed off the USB side, not the merged stream, so the keyboard cannot retarget itself.
        # Off until the UI says otherwise; source 0 is the TRS jack.
        m.submodules.partsel = partsel = MidiPartSelect()
        m.d.comb += [
            partsel.i_midi.payload.eq(usb_src.payload),
            partsel.i_midi.valid.eq(usb_src.valid & usb_src.ready),
            arb.chan[0].eq(partsel.o_chan),
            arb.chan_en[0].eq(partsel.o_en),
        ]

        # Audio up, tapped digitally rather than looped back through a patch cable. Without an
        # SoC the codec's calibration constants are never loaded, which puts 80-120 mV of DC on
        # every converter (~1.2% of full scale) -- enough to skew FFT grading. Teeing `dry`
        # means the graded signal never touches the DAC or the ADC. The jack still plays in
        # parallel, unchanged.
        #
        # The tap moved from `core.o` to `fx.o` in M26, and had to: `echo`, `reverb`,
        # `reverb_cathedral` and `stress_fx_tail` all grade the effects, and all four are
        # ungradable while the capture point sits upstream of them. In the CV variant there is no
        # `fx` and `dry` *is* `core.o`, which puts the tap back where M25 had it -- correct for
        # what that bitstream is graded on, and the reason those four presets are graded on the
        # other one.
        #
        # The tee must never backpressure `pmod0.i_cal`: a host that is not recording would
        # otherwise stall the codec. So it takes a copy only when the FIFO has room and silently
        # drops otherwise. Rates match by construction (both sides are 48 kHz off the same
        # mclk), so in a live capture the FIFO only ever absorbs jitter.
        #
        # Channels 2 and 3 do not carry audio. Together they carry one 31-bit counter of `audio`
        # clock cycles, sampled at the instant the frame was teed -- ch2 the low 15 bits, ch3 the
        # high 16. The host subtracts the counter at each end of a capture, divides by the
        # elapsed wall-clock time, and has the board's real audio clock in Hz, measured from
        # outside the FPGA against a reference the FPGA has no part in.
        #
        # This exists because M25 spent most of a day on a stale SI5351 `clk0` -- 49.152 MHz left
        # behind by another slot, so every rate in the design ran 4x high and the only visible
        # symptom was an inexplicable pitch error. check_loop.py reads it and says so now. See
        # the rate note at the top of xls_core.py.
        #
        # Why one wide counter and not, say, a cycle counter plus a frame counter: USB delivery
        # is bursty. Measured on hardware, most adjacent delivered frames are 256 audio cycles
        # apart -- one codec frame, since I2STDM takes lrck from clkdiv[7] -- but every twentieth
        # pair jumps by 5120 as the tee FIFO refills. So no per-frame statistic is the rate: the
        # median sees only inside a burst, the mean is thrown by the tail, and any delta wide
        # enough to span a burst can wrap a 16-bit counter. Only end-to-end advance is honest,
        # and 31 bits does not wrap inside any capture (175 s at 12.288 MHz).
        #
        # The counter is gray-coded before the CDC and decoded on the far side: a binary counter
        # sampled by a foreign clock can be caught mid-carry and read as any value at all, while
        # gray puts at most one bit in flight, so the worst case is off by one count -- 81 ns.
        # Bit 15 of ch2 is then forced high, which is what lets it double as the never-zero
        # "alive" marker the gap detector below depends on; without it the counter would pass
        # through zero once per wrap and that frame would read as a dropout.
        audio_ctr  = Signal(31)
        audio_next = Signal(31)
        audio_gray = Signal(31)
        m.d.comb  += audio_next.eq(audio_ctr + 1)
        m.d.audio += [
            audio_ctr.eq(audio_next),
            audio_gray.eq(audio_next ^ audio_next[1:]),
        ]
        gray_s = Signal(31)
        m.submodules.audio_ctr_ff = FFSynchronizer(audio_gray, gray_s, o_domain="sync")
        # Gray -> binary: bit i is the parity of every gray bit at or above it. yosys balances
        # each reduction into a LUT4 tree, so this is wide but shallow.
        ctr_s = Signal(31)
        m.d.comb += [ctr_s[i].eq(gray_s[i:].xor()) for i in range(31)]

        m.submodules.usb_tee = usb_tee = SyncFIFO(width=64, depth=16)
        m.d.comb += [
            usb_tee.w_data.eq(Cat(dry.payload[0].as_value(),
                                  dry.payload[1].as_value(),
                                  ctr_s[0:15], C(1, 1),         # ch2: low bits + alive marker
                                  ctr_s[15:31])),               # ch3: high bits
            usb_tee.w_en.eq(dry.valid & dry.ready),
        ]

        # Channel 2 is never zero, and that is what makes the host's gap detector exact.
        #
        # This was built when frames were believed to be arriving all-zero at 2.5-5%; that
        # figure was withdrawn after re-measurement (docs/TILIQUA_USB_DROPOUTS.md) and the real
        # rate is ~0.001%. The channel stays, because the host still has to *measure* the rate
        # to know that, and cannot do so without an unambiguous marker. Against the ADC that
        # was free, because a converter's noise floor is never exactly zero; against a digital
        # tee it is not, because digital silence *is* exactly zero and a note's release tail
        # would read as one long dropout. One channel that is never zero settles it: all-zero
        # means dropped, full stop. It costs nothing on the jacks -- `usbif.i` feeds the USB IN
        # stream only, and out2 still comes from `dry` like every other output.
        m.d.comb += [
            usbif.i.payload[0].as_value().eq(usb_tee.r_data[0:16]),
            usbif.i.payload[1].as_value().eq(usb_tee.r_data[16:32]),
            usbif.i.payload[2].as_value().eq(usb_tee.r_data[32:48]),
            usbif.i.payload[3].as_value().eq(usb_tee.r_data[48:64]),
            usbif.i.valid.eq(usb_tee.r_rdy),
            usb_tee.r_en.eq(usb_tee.r_rdy & usbif.i.ready),
            # Nothing consumes host-to-device audio, but the stream still has to be drained:
            # macOS opens the output direction whenever it opens the input one.
            usbif.o.ready.eq(1),
        ]

        return m


def simulation_ports(fragment):
    return {
        "clk_audio":  (ClockSignal("audio"),            None),
        "rst_audio":  (ResetSignal("audio"),            None),
        "clk_sync":   (ClockSignal("sync"),             None),
        "rst_sync":   (ResetSignal("sync"),             None),
        "clk_fast":   (ClockSignal("fast"),             None),
        "rst_fast":   (ResetSignal("fast"),             None),
        "i2s_sdin1":  (fragment.pmod0.pins.i2s.sdin1,   None),
        "i2s_sdout1": (fragment.pmod0.pins.i2s.sdout1,  None),
        "i2s_lrck":   (fragment.pmod0.pins.i2s.lrck,    None),
        "i2s_bick":   (fragment.pmod0.pins.i2s.bick,    None),
        # Undriven in simulation, so Amaranth infers it as a top-level input: the harness's
        # scripted MIDI transmitter drives it in place of the optoisolator.
        "midi_rx":    (fragment.serialrx.phy.i,         None),
        # Stall localisation; the harness prints these when the run ends.
        "dbg_rom":    (fragment.core.dbg_rom,           None),
        "dbg_midi":   (fragment.core.dbg_midi,          None),
        "dbg_eng":    (fragment.core.dbg_eng,           None),
        "dbg_res":    (fragment.core.dbg_res,           None),
        "dbg_out":    (fragment.core.dbg_out,           None),
    }


def argparse_callback(parser):
    # `--modeline` belongs to the SDK's CLI (build/cli.py:70) and defaults to 1280x720p60, which
    # is not what is plugged in. Overriding the default rather than passing the flag from build.sh
    # keeps MODELINE the one place the resolution is written down -- it also has to match what the
    # bootloader programmed clk1 to, so two copies of it is two chances to be silently wrong.
    parser.set_defaults(modeline=MODELINE)


if __name__ == "__main__":
    top_level_cli(
        CoreTop,
        argparse_callback=argparse_callback,
        # `video_core` is what adds `--modeline`, and with it the second ECP5 PLL and the
        # dvi/dvi5x domains. Both variants now, since the echo's move to BRAM made room in fx.
        video_core=True,
        sim_ports=simulation_ports,
        # Verilator resolves --exe sources relative to its --Mdir, so this has to be absolute.
        sim_harness=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "sim_xls_core.cpp"),
    )
