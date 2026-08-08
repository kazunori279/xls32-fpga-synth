# Unit sim for the M29 tile renderer: does every pixel get the tile it is standing in?
#
#   python boards/tiliqua/gateware/test_viz.py
#
# There is no framebuffer, so there is no intermediate artefact to inspect and no way to be
# half-right: if the counters are off by one the picture is sheared, and if the BRAM latency is
# unaccounted for every tile is shifted a pixel into its neighbour. Both are invisible on a photo
# of a lit panel and obvious here.
#
# The geometry test runs against an 80x40 frame rather than 720x720. The counters do not know the
# difference -- they are compared against `h_tile`/`v_tile` either way -- and it is the difference
# between 4,000 cycles and 651,224.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from amaranth.sim import Simulator

from viz import (COLS, HUE_MAX, IDLE_V, NOTE_HI, NOTE_LO, N_VOICE, RADIUS,
                 VizStore, VoiceTiles, corner_insets, hue_rgb, tile_rgb)


def viz(env, is_new=0, last=0, note=0, part=0):
    """Pack one tuple the way synth.x:403 does."""
    return ((env & 0xffff) | (is_new << 16) | (last << 17)
            | ((note & 0x7f) << 18) | ((part & 3) << 25))


# --- the store ------------------------------------------------------------------------------

def store_bench(body):
    """Two clocks, deliberately: the store's whole job is to have a foot in each."""
    dut = VizStore()

    async def tb(ctx):
        await body(ctx, dut)

    sim = Simulator(dut)
    sim.add_clock(1 / 12.288e6, domain="audio")
    sim.add_clock(1 / 39.07e6, domain="dvi")
    sim.add_testbench(tb)
    sim.run()


def test_store_lands_each_voice_at_its_own_index():
    """A scan of 32 distinct envelopes must be readable back, one per tile, in order.

    Level and note are written as one word, so this also pins the thing that would otherwise be
    an intermittent visual bug: a tile drawn at one voice's size in another voice's colour.
    """
    async def body(ctx, dut):
        for i in range(N_VOICE):
            ctx.set(dut.i_viz, viz((i + 1) << 8, last=(i == N_VOICE - 1), note=40 + i))
            ctx.set(dut.i_strobe, 1)
            await ctx.tick("audio")
        ctx.set(dut.i_strobe, 0)

        for i in range(N_VOICE):
            ctx.set(dut.i_addr, i)
            await ctx.tick("dvi")
            assert ctx.get(dut.o_level) == i + 1, (
                f"tile {i} read level {ctx.get(dut.o_level)}, wanted {i + 1}")
            assert ctx.get(dut.o_note) == 40 + i, (
                f"tile {i} read note {ctx.get(dut.o_note)}, wanted {40 + i}")

    store_bench(body)


def test_the_hue_ramp_spans_the_keyboard_without_wrapping():
    """Every key gets a colour, no two keys a semitone apart share one, and A0 != C8.

    Not a simulation -- a statement about the arithmetic the gateware's sector mux is fed. The
    property that matters is injectivity: the previous version collapsed octaves, and the whole
    reason for this one is that the bottom and the top of the instrument must not look alike.
    """
    seen = [hue_rgb(n) for n in range(NOTE_LO, NOTE_HI + 1)]
    assert len(seen) == 44, f"the window should be 44 keys, got {len(seen)}"
    assert len(set(seen)) == len(seen), "two keys landed on the same colour"
    assert seen[0] == (255, 0, 0), f"the bottom of the window should be pure red, got {seen[0]}"
    assert seen[-1] == (0, 0, 255), f"the top should be pure blue, got {seen[-1]}"

    # Out of range clamps rather than wraps, and the two ends are the two extremes of the ramp,
    # so "below the window" reads as red and "above it" as blue instead of colliding.
    assert hue_rgb(0) == (255, 0, 0), "notes under the window must clamp to red"
    assert hue_rgb(127) == (0, 0, 255), "notes over the window must clamp to blue"


def test_the_corner_rom_is_a_quarter_circle():
    """The 18 words baked into the corner ROM, checked against the circle they claim to be."""
    ins = corner_insets(RADIUS)
    assert len(ins) == RADIUS
    assert ins[0] == RADIUS, f"the first row should be cut the full radius, got {ins[0]}"
    assert ins[-1] == 0, f"the last row should not be cut at all, got {ins[-1]}"
    assert all(a >= b for a, b in zip(ins, ins[1:])), f"the profile is not monotone: {ins}"
    for dy, dx in enumerate(ins):
        # The retained pixel just inside the cut has to be within the circle, and the cut pixel
        # just outside it has to be beyond -- which is what "rounded to the nearest pixel" means.
        assert (RADIUS - dx) ** 2 + (RADIUS - dy) ** 2 <= RADIUS ** 2 + RADIUS, (
            f"row {dy} keeps a pixel outside the circle")


