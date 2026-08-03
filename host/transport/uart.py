"""Basys 3 transport: 2 Mbaud serial over the FT2232's channel B.

macOS won't set a non-standard baud through termios, hence the IOSSIOSPEED ioctl.
The board streams raw 16-bit little-endian samples with no framing at all, so
reassembly has to guess byte alignment — see samples_from_bytes.

Moved verbatim out of host/uartaudio.py in M20; the wire behaviour is unchanged.
"""
import os, sys, time, glob, termios, fcntl, array

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from boards import get_board                                          # noqa: E402

from transport.base import Transport                                  # noqa: E402

IOSSIOSPEED = 0x80045402      # macOS ioctl to set an arbitrary baud rate
DEFAULT_BAUD = 2000000        # 100 MHz / 50 (2 Mbaud: lets the board stream 32 kHz in real time)

_b = get_board()
BAUD = _b.transport_opts.get("baud", DEFAULT_BAUD) if _b.transport == "uart" else DEFAULT_BAUD

def find_port():
    """The board's UART. With more than one board plugged in, set XLS32_PORT to a full
    /dev path or to any substring of one (e.g. the board's FTDI serial) to pick — otherwise
    which board you get is just whichever serial number happens to sort last."""
    want = os.environ.get("XLS32_PORT", "")
    if want.startswith("/dev/"):
        return want
    for _ in range(10):
        p = sorted(glob.glob("/dev/cu.usbserial-*"))
        if want:
            p = [d for d in p if want in d]
        if p:
            return p[-1]                      # channel B (…1) = UART; …0 = JTAG
        time.sleep(0.5)
    seen = sorted(glob.glob("/dev/cu.*"))
    sys.exit(f"no {'matching ' if want else ''}/dev/cu.usbserial-* port found "
             f"(board connected, powered on, and flashed?)"
             + (f"\n  XLS32_PORT={want!r} matched none of: " if want else "\n  serial ports present: ")
             + (", ".join(seen) or "(none)"))

def list_ports():
    """Every board UART currently enumerated — both channels of every FTDI on the bus."""
    return sorted(glob.glob("/dev/cu.usbserial-*"))

def open_port(rw=False, baud=BAUD):
    dev = find_port()
    flags = (os.O_RDWR if rw else os.O_RDONLY) | os.O_NOCTTY | os.O_NONBLOCK
    fd = os.open(dev, flags)
    a = termios.tcgetattr(fd)
    a[2] = termios.CS8 | termios.CLOCAL | termios.CREAD; a[0] = 0; a[1] = 0; a[3] = 0
    a[4] = a[5] = termios.B9600               # placeholder; real speed set below
    termios.tcsetattr(fd, termios.TCSANOW, a)
    fcntl.ioctl(fd, IOSSIOSPEED, array.array('i', [baud]), True)
    return dev, fd

def read_bytes(fd, secs):
    termios.tcflush(fd, termios.TCIFLUSH); time.sleep(0.05); termios.tcflush(fd, termios.TCIFLUSH)
    buf = bytearray(); t0 = time.time()
    while time.time() - t0 < secs:
        try:
            c = os.read(fd, 16384); buf += c if c else b""
            if not c: time.sleep(0.001)
        except BlockingIOError:
            time.sleep(0.001)
    return bytes(buf)

def writeall(fd, data):
    """The port is O_NONBLOCK, so a big burst (a reset is 150+ MIDI messages)
    partial-writes and silently drops bytes -> missing note-ons, corrupt CCs."""
    mv = memoryview(bytes(data))
    while mv:
        try:
            mv = mv[os.write(fd, mv):]
        except BlockingIOError:
            time.sleep(0.0005)

