"""Hardware validation census: play every preset in a bank on the board and flag the ones that
MISBEHAVE on real hardware in ways the sim can't predict — chiefly the fixed-point SVF diverging
to full-scale noise (the sim's internal clamps hide this). Cheap use of physical sound: one ~2 s
capture per preset (~4 min/bank), vs the sim's blind spot.

A preset is flagged RAIL if its board capture is near full-scale AND mostly sample-to-sample
jumps (peak>0.9 & glitch-rate high) while the sim render is quiet. Stop webui/server.py first.

Board-agnostic since M27, via `open_transport()` and the CC_MAP that calibrate.py already carries
— which is what makes this, the roadmap's named check for the banks, runnable on the Tiliqua.

It also scores *agreement*: how close `engine.render()` is to what the board actually plays, per
preset. RAIL only catches catastrophe; the agreement pass is what would have caught the 28 kHz
rate and the wrong reverb topology that M27 fixed, both of which railed nothing. Set SCORE=0 to
skip it (it costs ~2 min of CPU per bank and no extra hardware time).
"""
import os, sys, json, time
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "host")))
from synth import BOARD, open_transport
import synth as u
import engine
import loss as lossmod
from calibrate import NOTE, GATE, TAIL, CC_MAP

WEBUI = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "webui"))
CAP = 1.3                                # capture seconds (enough to judge steady state)
QUIET = 0.01                             # below this peak there is nothing to compare
# Fixed offsets into the preset list, used to pick each preset's distractors. Deterministic on
# purpose: the identification rate is the headline number and it has to be reproducible run to
# run. Coprime with 128 so they never collide on the full banks.
DISTRACT = (17, 37, 61, 97)


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


def score(pairs):
    """How well does the model predict the hardware? `pairs` is [(name, sim_prepped, cap_prepped)].

    A raw distance is uninterpretable on its own -- there is no unit in which 2.4 is "good". So each
    preset is also compared against DISTRACT other presets' captures, and the statistic reported is
    whether the model's render of preset i is closer to i's own recording than to somebody else's.
    That is scale-free and it fails loudly: a model with the wrong sample rate or the wrong reverb
    still produces plausible-looking distances, but it stops being able to tell presets apart.
    """
    n = len(pairs)
    matched, distract, ident = [], [], 0
    for i, (_, sim, cap) in enumerate(pairs):
        m = lossmod.loss(sim, cap, a_prepped=True, b_prepped=True)
        others = [lossmod.loss(sim, pairs[(i + d) % n][2], a_prepped=True, b_prepped=True)
                  for d in DISTRACT if (i + d) % n != i]
        matched.append(m); distract.extend(others)
        if others and m < min(others): ident += 1
    return np.array(matched), np.array(distract), ident


def report_score(pairs, skipped):
    if len(pairs) < 2:
        print(f"\nagreement: not enough scorable presets ({len(pairs)}).")
        return
    m, d, ident = score(pairs)
    k = len([x for x in DISTRACT if x % len(pairs)])
    chance = 100.0 / (k + 1)
    print(f"\nmodel-vs-hardware agreement  (multi-resolution STFT + mel + envelope; lower = closer)")
    print(f"  scored          {len(pairs)}  ({skipped} skipped: railed or silent)")
    print(f"  matched         median {np.median(m):.2f}   p90 {np.percentile(m, 90):.2f}")
    print(f"  distractor      median {np.median(d):.2f}   p90 {np.percentile(d, 90):.2f}")
    print(f"  separation      {np.median(d) / max(np.median(m), 1e-9):.2f}x")
    print(f"  identification  {ident}/{len(pairs)} ({100.0*ident/len(pairs):.0f}%)   chance {chance:.0f}%")
    worst = sorted(zip(m, (p[0] for p in pairs)), reverse=True)[:8]
    print("  worst-predicted:", ", ".join(f"{nm[:22]} {v:.2f}" for v, nm in worst))


def main():
    src = os.environ.get("SRC", "soundfont")
    presets = json.load(open(os.path.join(WEBUI, f"presets_{src}.json")))["presets"]
    tp = open_transport().open()
    print(f"board: {BOARD.name} over {BOARD.transport} at {tp.sr} Hz   source: {src}   presets: {len(presets)}\n")
    engine.render(presets[0]["values"], gate_s=GATE, tail_s=TAIL)
    scoring = os.environ.get("SCORE", "1") != "0"
    rail, pairs, skipped = [], [], 0
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
        if scoring:
            # Prep (resample + loudness-normalize) now, while the arrays are hot and one at a time;
            # holding a bank of raw captures at board rate is several times the memory for nothing.
            if bad or peak < QUIET or simq < 1e-4:
                skipped += 1
            else:
                pairs.append((p["name"], lossmod.prep(sim, engine.SR), lossmod.prep(L, tp.sr)))
    sr = tp.sr
    tp.close()
    print(f"\n{len(rail)}/{len(presets)} presets diverge (rail) on hardware — measured from a verified-quiet start.")
    if rail:
        print("flagged:", ", ".join(rail))
    if scoring:
        print(f"scoring {len(pairs)} presets (model {engine.SR} Hz vs board {sr} Hz)...", flush=True)
        report_score(pairs, skipped)


if __name__ == "__main__":
    main()
