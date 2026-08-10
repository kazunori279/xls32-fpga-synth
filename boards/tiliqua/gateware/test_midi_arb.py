# Unit sim for the MIDI merge: sources talking over each other, and what comes out the far end.
#
#   python boards/tiliqua/gateware/test_midi_arb.py
#
# M34 added three more things worth a unit sim, all of them about who the TRS jack is playing:
# MidiChanWatch (what channel is it on), MidiPartSelect.i_clear (the keyboard taking the decision
# back from the panel), and TrsPanicInject (the All Notes Off the board sends itself when the
# answer changes). None of them can be checked on hardware without a keyboard in hand.
#
# The bug this guards against is silent. Interleaved bytes do not produce an error anywhere -- the
# engine parses whatever arrives, latches the wrong running status and plays the wrong thing, or
# nothing. So the check has to be structural rather than a spot value: reassemble the output into
# messages, insist every one of them is complete and well formed, and insist each source's own
# messages come back in the order it sent them.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from amaranth.sim import Simulator

from midi_arb import CC_PART, MidiArbiter, MidiChanWatch, MidiPartSelect, TrsPanicInject

DATA_LEN = {0x8: 2, 0x9: 2, 0xA: 2, 0xB: 2, 0xC: 1, 0xD: 1, 0xE: 2}
COMMON_LEN = {0xF1: 1, 0xF2: 2, 0xF3: 1}                # the rest of 0xF0-0xF7 carry none


def messages(stream):
    """Split a byte stream into complete messages, failing on anything malformed.

    No running status is tolerated here on purpose. The whole point of the arbiter re-inserting a
    source's remembered status is that what leaves it is self-describing, so a bare data byte at
    a message boundary is the failure, not a case to handle.
    """
    out, i = [], 0
    while i < len(stream):
        st = stream[i]
        assert st & 0x80, f"byte {i} ({st:#04x}) is data where a status byte was due: {out[-3:]}"
        if st >= 0xF8:                                  # System Real-Time, one byte, no grant
            out.append((st,))
            i += 1
            continue
        n = COMMON_LEN.get(st, 0) if st >= 0xF0 else DATA_LEN.get(st >> 4, 0)
        assert i + n < len(stream), f"message {st:#04x} at {i} is missing data bytes"
        for d in stream[i + 1:i + 1 + n]:
            assert not d & 0x80, f"status byte {d:#04x} inside message {st:#04x} at {i}"
        out.append(tuple(stream[i:i + 1 + n]))
        i += 1 + n
    return out


def run(sources, gap=0, stall=0, chan=None):
    """Drive every source at once and return the merged byte stream.

    `gap` idles each source between bytes and `stall` withholds `o.ready`; between them they move
    the moment the arbiter has to decide something, which is where an off-by-one in the grant hides.
    `chan` is a per-source channel override, or None to leave that source alone.
    """
    dut = MidiArbiter(len(sources))
    seen = []

    async def tb(ctx):
        for k, c in enumerate(chan or []):
            if c is not None:
                ctx.set(dut.chan[k], c)
                ctx.set(dut.chan_en[k], 1)
        pos = [0] * len(sources)
        wait = [0] * len(sources)
        clk = 0

        while any(p < len(s) for p, s in zip(pos, sources)) or clk < 40:
            ctx.set(dut.o.ready, 0 if stall and clk % (stall + 1) else 1)
            for k, src in enumerate(sources):
                live = pos[k] < len(src) and wait[k] == 0
                ctx.set(dut.i[k].valid, live)
                if live:
                    ctx.set(dut.i[k].payload, src[pos[k]])

            if ctx.get(dut.o.valid) and ctx.get(dut.o.ready):
                seen.append(ctx.get(dut.o.payload))
            taken = [k for k in range(len(sources))
                     if ctx.get(dut.i[k].valid) and ctx.get(dut.i[k].ready)]
            await ctx.tick()

            for k in taken:
                pos[k] += 1
                wait[k] = gap
            for k in range(len(sources)):
                wait[k] = max(0, wait[k] - 1)
            clk += 1
            assert clk < 5000, "arbiter stalled"

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()
    return seen


