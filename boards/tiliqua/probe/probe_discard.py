"""Issue #9, the unlooked-at half: does the Tiliqua's UAC2 path discard frames silently?

    XLS32_BOARD=tiliqua uv run boards/tiliqua/probe/probe_discard.py

On the Basys 3 UART, flushing and then not reading for >= 5 ms makes something between the FT2232
and the tty throw away ~6 kB, with no marker and no error -- the capture just has a seam in it. #9
records the dose response and says the equivalent question has never been asked of the Tiliqua.
This asks it.

**Why the answer is not obviously "no".** ``record_start``/``record_stop`` only move a buffer
pointer; the ``InputStream`` runs from ``open()`` to ``close()``, so between captures PortAudio's
callback still fires and still hands over frames, which ``usbaudio.py`` drops on the floor because
``_recording`` is False. Structurally there is no interval where the host stops collecting, which
is the condition the UART bug needs. But "structurally cannot happen" is an argument, and #9 exists
because an argument of exactly that shape was wrong about the UART.

**The instrument.** Channels 2 and 3 carry a 31-bit count of ``audio`` cycles latched when the frame
was teed, 256 per frame, and it counts frames the *device produced* whether or not any arrived. So
between two delivered frames the counter must advance by exactly 256 per frame of separation, and
anything more is frames that went missing on the way. No wall clock, no reference, no calibration --
the board timestamps its own output and the arithmetic closes or it does not.

That also separates the two failure modes, which ``gap_rate`` alone cannot:

* a **zero-filled** frame arrived, carrying no data. It occupies its slot, so the counter still
  lines up. This is what ``usbaudio.py`` measures and repairs.
* a **missing** frame never arrived at all. Nothing marks it, the samples either side sit adjacent
  in the array, and ``gap_rate`` reads 0.000 % while the timeline is short. This is the UART's
  failure mode, and until this script nothing here could see it.

**Two arms**, because they ask different questions:

``idle``  -- the real production shape. Leave a pause between captures with the callback still
             running, and look at the first frames after it. If the structural argument holds this
             is flat at every pause length, and *that is the result*, not a non-result.
``stall`` -- the UART's shape, forced. Sleep inside the callback so the host genuinely stops
             collecting while the device keeps producing. Something must give; the question is
             whether it gives loudly (``input_overflow``) or silently (frames gone, no flag).

The second arm is the one with a decision attached. ``usbaudio.py``'s callback takes PortAudio's
``status`` argument and ignores it, so if the answer is "loudly", the production transport is
throwing away the one signal that would have made this visible.

**Measured 2026-08-21**, 24-voice build from flash slot 7, 1 s captures, PortAudio choosing 4096-
frame blocks (85.3 ms):

* the **idle arm is clean at every pause out to one full second** -- 0 missing, 0 zeroed, 0 flags.
  The structural argument holds, and it is now measured rather than argued. Nothing in the shipped
  capture path can reach this bug.
* the **stall arm is clean to 0.90 of a block and falls off a cliff at 1.10**: 24576 frames
  delivered of ~216000 produced, 192000 gone. So the mechanism exists, and the threshold is one
  callback period -- 85 ms here against the UART's 5 ms, about seventeen times the tolerance.
* the losses are quantised to multiples of **12000 frames, exactly 250 ms**, which is presumably
  the depth of the ring being overwritten.
* a handful of **out-of-order** deliveries appear alongside them: the counter steps backwards by
  thousands of frames, meaning the host was handed a buffer that had already been superseded.
* **``input_overflow`` was never raised. Not once, at any stall.** So the answer to the question
  above is "silently", and the fix that suggested itself before measuring -- have ``usbaudio.py``
  watch ``status`` -- would have watched a flag that never sets.

What *does* see it is the arithmetic in ``missing`` below, which needs no flag and no reference
clock because the board timestamps its own frames. That is cheap enough to run on every capture,
and it is now in ``record_stop`` as ``missing_frames``.
"""
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_ROOT, "host"))
sys.path.insert(0, _ROOT)
os.environ.setdefault("XLS32_BOARD", "tiliqua")

from boards import get_board                                          # noqa: E402
from transport.usbaudio import find_audio_device                      # noqa: E402

