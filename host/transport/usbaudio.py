"""Tiliqua transport: audio up over USB Audio Class 2, MIDI down over USB-MIDI.

One cable, both directions, no FTDI and no patch lead. The gateware side is
``boards/tiliqua/gateware/usb_iface.py``; this is the host half.

Three things here are not obvious, and all three were paid for in M22's bring-up
(``ARCHITECTURE_tiliqua.md``, "The module -- measured baseline"):

**Open the stream once.** Nine open/close cycles wedged the device hard enough to
need a power cycle. ``open()`` starts one ``InputStream`` and ``close()`` stops it;
captures are bracketed with ``record_start`` / ``record_stop``, which only move a
buffer pointer.

**``blocksize=0``.** Let PortAudio choose. The measurement that motivated this (a
collapse to ~14% at ``blocksize=1024``) has since been withdrawn along with the rest
of the dropout report, but this is the configuration every clean run has been taken
on and it costs nothing to keep.

**The gap machinery is a measurement first, a repair second.** It was built for a
reported 2.5-5% of frames arriving all-zero device-side; that figure did not survive
re-measurement -- see ``docs/TILIQUA_USB_DROPOUTS.md`` -- and the rate on a
correctly-clocked board is ~0.001%. What it is still worth: the gateware keeps
channel 2 non-zero at all times, so a dropped frame is exactly "all four channels
zero" even during digital silence, ``record_stop`` repairs any holes rather than
discarding the capture, and every report publishes the rate it measured. That last
part is the point -- an unmeasured transport is how a bad number goes unnoticed for
weeks. See ``_repair`` for why repairing beats windowing.

Channels 2 and 3 are not audio: together they carry a 31-bit count of ``audio`` clock
cycles, which is how ``audio_clock_hz`` reports the board's real clock from the host
side. That number is the whole tuning of the instrument -- it comes from the SI5351,
which only the bootloader programs, so a load made while the module is running some
other slot silently detunes everything. See the rate note in
``boards/tiliqua/gateware/xls_core.py`` and the load recipe in
``boards/tiliqua/board.py``.

Scale: PortAudio hands back 24-bit samples left-justified in ``int32``, and the
gateware's ASQ word is the engine's own signed 1.15 sample shifted left by 8. So
``int32 >> 16`` recovers the engine's 16-bit sample exactly, and one more doubling
undoes the 6 dB Eurorack pad that ``xls_core.py`` applies on the way out. That lands
the result in the same +-32768 domain as the Basys 3 UART, which is what every
threshold in ``test/`` is calibrated against.
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from boards import get_board                                          # noqa: E402

from transport.base import Transport                                  # noqa: E402

#: Substring matched against device names. Overridable per side, same shape as XLS32_PORT.
#: The full `iProduct` from usb_iface.py, not just "Tiliqua": the vendor's own slots enumerate
#: as a bare "Tiliqua" UAC2 device with the same 4 in / 4 out shape, so a looser match happily
#: opens a bitstream that is not ours, streams silence, and reports it as a synth failure.
DEFAULT_MATCH = "Tiliqua XLS32"
#: Seconds to wait after the stimulus ends for the last USB packets to be delivered.
DRAIN_S = 0.08
#: What clk0 has to be for the design to be in tune (see xls_core.py's rate note).
NOMINAL_AUDIO_CLOCK = 12.288e6
#: mclk divider in the pmod's I2STDM: lrck = clkdiv[7], so 256 audio cycles per frame.
CYCLES_PER_FRAME = 256
#: How far the measured clock may sit from nominal. Deliberately tighter than the 50-cent
#: pitch bar (2.9%), so a clock bad enough to detune the instrument is caught here first,
#: where the message can name the cause; and loose enough not to trip on the ~0.3% the
#: wall-clock reference itself is worth over a two-second capture.
CLOCK_TOL = 0.02


def _match(want, names):
    """Indices of `names` containing `want`, case-insensitively."""
    w = want.lower()
    return [i for i, n in enumerate(names) if w in n.lower()]


def find_audio_device(want=None, min_channels=4):
    """PortAudio index of the board's input. Set XLS32_AUDIO_DEV to disambiguate."""
    import sounddevice as sd

    want = want or os.environ.get("XLS32_AUDIO_DEV") or DEFAULT_MATCH
    devices = sd.query_devices()
    usable = [i for i, d in enumerate(devices) if d["max_input_channels"] >= min_channels]
    hits = [i for i in usable if want.lower() in devices[i]["name"].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(
            f"no audio input device matching {want!r} with >= {min_channels} channels.\n"
            "  is the board powered on and loaded with the XLS32 bitstream?\n"
            "  inputs seen: "
            + (", ".join(f"{devices[i]['name']!r}" for i in usable) or "(none)")
            + "\n  set XLS32_AUDIO_DEV to a substring of the right one."
        )
    raise SystemExit(
        f"{want!r} matched {len(hits)} input devices: "
        + ", ".join(f"{devices[i]['name']!r}" for i in hits)
        + "\n  set XLS32_AUDIO_DEV to something more specific."
    )


def find_midi_port(want=None):
    """Name of the board's MIDI destination. Set XLS32_MIDI_DEV to disambiguate."""
    mido = _import_mido()
    want = want or os.environ.get("XLS32_MIDI_DEV") or DEFAULT_MATCH
    names = mido.get_output_names()
    hits = _match(want, names)
    if len(hits) == 1:
        return names[hits[0]]
    if not hits:
        raise SystemExit(
            f"no MIDI output matching {want!r}.\n"
            "  destinations seen: " + (", ".join(repr(n) for n in names) or "(none)")
            + "\n  set XLS32_MIDI_DEV to a substring of the right one."
        )
    raise SystemExit(
        f"{want!r} matched {len(hits)} MIDI outputs: "
        + ", ".join(repr(names[i]) for i in hits)
        + "\n  set XLS32_MIDI_DEV to something more specific."
    )


def _import_mido():
    """mido with a backend, or an error that says how to get one.

    python-rtmidi lives in the optional `localmidi` extra because it needs a C
    toolchain and the Basys 3 flow has never wanted it. Without it mido raises an
    ImportError from inside its backend loader, which reads like a mido bug.
    """
    try:
        import mido

        mido.get_output_names()
        return mido
    except ImportError as e:
        raise SystemExit(
            f"MIDI backend unavailable ({e}).\n"
            "  the Tiliqua transport sends MIDI over USB, which needs python-rtmidi:\n"
            "    uv sync --extra localmidi"
        )


class UsbAudioTransport(Transport):
    """UAC2 capture + USB-MIDI playback against one board."""

    def __init__(self, board=None):
        self._board = board or get_board()
        self.sr = self._board.sr
        self.channels = 1                    # read_frames returns engine channel 0
        opts = self._board.transport_opts or {}
        self._dev_channels = opts.get("channels", 4)
        self._dtype = opts.get("dtype", "int32")
        self._stream = None
        self._midi = None
        self._parser = None
        self._blocks = None
        self._stamps = None
        self._recording = False
        self._sink_q = None                  # continuous monitor; see stream_start
        self._sink_t = None
        self._sink_done = None
        #: Fraction of frames dropped in the most recent record_stop, or None.
        self.gap_rate = None
        #: Frames the device produced that never arrived at all, in the most recent capture.
        #: A different failure from `gap_rate` -- see `_measure_clock`.
        self.missing_frames = None
        #: Longest uninterrupted run in the most recent capture, in frames.
        self.longest_clean = None
        #: Longest single dropout in the most recent capture, in frames.
        self.longest_gap = None
        #: Measured board audio clock in Hz, from the channel-2 counter, or None.
        self.audio_clock_hz = None
        #: Frames the device produced per frame the host received (1.0 when in lock).
        self.device_frames_per_host_frame = None
        #: Frames per second the host actually received, measured against the wall clock.
        self.host_fs = None

    # ---- lifecycle ----
    def open(self):
        if self._stream is not None:
            return self
        import sounddevice as sd

        mido = _import_mido()
        self._midi = mido.open_output(find_midi_port())
        self._parser = mido.Parser()

        self._blocks = []
        self._stamps = []
        dev = find_audio_device(min_channels=self._dev_channels)

        def cb(indata, frames, t, status):
            # Deliberately trivial: anything expensive here shows up as dropouts. The
            # timestamp is the host's own clock on purpose -- PortAudio's `t` is derived
            # from the device, and the point of it is to measure the device against
            # something independent.
            if self._recording:
                self._stamps.append(time.monotonic())
                self._blocks.append(indata.copy())
            q = self._sink_q                 # continuous monitor, independent of record_*
            if q is not None:                # hand off raw; the worker does the arithmetic
                try:
                    q.put_nowait(indata.copy())
                except Exception:
                    pass                     # sink fell behind: drop, never stall the callback

        self._stream = sd.InputStream(
            device=dev,
            channels=self._dev_channels,
            dtype=self._dtype,
            samplerate=self.sr,
            blocksize=0,                     # PortAudio picks; forcing 1024 loses 86% of frames
            callback=cb,
        )
        self._stream.start()
        return self

    def close(self):
        self.stream_stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._midi is not None:
            self._midi.close()
            self._midi = None
        self._blocks = None

    # ---- MIDI down ----
    def send_midi(self, data):
        if self._midi is None:
            raise RuntimeError("transport not open")
        # mido speaks messages, the rest of the host speaks bytes. Parsing rather than
        # blitting also means a malformed burst fails here instead of confusing the
        # engine's DSLX parser.
        self._parser.feed(bytes(data))
        for msg in self._parser:
            self._midi.send(msg)

    # ---- audio up ----
    def record_start(self):
        if self._stream is None:
            raise RuntimeError("transport not open")
        self._blocks.clear()
        self._stamps.clear()
        self._recording = True

    def record_stop(self, select_clean=False):
        """Stop accumulating and return the capture as signed samples.

        `select_clean` returns only the longest uninterrupted run instead of
        repairing; use it when artefact-free samples matter more than duration.
        """
        import numpy as np

        time.sleep(DRAIN_S)                  # let the last packets land before we look
        self._recording = False
        blocks, self._blocks = self._blocks, []
        stamps, self._stamps = self._stamps, []
        if not blocks:
            self.gap_rate = self.longest_clean = self.longest_gap = None
            self.audio_clock_hz = self.device_frames_per_host_frame = None
            self.host_fs = None
            return []

        frames = np.concatenate(blocks)
        # A delivered frame is never all zero, because channel 2 is never zero; an
        # all-zero frame is a dropout. Both facts come from the gateware -- see the
        # comment on the tee in boards/tiliqua/gateware/top.py.
        alive = frames[:, 2] != 0 if frames.shape[1] > 2 else np.any(frames != 0, axis=1)

        self.gap_rate = float(1.0 - alive.mean())
        self.longest_clean = int(_longest_run(alive))
        self.longest_gap = int(_longest_run(~alive))
        self._measure_clock(frames, alive, blocks, stamps)

        # >> 16 to undo the 24-bit-in-int32 left justification and ASQ's 8-bit shift,
        # then * 2 to undo the board's 6 dB output pad.
        audio = (frames[:, 0].astype(np.int64) >> 16) * 2

        if select_clean:
            lo, hi = _longest_span(alive)
            audio, alive = audio[lo:hi], alive[lo:hi]
        elif not alive.all():
            audio = _repair(audio, alive)
            alive = np.ones_like(alive)

        if alive.any():
            audio = audio - int(round(float(audio[alive].mean())))
        return audio.astype(np.int64).tolist()

    def read_frames(self, n):
        self.record_start()
        time.sleep(n / self.sr)
        return self.record_stop()[:n]

    # ---- continuous monitoring (see Transport.stream_start) ----
    def stream_start(self, cb, chunk=512):
        """The InputStream is already running, so this only attaches a sink to it.

        The arithmetic runs on a worker rather than in the PortAudio callback, which the
        note above `cb` in `open()` asks to stay trivial: `blocksize=0` means block sizes
        vary, so fixed-size chunking has to buffer, and buffering in the audio callback is
        how dropouts start. The queue is bounded and drops on full for the same reason --
        a slow consumer must cost frames, not the stream.
        """
        import queue
        import threading
        import numpy as np

        self.stream_stop()
        self.open()
        q = queue.Queue(maxsize=64)
        done = threading.Event()

        def worker():
            pend = []
            have = 0
            while not done.is_set():
                try:
                    block = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    # ch 0/1 only: 2/3 carry the clock counter, not audio. >> 16 recovers
                    # the engine's 16-bit sample from the 24-bit-left-justified int32 and
                    # * 2 undoes the 6 dB Eurorack pad -- the same scaling record_stop uses.
                    pend.append(((block[:, :2].astype(np.int64) >> 16) * 2).astype(np.int32))
                    have += len(pend[-1])
                    while have >= chunk:
                        buf = np.concatenate(pend) if len(pend) > 1 else pend[0]
                        cb(buf[:chunk])
                        rest = buf[chunk:]
                        pend = [rest] if len(rest) else []
                        have = len(rest)
                except Exception as e:
                    print(f"[usbaudio] stream hiccup (continuing): {e}")
                    pend, have = [], 0

        self._sink_t = threading.Thread(target=worker, daemon=True)
        self._sink_done = done
        self._sink_t.start()
        self._sink_q = q                     # last: the callback starts pushing the moment this lands
        return self

    def stream_stop(self):
        t = self._sink_t
        self._sink_q = self._sink_t = None   # first: stop the callback pushing
        if self._sink_done is not None:
            self._sink_done.set()
            self._sink_done = None
        if t is not None:
            t.join(timeout=1.0)

    # ---- clock ----
    def _measure_clock(self, frames, alive, blocks, stamps):
        """Derive the board's audio clock from the counter on channels 2 and 3.

        Those two channels carry one 31-bit count of `audio` clock cycles, latched when
        the frame was teed: ch2 the low 15 bits (bit 15 is the alive marker, masked off
        here), ch3 the high 16. Both halves ride the same frame, so the value is never
        torn.

        The measurement is end-to-end and nothing else: the counter at the last
        delivered frame minus the counter at the first, over the wall-clock time
        between them. Per-frame deltas look tempting and are not usable -- USB delivery
        arrives in bursts, so their median reports the rate inside a burst and their
        mean is dominated by the refill jumps. Counting cycles over a known interval has
        neither failure mode. (The host-side interval below *is* a median, of a
        different quantity -- callback arrivals, which are not bursty.)
        """
        import numpy as np

        self.audio_clock_hz = self.device_frames_per_host_frame = None
        self.host_fs = self.missing_frames = None
        if frames.shape[1] < 4 or len(stamps) < 2:
            return

        # Wall-clock frame rate: the median seconds-per-frame across consecutive
        # callbacks. This is the reference the device is judged against, so it has to
        # be the robust statistic and not the tidy one. A least-squares fit over the
        # same stamps was tried first and reads 3% low on the first capture after the
        # stream opens, because PortAudio's delivery has not settled yet -- measured
        # per-frame intervals of 20.8 to 34.8 us in that first capture against 23.55 to
        # 23.68 in every later one. A fit spreads that transient over the whole slope;
        # the median steps around it, and lands within 0.3% on the same data.
        sizes = np.asarray([len(b) for b in blocks], dtype=float)
        if len(sizes) < 2 or stamps[-1] <= stamps[0]:
            return
        spf = float(np.median(np.diff(np.asarray(stamps, dtype=float)) / sizes[:-1]))
        if spf <= 0:
            return
        self.host_fs = 1.0 / spf

        live = np.flatnonzero(alive)
        if len(live) < 2:
            return
        lo, hi = int(live[0]), int(live[-1])
        word = (frames[:, 2].astype(np.int64) >> 16) & 0x7FFF
        word |= ((frames[:, 3].astype(np.int64) >> 16) & 0xFFFF) << 15

        # Frames that never arrived. `gap_rate` cannot see these and never could: a dropped frame
        # *arrives* as zeros and holds its slot, so the timeline stays intact, which is the whole
        # premise `_repair` rests on. A frame the host never received leaves nothing behind -- the
        # samples either side sit adjacent in the array and the capture is simply short. The
        # counter is the only witness, because it counts what the device produced.
        #
        # The wrap is read as signed. 31 bits at 12.288 MHz wraps every 175 s so it cannot happen
        # inside a capture, and an unsigned read turns a counter that stepped *backwards* -- which
        # it does when the host is handed a superseded buffer -- into an 8.4 M-frame forward jump.
        #
        # -1 is not a lost frame: the count is latched crossing into the frame's own domain, so a
        # boundary can be seen one cycle early. It happens a few hundred times per second and only
        # ever downwards. Only positive jumps are counted.
        step = ((np.diff(word[live].astype(np.int64)) + (1 << 30)) % (1 << 31)) - (1 << 30)
        short = (step - np.diff(live) * CYCLES_PER_FRAME) // CYCLES_PER_FRAME
        self.missing_frames = int(short[short > 0].sum())

        cycles = int(word[hi] - word[lo]) % (1 << 31)
        seconds = (hi - lo) * spf
        if seconds <= 0:
            return
        self.audio_clock_hz = cycles / seconds
        # 256 audio cycles per codec frame is fixed in the pmod's I2STDM, so this is a
        # derived number, not a second measurement -- but it is the one that says at a
        # glance how much the device overproduced relative to what the host took.
        self.device_frames_per_host_frame = (
            self.audio_clock_hz / CYCLES_PER_FRAME / self.host_fs)

    def clock_note(self):
        """One line about the audio clock, or None if it was not measured.

        Returns a `(text, ok)` pair. `ok` is False when the clock is off by more than
        `CLOCK_TOL` -- in practice that means an SI5351 `clk0` left behind by another
        slot, which no amount of rebuilding will fix and which a power cycle alone does
        not fix either. `boards/tiliqua/board.py` has the recipe.
        """
        if self.audio_clock_hz is None:
            return None
        ratio = self.audio_clock_hz / NOMINAL_AUDIO_CLOCK
        ok = abs(ratio - 1.0) < CLOCK_TOL
        text = (f"audio clock {self.audio_clock_hz/1e6:.3f} MHz "
                f"(nominal {NOMINAL_AUDIO_CLOCK/1e6:.3f}, ratio {ratio:.3f}); "
                f"host took {self.host_fs:.0f} frame/s of "
                f"{self.device_frames_per_host_frame:.2f} produced")
        return text, ok


def _longest_run(mask):
    """Length of the longest run of True in a boolean array."""
    import numpy as np

    if not mask.any():
        return 0
    # Run lengths via the positions where the value changes.
    edges = np.flatnonzero(np.diff(mask.astype(np.int8)))
    bounds = np.concatenate(([0], edges + 1, [len(mask)]))
    lengths = np.diff(bounds)
    return int(max((L for L, s in zip(lengths, bounds[:-1]) if mask[s]), default=0))


def _longest_span(mask):
    """(start, stop) of the longest run of True."""
    import numpy as np

    edges = np.flatnonzero(np.diff(mask.astype(np.int8)))
    bounds = np.concatenate(([0], edges + 1, [len(mask)]))
    best, span = 0, (0, len(mask))
    for s, e in zip(bounds[:-1], bounds[1:]):
        if mask[s] and e - s > best:
            best, span = e - s, (int(s), int(e))
    return span


def _repair(audio, alive):
    """Interpolate across dropped frames, in place on the timeline.

    This is not splicing. A dropped frame *arrives*, as zeros -- the device sends it,
    it just has no data in it -- so the sample timeline is intact and the hole has a
    known length. Filling it linearly keeps every later sample at exactly the time it
    belongs, which is what the phase-sensitive checks (pitch, echo delay, LFO rate,
    ADSR timing) depend on.

    Windowing to the longest uninterrupted run would be artefact-free but throws the
    duration away, and at a few percent dropout the longest clean run is tens of
    milliseconds -- far too short for an envelope or a delay-time measurement. The
    interpolated holes raise the noise floor slightly; the report states the gap rate
    so that trade is visible rather than hidden.
    """
    import numpy as np

    idx = np.arange(len(audio))
    return np.interp(idx, idx[alive], audio[alive]).round()
