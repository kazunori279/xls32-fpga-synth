"""Test harness: owns the board, resets it between tests, captures audio (with a
misalignment-retry), and models a TestCase / Result + 0-100 scoring. Reuses the
proven host helpers; drives the real board over the FT2232 channel-B UART."""
import os, sys, time, wave, struct
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "host"))
sys.path.insert(0, os.path.join(_ROOT, "webui"))
from uartaudio import (open_port, Recorder, samples_from_bytes, to_signed, normalize,   # noqa: E402
                       glitches, note_on, note_off, cc, pitch_bend, SR)
import synthspec                                                                          # noqa: E402
import analysis as A                                                                      # noqa: E402

# ---- robust write: the port is O_NONBLOCK, so a big burst (reset = 150+ MIDI
# messages) partial-writes and silently drops bytes -> dropped note-ons / corrupt CCs.
# writeall loops until every byte is out.
def writeall(fd, data):
    mv = memoryview(bytes(data))
    while mv:
        try:
            n = os.write(fd, mv)
        except BlockingIOError:
            n = 0
        mv = mv[n:] if n else mv
        if not n:
            time.sleep(0.001)

def send(fd, *msgs):
    """Robust MIDI send — the port is O_NONBLOCK so a plain os.write can partial-write
    and drop bytes (a dropped CC silently leaves the wrong waveform/setting)."""
    writeall(fd, b"".join(msgs))

# ---- between-test reset: all notes off, every CC to its synthspec default ----
RESET_CCS = [(c["cc"], c["default"]) for c in synthspec.CONTROLS] + [(1, 0)]  # +mod wheel

def reset_board(fd):
    # Only all-notes-off across the range tests actually use (33-84). A full 128-note
    # blast (384 bytes back-to-back) overwhelms the board's UART RX and makes it drop
    # ~40% of the *following* CCs (measured) — a smaller burst is reliable.
    writeall(fd, b"".join(note_off(n) for n in range(33, 85)))
    time.sleep(0.25)                     # let any prior note's release finish (no bleed)
    writeall(fd, b"".join(cc(c, v) for c, v in RESET_CCS) + pitch_bend(0.0))
    time.sleep(0.06)

# ---- results / scoring ----
@dataclass
class Result:
    score: float
    metric: str
    expected: str
    extra: dict = field(default_factory=dict)
    @property
    def verdict(self):
        return "PASS" if self.score >= 85 else "WARN" if self.score >= 60 else "FAIL"

def mk(score, metric, expected, **extra):
    return Result(max(0.0, min(100.0, float(score))), metric, expected, extra)

def grade(score):
    bands = [(97, "A+"), (93, "A"), (90, "A-"), (87, "B+"), (83, "B"), (80, "B-"),
             (77, "C+"), (73, "C"), (70, "C-"), (60, "D"), (0, "F")]
    return next(g for lo, g in bands if score >= lo)

@dataclass
class TestCase:
    id: str
    category: str          # basic | integration | stress
    title: str
    desc: str              # one-line caption for the video
    perform: object        # perform(fd): stimulus played WHILE recording
    check: object          # check(samples) -> Result
    setup: object = None   # optional setup(fd): CC config sent BEFORE recording
    capture_s: float = 3.0
    weight: float = 1.0
    expected: str = ""     # short human blurb shown on the caption card

# ---- capture with robust byte-alignment + misalignment retry ----
def _decode(raw, off):
    n = (len(raw) - off) // 2
    return [(raw[off + 2 * i] | (raw[off + 2 * i + 1] << 8)) - 32768 for i in range(n)]

def best_align(raw):
    """Pick the 2-byte phase with fewer glitches over the WHOLE signal — more robust
    than samples_from_bytes' fixed [200:1200] smoothness window for choppy audio
    (rapid retrigger, plucks) where that window can land on silence."""
    if len(raw) < 8:
        return []
    a, b = _decode(raw, 0), _decode(raw, 1)
    return a if glitches(a) <= glitches(b) else b

def _bad_take(s):
    """Reject a capture that must be re-run: too short, silent (a dropped note-on),
    or corrupted (partial byte-misalignment leaves big sample-to-sample jumps that
    clean synth audio never has, even at note onsets)."""
    if len(s) < 2000:
        return "short"
    if A.peak(s) < 800:
        return "silent"
    if glitches(s, 12000) > 0.01 * len(s):
        return "corrupt"
    return None

def _one_capture(fd, tc):
    reset_board(fd)
    if tc.setup:
        tc.setup(fd)
        time.sleep(0.05)
    rec = Recorder(fd)
    tc.perform(fd)
    return best_align(rec.stop())

def run_case(fd, tc, retries=5, pass_score=85.0):
    """Capture + grade, keeping the BEST take across retries. The board's 1 Mbaud MIDI
    RX drops the occasional CC/note under bursty traffic (~30-40%), which would show as
    a spurious low score; a dropped setup → low score → retry → a clean take wins. A
    genuinely broken feature scores low on every take. So drops become invisible while
    real regressions still fail."""
    best, best_s = None, []
    for _ in range(retries):
        s = _one_capture(fd, tc)
        if _bad_take(s):
            time.sleep(0.15); continue          # silent/corrupt take — don't even grade it
        try:
            res = tc.check(s)
        except Exception as e:
            res = mk(0, f"check error: {e}", tc.expected)
        if best is None or res.score > best.score:
            best, best_s = res, s
        if res.score >= pass_score:
            break
        time.sleep(0.15)
    if best is None:                             # every take was garbage
        best_s = _one_capture(fd, tc) or [0] * int(0.5 * SR)
        try:
            best = tc.check(best_s)
        except Exception as e:
            best = mk(0, f"check error: {e}", tc.expected)
    return best_s, best

def save_wav(path, s):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, x))) for x in normalize(s)))

# ---- reflash / board bring-up ----
def reflash():
    import subprocess
    bit = os.path.join(_ROOT, "build", "top.bit")
    if not os.path.exists(bit):
        raise SystemExit(f"no bitstream at {bit} — run scripts/build.sh first")
    print(f"==> reflashing {bit}")
    subprocess.run(["openFPGALoader", "-b", "basys3", bit], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)

def warmup(fd):
    """Prime the pipeline after reflash: discard the startup-DC note, then run one full
    reset+note+drain cycle (discarded) so the FIRST real test starts from exactly the
    same steady state as every subsequent one (else test #1 catches residue)."""
    os.write(fd, note_on(57, 90)); time.sleep(0.3); os.write(fd, note_off(57)); time.sleep(0.8)
    reset_board(fd)
    rec = Recorder(fd)
    os.write(fd, note_on(69, 100)); time.sleep(0.5); os.write(fd, note_off(69))
    time.sleep(0.2); rec.stop()               # discard this priming capture
    time.sleep(0.3)

def open_board():
    try:
        return open_port(rw=True)
    except SystemExit:
        raise
    except OSError as e:
        raise SystemExit(f"could not open serial port ({e}); is the web server holding it? "
                         "stop it with:  pkill -f webui/server.py")