CAP_S = 1.00            # per capture; long enough that a 6 kB-scale loss would be unmissable
CYCLES_PER_FRAME = 256  # fixed in the pmod's I2STDM: lrck = clkdiv[7]
IDLE_MS = (0, 2, 5, 10, 20, 50, 200, 1000)      # the UART sweep, plus two longer ones
#: Sleep held inside the callback, as a fraction of the block period -- see `Rig.block_s`. The
#: absolute-milliseconds sweep this replaced (0-50 ms) found nothing and could not have: PortAudio
#: picks 85 ms blocks here, so the whole sweep fitted inside one budget with room to spare.
STALL_FRAC = (0.0, 0.25, 0.5, 0.9, 1.1, 2.0, 5.0)


def counter(frames):
    """The device's 31-bit frame counter, one value per frame.

    ch2 low 15 bits (bit 15 is the alive marker and is masked off), ch3 the high 16, both riding
    the same frame so the value cannot tear. `>> 16` first, to undo PortAudio's 24-bit-in-int32
    left justification -- the same shift `record_stop` applies to the audio channel.
    """
    lo = (frames[:, 2].astype(np.int64) >> 16) & 0x7FFF
    hi = (frames[:, 3].astype(np.int64) >> 16) & 0xFFFF
    return lo | (hi << 15)


def missing(frames):
    """Frames the device produced and the host never received. Returns (total, seams, jitter).

    Walks the delivered frames that carry a counter and asks the only question the counter can
    answer: did it advance by 256 per frame of separation? Zero-filled frames are skipped rather
    than counted as missing -- they arrived, they just have nothing in them, and conflating the two
    is what makes `gap_rate` unable to see this at all.

    `seams` lists (index, frames_lost) so a loss can be *located*. #9's UART half turned on where
    the break landed ("the same place, about 20 kB in"), not on how big it was.

    Three ways the sum can fail to be 256, and they are three different facts, so they get three
    columns rather than one total:

    * **-1, and never +1**, a few hundred times per 49152-frame capture. The count is latched
      crossing into the frame's own domain, so a boundary can be observed one cycle early. Noise,
      of the wrong sign to be a lost frame -- folding it into the total would bury the answer.
    * **a forward jump**: frames the device produced and the host never got. What this is for.
    * **backwards, by thousands of frames**: the counter cannot run backwards, so the *frames* are
      out of order -- the host was handed a buffer whose contents had already been superseded.
      Only ever seen under a callback stall past one block period.

    The wrap is read as signed for that last one. The counter is 31 bits at 12.288 MHz, so it wraps
    every 175 s and cannot genuinely wrap inside a 1 s capture; a plain `% (1 << 31)` therefore
    turns every backwards step into a ~8.4 M-frame forward jump, which is how the first run of this
    script came to report 75 million frames missing out of 16384 delivered.
    """
    alive = np.flatnonzero(frames[:, 2] != 0)
    if len(alive) < 2:
        return 0, [], 0, 0
    word = counter(frames)
    half = 1 << 30
    gaps = ((np.diff(word[alive].astype(np.int64)) + half) % (1 << 31)) - half
    span = np.diff(alive) * CYCLES_PER_FRAME
    lost = (gaps - span) // CYCLES_PER_FRAME
    seams = [(int(alive[i]), int(n)) for i, n in enumerate(lost) if n > 0]
    return (int(lost[lost > 0].sum()), seams,
            int((lost == -1).sum()), int((lost < -1).sum()))


