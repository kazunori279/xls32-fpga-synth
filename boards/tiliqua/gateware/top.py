#!/usr/bin/env python3
# M24 — Tiliqua top level for the XLS32 engine.
#
# Modelled on the SDK's src/top/dsp/top.py CoreTop, minus PSRAM and video: this bitstream needs
# the codec, the clocks, the TRS MIDI-In jack, and a reboot button. Build it through
# boards/tiliqua/build.sh, which stages the engine Verilog and points the SDK's toolchain
# variables at yowasp.
#
# The MIDI chain here is deliberately *not* the SDK's. src/top/dsp/top.py:1122 auto-wires
# SerialRx -> MidiDecodeSerial -> core.i_midi for any core exposing `i_midi`, handing the core
# decoded MidiMessage structs. The XLS engine parses MIDI itself, in DSLX, so it takes raw bytes
# and this file names the port `i_midi_bytes` precisely so that auto-wiring does not fire.

import os
import sys

from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer

from tiliqua import midi
from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.periph import eurorack_pmod
from tiliqua.platform import RebootProvider

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from midi_filter import SysCommonFilter
from xls_core import XlsSynth


class CoreTop(Elaboratable):

    def __init__(self, clock_settings):
        self.core = XlsSynth()
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
        wiring.connect(m, pmod0.o_cal, self.core.i)
        wiring.connect(m, self.core.o, pmod0.i_cal)

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
        wiring.connect(m, serialrx.o, rt_filter.i)
        wiring.connect(m, rt_filter.o, sysex_filter.i)
        wiring.connect(m, sysex_filter.o, common_filter.i)
        wiring.connect(m, common_filter.o, self.core.i_midi_bytes)

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


if __name__ == "__main__":
    top_level_cli(
        CoreTop,
        video_core=False,
        sim_ports=simulation_ports,
        # Verilator resolves --exe sources relative to its --Mdir, so this has to be absolute.
        sim_harness=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "sim_xls_core.cpp"),
    )
