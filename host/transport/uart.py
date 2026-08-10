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
    """Drain the port for `secs` from this thread, starting immediately.

    **Never put a pause between the flush and the first read.** A pause is what made this
    function lose bytes, and it took three rounds to find because the first comparison was
    confounded: this flushed twice with a 50 ms sleep between, `Recorder` flushed once and read
    at once, so "the threaded reader is clean and this one is not" was really "no pause is clean
    and a pause is not". Crossing the two factors separates them -- with the pause it breaks in
    both threading modes, without it in neither -- and sweeping the pause gives a dose response:
    0 ms 0/4, 2 ms 0/4, 5 ms 2/4, 10 ms 3/4, 20 ms 3/4, 50 ms 4/4. Over every arm measured,
    no pause 0/24 -- 18 in those arms and 6 more through this function after the change,
    alternated against `Recorder`'s own 0 of 6 -- and pause >= 5 ms 20/26. Disabling the GC
    changed nothing, and the longest stall in the loop was 7.6 ms, far too little to account for
    the ~6 kB that goes missing.

    Nobody reading for 5 ms at 2 Mbaud is ~1.3 kB with nowhere to go, and something between the
    FT2232 and the tty then drops a chunk that is not a whole number of frames. The break lands
    at the seam between the start-up backlog and the live stream, ~20 kB in, and shifts every
    frame after it -- which `samples_from_bytes`, locking alignment once, decodes as full-scale
    hash for the rest of the take.

    `UartTransport.record_start/record_stop` is still the better path where it fits: it re-locks
    the frame phase every 128 bytes, trims the backlog against the wall clock, and reports whether
    the phase held. `host/filter_demo.py` stays here on purpose -- it interleaves writes with
    45 ms reads and wants no concurrent reader."""
    termios.tcflush(fd, termios.TCIFLUSH)     # and read at once -- see above, this is load-bearing
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

    Note it does *not* de-interleave — see frame_align, which is what record_stop calls."""
    if len(raw) < 8:
        return []
    from synth import glitches
    a, b = _decode(raw, 0), _decode(raw, 1)
    return a if glitches(a) <= glitches(b) else b


def _marker_score(raw, off, limit=8000):
    """Fraction of samples whose LSB channel marker breaks the L,R,L,R (0,1,0,1) pattern."""
    end = min(off + limit, len(raw) - 1)
    n = (end - off) // 2
    if n < 4:
        return 1.0
    bad = sum(1 for k in range(n) if ((raw[off + 2*k] | (raw[off + 2*k + 1] << 8)) & 1) != (k & 1))
    return bad / n


def marker_integrity(raw, win=8000):
    """Does the frame phase hold for the WHOLE capture, or only where frame_align looked?

    frame_align picks one byte offset from the first 8000 bytes and keeps it for the entire buffer.
    Its continuous sibling `Aligner` re-locks every 8192 bytes, with the comment "a mid-stream byte
    drop self-heals within ~0.1 s" -- so the failure mode is known to exist, and the bracketed path
    has no defence against it. A byte lost after the lock window shifts every following frame: the
    "L" channel is then reassembled from (Lhi, Rlo) byte pairs, whose high halves are the *previous*
    sample's low byte -- uniform noise at full scale. That decodes as peak ~1.0 with a large
    fraction of half-scale sample-to-sample jumps, which is exactly what validate_hw.py's RAIL test
    looks for and exactly what a diverging filter would also look like. Indistinguishable from the
    samples alone, so measure the framing instead.

    Measure the phase, not the score at one phase. Scoring the whole buffer against the offset
    frame_align chose misses a drop in the first window entirely: the aligner picks whichever phase
    the *majority* of those bytes are in, so an early drop makes it lock POST-drop and every later
    window then reads clean (measured: drop at 5% -> worst score 0.13, no window over threshold).
    Each window's own best offset does not have that blind spot -- it steps 0 -> 3 at the drop and
    stays there, whatever the audio is doing.

    Returns (n_phase_changes, first_change_byte or None, n_windows, worst_score_at_own_phase).
    A capture that held frame lock returns n_phase_changes == 0.
    """
    phases, worst = [], 0.0
    for start in range(0, max(0, len(raw) - 8), win):
        if len(raw) - start < win // 2:
            break                      # too short to score; a partial tail proves nothing
        o = min(range(4), key=lambda k: _marker_score(raw, start + k, win))
        worst = max(worst, _marker_score(raw, start + o, win))
        phases.append((start, o))
    changes = [s for (s, o), (_, po) in zip(phases[1:], phases) if o != po]
    return len(changes), (changes[0] if changes else None), len(phases), worst


def frame_align(raw, stereo=True, win=128):
    """Bracketed capture -> ONE channel of signed samples, which is what Transport.record_stop
    promises. The bracketed counterpart of Aligner, and it uses the same evidence: the board
    stamps a channel marker in each sample's LSB (L=0, R=1), so the frame offset whose LSBs read
    0,1,0,1,... fixes byte alignment and L/R order at once.

    This existed as `samples_from_bytes(..., stereo=True)` before M25, when moving the harness onto
    the transport seam swapped it for `best_align`, which picks a 2-byte phase and stops there. The
    interleaved stream that came back has twice the samples, so at the 32 kHz the harness assumes
    every Basys 3 measurement since has been an octave low -- `pitch_a4` reading 220 for A4 was the
    only case whose threshold was tight enough to say so out loud. Falls back to best_align when the
    marker pattern is absent, so a mono board build still grades.

    The phase is re-locked every `win` bytes, as `Aligner` has always done for the continuous
    stream. Locking once from the first window was the whole of the M28a rail bug: the frame phase
    really does shift mid-capture on this link, and every byte after the shift then decodes as
    (Lhi, Rlo) pairs whose high halves are the previous sample's low byte -- uniform full-scale
    noise. Measured on the board: 16/128 presets "railed", all 16 with a phase change, and every one
    of 24 lock-loss captures decoded to smooth audio (jump% 45 -> 0.1) from the *same bytes* once
    re-locking was allowed. Nothing was ever wrong with the audio. See DEVELOPMENT.md, M28a.

    `win` is small because re-locking only recovers at the NEXT boundary, so the window size is
    also the amount of full-scale hash a shift leaves in the capture. Measured worst case over 40
    drop positions x 4 signal types (tone, silence, loud saw, noise), in samples of residual
    garbage: win=4000 -> 476, win=512 -> 32, win=256 -> 32, win=128 -> 1, win=64 -> 1. At 4000 that
    is up to 9 ms of noise per shift, and 88/128 captures shift, which is not nothing when the
    result feeds a spectral loss. Small windows cost neither accuracy nor time: the marker is
    stamped whatever the audio does (silence separates as cleanly as a loud saw -- the wrong phase
    reads a data byte's LSB and scores ~50% against 0%), 128 bytes still carries 64 marker bits,
    and total work is 4 * len(raw) regardless of how it is divided, so all sizes decode in ~32 ms.
    A capture whose phase never moves is byte-identical at every window size."""
    if len(raw) < 8:
        return []
    if not stereo:
        return best_align(raw)
    off = min(range(4), key=lambda o: _marker_score(raw, o))
    if _marker_score(raw, off) > 0.25:            # no usable marker -- don't trust the phase
        s = best_align(raw)
        return s[::2] if s else s
    out, pos = [], None
    for i in range(0, len(raw) - 8, win):
        # Each window picks its own phase, so a shift costs the frames between the shift and the
        # next boundary rather than the rest of the capture.
        want = i + min(range(4), key=lambda k: _marker_score(raw, i + k, win))
        if pos is None:
            pos = want
        else:
            # Carry the frame position ACROSS the boundary and step only if the phase really moved.
            # Restarting the scan at each window instead drops the 1-3 bytes that do not fit a whole
            # frame, every window -- ~32 samples per second of capture, a 0.1% timebase error that
            # would quietly bias every pitch and duration measurement taken through this path.
            pos += (want - pos) % 4
        end = min(i + win, len(raw))
        while pos + 4 <= end:                                      # a WHOLE frame must fit
            out.append((raw[pos] | (raw[pos + 1] << 8)) - 32768)   # channel 0 (L) at the true Fs
            pos += 4
    return out


class Aligner:
    """Align the continuous STEREO 16-bit stream to its 4-byte frame boundary (Llo Lhi Rlo Rhi)
    and forward the RAW aligned bytes. The board stamps a 1-bit channel marker in each sample's
    LSB (L=0, R=1), so the correct frame offset is the one whose de-interleaved samples show
    LSBs 0,1,0,1,... (L,R,L,R). This nails BOTH byte-alignment (odd offsets scramble the pattern)
    AND L/R order (offset 2 would start on R) unambiguously. Re-checked periodically so a
    mid-stream byte drop self-heals within ~0.1 s.

    The *continuous* counterpart to best_align, which does the same job for a bracketed capture
    it can see all of at once. Lived in webui/server.py until M27; guessing frame phase is a
    property of a framing-free UART, not of the web UI, and moving it here is what let the UI
    stop naming a file descriptor.
    """
    def __init__(self):
        self.buf = bytearray()
        self.locked = False
        self.since = 0

    def _score(self, off):
        # fraction of samples whose LSB marker mismatches the expected L,R,L,R (0,1,0,1) pattern
        b = self.buf; end = min(off + 4000, len(b) - 1)
        vals = [b[i] | (b[i + 1] << 8) for i in range(off, end, 2)]
        if len(vals) < 4:
            return 1e12
        bad = sum(1 for k, v in enumerate(vals) if (v & 1) != (k & 1))
        return bad / len(vals)

    def feed(self, data: bytes) -> bytes:
        self.buf += data
        if not self.locked:
            if len(self.buf) < 4096:
                return b""
            best = min(range(4), key=self._score)
            if best:
                del self.buf[:best]
            self.locked = True
        self.since += len(data)
        if self.since >= 8192 and len(self.buf) >= 4100:   # periodic re-lock (heals a byte drop)
            self.since = 0
            best = min(range(4), key=self._score)
            if best != 0 and self._score(best) < self._score(0) * 0.5:
                del self.buf[:best]
        n = len(self.buf) & ~3        # whole 4-byte stereo frames only
        if n < 4:
            return b""
        out = bytes(self.buf[:n]); del self.buf[:n]
        return out


class UartTransport(Transport):
    """Transport view of the helpers above, for code written against the ABC."""

    def __init__(self, board=None):
        self._board = board or get_board()
        self.sr = self._board.sr
        self.channels = 1                     # samples_from_bytes de-interleaves to one
        self.dev = None
        self.fd = None
        self._rec = None
        self._t0 = None                       # wall clock at record_start, for the backlog trim
        self._stream_t = None
        self._stream_run = False
        self.last_align = None                # (n_phase_changes, first_change_byte, nwin, worst)

    def open(self):
        if self.fd is None:
            self.dev, self.fd = open_port(rw=True)
        return self

    def close(self):
        self.stream_stop()
        if self._rec is not None:
            self._rec.stop(); self._rec = None
        if self.fd is not None:
            os.close(self.fd); self.fd = None; self.dev = None

    def send_midi(self, data):
        writeall(self.fd, data)

    def record_start(self):
        if self._rec is not None:
            self._rec.stop()
        self._rec = Recorder(self.fd)         # its ctor flushes the kernel queue
        self._t0 = time.monotonic()

    def record_stop(self):
        if self._rec is None:
            return []
        t0, self._t0 = self._t0, None
        raw, self._rec = self._rec.stop(), None
        # Drop the backlog that predates record_start(). `tcflush` empties the kernel input queue
        # but not the FTDI chip or the USB pipeline, which between captures fill up with whatever
        # the board was playing -- the board never stops streaming and nothing drains it while the
        # caller is sending CCs. Measured at ~20 kB, a steady 157 ms, and it lands at the FRONT of
        # every recording: a 1.3 s capture of a note was really 157 ms of stale audio plus the
        # first 1.14 s of the note, shifted against `engine.render()` by a sixth of a second.
        #
        # The excess is exactly measurable rather than guessed: the link runs at a fixed
        # sr * bytes-per-frame, so anything beyond what the elapsed wall time can account for was
        # already buffered when recording began. Verified: 1.80 s of wall clock returned 1.960 s of
        # audio, 0.80 s returned 0.960 s -- the same 157 ms both times.
        if t0 is not None:
            bpf = 4 if self._board.stereo else 2
            excess = len(raw) - int((time.monotonic() - t0) * self.sr * bpf)
            if excess > 0:
                raw = raw[excess - excess % bpf:]      # trim on a frame boundary
        # Stash whether the frame phase held for the whole capture, so a caller that gets a
        # full-scale noisy result can tell a misdecode from real audio. Attribute, not a return
        # value, so the Transport ABC and the USB-audio board are untouched -- USB delivers whole
        # frames and has nothing to guess, which is why only this transport needs the check.
        self.last_align = marker_integrity(raw) if self._board.stereo else None
        return frame_align(raw, self._board.stereo)

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

    # --- continuous monitoring (see Transport.stream_start) ---
    def stream_start(self, cb, chunk=512):
        import threading
        import numpy as np
        self.stream_stop()
        self.open()
        termios.tcflush(self.fd, termios.TCIFLUSH)
        self._stream_run = True
        nbytes = chunk * 4                    # stereo, 4 bytes/frame

        def loop():
            aln = Aligner()
            pend = bytearray()
            while self._stream_run:
                fd = self.fd
                if fd is None:
                    time.sleep(0.2); continue
                try:
                    data = os.read(fd, 65536)
                except BlockingIOError:
                    time.sleep(0.0005); continue
                except OSError:
                    time.sleep(0.2); continue
                if not data:
                    time.sleep(0.0005); continue
                try:                          # never let a transient error kill the thread:
                    pend += aln.feed(data)    # a dead reader is frozen audio, not an error message
                    while len(pend) >= nbytes:
                        raw = bytes(pend[:nbytes]); del pend[:nbytes]
                        cb(np.frombuffer(raw, dtype="<u2").astype(np.int32).reshape(-1, 2) - 32768)
                except Exception as e:
                    print(f"[uart] stream hiccup (continuing): {e}"); time.sleep(0.01)

        self._stream_t = threading.Thread(target=loop, daemon=True)
        self._stream_t.start()
        return self

    def stream_stop(self):
        if self._stream_t is None:
            return
        self._stream_run = False
        self._stream_t.join(timeout=1.0)
        self._stream_t = None
