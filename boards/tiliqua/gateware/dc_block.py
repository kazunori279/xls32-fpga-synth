#!/usr/bin/env python3
# A multiplier-free DC blocker for the USB tee.
#
# A pulse wave at duty d sits at a mean of 2d-1, and the engine emits it that way. The Eurorack
# jacks are AC-coupled so out0/out1 never cared, but the USB tee is a direct digital copy and
# cares a great deal: measured over a 110 s capture the mean was +0.286 and 89.6% of the energy
# sat below 5 Hz, which pushes everything you can actually hear down to -25.9 dBFS. In a DAW that
# is a waveform pinned against the top of the window.
#
# The filter is x - lowpass(x), with the SDK's `dsp.OnePole` as the lowpass:
# `state += (input - state) >> shift`, so it costs no multiplier. That is the whole reason it is
# built this way -- MULT18X18D is 28 of 28 on this bitstream with nothing spare, which also rules
# out `dsp.filters.DCBlock`, the obvious choice, because it wants a MAC.
#
# Two rules it exists to enforce, both of them things the caller could get wrong:
#
#   - It never backpressures. `en` is a strobe, not a handshake; there is no `ready` to wire and
#     nothing here can stall the codec. A tee that can stall the codec is a worse bug than the
#     one this fixes.
#   - It is combinational out. `o` is valid in the same cycle as the sample presented on `i`,
#     so it drops into an existing datapath without shifting anything by a cycle relative to the
#     frame counter travelling alongside it.

from amaranth import *
from amaranth.lib import data, wiring
from amaranth.lib.wiring import In, Out

from tiliqua import dsp
from tiliqua.dsp import ASQ

# 10 puts the corner at about 7.5 Hz at 48 kHz: below the lowest note the engine can play, well
# above the drift it has to remove. `dsp.OnePole.shift` is unsigned(4), so 15 is the ceiling.
DEFAULT_SHIFT = 10

# Headroom below the input LSB for the accumulator, six bits more than `shift`.
#
# The failure this avoids is *not* a dead band, which is what it looks like it should be: at the
# SDK default of 10 the update `(inp - state) >> shift` quantises to exactly one input LSB, and
# the DC residual on a 0.5 step settles at 1 LSB either way -- measured, identical for 10, 12 and
# 16. What the extra bits buy is the noise the filter injects while tracking. On a quiet tone
# (0.01 full scale) over a small pedestal, the residual after removing the fundamental is
# 1.081 LSB at extra_bits=10 and 0.632 at 16, with the passband gain unchanged to five figures.
# Both are near -90 dBFS and neither is audible; six flip-flops per channel is a cheap enough
# price for the quieter one that there is no reason to take the louder. test_dcblock.py pins it.
DEFAULT_EXTRA_BITS = 16


class TeeDcBlock(wiring.Component):

    """
    Members
    -------
    i : :py:`In(data.ArrayLayout(ASQ, channels))`
        Samples in.
    en : :py:`In(1)`
        Strobe one cycle per accepted frame. Drive it from the *transfer* of the stream being
        tapped (``valid & ready``), not from ``valid`` alone, or the filter integrates stalled
        cycles and its corner frequency stops meaning anything.
    o : :py:`Out(data.ArrayLayout(ASQ, channels))`
        Samples out, combinationally, DC removed and saturated back into `ASQ`.
    """

    def __init__(self, channels=2, shift=DEFAULT_SHIFT, extra_bits=DEFAULT_EXTRA_BITS):
        self.channels = channels
        self.shift = shift
        self.extra_bits = extra_bits
        super().__init__({
            "i":  In(data.ArrayLayout(ASQ, channels)),
            "en": In(1),
            "o":  Out(data.ArrayLayout(ASQ, channels)),
        })

    def elaborate(self, platform):
        m = Module()

        for ch in range(self.channels):
            lp = dsp.OnePole(extra_bits=self.extra_bits)
            m.submodules[f"lp{ch}"] = lp
            m.d.comb += [
                lp.i.payload.eq(self.i[ch]),
                lp.i.valid.eq(self.en),
                lp.o.ready.eq(1),               # never stalls; `lp.i.ready` is left dangling
                lp.shift.eq(self.shift),
            ]
            # Saturating, because x reaches full scale while lp(x) can be a third of it, so the
            # difference leaves the range regularly. Wrapping would be an audible click per
            # occurrence -- precisely the artefact the rest of this change removes.
            m.d.comb += self.o[ch].eq((self.i[ch] - lp.o.payload).saturate(ASQ))

        return m
