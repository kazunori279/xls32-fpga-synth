"""Confirm the software simulator matches the real board (so matched patches sound right).

Renders probe patches on both the sim and the hardware (send CCs + note, capture the stereo
UART stream, de-interleave the L channel), and reports the sim<->board spectrogram loss per
probe. Low loss => the sim is faithful and the offline search transfers to hardware.

Needs the board connected and the serial port free (stop webui/server.py first).
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "host")))
import uartaudio as u
import engine, loss, params

def _w(v): return (v & 7) << 4
def _s(v): return (v & 3) << 5

PROBES = [
    ("saw open",     dict(wave=_w(1), cutoff=110, reso=10, asus=127, aatt=0, fx=0)),
    ("saw dark",     dict(wave=_w(1), cutoff=40, reso=90, asus=127, aatt=0, fx=0)),
    ("square",       dict(wave=_w(2), cutoff=100, pw=64, asus=127, aatt=0, fx=0)),
    ("sine",         dict(wave=_w(0), cutoff=127, asus=127, aatt=0, fx=0)),
    ("sub bass",     dict(wave=_w(1), sub=_s(3), cutoff=60, asus=127, aatt=0, fx=0)),
    ("unison saw",   dict(wave=_w(1), unison=_s(3), detune=_s(3), cutoff=90, asus=127, aatt=0, fx=0)),
    ("reverb",       dict(wave=_w(1), cutoff=90, asus=127, aatt=0, fx=_w(4), room=_s(3))),
    ("echo",         dict(wave=_w(1), cutoff=90, asus=110, aatt=0, fx=_w(2))),
]
NOTE = 60
GATE, TAIL = 1.55, 0.1          # held window aligned with the board capture (note held ~1.7s)

def board_capture(fd, preset, note=NOTE, secs=1.7):
    for n in range(128): os.write(fd, u.note_off(n))                 # clear stuck voices
    time.sleep(0.05)
    for cid, cc in [("wave",70),("pw",75),("detune",78),("sub",73),("cutoff",74),("reso",71),
                    ("fmode",72),("fatt",24),("fdec",25),("fsus",26),("frel",27),("fdepth",79),
                    ("aatt",20),("adec",21),("asus",22),("arel",23),("lforate",76),("lfodep",77),
                    ("trem",92),("unison",80),("porta",5),("fx",83),("room",91)]:
        if cid in preset: os.write(fd, u.cc(cc, preset[cid] & 0x7f)); time.sleep(0.004)
    time.sleep(0.05)
    rec = u.Recorder(fd)
    os.write(fd, u.note_on(note, 100)); time.sleep(secs); os.write(fd, u.note_off(note))
    time.sleep(0.05); raw = bytes(rec.buf); rec._run = False
    # de-interleave stereo (L marker LSB=0), return L as signed float
    sc = lambda off: sum(1 for k in range(2000) if ((raw[off+2*k] | (raw[off+2*k+1] << 8)) & 1) != (k & 1))
    off = min(range(4), key=sc) if len(raw) > 4100 else 0
    n = (len(raw) - off) // 4
    L = np.array([(raw[off+4*i] | (raw[off+4*i+1] << 8)) - 32768 for i in range(n)], dtype=np.float32)
    return L / 32768.0

def main():
    dev, fd = u.open_port(rw=True)
    print(f"board: {dev}")
    engine.render(PROBES[0][1], gate_s=GATE, tail_s=TAIL)   # warm JIT
    ls = []
    for name, preset in PROBES:
        sim = engine.render(preset, note=NOTE, gate_s=GATE, tail_s=TAIL)
        brd = board_capture(fd, preset)
        if len(brd) < 4000:
            print(f"  {name:12} board capture too short ({len(brd)}) — retry"); brd = board_capture(fd, preset)
        d = loss.loss(sim, brd, a_sr=engine.SR, b_sr=u.SR)
        ls.append(d)
        print(f"  {name:12} sim<->board loss = {d:6.2f}   (sim rms {np.sqrt(np.mean(sim**2)):.3f}, board rms {np.sqrt(np.mean(brd**2)):.3f})", flush=True)
    os.close(fd)
    print(f"\nmean sim<->board loss = {np.mean(ls):.2f}   (for reference: matched presets ~9-22, noise-vs-tone ~137)")

if __name__ == "__main__":
    main()