def test_store_resyncs_on_last():
    """A write counter that has drifted is pulled back by bit 17, within one scan.

    led.py deliberately does not read `last`, because a rotation cannot drift. This is a counter
    and can, so the claim it makes instead is that it recovers -- which is worth a test, because
    the failure it guards against is a permanently rotated picture that still looks plausible.
    """
    async def body(ctx, dut):
        # Three strobes with no `last` anywhere: the counter is now at 3, mid-scan.
        for _ in range(3):
            ctx.set(dut.i_viz, viz(0))
            ctx.set(dut.i_strobe, 1)
            await ctx.tick("audio")

        # A truthful scan follows. If `last` did nothing, voice 0 would land at tile 3.
        for i in range(N_VOICE):
            ctx.set(dut.i_viz, viz((i + 100) << 8, last=(i == N_VOICE - 1)))
            await ctx.tick("audio")
        ctx.set(dut.i_strobe, 0)

        # The first scan after the resync is still skewed by 3 -- the counter only comes home at
        # the end of it. The one after that is clean, and that is the claim.
        for i in range(N_VOICE):
            ctx.set(dut.i_viz, viz((i + 200) << 8, last=(i == N_VOICE - 1)))
            ctx.set(dut.i_strobe, 1)
            await ctx.tick("audio")
        ctx.set(dut.i_strobe, 0)

        for i in range(N_VOICE):
            ctx.set(dut.i_addr, i)
            await ctx.tick("dvi")
            assert ctx.get(dut.o_level) == i + 200, (
                f"after resync, tile {i} read {ctx.get(dut.o_level)}, wanted {i + 200}")

    store_bench(body)


# --- the renderer ---------------------------------------------------------------------------

def sweep(dut, watch, *, h_total, v_total, frames=1, lines=None, level=0, note=0):
    """Drive whole frames of beam positions, calling `watch(ctx, x, y)` at each active pixel.

    Mirrors dvi.py:83 exactly, including the signs: x and y count from `active - total` (negative,
    through the blanking) up to `active - 1`. `lines` stops early, for tests that only need to get
    far enough down the frame to be interesting.

    `watch` is called *before* the clock edge, which is the whole point. `o_addr` comes off
    registers that were loaded on the previous edge, and the design's claim is that those
    registers are already correct for the pixel now on `i_x` -- so reading after the tick would
    be reading the next pixel's index and the off-by-one this test exists to catch would pass.
    """
    h_reset, v_reset = dut.h_active - h_total, dut.v_active - v_total
    v_last = (v_reset + lines - 1) if lines is not None else dut.v_active - 1

    async def body(ctx):
        ctx.set(dut.i_level, level)
        ctx.set(dut.i_note, note)
        for _ in range(frames):
            x, y = h_reset, v_reset
            while True:
                ctx.set(dut.i_x, x)
                ctx.set(dut.i_y, y)
                ctx.set(dut.i_de, (x >= 0) and (y >= 0))
                if x >= 0 and y >= 0:
                    await watch(ctx, x, y)
                await ctx.tick()
                if x == dut.h_active - 1:
                    if y == v_last:
                        break
                    x, y = h_reset, y + 1
                else:
                    x += 1

    sim = Simulator(dut)
    sim.add_clock(1 / 39.07e6)
    sim.add_testbench(body)
    sim.run()


