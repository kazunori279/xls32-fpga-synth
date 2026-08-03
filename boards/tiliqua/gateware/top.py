#!/usr/bin/env python3
# M23 — Tiliqua top level for the XLS32 engine.
#
# Modelled on the SDK's src/top/dsp/top.py CoreTop, minus PSRAM, video and MIDI: this bitstream
# only needs the codec, the clocks, and a reboot button. Build it through boards/tiliqua/build.sh,
# which stages the engine Verilog and points the SDK's toolchain variables at yowasp.

import os
import sys

from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer

from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.periph import eurorack_pmod
from tiliqua.platform import RebootProvider

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xls_core import XlsSynth


class CoreTop(Elaboratable):

    def __init__(self, clock_settings):
        self.core = XlsSynth()
        self.clock_settings = clock_settings
        self.pmod0 = eurorack_pmod.EurorackPmod(clock_settings.audio_clock)
        self.bitstream_help = self.core.bitstream_help
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
        # Stall localisation; the harness prints these when the run ends.
        "dbg_rom":    (fragment.core.dbg_rom,           None),
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
