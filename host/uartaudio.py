"""Shared host helpers for the M4 synth: 1 Mbaud serial (macOS custom baud via
IOSSIOSPEED), 16-bit little-endian sample reassembly with byte-alignment
auto-detect, and MIDI helpers."""
import os, sys, time, glob, termios, fcntl, array

IOSSIOSPEED = 0x80045402      # macOS ioctl to set an arbitrary baud rate
SR = 32000                    # 100 MHz / 3125 — real-time rate with the ÷3 DSP pipeline (Vivado)
BAUD = 2000000               # 100 MHz / 50 (2 Mbaud: lets the board stream 32 kHz in real time)

def find_port():
    for _ in range(10):
        p = sorted(glob.glob("/dev/cu.usbserial-*"))
        if p:
            return p[-1]                      # channel B (…1) = UART; …0 = JTAG
        time.sleep(0.5)
    sys.exit("no /dev/cu.usbserial-* port found (board connected/flashed?)")

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

def glitches(signed, thresh=18000):
    """Count sample-to-sample jumps > thresh (dropout/misalignment signature)."""
    return sum(1 for i in range(1, len(signed)) if abs(signed[i] - signed[i-1]) > thresh)

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

def to_signed(samples):
    return [s - 32768 for s in samples]       # unsigned(center 32768) -> signed 16-bit

def normalize(signed, target=30000):
    peak = max((abs(x) for x in signed), default=1) or 1
    g = target / peak
    return [max(-32768, min(32767, int(round(x * g)))) for x in signed]

# --- MIDI ---
def note_on(n, vel=100, ch=0):  return bytes([0x90 | (ch & 0x0F), n & 0x7F, vel & 0x7F])  # ch 0-3 = part (multitimbral)
def note_off(n, ch=0):          return bytes([0x80 | (ch & 0x0F), n & 0x7F, 0])
def cc(ctrl, val, ch=0):        return bytes([0xB0 | (ch & 0x0F), ctrl & 0x7F, val & 0x7F])
def set_wave(w):          return cc(70, (w & 7) << 4)              # CC70 -> waveform 0..4 (evv[4:7])
def set_cutoff(v):        return cc(74, v)                         # CC74 -> per-voice filter cutoff
def set_reso(v):          return cc(71, v)                         # CC71 -> per-voice filter resonance
def set_fmode(m):         return cc(72, (m & 3) << 5)              # CC72 -> filter mode 0=LP 1=HP 2=BP 3=notch
def set_sub(s):           return cc(73, (s & 3) << 5)              # CC73 -> sub-osc level 0=off..3=full
def set_pw(v):            return cc(75, v)                         # CC75 -> pulse width (0..127, 64=square)
def set_detune(d):        return cc(78, (d & 3) << 5)              # CC78 -> detune 0=off 1=~3c 2=~7c 3=~13c
def set_unison(u):        return cc(80, (u & 3) << 5)              # CC80 -> unison 0=off 1=2v 2=3v 3=4v (voice-stack)
def set_vib(d):           return cc(1, (d & 3) << 5)               # CC1 mod wheel -> vibrato depth 0..3
def set_porta(t):         return cc(5, (t & 3) << 5)               # CC5 portamento 0=off 1=fast 2=med 3=slow
def set_fx(m):            return cc(83, (m & 7) << 4)              # DEPRECATED no-op: CC83 mode is ignored by the shell now (effects are depth-gated via CC94 chorus / CC95 echo / CC93 reverb). Kept for old callers.
def set_trem(d):          return cc(92, d & 0x7f)                  # CC92 tremolo depth 0..127 (continuous)
def set_volume(v):        return cc(7, v & 0x7f)                   # CC7 per-part output volume 0..127 (default 127)
def set_room(r):          return cc(91, (r & 3) << 5)              # CC91 reverb room 0=room 1=hall 2=large 3=cathedral
def set_reverb(w):        return cc(93, w & 0x7f)                  # CC93 reverb wet/send 0..127 (serial send after fx)
def set_chorus_depth(d):  return cc(94, d & 0x7f)                  # CC94 chorus depth/wet 0..127 (default 64)
def set_echo_depth(d):    return cc(95, d & 0x7f)                  # CC95 delay(echo) depth/wet 0..127 (default 64)
def set_delay_time(t):    return cc(82, t & 0x7f)                  # CC82 delay time 0..127 (~4..508 ms; default 63)
def set_dbg(m):           return cc(90, m & 0x7f)                  # CC90 DEBUG probe: 0 normal, 1 echo tap|input, 2 wptr|rptr
def set_xmode(m):         return cc(85, (m & 3) << 5)              # CC85 cross-osc 0=off 1=ring 2=FM 3=FM+
def set_xdepth(v):        return cc(86, v)                         # CC86 cross-osc depth (0..127)
def set_xratio(r):        return cc(87, (r & 7) << 4)              # CC87 mod:carrier ratio (8): 0=1 1=1.5 2=2 3=3 4=4 5=5 6=7 7=½
# ADSR (CC20-27): A/D/R take 0..127 (higher = longer, ~3ms..2s); sustain 0..127 (level).
def set_amp_attack(v):    return cc(20, v)                         # CC20 amp attack
def set_amp_decay(v):     return cc(21, v)                         # CC21 amp decay
def set_amp_sustain(v):   return cc(22, v)                         # CC22 amp sustain level
def set_amp_release(v):   return cc(23, v)                         # CC23 amp release
def set_flt_attack(v):    return cc(24, v)                         # CC24 filter-env attack
def set_flt_decay(v):     return cc(25, v)                         # CC25 filter-env decay
def set_flt_sustain(v):   return cc(26, v)                         # CC26 filter-env sustain level
def set_flt_release(v):   return cc(27, v)                         # CC27 filter-env release
def pitch_bend(norm):     # norm in [-1,1] -> 14-bit pitch bend (0xE0), center 8192
    b = max(0, min(16383, 8192 + int(norm * 8191)))
    return bytes([0xE0, b & 0x7F, (b >> 7) & 0x7F])
def set_noise():          return set_wave(4)                       # waveform 4 = white noise
def note_to_hz(n):        return 440.0 * 2 ** ((n - 69) / 12)
