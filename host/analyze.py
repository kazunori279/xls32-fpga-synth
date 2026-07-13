#!/usr/bin/env python3
"""Analyze the synth's 8-bit UART sample stream: estimate the sine period and
verify the ADSR envelope actually rises and falls.

Two modes:
  analyze.py                      # read whitespace ints (or "S N" lines) from stdin (sim)
  analyze.py --serial [dev] [sec] # read raw sample bytes from the UART (hardware)

Expected @ 4 kHz sample rate, A4 440 Hz: sine period ~9.1 samples; envelope
peak-to-peak swings high (note-on) and near-zero (silence) with the auto-gate.
"""
import sys, statistics

def read_stdin():
    vals = []
    for line in sys.stdin:
        t = line.split()
        if not t:
            continue
        tok = t[-1] if t[0] == "S" else t[0]   # "S 123" or "123"
        try:
            vals.append(int(tok) & 0xFF)
        except ValueError:
            pass
    return vals

def read_serial(dev, secs):
    import os, time, glob, termios
    if not dev:
        for _ in range(10):
            p = sorted(glob.glob("/dev/cu.usbserial-*"))
            if p: dev = p[-1]; break
            time.sleep(0.5)
    if not dev:
        sys.exit("no /dev/cu.usbserial-* port found")
    fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[2] = termios.CS8 | termios.CLOCAL | termios.CREAD; a[0]=0; a[1]=0; a[3]=0
    a[4] = a[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, a)
    termios.tcflush(fd, termios.TCIFLUSH); time.sleep(0.2); termios.tcflush(fd, termios.TCIFLUSH)
    buf = bytearray(); t0 = time.time()
    while time.time() - t0 < secs:
        try:
            c = os.read(fd, 4096); buf += c if c else b""
            if not c: time.sleep(0.002)
        except BlockingIOError:
            time.sleep(0.002)
    os.close(fd)
    print(f"[{dev}] {len(buf)} samples in {secs}s ({len(buf)/secs:.0f}/s)")
    return list(buf)

def analyze(s):
    n = len(s)
    if n < 100:
        print(f"too few samples ({n})"); return False
    # sine period from upward zero-crossings of the 128 midpoint (active regions)
    ups = [i for i in range(1, n) if s[i-1] < 128 <= s[i]]
    periods = [ups[i]-ups[i-1] for i in range(1, len(ups))]
    periods = [p for p in periods if p < 40]          # ignore silent gaps
    med = statistics.median(periods) if periods else 0
    # envelope: peak-to-peak per window
    W = 32
    pps = [max(s[i:i+W]) - min(s[i:i+W]) for i in range(0, n-W, W)]
    hi, lo = max(pps), min(pps)
    print(f"samples={n}  sine period median={med:.1f} (expect ~9.1 -> ~440Hz)")
    print(f"envelope peak-to-peak: max={hi} min={lo}  (max=note-on, min=silence)")
    ok_freq = 7 <= med <= 12
    ok_env  = hi > 150 and lo < 30
    print("PASS" if (ok_freq and ok_env) else "CHECK",
          f"(freq {'ok' if ok_freq else 'BAD'}, envelope {'ok' if ok_env else 'BAD'})")
    return ok_freq and ok_env

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serial":
        dev = sys.argv[2] if len(sys.argv) > 2 else None
        secs = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
        data = read_serial(dev, secs)
    else:
        data = read_stdin()
    sys.exit(0 if analyze(data) else 1)
