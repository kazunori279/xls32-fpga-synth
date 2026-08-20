#!/usr/bin/env python3
"""Play the shipped bank on a board and measure how far each preset lands from `engine.py` (#24).

Every preset in `webui/presets_*.json` was chosen by minimising a distance on the software model.
The bank has been refitted twice and consolidated once, and none of those renders had come out of
a DAC. `calibrate.py` already showed the gap is real -- it found sim<->board differences in
resonance and effects character -- but it asks the question with eight probe patches, not with the
presets that shipped. This asks it with the bank.

    uv run python presetgen/bank_hw.py                       # soundfont, best of 3
    uv run python presetgen/bank_hw.py --bank armxmod -n 5
    uv run python presetgen/bank_hw.py --limit 8 --no-align  # quick, and the unaligned number

What comes out is a per-preset sim<->board distance, sorted worst first. `calibrate.py` puts
matched patches at ~9-22 and noise-against-a-tone at ~137, so a preset well above the bank's own
median is either an RTL finding or a preset to re-voice -- it is not, on its own, either one.

Three things this does that `calibrate.py` does not, each because a 64-preset ranking is a harder
question than an 8-probe check:

  Every control, every preset.  The board keeps whatever it was last sent, so a preset that omits
  a key inherits the previous preset's value for it. `calibrate.py` sends only the keys its probe
  defines and its probes run in a fixed order, so its residue is at least repeatable; over a bank
  it would make the measurement depend on the sort. Each preset here is sent the full control set,
  filled from DEFAULTS -- which is also what `engine.decode()` renders, so the two sides start in
  the same place. The map comes from `synthspec.CONTROLS` rather than a second copy: `calibrate.py`
  hardcodes 26 of the 30 and is missing all three cross-osc controls, so an xmod preset would
  reach the board without its xmod.

  Onset alignment.  `record_start()` runs before the note-on and the note takes the USB round trip
  to arrive, so the capture begins with some tens of ms of silence that the render does not have.
  `loss.py` has an explicit amplitude-envelope term, so that offset is not a constant tax -- a
  pluck is punished for it and a slow pad is not, and the top of an unaligned ranking would be
  "presets with fast attacks" rather than presets the board disagrees about. The lag is found per
  capture by cross-correlating the two envelopes and reported, because if it is constant it is the
  transport's latency and worth knowing on its own.

  A floor measured from the board, not assumed.  The board's LFO free-runs and `engine.py`'s does
  not (`core/synth.x:460` advances `lfo_ph` every sample and nothing resets it; `engine.py:153`
  starts it at 0 for every render), so a note lands at whatever LFO phase it finds and the same
  preset captured twice is two different sounds. Measured here: `Bass Lead G2` (lfodep 78) returned
  19.2, 50.1, 26.2, 31.3, 43.9 over five identical captures, while `Glockenspiel G4` (lfodep 0)
  returned 4.92-5.15. 62 of the 64 soundfont presets have lfodep > 0, so on this bank that noise is
  the rule and a bare sim<->board number is close to meaningless.

  So each preset is captured N times and compared two ways: sim against the best capture, and the
  captures against *each other*. The second is the floor -- the part of the distance the LFO phase
  alone accounts for, which no model change could remove -- and the finding is the excess over it.
  A preset whose excess is near zero is not modelled badly; it is modelled unobservably. (The floor
  is a min over C(N,2) pairs against N sim comparisons, so it is biased slightly low, which makes
  the excess conservative.) Thresholds from the run rather than from a constant, for the reason
  #42 landed on: the scatter belongs to the take, not to the row.

Needs the board connected and its link free (close the web UI tab first).
"""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "host"))
sys.path.insert(0, os.path.join(REPO, "webui"))
sys.path.insert(0, HERE)

from synth import BOARD, open_transport                                    # noqa: E402
import synth as u                                                          # noqa: E402
import synthspec                                                           # noqa: E402
import engine                                                              # noqa: E402
import loss                                                                # noqa: E402
import protocol                                                            # noqa: E402
from name_audit import note_of                                             # noqa: E402

WEBUI = os.path.join(REPO, "webui")

# id -> CC, from the one source of truth. `volume` is excluded: it is a master gain the model does
# not have, and sending it would scale the board's side of a comparison the sim cannot follow.
CC_OF = {c["id"]: c["cc"] for c in synthspec.CONTROLS if c["id"] != "volume"}

CC_GAP = 0.004        # between CCs; the board drops them if they arrive faster
SETTLE = 0.06         # after the last CC, before the note
# The alignment search window. Measured lags run 15-88 ms, so 150 ms is generous; it used to be
# 300 ms and that was too wide -- a heavily vibratoed preset has a multi-modal envelope correlation
# and the search would lock onto a second peak at 211 ms. Those presets are floor-limited anyway,
# but a search window should not be wide enough to find an answer that cannot be true.
LAG_MAX_S = 0.15


