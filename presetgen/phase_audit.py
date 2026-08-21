#!/usr/bin/env python3
"""How much of each preset is decided by note-on state the board does not control (#44).

Two accumulators in `core/synth.x` are free-running, and `presetgen/engine.py` starts both from a
fixed value. Every preset in every bank was fitted against those fixed values -- one arbitrary
point of a space the hardware moves through continuously.

  LFO phase     `synth.x:460` advances `lfo_ph` every sample from reset; note-on never touches it.
                engine.py:153 starts each render at 0. This is what #44 reported.
  osc seed      `synth.x:176` seeds each voice's start phase with `(lfsr << 16) ^ (cnt << 29)` out
                of the SHARED noise LFSR, which advances every sample (`synth.x:416`). engine.py
                used the reset value 0xACE1 -- a value the board holds for exactly one sample.
                Same bug, different accumulator, and it is NOT in #44's report.

#44 measured the cost on hardware: five identical captures each of three presets. Two of those
three are explained by the LFO. `Synth Bass 2 G3` is not -- it has cutoff 125, so the LFO cannot
reach the output, and its captures still came back bimodal at 13.3 / 3.4. That preset has unison
32, which is what sent this at the seed.

Per preset, rendered across N LFO phases and N seeds:

  spread   worst distance between two renders that differ only in note-on state. This is the
           board's own repeat floor -- what two identical captures can differ by.
  gap      worst distance from the state the preset was FITTED at (phase 0, seed 0xACE1) to any
           other. How wrong the model can be about the board with both behaving correctly.
  fit      distance from the fitted-state render to the GM target. The number CMA-ES minimised,
           and the scale the other two have to be read against.

    uv run python presetgen/phase_audit.py [bank.json] [--n 8] [--lfo|--seed]

`marginal()` at the bottom is the other half: comparing a board capture against the best-matching
start state rather than against the fitted one, which is what #24's harness needs to get a stable
sim-board distance out of two free-running accumulators.
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import engine                                                              # noqa: E402
import loss                                                                # noqa: E402
import protocol                                                            # noqa: E402
import soundfont                                                           # noqa: E402

BANK = os.path.join(REPO, "webui", "presets_soundfont.json")
N = 8
RESET_SEED = 0xACE1
# What the aligned-window refit bought over the whole bank, from #22: mean loss 27.31 -> 26.78.
# A yardstick, not a threshold -- a spread wider than this means the search was resolving a
# difference smaller than the one the hardware introduces for free.
REFIT_GAIN = 0.53


def seeds(n):
    """`n` values of the noise LFSR, spaced around its cycle.

    Not random: the same Galois LFSR synth.x:416 runs, stepped in equal strides from the reset
    value, so these are states the board really passes through and 0xACE1 is really one of them.
    """
    out, s = [], RESET_SEED
    stride = max(1, 65535 // n)
    for _ in range(n):
        out.append(s)
        for _ in range(stride):
            s = (s >> 1) ^ (0xB400 if s & 1 else 0)
    return out


def states(n, lfo=True, seed=True):
    """The note-on states to render. The fitted state, (phase 0, reset seed), is always first."""
    out = [(0.0, RESET_SEED)]
    if lfo:
        out += [(k / n, RESET_SEED) for k in range(1, n)]
    if seed:
        out += [(0.0, s) for s in seeds(n)[1:]]
    return out


def renders(values, note, win, sts, board=None):
    return [loss.prep(engine.render(values, note=note, gate_s=win[0], tail_s=win[1], board=board,
                                    lfo_phase=ph, osc_seed=sd), engine.SR)
            for ph, sd in sts]


def sensitivity(rs, win, n_lfo):
    """(spread, gap, lfo, seed) -- the worst distance between two states overall, the worst from
    the fitted state, and the worst within each accumulator on its own.

    Splitting the two matters for the fix, not just the diagnosis: resetting `lfo_ph` at note-on
    leaves the seed column exactly where it is.
    """
    n = len(rs)
    d = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d[i, j] = d[j, i] = float(loss.loss(rs[i], rs[j], a_prepped=True, b_prepped=True,
                                                window=win))
    lfo = d[:n_lfo, :n_lfo].max() if n_lfo > 1 else 0.0
    keep = [0] + list(range(n_lfo, n))
    seed = d[np.ix_(keep, keep)].max() if len(keep) > 1 else 0.0
    return float(d.max()), float(d[0].max()), float(lfo), float(seed)


def marginal(capture, values, note, win, n=N, board=None, prepped=False):
    """Distance from a capture to the NEAREST note-on state of the model, and which state that was.

    Two free-running accumulators mean a board capture is a sample from a distribution, so scoring
    it against the fitted state charges the model for something it cannot predict. Taking the
    minimum separates "the model is wrong" from "the note started elsewhere". It is a measurement
    fix and not a modelling one: it makes #24's sim-board distance stable without making the
    shipped bank any more correct, because the board still plays the state it lands on.
    """
    c = capture if prepped else loss.prep(capture, engine.SR)
    sts = states(n)
    ds = [float(loss.loss(c, r, a_prepped=True, b_prepped=True, window=win))
          for r in renders(values, note, win, sts, board)]
    k = int(np.argmin(ds))
    return ds[k], sts[k], ds


def main():
    argv = sys.argv[1:]
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else N
    lfo, seed = "--seed" not in argv, "--lfo" not in argv
    path = next((a for a in argv if a.endswith(".json")), BANK)
    sts = states(n, lfo, seed)

    bank = json.load(open(path))["presets"]
    win = protocol.window("soundfont")
    targets = {nm: (w, note) for _, nm, w, note in soundfont.list_targets(per_cat=16)}
    engine.render(engine._DEFAULTS, gate_s=win[0], tail_s=win[1])          # warm the JIT

    what = " + ".join(([f"{n} LFO phases"] if lfo else []) + ([f"{n} seeds"] if seed else []))
    print(f"\n{len(bank)} presets x {len(sts)} note-on states ({what}), window "
          f"{win[0]}+{win[1]}s, {os.path.relpath(path, REPO)}")
    rows = []
    for p in bank:
        if p["name"] not in targets:
            print(f"  !! no target for {p['name']}, skipped")
            continue
        wav, note = targets[p["name"]]
        rs = renders(p["values"], note, win, sts)
        spread, gap, s_lfo, s_seed = sensitivity(rs, win, n if lfo else 1)
        fit = float(loss.loss(rs[0], loss.prep(*soundfont.load(wav)), a_prepped=True,
                              b_prepped=True, window=win))
        v = p["values"]
        rows.append({"name": p["name"], "category": p["category"], "lfodep": v.get("lfodep", 0),
                     "cutoff": v.get("cutoff", 0), "unison": v.get("unison", 0),
                     "fit": fit, "spread": spread, "gap": gap, "lfo": s_lfo, "seed": s_seed,
                     "ratio": spread / fit if fit else 0.0})

    rows.sort(key=lambda r: -r["spread"])
    print(f"\n{'preset':22} {'cat':8} {'lfodep':>6} {'cutoff':>6} {'uni':>4} {'fit':>7} "
          f"{'spread':>7} {'gap':>7} {'/fit':>6} {'by LFO':>7} {'by seed':>8}")
    for r in rows:
        print(f"  {r['name']:20} {r['category']:8} {r['lfodep']:6} {r['cutoff']:6} "
              f"{r['unison']:4} {r['fit']:7.2f} {r['spread']:7.2f} {r['gap']:7.2f} "
              f"{r['ratio']:6.2f} {r['lfo']:7.2f} {r['seed']:8.2f}")

    s = np.array([r["spread"] for r in rows])
    f = np.array([r["fit"] for r in rows])
    print(f"\n{len(rows)} presets")
    print(f"  {int((s < 0.01).sum()):3} are deaf to note-on state (spread < 0.01); "
          f"{int((s > REFIT_GAIN).sum()):3} move further than {REFIT_GAIN}, the entire mean")
    print(f"      improvement the aligned-window refit was adopted for")
    print(f"  {int((s >= f).sum()):3} move further than their own fit residual -- for these the "
          f"start state moves the sound")
    print(f"      further than the distance the search spent its budget closing")
    print(f"  spread: median {np.median(s):.2f}  worst {s.max():.2f}   "
          f"as a share of fit: median {np.median(s / f):.0%}")
    for k in ("lfodep", "cutoff", "unison"):
        x = np.array([r[k] for r in rows], dtype=float)
        if x.std() > 0:
            print(f"  spread vs {k:7} rho = {np.corrcoef(x, s)[0, 1]:+.2f}")

    # Which accumulator to fix. #44 proposes resetting `lfo_ph` at note-on; that column is the
    # only part of the spread such a change removes.
    if lfo and seed:
        a = np.array([r["lfo"] for r in rows])
        b = np.array([r["seed"] for r in rows])
        print(f"\n  by accumulator, median spread:  LFO phase {np.median(a):.2f}   "
              f"osc seed {np.median(b):.2f}")
        print(f"  worse from the seed on {int((b > a).sum())} of {len(rows)} presets; "
              f"{int((b > REFIT_GAIN).sum())} have a seed spread over {REFIT_GAIN} alone")
        # The seed does two different things depending on unison. Stacked, it sets the voices'
        # phases RELATIVE to each other, which is what they beat on -- a timbre change nothing
        # takes back out. Single-voice, it is one oscillator's phase against its own envelope,
        # which the bench's onset alignment partly removes; read those rows as an upper bound.
        u = np.array([r["unison"] for r in rows]) > 0
        if u.any() and (~u).any():
            print(f"  seed spread, median: unison {np.median(b[u]):.2f} ({int(u.sum())} presets)"
                  f"   single voice {np.median(b[~u]):.2f} ({int((~u).sum())})")
        print("  Resetting `lfo_ph` at note-on removes the first column and nothing else.")

    # The bench is the check on all of this: same loss, same presets, real hardware (#44). Read the
    # single-voice rows as an upper bound: with unison 0 the seed is one oscillator's absolute
    # start phase, and the bench onset-aligns its captures, which takes part of that back out.
    print("\nagainst #44's five-capture bench, same loss:")
    for nm, board in (("Bass Lead G2", "19-50, floor 31.16"), ("Synth Bass 2 G3", "13.3 / 3.4"),
                      ("Glockenspiel G4", "5.05-5.15, within 0.23")):
        r = next((r for r in rows if r["name"] == nm), None)
        print(f"  {nm:18} board {board:22} model spread {r['spread']:6.2f}" if r else
              f"  {nm:18} not in this bank")


if __name__ == "__main__":
    main()
