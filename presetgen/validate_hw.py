"""Hardware validation census: play every preset in a bank on the board and flag the ones that
MISBEHAVE on real hardware in ways the sim can't predict — chiefly the fixed-point SVF diverging
to full-scale noise (the sim's internal clamps hide this). Cheap use of physical sound: one ~2 s
capture per preset (~4 min/bank), vs the sim's blind spot.

A preset is flagged RAIL if its board capture is near full-scale AND mostly sample-to-sample
jumps (peak>0.9 & glitch-rate high) while the sim render is quiet. Stop webui/server.py first.

Board-agnostic since M27, via `open_transport()` and the CC_MAP that calibrate.py already carries
— which is what makes this, the roadmap's named check for the banks, runnable on the Tiliqua.
"""
import os, sys, json, time
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "host")))
from synth import BOARD, open_transport
import synth as u
import engine
from calibrate import NOTE, GATE, TAIL, CC_MAP

WEBUI = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "webui"))
CAP = 1.3                                # capture seconds (enough to judge steady state)


def _peak(tp, secs=0.3):
    tp.record_start(); time.sleep(secs); s = np.asarray(tp.record_stop(), dtype=np.float32)
    if len(s) < 20: return 1.0
    return float(np.max(np.abs(s))) / 32768.0


def recover(tp, timeout=6.0):
    """Wait until the board is actually quiet before the next preset, so a diverged SVF from the
    previous one can't cascade. Returns True if it settled, False if it stayed railed (permanent)."""
    for n in range(128): tp.send_midi(u.note_off(n))
    # Silence the tank explicitly. This used to be `cc(83, 0)` — mode 0 = dry — which the shell
    # ignores now; each effect has to be zeroed on its own depth (CC93/94/95).
    for cc in (93, 94, 95): tp.send_midi(u.cc(cc, 0))
    tp.send_midi(u.cc(71, 40)); tp.send_midi(u.cc(74, 64))                  # mild filter
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _peak(tp, 0.3) < 0.08: return True
    return False


def capture(tp, vals, note=NOTE, secs=CAP):
    settled = recover(tp)                              # guarantee a clean start (no cascade)
    for n in range(128): tp.send_midi(u.note_off(n))
    for cid, cc in CC_MAP:
        if cid in vals: tp.send_midi(u.cc(cc, vals[cid] & 0x7f)); time.sleep(0.003)
    time.sleep(0.04)
    tp.record_start(); tp.send_midi(u.note_on(note, 100)); time.sleep(secs)
    tp.send_midi(u.note_off(note)); time.sleep(0.04)
    L = np.asarray(tp.record_stop(), dtype=np.float32) / 32768.0
    return L, settled


def main():
    src = os.environ.get("SRC", "soundfont")
    presets = json.load(open(os.path.join(WEBUI, f"presets_{src}.json")))["presets"]
    tp = open_transport().open()
    print(f"board: {BOARD.name} over {BOARD.transport} at {tp.sr} Hz   source: {src}   presets: {len(presets)}\n")
    engine.render(presets[0]["values"], gate_s=GATE, tail_s=TAIL)
    rail = []
    for i, p in enumerate(presets):
        vals = p["values"]
        sim = engine.render(vals, note=NOTE, gate_s=GATE, tail_s=TAIL)
        L, settled = capture(tp, vals)
        peak = float(np.max(np.abs(L))) if len(L) else 0
        glr = float(np.mean(np.abs(np.diff(L)) > 0.5)) if len(L) > 1 else 0     # jump fraction
        simq = float(np.sqrt(np.mean(sim**2)))
        bad = peak > 0.9 and glr > 0.15 and simq < 0.35                          # board noise, sim quiet
        if bad:
            rail.append(p["name"])
            tag = "" if settled else "  (started dirty!)"
            print(f"  RAIL  [{i:3}] {p['name'][:26]:26} peak {peak:.2f} jump% {glr*100:4.0f} simrms {simq:.3f}{tag}", flush=True)
    tp.close()
    print(f"\n{len(rail)}/{len(presets)} presets diverge (rail) on hardware — measured from a verified-quiet start.")
    if rail:
        print("flagged:", ", ".join(rail))


if __name__ == "__main__":
    main()
