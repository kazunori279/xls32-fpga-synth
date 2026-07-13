"""Effects noise diagnostic: capture dry / echo / reverb on a held note and on
silence, and report per-channel RMS, peak, and spectral flatness (white noise ->
flatness ~1, tone -> ~0). Uses verified-quiet recovery so no measurement cascade."""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "presetgen"))
import uartaudio as u
from validate_hw import recover

NOTE = 57  # A3


def cap_stereo(fd, secs=1.2):
    """Return (L, R) float arrays in [-1,1), byte-aligned on the 4-byte frame.
    The firmware watermarks the stream: consecutive 16-bit samples alternate LSB
    (0,1,0,1,...). Align on that (works even on true silence)."""
    rec = u.Recorder(fd); time.sleep(secs); raw = bytes(rec.buf); rec._run = False
    if len(raw) < 4100:
        return np.zeros(1), np.zeros(1)
    # pick the 4-byte frame offset whose 16-bit-sample LSBs best match parity 0,1,0,1,...
    best, o = -1, 0
    K = min(2000, (len(raw) - 3) // 2)
    for cand in range(4):
        hits = sum(1 for k in range(K)
                   if ((raw[cand+2*k] | (raw[cand+2*k+1] << 8)) & 1) == (k & 1))
        if hits > best: best, o = hits, cand
    n = (len(raw) - o) // 4
    L = np.array([(raw[o+4*i]   | (raw[o+4*i+1] << 8)) - 32768 for i in range(n)], dtype=np.float32) / 32768
    R = np.array([(raw[o+4*i+2] | (raw[o+4*i+3] << 8)) - 32768 for i in range(n)], dtype=np.float32) / 32768
    return L, R


def flatness(x):
    """Spectral flatness (geo mean / arith mean of power spectrum). ~1 = white noise."""
    if len(x) < 256: return 0.0
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2 + 1e-12
    return float(np.exp(np.mean(np.log(X))) / np.mean(X))


def stats(tag, L, R):
    print(f"{tag:>16}:  Lrms={np.sqrt(np.mean(L*L)):.4f} Lpk={np.max(np.abs(L)):.3f} "
          f"Rrms={np.sqrt(np.mean(R*R)):.4f} Rpk={np.max(np.abs(R)):.3f} "
          f"flatL={flatness(L):.3f} flatR={flatness(R):.3f}  n={len(L)}")


def setup_patch(fd):
    # a clean, sustained saw so the wet is easy to judge (no filter movement, full sustain)
    for c, v in [(70, 1<<4), (74, 90), (71, 30), (72, 0), (78, 0), (80, 0), (73, 0),
                 (20, 0), (21, 0), (22, 127), (23, 20), (24, 0), (25, 0), (26, 127), (27, 20),
                 (79, 0), (76, 40), (77, 0), (92, 0), (1, 0), (85, 0)]:
        os.write(fd, u.cc(c, v)); time.sleep(0.003)


def run_mode(fd, fxname, fxval, room=3):
    recover(fd)
    setup_patch(fd)
    os.write(fd, u.cc(91, room << 5)); time.sleep(0.003)
    os.write(fd, u.cc(83, fxval << 4)); time.sleep(0.02)
    # (a) silence: effect enabled, no note -> does the effect self-generate noise?
    L, R = cap_stereo(fd, 0.6); stats(f"{fxname} SILENCE", L, R)
    # (b) held note
    os.write(fd, u.note_on(NOTE, 100)); time.sleep(0.15)
    L, R = cap_stereo(fd, 1.2); stats(f"{fxname} NOTE", L, R)
    os.write(fd, u.note_off(NOTE)); time.sleep(0.05)


def main():
    dev, fd = u.open_port(rw=True)
    print("port:", dev)
    if not recover(fd):
        print("WARNING: board did not go quiet (railed?)")
    run_mode(fd, "DRY", 0)
    run_mode(fd, "ECHO", 2)
    run_mode(fd, "REVERB", 4)
    run_mode(fd, "CHORUS", 1)
    os.close(fd)


if __name__ == "__main__":
    main()