def test_interleave():
    """Three sources, all talking from cycle zero, one of them using running status."""
    a = [0x90, 0x3C, 0x64, 0x3E, 0x64, 0x40, 0x64]      # note on, then two by running status
    b = [0xB0, 0x4A, 0x20, 0xB0, 0x47, 0x10]
    c = [0xE3, 0x00, 0x40, 0x93, 0x30, 0x50]

    for gap in (0, 1, 3):
        for stall in (0, 1, 2):
            got = messages(run([a, b, c], gap=gap, stall=stall))
            per = {0x90: [], 0xB0: [], 0xE3: [], 0x93: []}
            for msg in got:
                per.setdefault(msg[0], []).append(msg)
            assert per[0x90] == [(0x90, 0x3C, 0x64), (0x90, 0x3E, 0x64), (0x90, 0x40, 0x64)], \
                f"gap={gap} stall={stall}: running status not expanded: {per[0x90]}"
            assert per[0xB0] == [(0xB0, 0x4A, 0x20), (0xB0, 0x47, 0x10)], per[0xB0]
            assert per[0xE3] == [(0xE3, 0x00, 0x40)], per[0xE3]
            assert per[0x93] == [(0x93, 0x30, 0x50)], per[0x93]
    print("  interleave:   3 sources x 9 timings, running status expanded   PASS")


def test_fairness():
    """A source with an endless supply must not lock the others out."""
    hog = [0xB0, 0x4A, 0x20] * 40
    quiet = [0x90, 0x3C, 0x64]
    got = messages(run([hog, quiet]))
    assert (0x90, 0x3C, 0x64) in got, "the quiet source never got the bus"
    before = got.index((0x90, 0x3C, 0x64))
    assert before <= 1, f"quiet source waited {before} messages behind the hog"
    print(f"  fairness:     quiet source served after {before} message(s)     PASS")


def test_system():
    """Real-Time passes without taking a grant; System Common cancels running status."""
    # 0xFE lands between two running-status pairs. It must come out whole and must not be counted
    # as one of the note message's data bytes.
    a = [0x90, 0x3C, 0x64, 0xFE, 0x3E, 0x64]
    got = messages(run([a]))
    assert got == [(0x90, 0x3C, 0x64), (0xFE,), (0x90, 0x3E, 0x64)], got

    # 0xF1 (MTC quarter frame) carries one data byte and clears running status, so the bare pair
    # after it has no status to be re-inserted and is dropped rather than mis-attributed.
    b = [0x90, 0x3C, 0x64, 0xF1, 0x20, 0x3E, 0x64]
    got = messages(run([b]))
    assert got == [(0x90, 0x3C, 0x64), (0xF1, 0x20)], got

    # A truncated message costs its own bytes and no others. The two bytes already forwarded
    # cannot be recalled -- this is a stream, not a store-and-forward buffer -- so what the
    # guarantee actually means is that nothing foreign was spliced into the gap, and that the
    # arbiter re-arbitrated on the 0xB0 instead of eating it as a velocity. The engine agrees:
    # synth.x:114 latches the 0xB0 as a new status and abandons the half-built note.
    c = [0x90, 0x3C, 0xB0, 0x4A, 0x20]
    assert run([c]) == c, run([c])
    print("  system:       real-time, system common, truncation             PASS")


