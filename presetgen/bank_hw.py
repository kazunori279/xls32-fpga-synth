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
    uv run python presetgen/bank_hw.py --replay caps_soundfont.npz   # re-score, no board

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

  The note-on state the board landed in, modelled instead of subtracted.  Two accumulators in
  `core/synth.x` free-run and `engine.py` used to start both from a constant, so a note lands at
  whatever state it finds and the same preset captured twice is two different sounds:
  `synth.x:460` advances `lfo_ph` every sample and note-on never touches it, and `synth.x:176`
  seeds every voice's start phase out of the shared noise LFSR that `synth.x:416` advances every
  sample. #44 measured both; the seed is the larger one, median 15.61 against the LFO's 5.50 over
  the soundfont bank, because it sets stacked voices' phases relative to each other.

  The first version of this file measured that as a board<->board floor -- capture each preset N
  times, take the closest pair, subtract -- and ranked on the excess. That does not work. The floor
  is a min over C(N,2) pairs against N sim comparisons, so the two sides are not sampled alike, and
  on `Bass Lead G2` it came out at 30.86 against a distance of 19.13: an excess of -11.73, which is
  not a small finding but no finding at all. Nine presets in the first run went negative.

  So the state is modelled now, not estimated from the spread. `engine.render()` takes `lfo_phase`
  and `osc_seed`, and each capture is scored against the NEAREST of the states in
  `phase_audit.states()` rather than against the one arbitrary state the preset was fitted at
  (`marginal`). A residual after that is a sim<->board difference the note-on state does not
  explain, which is the number #24 asked for. The fitted-state distance is still reported as
  `loss`, because it is what the shipped bank was actually chosen by.

  Two honesty items on that. The state set is a sample -- N LFO phases and N LFSR values out of a
  continuum and a 65535-long cycle -- so `resolution` renders one state deliberately BETWEEN the
  samples and scores it against them, giving the floor the sampling grid imposes on `marginal`.
  And the board<->board floor is still measured and printed, now as a diagnostic rather than a
  subtrahend: if it is large while `marginal` is small, the model reaches every state the board
  visits, which is the outcome this is hoping for.

Captures are written to `caps_<bank>.npz` and `--replay` re-scores them with no board attached, so
a change to the loss or to the state set does not cost another hour of hardware time.

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
import phase_audit                                                         # noqa: E402
import protocol                                                            # noqa: E402
from name_audit import note_of                                             # noqa: E402

WEBUI = os.path.join(REPO, "webui")


def rel(p):
    """Repo-relative if it is in the repo, absolute otherwise -- a replay set is often in /tmp,
    and `../../../../../tmp/x.npz` is not a path anyone can read."""
    r = os.path.relpath(p, REPO)
    return p if r.startswith("..") else r


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


def take(tp, p, note, gate_s, tail_s, n, sim, sr, align=True):
    """N board captures of one preset, onset-aligned, as raw float arrays plus their lags.

    Aligned against the fitted-state render rather than against each other, so every capture and
    every model state below sits in the same window and the distances are comparable. The onset is
    where the envelope rises, which barely moves with LFO phase or start seed -- the alignment does
    not need the state to be right, only the note.
    """
    caps, lags = [], []
    for _ in range(n):
        brd = capture(tp, p["values"], note, gate_s, tail_s)
        if len(brd) < int(0.5 * gate_s * sr):
            continue                                     # the capture failed, not the preset
        lag = find_lag(sim, brd, sr) if align else 0
        caps.append(brd[lag:])
        lags.append(lag / sr)
    return caps, lags