class Recorder:
    """Background thread that continuously drains the FTDI RX buffer so it never
    overflows while the main thread sends MIDI. At 1 Mbaud a single dropped byte
    misaligns the whole 16-bit stream (-> noise), so draining must not stall."""
    def __init__(self, fd):
        import threading
        termios.tcflush(fd, termios.TCIFLUSH)
        self.fd = fd; self.buf = bytearray(); self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True); self._t.start()
    def _loop(self):
        while self._run:
            try:
                c = os.read(self.fd, 65536)
                if c: self.buf.extend(c)
                else: time.sleep(0.0003)
            except BlockingIOError:
                time.sleep(0.0003)
    def stop(self):
        self._run = False; self._t.join(); return bytes(self.buf)

def samples_from_bytes(buf, stereo=True):
    """Little-endian unsigned 16-bit (centered 32768). Auto-pick byte alignment:
    real audio is smooth, a 1-byte-shifted stream is noise. The board streams STEREO
    (L,R interleaved, 4 bytes/frame), so by default we de-interleave to one channel at
    the true Fs — otherwise every tone reads an octave low (2x samples). Pass stereo=False
    for a mono board build."""
    def decode(off):
        n = (len(buf) - off) // 2
        return [buf[off + 2*i] | (buf[off + 2*i + 1] << 8) for i in range(n)]
    a0, a1 = decode(0), decode(1)
    def rough(a):
        seg = a[200:1200] if len(a) > 1200 else a
        return sum(abs(seg[i] - seg[i-1]) for i in range(1, len(seg))) or 1
    chosen = a0 if rough(a0) <= rough(a1) else a1
    return chosen[::2] if stereo else chosen


def _decode(raw, off):
    n = (len(raw) - off) // 2
    return [(raw[off + 2 * i] | (raw[off + 2 * i + 1] << 8)) - 32768 for i in range(n)]

def best_align(raw):
    """Pick the 2-byte phase with fewer glitches over the WHOLE signal — more robust
    than samples_from_bytes' fixed [200:1200] smoothness window for choppy audio
    (rapid retrigger, plucks) where that window can land on silence.

    Lived in test/harness.py until M25. Guessing byte alignment is a property of a
    framing-free UART, not of the test harness: USB delivers whole frames and has
    nothing to guess, so the Tiliqua transport has no counterpart to this.

    Note it does *not* de-interleave, where samples_from_bytes does — moved verbatim,
    because changing it would move every Basys 3 score at the same time as the port."""
    if len(raw) < 8:
        return []
    from synth import glitches
    a, b = _decode(raw, 0), _decode(raw, 1)
    return a if glitches(a) <= glitches(b) else b


class UartTransport(Transport):
    """Transport view of the helpers above, for code written against the ABC."""

    def __init__(self, board=None):
        self._board = board or get_board()
        self.sr = self._board.sr
        self.channels = 1                     # samples_from_bytes de-interleaves to one
        self.dev = None
        self.fd = None
        self._rec = None

    def open(self):
        if self.fd is None:
            self.dev, self.fd = open_port(rw=True)
        return self

    def close(self):
        if self._rec is not None:
            self._rec.stop(); self._rec = None
        if self.fd is not None:
            os.close(self.fd); self.fd = None; self.dev = None

    def send_midi(self, data):
        writeall(self.fd, data)

    def record_start(self):
        if self._rec is not None:
            self._rec.stop()
        self._rec = Recorder(self.fd)         # its ctor flushes, so old audio is dropped

    def record_stop(self):
        if self._rec is None:
            return []
        raw, self._rec = self._rec.stop(), None
        return best_align(raw)

    def read_frames(self, n):
        from synth import to_signed
        if self._rec is None:
            self.record_start()
        want = n * 4 + 4096                   # 4 bytes/frame, plus slack for alignment
        t0 = time.time()
        while len(self._rec.buf) < want and time.time() - t0 < n / self.sr + 1.0:
            time.sleep(0.002)
        buf = bytes(self._rec.buf); self._rec.buf.clear()
        return to_signed(samples_from_bytes(buf, stereo=self._board.stereo))[:n]