def test_rechannel():
    """A source re-addressed to another part, including the messages it never restated."""
    # Running status is the case that matters. The keyboard says 0x90 once and then sends bare
    # pairs forever; if the rewrite happened on the way *into* the running-status register instead
    # of on the way out, only the first note would move and the rest would keep playing part 1.
    a = [0x90, 0x3C, 0x64, 0x3E, 0x64]
    got = messages(run([a], chan=[2]))
    assert got == [(0x92, 0x3C, 0x64), (0x92, 0x3E, 0x64)], got

    # Every channel-voice type moves; system messages have no channel and must not be touched.
    b = [0xB0, 0x4A, 0x20, 0xC5, 0x07, 0xE0, 0x00, 0x40, 0xF1, 0x20, 0xFE]
    got = messages(run([b], chan=[3]))
    assert got == [(0xB3, 0x4A, 0x20), (0xC3, 0x07), (0xE3, 0x00, 0x40), (0xF1, 0x20), (0xFE,)], got

    # The override is per source: one keyboard moves, the other keeps the channel it sent on.
    got = messages(run([[0x90, 0x3C, 0x64], [0x90, 0x40, 0x64]], chan=[1, None]))
    assert (0x91, 0x3C, 0x64) in got and (0x90, 0x40, 0x64) in got, got

    # Unset, the arbiter is byte-for-byte what it was before the override existed.
    assert run([[0x90, 0x3C, 0x64]]) == run([[0x90, 0x3C, 0x64]], chan=[None])
    print("  rechannel:    running status, every voice type, per source       PASS")


def test_partselect():
    """CC103 -> (channel, enable), from a stream that uses running status like the bridge does."""
    dut = MidiPartSelect()
    seen = []

    async def tb(ctx):
        # CC7 first so the sniffer has to keep its place, then CC103=2 by running status, then a
        # note (which must not disturb the latch), then CC103=127 to hand the keyboard back.
        for byte in [0xB0, 0x07, 0x64, CC_PART, 0x02, 0x90, 0x3C, 0x40,
                     0xB0, CC_PART, 0x7F]:
            ctx.set(dut.i_midi.payload, byte)
            ctx.set(dut.i_midi.valid, 1)
            await ctx.tick()
            seen.append((ctx.get(dut.o_en), ctx.get(dut.o_chan)))
        ctx.set(dut.i_midi.valid, 0)
        await ctx.tick()
        seen.append((ctx.get(dut.o_en), ctx.get(dut.o_chan)))

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()

    assert seen[0] == (0, 0), f"enabled before any CC103: {seen[0]}"
    assert (1, 2) in seen, f"CC103=2 never selected part 3: {seen}"
    assert seen[-1][0] == 0, f"CC103=127 did not release the keyboard: {seen[-1]}"
    print("  partselect:   CC103 latched under running status, 127 releases   PASS")


def test_chanwatch():
    """The channel the TRS keyboard is actually transmitting on, and when it moves."""
    dut = MidiChanWatch()
    seen = []

    async def tb(ctx):
        # Channel 1 (nibble 0) first, which is also the reset value -- a board that fires a
        # cleanup at power-on because a keyboard started playing on channel 1 would be a nuisance.
        # Then a move to channel 3, an Active Sensing byte, a note-off on the same channel, and a
        # move back. Only the two real moves may pulse.
        for byte in [0x90, 0x3C, 0x64, 0x92, 0x40, 0x50, 0xFE, 0x82, 0x40, 0x00, 0xB0]:
            ctx.set(dut.i_midi.payload, byte)
            ctx.set(dut.i_midi.valid, 1)
            await ctx.tick()
            seen.append((ctx.get(dut.o_chan), ctx.get(dut.o_change)))

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()

    assert [s[1] for s in seen] == [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1], seen
    assert [s[0] for s in seen] == [0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 0], seen
    print("  chanwatch:    channel tracked, data and real-time ignored        PASS")


def test_partselect_clear():
    """`i_clear` releases the override -- unless the panel speaks on the very same cycle."""
    dut = MidiPartSelect()
    out = {}

    async def tb(ctx):
        async def byte(b, clear=0):
            ctx.set(dut.i_midi.payload, b)
            ctx.set(dut.i_midi.valid, 1)
            ctx.set(dut.i_clear, clear)
            await ctx.tick()
            return (ctx.get(dut.o_en), ctx.get(dut.o_chan))

        for b in (0xB0, CC_PART, 0x02):
            out["set"] = await byte(b)
        ctx.set(dut.i_midi.valid, 0)
        ctx.set(dut.i_clear, 1)
        await ctx.tick()
        ctx.set(dut.i_clear, 0)
        out["cleared"] = (ctx.get(dut.o_en), ctx.get(dut.o_chan))
        # Both at once. The keyboard moving and the panel clicking in the same cycle is not a
        # scenario anyone will hit deliberately, but the tie has to break somewhere, and it breaks
        # towards the panel: `i_clear` is written first and the CC overwrites it.
        for b, c in ((0xB0, 0), (CC_PART, 0), (0x03, 1)):
            out["tie"] = await byte(b, c)

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()

    assert out["set"] == (1, 2), out
    assert out["cleared"][0] == 0, out
    assert out["tie"] == (1, 3), out
    print("  partsel clr:  keyboard releases the override, panel wins a tie   PASS")


