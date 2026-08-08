# M29 — the 32 voices as 32 tiles, drawn by racing the beam.
#
# The engine already tells us what to draw. `viz_out` (synth.x:403) is a 32-bit tuple emitted once
# per voice per scan -- {env[15:0], is_new@16, last@17} -- and M28's LED comet is already reading
# it (led.py). This draws the same envelopes at 32x the resolution the pmod's eight LEDs allow.
#
# **There is no framebuffer.** The colour of each pixel is computed in the cycle before it is sent,
# from the beam position and a 32-byte store. That is not a shortcut, it is the design:
#
#   * The SDK's usual video path is `framebuffer.py`, which streams a 720x720 image out of PSRAM.
#     PSRAM is where M26's echo delay line lives, and the two would then be sharing one controller
#     for the whole of every frame. "The audio must not glitch" would become a bandwidth argument
#     to be won. With no framebuffer there is no second PSRAM client and nothing to argue about.
#   * 32 tiles x 8 bits of brightness is 32 bytes. A framebuffer for the same information is
#     1.5 MB, and every one of those bytes would be a copy of one of these 32.
#
# So the whole store is one BRAM, written from the engine's domain and read from the pixel clock,
# and the renderer is a pair of counters. See `docs/TILIQUA_PORT.md` for how it is clocked: the
# pixel clock arrives from the SI5351's clk1, which the *bootloader* programs from the panel's
# EDID before any slot runs, so this needs no flash write and no manifest of its own.

import math

from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

N_VOICE = 32
COLS, ROWS = 8, 4               # 8 across, 4 down -- 32 tiles, and both divide 720 exactly
H_ACTIVE = V_ACTIVE = 720       # 720x720p60r2; the panel's own EDID confirms it (see below)

# synth.x:403 packs {env[15:0], is_new@16, last@17, note@18..24, part@25..26}
VIZ_NEW, VIZ_LAST = 16, 17
VIZ_NOTE = slice(18, 25)

# Where the ink stops: dead space between tiles, and the radius the corners are taken off with.
GUTTER_H, GUTTER_V = 3, 6
RADIUS = 18

# The floor a silent voice sits at, out of 255. Not zero, because all 32 cells have to stay
# visible when nothing is playing -- a black screen is indistinguishable from a video path that
# is not working, and that ambiguity has cost a debugging session before. Not the dim grey ring
# this used to draw either: with brightness carrying loudness, a dim version of the voice's own
# colour says the same thing and leaves the shape clean enough to round the corners of.
IDLE_V = 0x14

# --- pitch -> hue, across the whole keyboard --------------------------------------------------
#
# The first version of this collapsed octaves (`note % 12`, twelve fixed hues), which made a
# chord read as one stable chord of colours -- but it also made the bottom and top of the
# instrument identical, and on 32 tiles the thing worth seeing is *register*: which voices are
# holding the bass and which are up in the melody. So the ramp is stretched over the keyboard
# instead, and an octave visibly moves.
#
# The window is 44 keys, not the piano's 88. Spreading the ramp over the full A0..C8 was
# technically correct and visually useless: real music lives in the middle two octaves, so a
# whole song came out in one shade of green and the map wasted both ends on notes nobody plays.
# Halving the window doubles the colour separation where the notes actually are. Anything
# outside it clamps rather than wraps, which is why the ends of the ramp are worth choosing --
# every bass note below C2 is the same pure red and every lead above G5 the same pure blue, and
# "off the bottom" and "off the top" stay distinguishable at a glance.
#
# Four sectors for the same reason: red -> yellow -> green -> cyan -> blue lands exactly on
# (0, 0, 255) at the top with no wrap, where a fifth sector would carry on into magenta and a
# sixth would return to red and collide with the bottom of the range.
NOTE_LO, NOTE_HI = 36, 79       # C2 .. G5, 44 keys
HUE_SECTORS = 4
HUE_MAX = HUE_SECTORS * 256 - 1                                 # 1023
# Rounded *up*, so the top key reaches HUE_MAX exactly. Rounding to nearest leaves it one short,
# which is invisible on its own -- but it is the pure blue that every note above the window
# clamps to, and the ramp's last step and its out-of-range colour have to be the same one.
HUE_K, HUE_SH = math.ceil(HUE_MAX * 256 / (NOTE_HI - NOTE_LO)), 8   # 6091


def hue_rgb(note):
    """Reference model: the fully-saturated colour of a note, before brightness. Returns 0..255.

    The gateware does not evaluate this -- it builds the same four cases as a mux over the top
    two bits of `hue` -- but the two have to agree, so this is what test_viz.py checks against.
    """
    n = min(max(note, NOTE_LO), NOTE_HI) - NOTE_LO
    hue = (n * HUE_K) >> HUE_SH
    sect, f = hue >> 8, hue & 0xFF
    return [(255, f, 0), (255 - f, 255, 0), (0, 255, f), (0, 255 - f, 255)][sect]