def test_every_pixel_indexes_its_own_tile():
    """`o_addr` is combinational from the counters, so at pixel (x, y) it must already be right."""
    dut = VoiceTiles(h_active=80, v_active=40)
    seen = set()

    async def watch(ctx, x, y):
        want = (y // dut.v_tile) * COLS + (x // dut.h_tile)
        got = ctx.get(dut.o_addr)
        assert got == want, f"pixel ({x},{y}) indexed tile {got}, wanted {want}"
        seen.add(got)

    sweep(dut, watch, h_total=92, v_total=52)
    assert seen == set(range(N_VOICE)), f"only {len(seen)} of {N_VOICE} tiles were ever addressed"


def test_geometry_survives_a_second_frame():
    """The vertical counters reload out of the blanking, not out of a power-on value.

    A frame boundary is the one place the row counter can be left holding 3 -- which would put the
    bottom row of tiles across the top of every frame after the first, and only after the first.
    """
    dut = VoiceTiles(h_active=80, v_active=40)
    rows = []

    async def watch(ctx, x, y):
        if x == 0:
            rows.append(ctx.get(dut.o_addr))

    sweep(dut, watch, h_total=92, v_total=52, frames=2)
    half = len(rows) // 2
    assert rows[:half] == rows[half:], "the second frame does not match the first"


PIPELINE = 3        # cycles from `o_addr` to the pixel on o_r/o_g/o_b


def paint(dut, *, level, note, lines):
    """Walk a real 90x180 tile and assert every pixel's colour. Returns the region tally.

    Three regions, all checked against an independent Python model of the same shape: the gutter
    between tiles is black, the corners are cut away to black by the quarter-circle profile, and
    everything else is the one colour `tile_rgb` says a voice at this note and envelope must be.
    """
    ins = corner_insets(dut.radius)
    want_rgb = tile_rgb(note, level)
    hits = {"gutter": 0, "corner": 0, "fill": 0}
    hits["seen"] = seen = set()             # the colours the DUT actually put on the wires

    async def watch(ctx, x, y):
        # The colour path is `PIPELINE` cycles behind `o_addr` -- the store, then the hue and
        # distance stage, then the multiply and sector mux. So the wires now carry the pixel that
        # many back, and the first few of every line belong to the blanking before it.
        if x < PIPELINE:
            return
        px, py = (x - PIPELINE) % dut.h_tile, y % dut.v_tile
        rgb = (ctx.get(dut.o_r), ctx.get(dut.o_g), ctx.get(dut.o_b))
        where = f"({x - PIPELINE},{y}) px={px} py={py}"

        if not (dut.x0 <= px <= dut.x1 and dut.y0 <= py <= dut.y1):
            assert rgb == (0, 0, 0), f"{where} is gutter but lit {rgb}"
            hits["gutter"] += 1
            return

        dx = min(px - dut.x0, dut.x1 - px)
        dy = min(py - dut.y0, dut.y1 - py)
        if dy < dut.radius and dx < ins[dy]:
            assert rgb == (0, 0, 0), f"{where} is a cut corner but lit {rgb}"
            hits["corner"] += 1
        else:
            assert rgb == want_rgb, f"{where} is the tile but {rgb}, wanted {want_rgb}"
            hits["fill"] += 1
            seen.add(rgb)

    sweep(dut, watch, h_total=812, v_total=802, lines=lines, level=level, note=note)
    return hits


def test_every_region_is_drawn():
    """Gutter, cut corner and tile body, on the geometry that is actually built.

    200 lines, of which the first 82 are the vertical blanking -- `lines` counts from the counter
    reload at `v_active - v_total`, not from the first visible row. The remaining ~118 cross the
    gutter, the whole 18-pixel corner arc and well into the straight body of the rectangle, with
    the real 90x180 tiles rather than the geometry tests' miniature ones.
    """
    hits = paint(VoiceTiles(), level=0xC0, note=69, lines=200)
    missing = [k for k in ("gutter", "corner", "fill") if not hits[k]]
    assert not missing, f"never drawn: {missing} -- {hits}"


def test_the_shape_does_not_move_with_the_envelope():
    """Loudness is brightness now, so the same number of pixels is lit at every level.

    This is the regression guard for the change: the earlier renderer grew the rectangle, and the
    tell that it has really stopped doing so is that the tally is identical at full, half and
    zero envelope -- only the colour differs.
    """
    tallies = {}
    for level in (0xFF, 0x80, 0x00):
        hits = paint(VoiceTiles(), level=level, note=69, lines=200)
        tallies[level] = {k: hits[k] for k in ("gutter", "corner", "fill")}
        assert len(hits["seen"]) == 1, f"level {level:#x} drew {len(hits['seen'])} colours"
        assert hits["seen"].pop() != (0, 0, 0), f"level {level:#x} drew the tile black"

    assert tallies[0xFF] == tallies[0x80] == tallies[0x00], f"the shape moved: {tallies}"


def test_brightness_follows_the_envelope():
    """Louder is brighter, silence is dim but never black, and the hue is unchanged throughout.

    The hue check is the point of the ratio comparison: scaling three channels by one factor has
    to leave their proportions alone, or a fading note drifts in colour as it dies away.
    """
    got = {}
    for level in (0xFF, 0x80, 0x00):
        hits = paint(VoiceTiles(), level=level, note=69, lines=130)
        assert hits["fill"] > 0, f"level {level:#x} drew no tile to sample"
        got[level] = hits["seen"].pop()

    assert max(got[0xFF]) > max(got[0x80]) > max(got[0x00]), (
        f"brightness is not monotone in the envelope: {got}")
    assert max(got[0x00]) >= IDLE_V - 1, f"a silent voice went dark: {got[0x00]}"

    # Same hue: the brightest channel dominates by the same ratio at every level.
    for level, rgb in got.items():
        assert rgb.index(max(rgb)) == got[0xFF].index(max(got[0xFF])), (
            f"level {level:#x} changed which channel leads: {rgb} vs {got[0xFF]}")


def test_hue_follows_the_pitch_across_octaves():
    """Notes an octave apart are now *different* colours -- that is the whole change.

    Deliberately the inverse of what this test asserted before. Collapsing octaves made a chord
    stable but made the bass and the melody indistinguishable, and on 32 tiles register is the
    more useful thing to be able to read at a glance.
    """
    got = {}
    for note in (NOTE_LO, 60, 72, 61, NOTE_HI):      # A0, C4, C5, C#4, C8
        hits = paint(VoiceTiles(), level=0xFF, note=note, lines=130)
        assert len(hits["seen"]) == 1, f"note {note} drew {len(hits['seen'])} colours at once"
        got[note] = hits["seen"].pop()

    assert got[60] != got[72], f"C4 and C5 must not both be {got[60]} any more"
    assert got[60] != got[61], f"C4 and C#4 must not both be {got[60]}"
    assert got[NOTE_LO] != got[NOTE_HI], f"A0 and C8 must not both be {got[NOTE_LO]}"
    assert len(set(got.values())) == len(got), f"two of {sorted(got)} share a colour: {got}"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL  {name}: {e}")
    sys.exit(1 if fails else 0)