def send_patch(tp, values):
    """Put the board in a known state and then in this preset's state.

    All four parts are silenced, not just part 0: the panel is multitimbral and anything left
    holding a note on parts 1-3 would be in the capture with no way to tell it apart from the
    patch under test.
    """
    for ch in range(4):
        for n in range(128):
            tp.send_midi(u.note_off(n, ch))
    time.sleep(0.05)
    full = dict(synthspec.DEFAULTS)
    full.update(values)
    for cid, cc in CC_OF.items():
        tp.send_midi(u.cc(cc, int(full[cid]) & 0x7F))
        time.sleep(CC_GAP)
    time.sleep(SETTLE)


def capture(tp, values, note, gate_s, tail_s):
    """One board capture of this preset, as float in [-1, 1] at `tp.sr`."""
    send_patch(tp, values)
    tp.record_start()
    tp.send_midi(u.note_on(note, 100))
    time.sleep(gate_s)
    tp.send_midi(u.note_off(note))
    time.sleep(tail_s)
    return np.asarray(tp.record_stop(), dtype=np.float32) / 32768.0


def _env(x, w):
    e = np.sqrt(np.convolve(x * x, np.ones(w) / w, "same"))
    return e / (e.max() + 1e-9)


def find_lag(sim, brd, sr, max_s=LAG_MAX_S):
    """How many samples of `brd` to drop so its onset sits where `sim`'s does.

    Envelopes, not waveforms: the board and the model are not sample-locked and their phases have
    no reason to agree, so a waveform correlation would be reading the oscillator's start phase.
    Searched one-sided -- the board cannot respond before it is asked -- so a negative answer would
    mean the envelopes are too flat to locate, and 0 is the honest reading of that.
    """
    w = max(1, int(0.005 * sr))
    a, b = _env(np.asarray(sim, dtype=np.float64), w), _env(np.asarray(brd, dtype=np.float64), w)
    hi = min(int(max_s * sr), len(b) - w)
    if hi <= 0:
        return 0
    n = min(len(a), len(b) - hi)
    if n <= w:
        return 0
    step = max(1, int(0.001 * sr))                       # 1 ms resolution is finer than the jitter
    lags = range(0, hi, step)
    score = [float(np.dot(a[:n], b[k:k + n])) for k in lags]
    return list(lags)[int(np.argmax(score))]