def tile_rgb(note, level):
    """Reference model: what a lit pixel of a tile showing `note` at envelope `level` must be.

    Brightness is folded in with a single multiply. Each channel of `hue_rgb` is one of 0, f,
    255-f or 255, so scaling by `v` needs only `fv = (f*v)>>8` and then 0 / fv / v-fv / v -- the
    255 and 255-f cases reuse `v` and `v-fv`, which are within one LSB of the exact products
    and save two multipliers for a difference no panel can show.
    """
    v = IDLE_V + ((level * (255 - IDLE_V)) >> 8)
    n = min(max(note, NOTE_LO), NOTE_HI) - NOTE_LO
    hue = (n * HUE_K) >> HUE_SH
    sect, f = hue >> 8, hue & 0xFF
    fv = (f * v) >> 8
    inv = v - fv
    return [(v, fv, 0), (inv, v, 0), (0, v, fv), (0, inv, v)][sect]


def corner_insets(r):
    """How far row `dy` of a corner is eaten into, for dy in 0..r-1. A quarter circle, rounded.

    Baked into a ROM rather than solved per pixel: `dx*dx + dy*dy <= r*r` is two multiplies in
    the pixel path, and r is 18, so the answer fits in eighteen 5-bit words.
    """
    return [round(r - math.sqrt(r * r - (r - dy) ** 2)) for dy in range(r)]


class VizStore(wiring.Component):

    """
    The engine's `viz_out` tap -> 32 bytes of brightness, readable from the pixel clock.

    This is the whole clock crossing. `audio` (12.288 MHz) writes one byte per voice per scan;
    `dvi` (39.07 MHz) reads one byte per pixel. A true dual-port BRAM does both without a FIFO,
    a handshake or a synchroniser, because neither side ever needs to know what the other is
    doing: the reader wants the most recent value and does not care which scan it came from, and
    a byte that is read mid-write yields one of the two values, both of which were true within
    the last 2.7 ms. An AsyncFIFO here would be machinery in service of a guarantee nobody wants.

    `addr` tracks the voice on the wire rather than being carried with it. `send(tok, viz_out, ..)`
    at synth.x:404 is unconditional and `vidx` is a ring, so the tuples arrive 0,1,...,31,0,...
    in order -- the same property led.py leans on to turn its lookup into a rotation. Unlike
    led.py this *does* read bit 17: a counter can drift where a rotation cannot, and `last` costs
    one comparison to make it self-correcting on every scan.
    """

    i_viz:    In(32)                    # `audio`
    i_strobe: In(1)                     # `audio` -- the tap's `valid`
    i_addr:   In(range(N_VOICE))        # `dvi`
    o_level:  Out(8)                    # `dvi`, one cycle after `i_addr`
    o_note:   Out(7)                    # `dvi`, alongside o_level

    def elaborate(self, platform):
        m = Module()

        # 15 bits per voice: the envelope's top byte and the note that produced it, stored
        # together so a tile's brightness and its colour can never disagree about which scan they
        # came from. 32 x 15 is small enough that yosys puts it in LUT RAM, not a DP16KD.
        m.submodules.mem = mem = Memory(shape=unsigned(15), depth=N_VOICE, init=[])
        w = mem.write_port(domain="audio")
        r = mem.read_port(domain="dvi")

        addr = Signal(range(N_VOICE))
        m.d.comb += [
            w.addr.eq(addr),
            w.data.eq(Cat(self.i_viz[8:16], self.i_viz[VIZ_NOTE])),
            w.en.eq(self.i_strobe),
            r.addr.eq(self.i_addr),
            self.o_level.eq(r.data[0:8]),
            self.o_note.eq(r.data[8:15]),
        ]
        with m.If(self.i_strobe):
            m.d.audio += addr.eq(Mux(self.i_viz[VIZ_LAST], 0, addr + 1))

        return m