class Rig:
    """One InputStream held open for the whole run, mirroring usbaudio.py's configuration.

    Held open deliberately: nine open/close cycles wedged this device hard enough to need a power
    cycle during M22 bring-up, and reopening per arm would also hand every arm its own settling
    transient -- `_measure_clock` records the first capture after an open reading 3 % low. One
    stream means the only thing differing between arms is the thing being swept.
    """

    def __init__(self):
        import sounddevice as sd

        board = get_board()
        self.sr = board.sr
        self.ch = (board.transport_opts or {}).get("channels", 4)
        self.blocks, self.on, self.stall = [], False, 0.0
        self.over = self.under = self.slept = 0

        def cb(indata, n, t, status):
            if status.input_overflow:
                self.over += 1
            if status.input_underflow:
                self.under += 1
            if self.on:
                self.blocks.append(indata.copy())
            if self.stall:
                self.slept += 1              # so a stall arm that did nothing cannot look clean
                time.sleep(self.stall)       # the whole point of the stall arm

        self.stream = sd.InputStream(device=find_audio_device(min_channels=self.ch),
                                     channels=self.ch, dtype="int32", samplerate=self.sr,
                                     blocksize=0, callback=cb)
        self.stream.start()

    def capture(self, seconds=CAP_S):
        self.blocks, self.over, self.under, self.slept = [], 0, 0, 0
        self.on = True
        time.sleep(seconds)
        self.on = False
        time.sleep(0.08)                     # usbaudio.DRAIN_S: let the last packets land
        b, self.blocks = self.blocks, []
        self.sizes = [len(x) for x in b]
        return np.concatenate(b) if b else np.zeros((0, self.ch), dtype=np.int32)

    def block_s(self):
        """Seconds of audio in one callback, measured rather than assumed.

        `blocksize=0` lets PortAudio choose and it chooses large -- 4096 frames, 85 ms, on this
        machine. That number decides what a meaningful stall even is: the first run of this script
        swept the callback sleep out to 50 ms and found nothing, because 50 ms of sleep inside an
        85 ms budget is not a stall, it is slack. The sweep has to be in units of this.
        """
        self.capture(0.5)
        return (max(self.sizes) if self.sizes else 1024) / self.sr

    def close(self):
        self.stall = 0.0
        self.stream.stop()
        self.stream.close()


def report(rig, label, values, unit, setup):
    print(f"\n{label}")
    print(f"  {unit:>8} | frames | zeroed | missing | back | over | under | slept | jit | seams")
    worst = flagged = 0
    for v, shown in values:
        setup(v)
        f = rig.capture()
        rig.stall = 0.0
        if len(f) < 1024:
            print(f"  {shown:>8} |  -- capture failed --")
            continue
        zero = int((f[:, 2] == 0).sum())
        lost, seams, jit, back = missing(f)
        worst = max(worst, lost)
        flagged += rig.over
        where = ", ".join(f"@{i}:{n}" for i, n in seams[:3]) + (" ..." if len(seams) > 3 else "")
        print(f"  {shown:>8} | {len(f):6d} | {zero:6d} | {lost:7d} | {back:4d} | {rig.over:4d} | "
              f"{rig.under:5d} | {rig.slept:5d} | {jit:3d} | {where or '-'}")
    return worst, flagged


def main():
    rig = Rig()
    over_at = []
    try:
        blk = rig.block_s()                  # also burns the post-open settling transient
        print(f"stream open at {rig.sr} Hz, {rig.ch} ch; {CAP_S:.2f} s per capture, "
              f"callback block {blk*1000:.1f} ms ({int(blk*rig.sr)} frames)")

        def idle(ms):
            time.sleep(ms / 1000.0)

        def stall(frac):
            # Set before the capture and cleared after, so the sleep is inside every callback the
            # capture sees. In units of the block period, because that is the budget: below 1.0 the
            # host still finishes each block in time and nothing is being asked of the buffering.
            rig.stall = frac * blk

        a, _ = report(rig, "idle between captures, callback still collecting",
                      [(ms, f"{ms} ms") for ms in IDLE_MS], "idle", idle)
        b, over_at = report(rig, "sleep held inside the callback, so the host stops collecting",
                            [(f, f"{f:.2f} blk") for f in STALL_FRAC], "stall", stall)

        print("\nverdict")
        if a:
            print(f"  idle arm LOSES frames (worst {a}) -- the UART's bug has an analogue here, "
                  "and record_start after a pause is where it lands")
        else:
            print("  idle arm is clean at every pause: with the callback running there is no "
                  "interval where the host stops collecting, so there is nothing to discard")
        if b:
            print(f"  stall arm loses frames (worst {b}), as it must -- the question is whether "
                  "it said so")
            print("  " + ("it did: input_overflow was raised, and usbaudio.py's callback takes "
                          "that argument and ignores it" if over_at else
                          "it did NOT: frames vanished with no flag, which is the UART failure "
                          "mode exactly"))
        else:
            print("  stall arm lost nothing either; the buffering absorbs everything swept here")
        return 0
    finally:
        rig.close()


if __name__ == "__main__":
    sys.exit(main())
