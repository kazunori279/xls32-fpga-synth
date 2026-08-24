#!/usr/bin/env python3
# M39 (#47) — a clock generator that stops `sync` and `usb` sharing one net.
#
# On Tiliqua the vendor's generator drives both from the same PLL output:
#
#     ClockSignal("sync").eq(feedback60)      # pll.py:604
#     ClockSignal("usb") .eq(feedback60)
#
# so nextpnr sees one 60 MHz constraint covering all 22,722 cells. Only `usb` has a reason to be
# 60: ULPI carries USB 2.0 high speed as 8 bits at 60 MHz and luna's turnaround tables are keyed on
# that exact constant (`_HS_RX_TO_TX_DELAY = {60e6: (1, 24)}`). Nothing pins `sync` — it is one
# integer at `pll.py:78` and the UART divider, the TRS baud divider and the engine's cycle budget
# all derive from it.
#
# Splitting them costs no new PLL. `CLKOS` already exists, already runs off the same 600 MHz VCO,
# and makes 120 MHz for a PSRAM DQS that M29 deleted — nothing in the SDK or in this repo reads the
# `fast` domain. `CLKOS_DIV` 5 -> 12 turns it into 50 MHz, `sync` moves onto it, and the 60 MHz
# constraint shrinks to `usbif` and its wrappers: 2,429 cells by the E2 census, about 11 % of the
# design. nextpnr-ecp5 reads EHXPLLL parameters itself, so both domains get constrained correctly
# with no `create_clock` on our side.
#
# The bet this is testing (#47, feeding #3): the worst path on the shipping build is
# `usb.timer.counter[7]` -> `usb.data_crc.crc[1]` at 4.62 ns logic and **13.04 ns routing** — three
# quarters wire, which is distance, which is placement. The vendor's stock `usb_audio` shell makes
# 66.49 MHz with the same luna at ~20 % occupancy. If luna is spread out because 20,000 of our
# cells are competing for the same fabric under the same deadline, this recovers it. If it is
# spread out because of where the ULPI pins are, this buys nothing and the answer is #34 item 3.
#
# --- what it measured ---
#
# Twelve seeds, ten routed: usb mean 55.26, best 59.40, worst 51.32, against the unsplit netlist's
# 24-seed draw of mean 53.99 / best 56.63 / worst 51.60. Real, and still a FAIL at 60. The best
# seed's worst path is seventeen LUT levels across four modules of luna and the SDK with none of our
# cells in it, so the shortfall is depth and not congestion, and the answer is #34 item 3 after all.
# This stays off by default: it buys about a megahertz on a domain that still fails, and turning it
# on is a different netlist with its own seed lottery. See DEVELOPMENT_tiliqua.md M39.
#
# --- why this file is a copy ---
#
# `TiliquaDomainGeneratorPLLExternal.elaborate` is one monolithic method and the two lines we need
# are in the middle of it, so there is nothing to override. The SDK is read-only for us — it is the
# vendor's library and it is proven to meet timing — so this subclasses it and restates the method
# rather than editing it. Copied from **tiliqua v1.2.1-24-gd760756**, `src/tiliqua/pll.py:328-471`.
# Every line is the vendor's except the four marked `# XLS32:`. Re-diff against that file after an
# SDK bump; nothing here is a fix, it is a fork with a reason.

import os
import textwrap

from amaranth import *

from tiliqua.pll import (ClockStabilityMonitor, TiliquaDomainGeneratorPLLExternal,
                         create_dynamic_dvi_pll)

# ULPI carries USB 2.0 high speed as 8 bits at 60 MHz, and luna's turnaround tables are keyed on
# that constant -- `_HS_RX_TO_TX_DELAY = {60e6: (1, 24)}` in `usb2/packet.py`, with a 12e6 entry
# only for full speed. This half of the split is fixed and not worth arguing with.
USB_HZ = 60_000_000

# The VCO is 48 MHz / CLKI_DIV 4 * CLKFB_DIV 5 * CLKOP_DIV 10 = 600 MHz, and CLKOS divides the same
# VCO, so the reachable `sync` frequencies are 600/N. The ones worth naming:
#
#   N=10  60.00 MHz   what it is today
#   N=11  54.55 MHz   under the 24-voice build's measured 56.63
#   N=12  50.00 MHz   6.6 MHz of slack on 24 voices, 3.7 above the 32-voice build's 46.35
#   N=13  46.15 MHz   what the 32-voice build measures now
#
# 12 is the default: it clears both builds' measured Fmax without asking the placer for anything,
# which keeps the experiment about `usb` and not about `sync`. Override with XLS32_SYNC_DIV.
SYNC_DIV = int(os.environ.get("XLS32_SYNC_DIV", "12"))
VCO_HZ = 600_000_000
SYNC_HZ = VCO_HZ // SYNC_DIV