def run_inject(script):
    """Drive TrsPanicInject with a list of (i_chan, o_ready) pairs, one per clock."""
    dut = TrsPanicInject()
    seen = []

    async def tb(ctx):
        for chan, rdy in script:
            ctx.set(dut.i_chan, chan)
            ctx.set(dut.o.ready, rdy)
            if ctx.get(dut.o.valid) and rdy:
                seen.append(ctx.get(dut.o.payload))
            await ctx.tick()

    sim = Simulator(dut)
    sim.add_clock(1 / 60e6)
    sim.add_testbench(tb)
    sim.run()
    return seen


def test_panic_inject():
    """One All Notes Off per change of target, addressed to the target being left."""
    # Silent at reset. Both the override and the keyboard's channel start at 0, and a board that
    # announced itself with a CC123 every time it powered up would be its own bug report.
    assert run_inject([(0, 1)] * 10) == []

    # The message goes to the part being abandoned. Sending it to the arriving part would silence
    # the notes the player is about to hear and leave the stuck ones exactly where they were.
    assert run_inject([(0, 1)] * 3 + [(2, 1)] * 10) == [0xB0, 0x7B, 0x00]

    got = run_inject([(0, 1)] * 3 + [(2, 1)] * 6 + [(3, 1)] * 6 + [(0, 1)] * 8)
    assert got == [0xB0, 0x7B, 0x00, 0xB2, 0x7B, 0x00, 0xB3, 0x7B, 0x00], got

    # Backpressure. The arbiter withholds `ready` for as long as the keyboard is mid-message, which
    # at 31250 baud is a third of a millisecond -- tens of thousands of cycles at 60 MHz.
    assert run_inject([(0, 1)] * 2 + [(2, 0)] * 20 + [(2, 1)] * 8) == [0xB0, 0x7B, 0x00], "stalled"

    # A second change arriving mid-message. The intermediate part must get its own cleanup: it is
    # the one the notes would be stranded on, and it is the one a naive implementation forgets.
    got = run_inject([(0, 1)] * 2 + [(1, 1)] + [(2, 1)] * 12)
    assert got == [0xB0, 0x7B, 0x00, 0xB1, 0x7B, 0x00], got
    print("  panic inject: one CC123 per move, to the part being left         PASS")


def test_panic_through_arbiter():
    """The injected message reaches the engine whole, and is not re-addressed on the way."""
    # This is the top.py wiring as a claim: source 0 is the keyboard, re-addressed to part 3;
    # source 1 is the injector, whose `chan_en` stays low precisely so its CC123 keeps naming the
    # part it means. If the override applied to both, the cleanup would follow the keyboard to its
    # new part and silence the wrong one.
    keys = [0x90, 0x3C, 0x64, 0x3E, 0x64]
    panic = [0xB0, 0x7B, 0x00]
    for gap in (0, 1, 3):
        for stall in (0, 2):
            got = messages(run([keys, panic], gap=gap, stall=stall, chan=[3, None]))
            assert (0xB0, 0x7B, 0x00) in got, f"gap={gap} stall={stall}: {got}"
            assert (0x93, 0x3C, 0x64) in got and (0x93, 0x3E, 0x64) in got, got
    print("  panic + arb:  survives interleaving, keeps its own channel       PASS")


if __name__ == "__main__":
    test_interleave()
    test_fairness()
    test_system()
    test_rechannel()
    test_partselect()
    test_chanwatch()
    test_partselect_clear()
    test_panic_inject()
    test_panic_through_arbiter()