class VoiceTiles(wiring.Component):

    """
    Beam position -> tile index -> colour. Entirely in `dvi`; instantiate under a DomainRenamer.

    No divider. `x // 90` and `y // 180` are counters that reset with the beam, which is the
    standard beamracing trade and the reason the tiles can be 90x180 -- filling the panel exactly
    -- instead of the power-of-two sizes a bit-slice would force, with a border of dead pixels
    to make up the difference.

    Two variables, two channels. **Brightness is loudness**: every tile is the same rounded
    rectangle, filling its cell, and the envelope scales how brightly it burns -- so a struck
    voice flashes and a released one fades out without anything moving. **Hue is pitch**, spread
    across the 88 keys (see `hue_rgb`), so register is legible: the bass voices sit at the red
    end and the melody at the blue.

    Brightness rather than size is not just taste. The envelope reaches these tiles at the
    engine's own rate and is resampled once per pixel, so the only limit on how fast a tile can
    change is the panel's 60 Hz. A growing rectangle spends that bandwidth on edges that step one
    pixel at a time -- 80 distinct sizes between silence and full -- where brightness has 235
    levels and no quantised motion to give the frame rate away.

    The pipeline is four deep and the sync signals are delayed to match, so what leaves this
    component is a pixel and the syncs that belong to it:

        cycle 0   x, y from DVITimingGen; col/row valid for this pixel; `o_addr` combinational
        cycle 1   the store answers. Hue and brightness are derived from it -- a constant
                  multiply each -- and the pixel's distance to the nearest tile edge is taken
        cycle 2   the one real multiply (`f * v`), the five-way sector mux that turns it into
                  RGB, and the corner ROM lookup that decides whether this pixel is cut away
        cycle 3   a two-way mux, and the colour is out

    Split this finely because it is free: nothing here is on a feedback path, and at 39 MHz an
    8x8 multiply followed by a mux followed by a compare would be the only thing in the design
    with any reason to be marginal.

    `x` and `y` are signed and count *through* the blanking (dvi.py:83) -- negative until the
    active region starts -- which is what makes `x == -1` an unambiguous "one pixel before the
    line", and the natural place to reload the counters.
    """

    i_x:     In(signed(12))
    i_y:     In(signed(12))
    i_de:    In(1)
    i_hsync: In(1)
    i_vsync: In(1)

    o_addr:  Out(range(N_VOICE))
    i_level: In(8)                      # VizStore's answer to `o_addr`, one cycle later
    i_note:  In(7)                      # ditto

    o_r:     Out(8)
    o_g:     Out(8)
    o_b:     Out(8)
    o_de:    Out(1)
    o_hsync: Out(1)
    o_vsync: Out(1)

    def __init__(self, h_active=H_ACTIVE, v_active=V_ACTIVE):
        # The actives are parameters only so test_viz.py can walk a whole frame's worth of tile
        # indices in a few thousand cycles instead of the 651,224 a real one takes. Nothing but
        # the test passes anything but the defaults.
        assert h_active % COLS == 0 and v_active % ROWS == 0
        self.h_active, self.v_active = h_active, v_active
        self.h_tile, self.v_tile = h_active // COLS, v_active // ROWS
        # The drawn rectangle: the cell, inset by the gutter, inclusive at both ends.
        self.x0, self.x1 = GUTTER_H, self.h_tile - 1 - GUTTER_H
        self.y0, self.y1 = GUTTER_V, self.v_tile - 1 - GUTTER_V
        # A corner cannot eat past the middle of the shape, or the two corners on one edge meet
        # and the rectangle develops a waist. Only the test geometry's miniature tiles come
        # anywhere near that, but clamping is one line and a waist is a confusing bug.
        self.radius = max(0, min(RADIUS, (self.x1 - self.x0 + 1) // 2,
                                 (self.y1 - self.y0 + 1) // 2))
        self.insets = corner_insets(self.radius)
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        h_tile, v_tile = self.h_tile, self.v_tile
        col = Signal(range(COLS))
        row = Signal(range(ROWS))
        px  = Signal(range(h_tile))
        py  = Signal(range(v_tile))

        # Horizontal: reload one pixel before the line so the counters read 0 *at* x == 0.
        with m.If(self.i_x == -1):
            m.d.sync += [px.eq(0), col.eq(0)]
        with m.Elif(self.i_x >= 0):
            with m.If(px == h_tile - 1):
                m.d.sync += [px.eq(0), col.eq(col + 1)]
            with m.Else():
                m.d.sync += px.eq(px + 1)

        # Vertical: reload at the last pixel of the last blanking line, advance at the end of each
        # active line. `col`/`row` are 3 and 2 bits, so the tile index is a concatenation and the
        # multiply by 8 costs nothing.
        with m.If((self.i_y == -1) & (self.i_x == -1)):
            m.d.sync += [py.eq(0), row.eq(0)]
        with m.Elif((self.i_x == self.h_active - 1) & (self.i_y >= 0)):
            with m.If(py == v_tile - 1):
                m.d.sync += [py.eq(0), row.eq(row + 1)]
            with m.Else():
                m.d.sync += py.eq(py + 1)

        m.d.comb += self.o_addr.eq(Cat(col, row))

        px_d1, py_d1 = Signal.like(px), Signal.like(py)
        de_d1, hs_d1, vs_d1 = Signal(), Signal(), Signal()
        m.d.sync += [
            px_d1.eq(px), py_d1.eq(py),
            de_d1.eq(self.i_de), hs_d1.eq(self.i_hsync), vs_d1.eq(self.i_vsync),
        ]

        # --- cycle 1: the store has answered. Hue, brightness, and where in the tile we are. ---
        x0, x1, y0, y1 = self.x0, self.x1, self.y0, self.y1
        r = self.radius

        note_c = Signal(7)
        with m.If(self.i_note < NOTE_LO):
            m.d.comb += note_c.eq(NOTE_LO)
        with m.Elif(self.i_note > NOTE_HI):
            m.d.comb += note_c.eq(NOTE_HI)
        with m.Else():
            m.d.comb += note_c.eq(self.i_note)

        # Held in its own unsigned signal: `note_c - NOTE_LO` is a signed expression in Amaranth
        # even though the clamp above has just guaranteed it cannot be negative, and a signed
        # operand would carry a sign bit through the multiply for no reason.
        nrel = Signal(range(NOTE_HI - NOTE_LO + 1))
        hue = Signal(range(HUE_MAX + 1))
        v = Signal(8)
        m.d.comb += [
            nrel.eq(note_c - NOTE_LO),
            hue.eq((nrel * HUE_K) >> HUE_SH),
            # IDLE_V is the floor; the envelope buys the rest of the range above it.
            v.eq(IDLE_V + ((self.i_level * (255 - IDLE_V)) >> 8)),
        ]

        # Distance to the nearest edge of the rectangle, in each axis. The Muxes only guard the
        # gutter, where the subtraction would go negative and where nothing is drawn anyway.
        dxl, dxr = Signal(range(h_tile)), Signal(range(h_tile))
        dyt, dyb = Signal(range(v_tile)), Signal(range(v_tile))
        m.d.comb += [
            dxl.eq(Mux(px_d1 > x0, px_d1 - x0, 0)), dxr.eq(Mux(px_d1 < x1, x1 - px_d1, 0)),
            dyt.eq(Mux(py_d1 > y0, py_d1 - y0, 0)), dyb.eq(Mux(py_d1 < y1, y1 - py_d1, 0)),
        ]

        sect_d2, f_d2, v_d2 = Signal(3), Signal(8), Signal(8)
        dx_d2, dy_d2 = Signal(range(h_tile)), Signal(range(v_tile))
        gutter_d2 = Signal()
        de_d2, hs_d2, vs_d2 = Signal(), Signal(), Signal()
        m.d.sync += [
            sect_d2.eq(hue[8:]), f_d2.eq(hue[0:8]), v_d2.eq(v),
            dx_d2.eq(Mux(dxl < dxr, dxl, dxr)), dy_d2.eq(Mux(dyt < dyb, dyt, dyb)),
            gutter_d2.eq((px_d1 < x0) | (px_d1 > x1) | (py_d1 < y0) | (py_d1 > y1)),
            de_d2.eq(de_d1), hs_d2.eq(hs_d1), vs_d2.eq(vs_d1),
        ]

        # --- cycle 2: the multiply, the sector mux, and the corner. ----------------------------
        fv, inv = Signal(8), Signal(8)
        m.d.comb += [fv.eq((f_d2 * v_d2) >> 8), inv.eq(v_d2 - fv)]

        # Cat is LSB-first, so each of these reads (blue, green, red).
        rgb = Signal(24)
        with m.Switch(sect_d2):
            with m.Case(0):
                m.d.comb += rgb.eq(Cat(C(0, 8), fv, v_d2))       # red -> yellow
            with m.Case(1):
                m.d.comb += rgb.eq(Cat(C(0, 8), v_d2, inv))      # yellow -> green
            with m.Case(2):
                m.d.comb += rgb.eq(Cat(fv, v_d2, C(0, 8)))       # green -> cyan
            with m.Default():
                m.d.comb += rgb.eq(Cat(v_d2, inv, C(0, 8)))      # cyan -> blue

        # The corner. `dy_d2` indexes the quarter-circle ROM, and only rows within the radius of
        # an edge can be cut at all -- everything else is the straight side of the rectangle.
        cut = Signal()
        if r:
            idx = Signal(range(r))
            m.d.comb += idx.eq(Mux(dy_d2 < r, dy_d2, 0))
            inset = Array([C(i, range(r + 1)) for i in self.insets])[idx]
            m.d.comb += cut.eq((dy_d2 < r) & (dx_d2 < inset))

        rgb_d3 = Signal(24)
        dark_d3 = Signal()
        m.d.sync += [
            rgb_d3.eq(rgb), dark_d3.eq(gutter_d2 | cut),
            self.o_de.eq(de_d2), self.o_hsync.eq(hs_d2), self.o_vsync.eq(vs_d2),
        ]

        # --- cycle 3: black outside the shape, the voice's colour inside it. -------------------
        with m.If(self.o_de & ~dark_d3):
            m.d.comb += [self.o_r.eq(rgb_d3[16:24]),
                         self.o_g.eq(rgb_d3[8:16]),
                         self.o_b.eq(rgb_d3[0:8])]

        return m
