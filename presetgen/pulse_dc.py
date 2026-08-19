"""Does removing the pulse wave's DC (issue #2) cost the shipped bank anything?

The engine-side fix for #2 is one adder: a pulse at duty `pwthr/256` carries a DC term of
`(pwthr - 128) << 4` in the oscillator's units, and subtracting it there fixes every pulse preset
at once. The objection to it has always been that it breaks bit-exact parity with the Basys 3
build and "re-scores the bank" -- the presets were fitted by CMA-ES against an engine model that
had the offset, so changing the engine invalidates the fits.

That objection is testable without re-fitting anything, and this script tests it. For every pulse
preset in a bank it renders the *fitted parameters, unchanged* twice -- with the DC and without --
and scores both against the same target the preset was fitted to, under the same distance the
search used. If the no-DC render is closer, the fits do not need redoing: they were being charged
for an offset the engine put there and the target never had.

Two things make this a fair test rather than a rigged one:

* Non-pulse presets are included as a control. `_voice_wave` only changes for `wave == 2`, so their
  delta must be exactly 0.0 -- if it is not, the harness is measuring its own noise.
* The distance is whichever one the bank's `meta.loss` names, not a convenient one. The shipped
  banks are all `clap+stft`, so that needs `uv sync --extra deepfit`.

    uv run --extra deepfit python presetgen/pulse_dc.py webui/presets_soundfont.json [source]

What it cannot tell you: whether the *optimum* moves. A fit re-run with the DC gone would find
different parameters, possibly better ones. This only answers the narrower and more useful
question -- whether the bank we already have gets worse.
"""
import json
import os
import sys
import importlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine                                                              # noqa: E402
import loss_deep                                                           # noqa: E402
import protocol                                                            # noqa: E402
from name_audit import note_of                                             # noqa: E402

BANKS = [a for a in sys.argv[1:] if a.endswith(".json")] or ["webui/presets_soundfont.json"]
SOURCE = ([a for a in sys.argv[1:] if not a.endswith(".json")] or [None])[0]
PER_CAT = int(os.environ.get("PER_CAT", 16))
PULSE = 2                       # engine.decode()'s wave index for PULSE


def duty_pct(pw):
    """The oscillator's static duty, before the LFO modulates it: pwthr = pw << 1 of 256."""
    return min(244, max(12, pw * 2)) / 256 * 100


def verdict(label, rows):
    if not rows:
        print(f"  {label}: no pulse presets")
        return
    dl = np.array([r[5] - r[6] for r in rows])              # >0 = the no-DC render is closer
    w, l = int((dl > 0).sum()), int((dl < 0).sum())
    from scipy.stats import binomtest
    p = float(binomtest(w, w + l, 0.5, alternative="two-sided").pvalue) if w + l else 1.0
    print(f"  {label:34} {w:3d}W {l:3d}L of {len(dl):3d}   mean {dl.mean():+7.3f}"
          f"   median {np.median(dl):+7.3f}   p={p:.4g}{'  *' if p < 0.05 else ''}")


def main():
    first = json.load(open(BANKS[0])).get("meta", {})
    source = SOURCE or first.get("targets", "soundfont")
    lossname = first.get("loss", "stft")
    ns = importlib.import_module(source)
    win = protocol.window(ns)
    print(f"{len(BANKS)} bank(s), targets={source}, loss={lossname}, window={win}")

    loss_deep.select(lossname)
    engine.render(engine._DEFAULTS, gate_s=win[0], tail_s=win[1])          # warm the jit
    targets = {name: (path, note) for _, name, path, note in ns.list_targets(per_cat=PER_CAT)}
    # The banks share one corpus, so each target is prepped once and reused across all of them.
    prepped = {}

    per_bank, pooled, control = {}, [], []
    for path_json in BANKS:
        bank = json.load(open(path_json))
        label = os.path.basename(path_json)
        rows, miss = [], 0
        for p in bank["presets"]:
            if p["name"] not in targets:
                miss += 1
                continue
            if p["name"] not in prepped:
                audio, sr = ns.load(targets[p["name"]][0])
                prepped[p["name"]] = loss_deep.prep(audio, sr, window=win)
            tgt = prepped[p["name"]]
            kw = dict(note=note_of(p["name"], p["category"]), gate_s=win[0], tail_s=win[1])
            a0 = engine.render(p["values"], **kw)
            a1 = engine.render(p["values"], pwdc=True, **kw)
            l0 = float(loss_deep.loss(a0, tgt, a_sr=engine.SR, b_prepped=True))
            l1 = float(loss_deep.loss(a1, tgt, a_sr=engine.SR, b_prepped=True))
            d = engine.decode(p["values"])
            rec = (p["name"], p["category"], d, float(a0.mean()), float(a1.mean()), l0, l1,
                   float(np.abs(a0).max()), float(np.abs(a1).max()))
            (rows if d["wave"] == PULSE else control).append(rec)
        if miss:
            print(f"{label}: {miss} presets had no target in {source} (skipped)")
        per_bank[label] = rows
        pooled += rows

    print(f"\ncontrol -- {len(control)} non-pulse presets, delta must be exactly 0")
    bad = [r for r in control if r[5] != r[6]]
    print(f"  {len(control) - len(bad)}/{len(control)} identical"
          + ("" if not bad else f"  <-- HARNESS BROKEN: {[r[0] for r in bad][:5]}"))

    print(f"\n{len(pooled)} pulse presets, fitted parameters unchanged, scored under {lossname}")
    print(f"  {'preset':28}{'cat':9}{'duty':>7}{'DC before':>11}{'DC after':>10}"
          f"{'loss before':>13}{'loss after':>12}{'delta':>10}{'peak':>8}{'->':>8}")
    for name, cat, d, m0, m1, l0, l1, k0, k1 in sorted(pooled, key=lambda r: r[5] - r[6]):
        print(f"  {name[:27]:28}{cat[:8]:9}{duty_pct(d['pw']):6.1f}%{m0:+11.4f}{m1:+10.4f}"
              f"{l0:13.3f}{l1:12.3f}{l0 - l1:+10.3f}{k0:8.3f}{k1:8.3f}")

    print("\nremoving the DC, per bank and pooled (W = the no-DC render is closer)")
    for label, rows in per_bank.items():
        verdict(label, rows)
    if len(per_bank) > 1:
        verdict("POOLED (shares targets, not iid)", pooled)
    if pooled:
        print(f"\n  |DC| over the pulse presets: {np.abs([r[3] for r in pooled]).mean():.4f}"
              f" -> {np.abs([r[4] for r in pooled]).mean():.6f}")
        # The counterweight. Subtracting the DC does not shrink the waveform, it re-centres it:
        # at 95% duty the pulse goes from +2047/-2047 to +191/-3903, so one voice's *peak*
        # excursion grows even as its mean goes to zero. synth.x:303 currently annotates
        # `|main| <= 2048`; the fix would make that 3903. Whether that costs anything depends on
        # whether the mix clamp is reached by coherent DC (which adds linearly across voices) or
        # by peaks (which do not align), so this is the number the polyphonic test needs.
        k0 = np.array([r[7] for r in pooled]); k1 = np.array([r[8] for r in pooled])
        grew = int((k1 > k0 * 1.001).sum())
        print(f"  peak |sample|: mean {k0.mean():.3f} -> {k1.mean():.3f}, "
              f"worst ratio {np.max(k1 / np.maximum(k0, 1e-9)):.2f}x, grew on {grew} of {len(k0)}")


if __name__ == "__main__":
    main()