def measure(tp, p, gate_s, tail_s, n, align=True):
    """One preset: the sim<->board distance, and the board<->board floor it has to beat."""
    note = note_of(p["name"], p["category"])
    sim = engine.render(p["values"], note=note, gate_s=gate_s, tail_s=tail_s)
    sim_p = loss.prep(sim, engine.SR)
    caps, runs, lags = [], [], []
    for _ in range(n):
        brd = capture(tp, p["values"], note, gate_s, tail_s)
        if len(brd) < int(0.5 * gate_s * tp.sr):
            continue                                     # the capture failed, not the preset
        # Aligned against the sim, so the captures end up aligned with each other too -- the floor
        # and the distance are then measured through the same window and are comparable.
        lag = find_lag(sim, brd, tp.sr) if align else 0
        caps.append(loss.prep(brd[lag:], tp.sr))
        runs.append(float(loss.loss(sim_p, caps[-1], a_prepped=True, b_prepped=True)))
        lags.append(lag / tp.sr)
    if not runs:
        return None
    pairs = [float(loss.loss(caps[i], caps[j], a_prepped=True, b_prepped=True))
             for i in range(len(caps)) for j in range(i + 1, len(caps))]
    floor = min(pairs) if pairs else 0.0
    return {"name": p["name"], "category": p["category"], "note": note,
            "loss": min(runs), "spread": max(runs) - min(runs), "n": len(runs),
            "floor": floor, "excess": min(runs) - floor,
            "lfodep": int(p["values"].get("lfodep", 0)), "trem": int(p["values"].get("trem", 0)),
            "lag_s": float(np.median(lags)),
            "sim_rms": float(np.sqrt(np.mean(np.asarray(sim) ** 2)))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default="soundfont", help="bank stem, e.g. soundfont / armxmod")
    ap.add_argument("-n", "--best-of", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="first N presets only (a dry run)")
    ap.add_argument("--no-align", action="store_true", help="skip onset alignment (see the docstring)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    path = os.path.join(WEBUI, f"presets_{args.bank}.json")
    d = json.load(open(path))
    presets = d["presets"][:args.limit or None]
    gate_s, tail_s = protocol.window((d.get("meta") or {}).get("targets", args.bank))

    tp = open_transport().open()
    print(f"board: {BOARD.name} over {BOARD.transport} at {tp.sr} Hz")
    print(f"bank: {args.bank}, {len(presets)} presets, window {gate_s}s + {tail_s}s, "
          f"best of {args.best_of}{'' if not args.no_align else ', UNALIGNED'}")
    engine.render(engine._DEFAULTS, gate_s=gate_s, tail_s=tail_s)          # warm the JIT
    rows = []
    try:
        for i, p in enumerate(presets, 1):
            r = measure(tp, p, gate_s, tail_s, args.best_of, align=not args.no_align)
            if r is None:
                print(f"  [{i:2}/{len(presets)}] {p['name']:24} no usable capture", flush=True)
                continue
            rows.append(r)
            print(f"  [{i:2}/{len(presets)}] {r['name']:24} {r['category']:8} "
                  f"loss {r['loss']:6.2f}  floor {r['floor']:6.2f}  excess {r['excess']:6.2f}"
                  f"  lag {r['lag_s']*1e3:4.0f}ms", flush=True)
    finally:
        for ch in range(4):
            for n in range(128):
                tp.send_midi(u.note_off(n, ch))
        # Leave the board neutral. A run that ends on the last preset's patch is a trap for the
        # next tool to open the link -- check_loop.py spent a session being wrong about a pitch
        # because check_headroom_hw.py left a 2% duty pulse behind.
        for cid, cc in CC_OF.items():
            tp.send_midi(u.cc(cc, int(synthspec.DEFAULTS[cid]) & 0x7F))
            time.sleep(CC_GAP)
        tp.close()

    if not rows:
        sys.exit("no presets measured")
    vals = np.array([r["loss"] for r in rows])
    floors = np.array([r["floor"] for r in rows])
    excess = np.array([r["excess"] for r in rows])
    lags = np.array([r["lag_s"] for r in rows])
    med = float(np.median(vals))
    print(f"\n{len(rows)} presets: sim<->board median {med:.2f}, worst {vals.max():.2f}, "
          f"best {vals.min():.2f}")
    print(f"  board<->board floor: median {np.median(floors):.2f}, worst {floors.max():.2f} "
          f"-- the same preset played twice, so nothing below this is a model finding")
    print(f"  excess over the floor: median {np.median(excess):.2f}, worst {excess.max():.2f}")
    print(f"  onset lag: median {np.median(lags)*1e3:.0f} ms, "
          f"spread {(lags.max()-lags.min())*1e3:.0f} ms"
          + ("  (a tight spread means this is the transport, not the patches)"
             if lags.max() - lags.min() < 0.02 else ""))

    # How much of the bank can be read at all. A preset whose floor is as large as its distance was
    # not measured -- the LFO phase used up the whole budget -- and it belongs in neither list.
    mute = [r for r in rows if r["excess"] <= 0.25 * r["loss"]]
    if mute:
        lf = sum(1 for r in mute if r["lfodep"])
        print(f"  {len(mute)} of {len(rows)} presets are floor-limited (excess under a quarter of "
              f"the distance); {lf} of those have lfodep > 0")

    # Ranked on the excess, not the raw distance: the raw number rewards presets whose LFO happens
    # to be slow or shallow. Read against the bank's own median rather than calibrate.py's 9-22 --
    # that range came from probe patches with no effects and a held sustain, which a bank preset
    # is not. The absolute number is the scale; the excess is the claim.
    print("\nfurthest from the model, floor removed (worst 12):")
    for r in sorted(rows, key=lambda r: -r["excess"])[:12]:
        print(f"  excess {r['excess']:6.2f}  = loss {r['loss']:6.2f} - floor {r['floor']:5.2f}"
              f"   {r['category']:8} {r['name']:24}  lfodep {r['lfodep']:3}")

    by = {}
    for r in rows:
        by.setdefault(r["category"], []).append(r["excess"])
    print("\nby category (excess):")
    for c in sorted(by, key=lambda c: -np.median(by[c])):
        print(f"  {c:9} median {np.median(by[c]):6.2f}  n {len(by[c]):3}")

    out = args.out or os.path.join(HERE, f"bank_hw_{args.bank}.json")
    json.dump({"board": BOARD.name, "bank": args.bank, "sr": tp.sr, "best_of": args.best_of,
               "aligned": not args.no_align, "window": [gate_s, tail_s],
               "median": med, "median_floor": float(np.median(floors)),
               "presets": rows}, open(out, "w"), indent=1)
    print(f"\nwrote {os.path.relpath(out, REPO)}")


if __name__ == "__main__":
    main()