# 60e6/31250 = 1920 and 50e6/31250 = 1600, both exact. A `sync` that cannot divide to 31250 baud
# without error breaks the TRS jack quietly, so it is an assertion and not a comment.
assert SYNC_HZ % 31250 == 0, f"sync {SYNC_HZ} Hz does not divide to 31250 baud exactly"


def apply_to(clock_settings):
    """Tell the rest of the design what `sync` actually is.

    `ClockFrequencies` is a plain dataclass built in `build/cli.py` with `sync=60_000_000`
    hardcoded, and everything downstream reads it rather than measuring: `SerialRx`'s baud divider,
    `RebootProvider`'s hold-down counter, and the sim harness's clock period. Mutating it here is
    the whole of the software-side change.
    """
    clock_settings.frequencies.sync = SYNC_HZ
    clock_settings.frequencies.fast = SYNC_HZ  # `fast` and `sync` are now the same net
    return clock_settings


class SplitDomainGenerator(TiliquaDomainGeneratorPLLExternal):
    """`usb` stays on CLKOP at 60 MHz; `sync` and `fast` move to CLKOS at 600/SYNC_DIV."""

    # The vendor's diagram prints `{sync}` on the `usb` row too, because on its generator they are
    # the same net. Leaving that would put "usb 50.0000 MHz" in every build log of a design whose
    # whole point is that usb is still 60.
    clock_tree_base = TiliquaDomainGeneratorPLLExternal.clock_tree_base.replace(
        "├>[usb] {sync:12.4f} MHz", "├>[usb] {usb:12.4f} MHz")

    def prettyprint(self):
        print(textwrap.dedent(self.clock_tree_base).format(
            sync=self.settings.frequencies.sync / 1e6,
            usb=USB_HZ / 1e6,
            fast=self.settings.frequencies.fast / 1e6,
            audio=self.settings.frequencies.audio / 1e6,
        ))
        # The video half is the vendor's, unchanged by the split.
        if self.settings.frequencies.dvi is not None:
            print(textwrap.dedent(self.clock_tree_video[1:]).format(
                dvi=self.settings.frequencies.dvi / 1e6,
                dvi5x=self.settings.frequencies.dvi5x / 1e6,
                dyn1='(dynamic)' if self.settings.dynamic_modeline else '',
                dyn2='(dynamic)' if self.settings.dynamic_modeline else '',
            ))
            if self.settings.dynamic_modeline:
                print("PLL configured for DYNAMIC video mode (maximum pixel clock shown).")
            else:
                print(f"PLL configured for STATIC video mode: {self.settings.modeline}.")
        else:
            print(textwrap.dedent(self.clock_tree_no_video[1:]))
            print("Video clocks disabled (no video out).")

    def elaborate(self, platform):
        m = Module()

        self.prettyprint()

        # Create our domains.
        m.domains.sync       = ClockDomain()
        m.domains.usb        = ClockDomain()
        m.domains.fast       = ClockDomain()
        m.domains.audio      = ClockDomain()
        m.domains.raw48      = ClockDomain()
        m.domains.expll_clk0 = ClockDomain()
        if self.settings.modeline or self.settings.dynamic_modeline:
            m.domains.expll_clk1 = ClockDomain()

        clk48 = platform.request(platform.default_clk, dir='i').i
        reset = Signal(init=0)

        m.d.comb += [
            ClockSignal("raw48")     .eq(clk48),
            # external PLL clock domain with no synchronous reset.
            ClockSignal("expll_clk0").eq(platform.request("clkex", 0).i),
            ResetSignal("expll_clk0").eq(0),
        ]

        if self.settings.modeline or self.settings.dynamic_modeline:
            m.d.comb += [
                ClockSignal("expll_clk1").eq(platform.request("clkex", 1).i),
                ResetSignal("expll_clk1").eq(0),
            ]

        # Generate synchronous reset for audio domain (there is no internal
        # PLL between the external PLL clock and the audio domain).
        #
        # XLS32: unchanged, and it survives the split on the numbers. The monitor counts edges of
        # the 12.288 MHz audio clock as seen from `sync`; at 50 MHz that is 4.07 samples per audio
        # period against 4.88 today, still far enough above two that no transition is missed.
        m.submodules.clock_monitor = clock_monitor = ClockStabilityMonitor(
            monitor_domain="sync",
            target_domain="expll_clk0"
        )
        m.d.comb += [
            clock_monitor.clk_in.eq(ClockSignal("expll_clk0")),
            ClockSignal("audio").eq(clock_monitor.clk_in),
            ResetSignal("audio").eq(clock_monitor.reset_out),
        ]

        m.d.comb += platform.request("led_b").o.eq(ResetSignal("audio")),

        # ecppll -i 48 --clkout0 60 --clkout1 120 --clkout2 50 --reset -f pll60.v
        # 60MHz for USB (currently also sync domain. fast is for DQS)

        feedback60 = Signal()
        locked60   = Signal()
        clk_sync   = Signal()   # XLS32: CLKOS, 600/SYNC_DIV MHz, drives `sync` and `fast`
        m.submodules.pll = Instance("EHXPLLL",

                # Clock in.
                i_CLKI=clk48,

                # Generated clock outputs.
                o_CLKOP=feedback60,
                o_CLKOS=clk_sync,          # XLS32: was ClockSignal("fast") at 120 MHz

                # Status.
                o_LOCK=locked60,

                # PLL parameters...
                p_PLLRST_ENA="ENABLED",
                p_INTFB_WAKE="DISABLED",
                p_STDBY_ENABLE="DISABLED",
                p_DPHASE_SOURCE="DISABLED",
                p_OUTDIVIDER_MUXA="DIVA",
                p_OUTDIVIDER_MUXB="DIVB",
                p_OUTDIVIDER_MUXC="DIVC",
                p_OUTDIVIDER_MUXD="DIVD",
                p_CLKI_DIV=4,
                p_CLKOP_ENABLE="ENABLED",
                p_CLKOP_DIV=10,
                p_CLKOP_CPHASE=4,
                p_CLKOS_ENABLE="ENABLED",
                p_CLKOS_DIV=SYNC_DIV,               # XLS32: was 5 (120 MHz)
                p_CLKOS_CPHASE=SYNC_DIV // 2 - 1,   # XLS32: 50 % duty, as ecppll would emit
                p_CLKOS_FPHASE=0,
                p_FEEDBK_PATH="CLKOP",
                p_CLKFB_DIV=5,

                # Internal feedback.
                i_CLKFB=feedback60,

                # Control signals.
                i_RST=reset,
                i_PHASESEL0=0,
                i_PHASESEL1=0,
                i_PHASEDIR=1,
                i_PHASESTEP=1,
                i_PHASELOADREG=1,
                i_STDBY=0,
                i_PLLWAKESYNC=0,

                # Output Enables.
                i_ENCLKOP=0,
                i_ENCLKOS=0,
                i_ENCLKOS2=0,
                i_ENCLKOS3=0,

                # Synthesis attributes.
                a_ICP_CURRENT="12",
                a_LPF_RESISTOR="8",
        )

        # Video PLL and derived signals
        if self.settings.modeline or self.settings.dynamic_modeline:

            m.domains.dvi   = ClockDomain()
            m.domains.dvi5x = ClockDomain()

            locked_dvi = Signal()
            m.submodules.pll_dvi = create_dynamic_dvi_pll(self.reset_dvi_pll, locked_dvi)

            # XXX/HACK: ensure clean reset deassertion.
            # FFSync should be able to accomplish this, but for some reason, it did not.
            # Tested by rebuilding all bitstreams, switching back and forth with power
            # cycles about 100 times, no dvi domain initialization glitches seen.
            m.domains += ClockDomain("_dvi_rstsync", reset_less=True, local=True)
            m.d.comb += ClockSignal("_dvi_rstsync").eq(ClockSignal("dvi"))
            lock_pipe = Signal(2, init=0)
            m.d._dvi_rstsync += lock_pipe.eq(Cat(locked_dvi, lock_pipe[0]))

            m.d.comb += [
                ResetSignal("dvi")  .eq(~locked_dvi | ~lock_pipe[1]),
                ResetSignal("dvi5x").eq(~locked_dvi | ~lock_pipe[1]),
            ]

            # LED off when DVI PLL locked
            m.d.comb += platform.request("led_a").o.eq(ResetSignal("dvi"))

        # Derived clocks and resets
        #
        # XLS32: the split. `usb` keeps CLKOP because luna's high-speed turnaround tables only know
        # 60 MHz; `sync` and `fast` take CLKOS. Both come off the same VCO and the same LOCK, so the
        # reset story is unchanged — and every sync<->usb path already crosses through an
        # AsyncFIFO or an FFSynchronizer, because both sides were written as if the two clocks were
        # independent (see #47 for the audit).
        m.d.comb += [
            ClockSignal("sync")  .eq(clk_sync),      # XLS32: was feedback60
            ClockSignal("fast")  .eq(clk_sync),      # XLS32: was driven directly by o_CLKOS
            ClockSignal("usb")   .eq(feedback60),

            ResetSignal("sync")  .eq(~locked60),
            ResetSignal("fast")  .eq(~locked60),
            ResetSignal("usb")   .eq(~locked60),
        ]

        return m
