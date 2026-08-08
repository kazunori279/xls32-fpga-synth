"""Confirm the software simulator matches the real board (so matched patches sound right).

Renders probe patches on both the sim and the hardware (send CCs + note, capture the board's
audio, grade channel 0) and reports the sim<->board spectrogram loss per probe. Low loss => the
sim is faithful and the offline search transfers to hardware.

Board-agnostic since M27: `open_transport()` picks the link from $XLS32_BOARD, so this runs on
the Tiliqua over USB as well as the Basys 3 over UART. It used to open the serial port itself and
hand-align the LSB channel markers, which is now the UART transport's business.

Needs the board connected and its link free (close the web UI tab first).
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "host")))
from synth import BOARD, open_transport
import synth as u
import engine, loss, params

def _w(v): return (v & 7) << 4
def _s(v): return (v & 3) << 5

# The six dry probes carry no effects key at all, which is how the shell resets: dry. The last
# two used to select CC83 fx modes 4 and 2; they now set the depth knob that replaced each,
# which is also what makes them exercise the 8-comb tank and the ping-pong echo rather than the
# 4-comb model that used to be on the sim side of this comparison.
PROBES = [
    ("saw open",     dict(wave=_w(1), cutoff=110, reso=10, asus=127, aatt=0)),
    ("saw dark",     dict(wave=_w(1), cutoff=40, reso=90, asus=127, aatt=0)),
    ("square",       dict(wave=_w(2), cutoff=100, pw=64, asus=127, aatt=0)),
    ("sine",         dict(wave=_w(0), cutoff=127, asus=127, aatt=0)),
    ("sub bass",     dict(wave=_w(1), sub=_s(3), cutoff=60, asus=127, aatt=0)),
    ("unison saw",   dict(wave=_w(1), unison=_s(3), detune=_s(3), cutoff=90, asus=127, aatt=0)),
    ("reverb",       dict(wave=_w(1), cutoff=90, asus=127, aatt=0, reverb=110, room=_s(3))),
    ("echo",         dict(wave=_w(1), cutoff=90, asus=110, aatt=0, echod=100, dtime=63)),
]
NOTE = 60
GATE, TAIL = 1.55, 0.1          # held window aligned with the board capture (note held ~1.7s)

CC_MAP = [("wave",70),("pw",75),("detune",78),("sub",73),("cutoff",74),("reso",71),
          ("fmode",72),("fatt",24),("fdec",25),("fsus",26),("frel",27),("fdepth",79),
          ("aatt",20),("adec",21),("asus",22),("arel",23),("lforate",76),("lfodep",77),
          ("trem",92),("unison",80),("porta",5),
          ("dtime",82),("room",91),("reverb",93),("chorusd",94),("echod",95)]

def board_capture(tp, preset, note=NOTE, secs=1.7):
    for n in range(128): tp.send_midi(u.note_off(n))                 # clear stuck voices
    time.sleep(0.05)
    for cid, cc in CC_MAP:
        if cid in preset: tp.send_midi(u.cc(cc, preset[cid] & 0x7f)); time.sleep(0.004)
    time.sleep(0.05)
    tp.record_start()
    tp.send_midi(u.note_on(note, 100)); time.sleep(secs); tp.send_midi(u.note_off(note))
    time.sleep(0.05)
    return np.asarray(tp.record_stop(), dtype=np.float32) / 32768.0

def main():
    tp = open_transport().open()
    print(f"board: {BOARD.name} over {BOARD.transport} at {tp.sr} Hz")
    engine.render(PROBES[0][1], gate_s=GATE, tail_s=TAIL)   # warm JIT
    ls = []
    for name, preset in PROBES:
        sim = engine.render(preset, note=NOTE, gate_s=GATE, tail_s=TAIL)
        brd = board_capture(tp, preset)
        if len(brd) < 4000:
            print(f"  {name:12} board capture too short ({len(brd)}) — retry"); brd = board_capture(tp, preset)
        d = loss.loss(sim, brd, a_sr=engine.SR, b_sr=tp.sr)
        ls.append(d)
        print(f"  {name:12} sim<->board loss = {d:6.2f}   (sim rms {np.sqrt(np.mean(sim**2)):.3f}, board rms {np.sqrt(np.mean(brd**2)):.3f})", flush=True)
    tp.close()
    print(f"\nmean sim<->board loss = {np.mean(ls):.2f}   (for reference: matched presets ~9-22, noise-vs-tone ~137)")

if __name__ == "__main__":
    main()
