"""Test harness: owns the board, resets it between tests, captures audio, and models a
TestCase / Result + 0-100 scoring.

Since M25 the board is a ``Transport`` (``host/transport/base.py``) rather than a file
descriptor, so the same cases grade a Basys 3 over its 2 Mbaud UART and a Tiliqua over
USB. The case files never noticed: every one of them reaches the board through
``H.send`` and nothing else, which is why the object they are handed could change
underneath them. The argument is still spelled ``fd`` in those files; it is a Transport.
"""
import os, sys, time, wave, struct
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "host"))
sys.path.insert(0, os.path.join(_ROOT, "webui"))
sys.path.insert(0, _ROOT)                        # for `boards`, the board registry
from boards import get_board                                                            # noqa: E402
from transport.base import open_transport                                                 # noqa: E402
from synth import normalize, glitches, note_on, note_off, cc, pitch_bend, SR
import synthspec                                                                          # noqa: E402
import analysis as A                                                                      # noqa: E402

def send(t, *msgs):
    """Send MIDI to the board. Every transport promises the whole burst goes out --
    on the UART that means looping past partial writes, since a dropped CC silently
    leaves the wrong waveform behind and reads as a failing feature."""
    t.send_midi(b"".join(msgs))

# ---- between-test reset: all notes off, every CC to its synthspec default ----
RESET_CCS = [(c["cc"], c["default"]) for c in synthspec.CONTROLS] + [(1, 0)]  # +mod wheel

def reset_board(t):
    # Only all-notes-off across the range tests actually use (33-84). A full 128-note
    # blast (384 bytes back-to-back) overwhelms the board's UART RX and makes it drop
    # ~40% of the *following* CCs (measured) — a smaller burst is reliable.
    t.send_midi(b"".join(note_off(n) for n in range(33, 85)))
    time.sleep(0.25)                     # let any prior note's release finish (no bleed)
    t.send_midi(b"".join(cc(c, v) for c, v in RESET_CCS) + pitch_bend(0.0))
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

# ---- capture, with a retry for takes the link mangled ----
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

def _one_capture(t, tc):
    reset_board(t)
    if tc.setup:
        tc.setup(t)
        time.sleep(0.05)
    t.record_start()
    tc.perform(t)
    return t.record_stop()

def run_case(t, tc, retries=5, pass_score=85.0):
    """Capture + grade, keeping the BEST take across retries. The Basys 3's MIDI RX
    drops the occasional CC/note under bursty traffic (~30-40%), which would show as a
    spurious low score; a dropped setup → low score → retry → a clean take wins. A
    genuinely broken feature scores low on every take. So drops become invisible while
    real regressions still fail.

    On Tiliqua the MIDI path backpressures end to end (bulk endpoint → unpack → filters
    → CDC → engine), so nothing is dropped and the retry only ever costs time."""
    best, best_s = None, []
    for _ in range(retries):
        s = _one_capture(t, tc)
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
        best_s = _one_capture(t, tc) or [0] * int(0.5 * SR)
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
def reflash(board=None):
    """Load the selected board's bitstream with the command the board declares.

    On Tiliqua that is an SRAM load, deliberately: it never writes the nine flash slots.
    Re-loading *our own* bitstream this way is safe without a power cycle, because
    nothing in it reprograms the SI5351 — arriving from some other slot is not, since
    the audio domain would then clock off whatever clk0 that slot left behind."""
    import shlex, subprocess
    board = board or get_board()
    if not board.load_cmd:
        raise SystemExit(f"board {board.name!r} declares no load_cmd")
    bit = next((a for a in shlex.split(board.load_cmd) if a.endswith(".bit")), None)
    if bit and not os.path.exists(os.path.join(_ROOT, bit)):
        raise SystemExit(f"no bitstream at {bit} — build it first ({board.root}/build.sh)")
    print(f"==> reflashing: {board.load_cmd}")
    subprocess.run(board.load_cmd, shell=True, cwd=_ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)

def warmup(t):
    """Prime the pipeline after reflash: discard the startup-DC note, then run one full
    reset+note+drain cycle (discarded) so the FIRST real test starts from exactly the
    same steady state as every subsequent one (else test #1 catches residue)."""
    t.send_midi(note_on(57, 90)); time.sleep(0.3)
    t.send_midi(note_off(57)); time.sleep(0.8)
    reset_board(t)
    t.record_start()
    t.send_midi(note_on(69, 100)); time.sleep(0.5); t.send_midi(note_off(69))
    time.sleep(0.2); t.record_stop()          # discard this priming capture
    time.sleep(0.3)

def open_board(board=None):
    """The selected board's Transport, opened."""
    try:
        return open_transport(board).open()
    except (SystemExit, NotImplementedError):
        raise
    except OSError as e:
        raise SystemExit(f"could not open the board ({e}); is something else holding it? "
                         "for the Basys 3 web UI:  pkill -f webui/server.py")
