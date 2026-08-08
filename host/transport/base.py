"""The contract between the host tools and whatever carries audio off the board.

Basys 3 streams 16-bit stereo up a 2 Mbaud UART; Tiliqua presents itself as a UAC2
sound card on its own USB HS PHY. Those have nothing in common at the wire level,
so this is the line they meet at.

Every tool that talks to a board now comes through here: the graded suite (M25), the
web UI bridge and the three presetgen hardware tools (M27). What still calls the
fd-level helpers in ``transport.uart`` directly is ``uart.py`` itself, plus the
one-off Basys 3 scripts under ``host/demos/``. New code should take a ``Transport``.
"""
from abc import ABC, abstractmethod


class Transport(ABC):
    """Bidirectional link to a running synth: MIDI down, audio up."""

    #: Sample rate of the frames read back, in Hz.
    sr = 0
    #: Channels per frame in the data returned by read_frames.
    channels = 1

    @abstractmethod
    def open(self):
        """Acquire the device. Idempotent; returns self so it can be used inline."""

    @abstractmethod
    def close(self):
        """Release the device. Safe to call when never opened."""

    @abstractmethod
    def send_midi(self, data: bytes):
        """Write raw MIDI bytes, retrying until every byte is out.

        A partial write silently drops note-ons and corrupts CCs, which is why
        this is the transport's job and not the caller's.
        """

    @abstractmethod
    def read_frames(self, n: int):
        """Return up to n frames of signed audio, blocking until they arrive or the
        link goes idle. Frames are interleaved when ``channels`` > 1."""

    @abstractmethod
    def record_start(self):
        """Begin accumulating audio, discarding anything captured before now.

        Tests do not know how many frames they want: they play a stimulus of
        unpredictable length and take whatever came back. So capture is bracketed
        rather than sized, and ``read_frames`` is the special case built on top.
        """

    @abstractmethod
    def record_stop(self):
        """Stop accumulating and return everything since ``record_start`` as signed
        samples, one channel, ready to grade."""

    # --- continuous monitoring (M27) ---
    # record_* is *bracketed*: the graded suite plays a stimulus of unpredictable length
    # and takes whatever came back. A live monitor wants the opposite -- an open-ended
    # push -- so it gets its own pair rather than a flag on record_start.
    #
    # Two channels, where record_stop returns one. Grading only ever needed channel 0,
    # but since M26 the two genuinely differ (ping-pong echo, anti-phase chorus), and the
    # monitor is the consumer that has to hear that.

    def stream_start(self, cb, chunk=512):
        """Call ``cb(frames)`` with each block as it arrives, until ``stream_stop``.

        ``frames`` is an ``(n, 2)`` int32 array of int16-domain signed samples, L then R.
        Independent of ``record_start``/``record_stop``; the callback runs on the
        transport's own thread and must not raise.
        """
        raise NotImplementedError(f"{type(self).__name__} cannot stream continuously")

    def stream_stop(self):
        """Stop the ``stream_start`` push. Safe to call when never started."""

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False


def open_transport(board=None):
    """Construct the Transport for a board descriptor (default: the selected board)."""
    from boards import get_board

    board = board or get_board()
    if board.transport == "uart":
        from transport.uart import UartTransport

        return UartTransport(board)
    if board.transport == "usbaudio":
        from transport.usbaudio import UsbAudioTransport

        return UsbAudioTransport(board)
    raise NotImplementedError(
        f"board {board.name!r} wants transport {board.transport!r}, "
        f"which is not implemented yet ({board.unsupported or 'see the board package'})"
    )