def score(p, note, caps, lags, win, sr, n_states):
    """One preset's row: distance from the board to the model, at the fitted state and marginally.

    `loss` is the old number -- board against the single state the preset was fitted at -- kept
    because that state is what the shipped bank was chosen by. `marginal` is the new one: the
    board against the CLOSEST state `engine.py` can now reach, which charges the model for being
    wrong and not for the note having started somewhere the model was never asked about.
    """
    if not caps:
        return None
    sts = phase_audit.states(n_states)
    sims = phase_audit.renders(p["values"], note, win, sts)
    cp = [loss.prep(c, sr) for c in caps]
    L = np.array([[float(loss.loss(c, s, a_prepped=True, b_prepped=True, window=win))
                   for s in sims] for c in cp])          # captures x states
    best = int(np.argmin(L.min(axis=0)))                 # the state the board looks most like

    # What the state grid costs on its own. A render at an LFO phase and an LFSR value that are
    # both BETWEEN the sampled ones, scored against the sample set: the board can land there too,
    # so `marginal` cannot be expected below this.
    off = phase_audit.seeds(2 * n_states)[1]
    probe = phase_audit.renders(p["values"], note, win, [(0.5 / n_states, off)])[0]
    res = min(float(loss.loss(probe, s, a_prepped=True, b_prepped=True, window=win)) for s in sims)

    pairs = [float(loss.loss(cp[i], cp[j], a_prepped=True, b_prepped=True, window=win))
             for i in range(len(cp)) for j in range(i + 1, len(cp))]
    fitted = L[:, 0]
    return {"name": p["name"], "category": p["category"], "note": note, "n": len(cp),
            "loss": float(fitted.min()), "spread": float(fitted.max() - fitted.min()),
            "marginal": float(L.min()), "resolution": res,
            "state": [round(sts[best][0], 4), sts[best][1]],
            # Kept as a diagnostic, not subtracted from anything: a large floor beside a small
            # marginal is the model reaching every state the board visits, which is the good case.
            "floor": min(pairs) if pairs else 0.0,
            "lfodep": int(p["values"].get("lfodep", 0)), "trem": int(p["values"].get("trem", 0)),
            "unison": int(p["values"].get("unison", 0)),
            "lag_s": float(np.median(lags)) if lags else 0.0}


def collect(bank, best_of, limit, align, npz):
    """Capture the bank off the board and write the raw audio to `npz`. Needs the hardware."""
    d, presets, gate_s, tail_s = load_bank(bank, limit)
    tp = open_transport().open()
    print(f"board: {BOARD.name} over {BOARD.transport} at {tp.sr} Hz")
    print(f"bank: {bank}, {len(presets)} presets, window {gate_s}s + {tail_s}s, "
          f"best of {best_of}{'' if align else ', UNALIGNED'}")
    engine.render(engine._DEFAULTS, gate_s=gate_s, tail_s=tail_s)          # warm the JIT
    store = {}
    try:
        for i, p in enumerate(presets, 1):
            note = note_of(p["name"], p["category"])
            sim = engine.render(p["values"], note=note, gate_s=gate_s, tail_s=tail_s)
            caps, lags = take(tp, p, note, gate_s, tail_s, best_of, sim, tp.sr, align)
            if not caps:
                print(f"  [{i:2}/{len(presets)}] {p['name']:24} no usable capture", flush=True)
                continue
            for k, c in enumerate(caps):
                store[f"{i - 1}|{k}"] = c
            store[f"{i - 1}|lags"] = np.array(lags)
            print(f"  [{i:2}/{len(presets)}] {p['name']:24} {p['category']:8} "
                  f"{len(caps)} captures, lag {np.median(lags)*1e3:4.0f}ms", flush=True)
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
    np.savez_compressed(npz, sr=tp.sr, bank=bank, board=BOARD.name, aligned=align,
                        names=np.array([p["name"] for p in presets]), **store)
    print(f"\nwrote {rel(npz)}")
    return npz


def load_bank(bank, limit=0):
    path = os.path.join(WEBUI, f"presets_{bank}.json")
    d = json.load(open(path))
    gate_s, tail_s = protocol.window((d.get("meta") or {}).get("targets", bank))
    return d, d["presets"][:limit or None], gate_s, tail_s


