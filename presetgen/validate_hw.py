"""Hardware validation census: play every preset in a bank on the board and flag the ones that
MISBEHAVE on real hardware in ways the sim can't predict — chiefly the fixed-point SVF diverging
to full-scale noise (the sim's internal clamps hide this). Cheap use of physical sound: one ~2 s
capture per preset (~4 min/bank), vs the sim's blind spot.

A preset is flagged RAIL if its board capture is near full-scale AND mostly sample-to-sample
jumps (peak>0.9 & glitch-rate high) while the sim render is quiet. Stop webui/server.py first.
"""
import os, sys, json, glob, time
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "host")))
import uartaudio as u
import engine
from calibrate import NOTE, GATE, TAIL

WEBUI = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "webui"))
CAP = 1.3                                # capture seconds (enough to judge steady state)
CC_MAP = [("wave",70),("pw",75),("detune",78),("sub",73),("cutoff",74),("reso",71),("fmode",72),
          ("fatt",24),("fdec",25),("fsus",26),("frel",27),("fdepth",79),("aatt",20),("adec",21),
          ("asus",22),("arel",23),("lforate",76),("lfodep",77),("trem",92),("unison",80),
          ("porta",5),("fx",83),("room",91)]


def _peak(fd, secs=0.3):
    rec = u.Recorder(fd); time.sleep(secs); raw = bytes(rec.buf); rec._run = False
    if len(raw) < 40: return 1.0
    s = np.array([(raw[2*i] | (raw[2*i+1] << 8)) - 32768 for i in range(len(raw)//2)], dtype=np.float32) / 32768
    return float(np.max(np.abs(s)))


def recover(fd, timeout=6.0):
    """Wait until the board is actually quiet before the next preset, so a diverged SVF from the
    previous one can't cascade. Returns True if it settled, False if it stayed railed (permanent)."""
    for n in range(128): os.write(fd, u.note_off(n))
    os.write(fd, u.cc(83, 0)); os.write(fd, u.cc(71, 40)); os.write(fd, u.cc(74, 64))  # dry, mild filter
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _peak(fd, 0.3) < 0.08: return True
    return False


def capture(fd, vals, note=NOTE, secs=CAP):
    settled = recover(fd)                              # guarantee a clean start (no cascade)
    for n in range(128): os.write(fd, u.note_off(n))
    for cid, cc in CC_MAP:
        if cid in vals: os.write(fd, u.cc(cc, vals[cid] & 0x7f)); time.sleep(0.003)
    time.sleep(0.04)
    rec = u.Recorder(fd); os.write(fd, u.note_on(note, 100)); time.sleep(secs)
    os.write(fd, u.note_off(note)); time.sleep(0.04); raw = bytes(rec.buf); rec._run = False
    off = min(range(4), key=lambda o: sum(1 for k in range(2000)
              if ((raw[o+2*k] | (raw[o+2*k+1] << 8)) & 1) != (k & 1))) if len(raw) > 4100 else 0
    n = (len(raw) - off) // 4
    L = np.array([(raw[off+4*i] | (raw[off+4*i+1] << 8)) - 32768 for i in range(n)], dtype=np.float32) / 32768
    return L, settled


def main():
    src = os.environ.get("SRC", "soundfont")
    presets = json.load(open(os.path.join(WEBUI, f"presets_{src}.json")))["presets"]
    dev, fd = u.open_port(rw=True)
    print(f"board: {dev}   source: {src}   presets: {len(presets)}\n")
    engine.render(presets[0]["values"], gate_s=GATE, tail_s=TAIL)
    rail = []
    for i, p in enumerate(presets):
        vals = p["values"]
        sim = engine.render(vals, note=NOTE, gate_s=GATE, tail_s=TAIL)
        L, settled = capture(fd, vals)
        peak = float(np.max(np.abs(L))) if len(L) else 0
        glr = float(np.mean(np.abs(np.diff(L)) > 0.5)) if len(L) > 1 else 0     # jump fraction
        simq = float(np.sqrt(np.mean(sim**2)))
        bad = peak > 0.9 and glr > 0.15 and simq < 0.35                          # board noise, sim quiet
        if bad:
            rail.append(p["name"])
            tag = "" if settled else "  (started dirty!)"
            print(f"  RAIL  [{i:3}] {p['name'][:26]:26} peak {peak:.2f} jump% {glr*100:4.0f} simrms {simq:.3f}{tag}", flush=True)
    os.close(fd)
    print(f"\n{len(rail)}/{len(presets)} presets diverge (rail) on hardware — measured from a verified-quiet start.")
    if rail:
        print("flagged:", ", ".join(rail))


if __name__ == "__main__":
    main()
