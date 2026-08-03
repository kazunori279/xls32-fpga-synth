"""The contract between the host tools and whatever carries audio off the board.

Basys 3 streams 16-bit stereo up a 2 Mbaud UART; Tiliqua will present itself as a
UAC2 sound card on its own USB HS PHY. Those have nothing in common at the wire
level, so this is the line they meet at.

Existing Basys 3 tools still call the fd-level helpers in ``transport.uart``
directly — the seam is deliberately visible, so the lines M25 has to touch are the
ones that name a transport. New code should take a ``Transport``.
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
        f"which is not implemented yet ({board.unsupported or 'see docs/TILIQUA_PORT.md'})"
    )