def report(rows, n_states):
    marg = np.array([r["marginal"] for r in rows])
    fitted = np.array([r["loss"] for r in rows])
    res = np.array([r["resolution"] for r in rows])
    floors = np.array([r["floor"] for r in rows])
    lags = np.array([r["lag_s"] for r in rows])
    print(f"\n{len(rows)} presets, model marginalised over {n_states} LFO phases "
          f"+ {n_states} seeds")
    print(f"  marginal (board vs the nearest state the model can reach): "
          f"median {np.median(marg):.2f}, worst {marg.max():.2f}, best {marg.min():.2f}")
    print(f"  at the fitted state alone: median {np.median(fitted):.2f}, worst {fitted.max():.2f} "
          f"-- the state the shipped bank was chosen by, and the one the board rarely lands in")
    print(f"  state-grid resolution: median {np.median(res):.2f} -- a render BETWEEN the sampled "
          f"states, so nothing below this is measurable")
    print(f"  board<->board floor: median {np.median(floors):.2f}, worst {floors.max():.2f} "
          f"(diagnostic: large here beside a small marginal is the model doing its job)")
    print(f"  onset lag: median {np.median(lags)*1e3:.0f} ms, "
          f"spread {(lags.max()-lags.min())*1e3:.0f} ms"
          + ("  (a tight spread means this is the transport, not the patches)"
             if lags.max() - lags.min() < 0.02 else ""))

    # How much of the bank can be read at all. A preset whose marginal sits at the grid resolution
    # was not measured -- the state sampling used up the whole budget -- and it is neither a
    # finding nor a clean bill of health.
    mute = [r for r in rows if r["marginal"] <= 1.5 * r["resolution"]]
    if mute:
        print(f"  {len(mute)} of {len(rows)} presets are grid-limited (marginal within 1.5x the "
              f"resolution); raise --states to read them")

    # Ranked on the marginal. The fitted-state number rewards presets whose LFO happens to be slow
    # or shallow and punishes ones the board simply caught mid-cycle; this one does neither. Read
    # against the bank's own median rather than calibrate.py's 9-22 -- that range came from probe
    # patches with no effects and a held sustain, which a bank preset is not.
    print("\nfurthest from the model once the note-on state is accounted for (worst 12):")
    for r in sorted(rows, key=lambda r: -r["marginal"])[:12]:
        print(f"  marginal {r['marginal']:6.2f}  (fitted {r['loss']:6.2f}, "
              f"res {r['resolution']:5.2f})   {r['category']:8} {r['name']:24}  "
              f"lfodep {r['lfodep']:3} uni {r['unison']:3}")

    by = {}
    for r in rows:
        by.setdefault(r["category"], []).append(r["marginal"])
    print("\nby category (marginal):")
    for c in sorted(by, key=lambda c: -np.median(by[c])):
        print(f"  {c:9} median {np.median(by[c]):6.2f}  n {len(by[c]):3}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default="soundfont", help="bank stem, e.g. soundfont / armxmod")
    ap.add_argument("-n", "--best-of", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="first N presets only (a dry run)")
    ap.add_argument("--no-align", action="store_true", help="skip onset alignment (see the docstring)")
    ap.add_argument("--states", type=int, default=phase_audit.N,
                    help="LFO phases and LFSR values to marginalise the model over, each")
    ap.add_argument("--replay", default=None, help="re-score a saved capture set, no board needed")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    npz = args.replay or collect(args.bank, args.best_of, args.limit, not args.no_align,
                                 os.path.join(HERE, f"caps_{args.bank}.npz"))
    z = np.load(npz, allow_pickle=True)
    bank, sr, aligned = str(z["bank"]), int(z["sr"]), bool(z["aligned"])
    d, presets, gate_s, tail_s = load_bank(bank)
    win = (gate_s, tail_s)
    if args.replay:
        print(f"replaying {rel(npz)}: {bank} off {z['board']} at {sr} Hz"
              f"{'' if aligned else ', UNALIGNED'}")
    engine.render(engine._DEFAULTS, gate_s=gate_s, tail_s=tail_s)          # warm the JIT

    rows = []
    for i, p in enumerate(presets):
        # Scanned rather than counted up to --best-of: a replay may be re-scoring a set captured
        # with a different N, and silently dropping captures would make the two runs incomparable.
        caps = [z[f"{i}|{j}"] for j in range(64) if f"{i}|{j}" in z]
        if not caps:
            continue
        r = score(p, note_of(p["name"], p["category"]), caps,
                  list(z.get(f"{i}|lags", [])), win, sr, args.states)
        rows.append(r)
        print(f"  [{i+1:2}/{len(presets)}] {r['name']:24} {r['category']:8} "
              f"marginal {r['marginal']:6.2f}  fitted {r['loss']:6.2f}  "
              f"res {r['resolution']:5.2f}", flush=True)

    if not rows:
        sys.exit("no presets measured")
    report(rows, args.states)

    out = args.out or os.path.join(HERE, f"bank_hw_{bank}.json")
    json.dump({"board": str(z["board"]), "bank": bank, "sr": sr, "best_of": args.best_of,
               "aligned": aligned, "window": [gate_s, tail_s], "states": args.states,
               "captures": rel(npz),
               "median": float(np.median([r["marginal"] for r in rows])),
               "median_fitted": float(np.median([r["loss"] for r in rows])),
               "presets": rows}, open(out, "w"), indent=1)
    print(f"\nwrote {rel(out)}")


if __name__ == "__main__":
    main()
