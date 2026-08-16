"""Would the preset matcher use cross-mod, if the search space let it?

M19 asked this and answered no: with the magnitude-STFT loss, FM ties at best, because a per-bin
distance rewards filling the gross energy curve (saw + noise + filter) over placing sparse exact
partials. So `params.py:SELECTS` leaves X-Mod/X-Depth/X-Ratio out, every fitted preset renders at
xmode=0, and the bell/EP/metallic patches are voiced by ear in presets_fm.json.

That verdict was reached under the STFT loss. The shipped bank is now fitted under **clap+stft**,
and a CLAP embedding is the opposite kind of distance -- it does not compare bins at all. So the
premise the verdict rested on no longer holds, and it is worth re-asking before spending a full
re-fit on it. This probe is the cheap version of the question, in two stages:

  graft   Hold the shipped preset fixed and sweep xmode x xratio x xdepth over it. 96 renders per
          preset, seconds. This UNDERSTATES cross-mod: cutoff, resonance and both envelopes were
          fitted for a non-FM carrier, and turning the modulator on also takes the detune
          oscillator away (engine.py:202 -- ph2 is either detune or modulator, never both). A win
          here is strong evidence; a loss here is weak evidence.
  refit   Then, for the best graft setting, re-run CMA-ES over the full vector with those three
          CCs pinned, against a same-budget control re-run with cross-mod off. Both start from the
          shipped preset, so the control measures what the extra budget alone buys and the
          difference is what cross-mod buys. This is the fair comparison.

Prints, and writes xmod_probe.json. Loss values are only comparable within a row: the two backends
have unrelated scales, and each row is scored against its own target.

    uv sync --extra deepfit
    LOSS=clap+stft uv run python presetgen/xmod_probe.py [--refit N] [preset name ...]
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cma                                                                 # noqa: E402
import engine                                                             # noqa: E402
import loss_deep                                                          # noqa: E402
import params                                                             # noqa: E402
import protocol                                                           # noqa: E402
import search                                                             # noqa: E402
import soundfont                                                          # noqa: E402
from name_audit import clean_name, note_of                                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.abspath(os.path.join(HERE, "..", "webui", "presets_soundfont.json"))
# The corpus owns the window; read it from there rather than from search.py, so that a probe render
# and the target it is scored against cannot drift apart the way they did before protocol.py.
WINDOW = protocol.window(soundfont)

# The targets whose attack is audibly metallic/FM-ish -- the complaint this probe exists to test.
# In the SoundFont these are largely recordings of DX-class hardware, so if cross-mod is ever the
# right answer for a fit, it is here. Anything else would be testing the question somewhere it was
# never in doubt.
METALLIC = ["Crystal", "Metallic Pad", "E-Piano 1", "E-Piano 2", "Vibraphone", "Glockenspiel",
            "Charang", "Music Box", "Clavinet", "Celesta"]

XMODES = {1: "ring", 2: "FM", 3: "FM+"}
RATIOS = [1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 0.5]         # index order of engine.py:194-201
DEPTHS = [31, 63, 95, 127]                                 # ring reads only bits 5-6, so 4 is all


def xcc(mode, ratio_i, depth):
    """The three raw CCs, bit-packed the way synthspec/params do it."""
    return {"xmode": (mode & 3) << 5, "xratio": (ratio_i & 7) << 4, "xdepth": depth}


def grid():
    """Baseline first, so index 0 is always the shipped preset untouched."""
    out = [("off", xcc(0, 0, 0))]
    for m, mn in XMODES.items():
        for ri, r in enumerate(RATIOS):
            for d in DEPTHS:
                out.append((f"{mn} x{r:g} d{d}", xcc(m, ri, d)))
    return out


def render_all(base, note, cands):
    return [engine.render(dict(base, **cc), note=note, gate_s=WINDOW[0], tail_s=WINDOW[1])
            for _, cc in cands]


def refit(target, note, x0, xcc_fixed, budget, seed=1):
    """CMA-ES from the shipped preset with three CCs pinned. Same shape as search.match(), but
    seeded from an already-good vector rather than the category seed: this asks "can it do better
    from here", which is the only question a re-fit of a shipped bank would be asking. sigma is
    0.15 rather than match()'s 0.30 for the same reason -- this is a local move, not a search."""
    es = cma.CMAEvolutionStrategy(list(x0), 0.15, {
        "bounds": [0.0, 1.0], "maxfevals": budget, "verbose": -9, "seed": seed})
    best_v, best_f = x0, _scores([x0], target, note, xcc_fixed)[0]
    while not es.stop():
        xs = es.ask()
        fs = _scores(xs, target, note, xcc_fixed)
        es.tell(xs, list(fs))
        i = int(np.argmin(fs))
        if fs[i] < best_f:
            best_v, best_f = xs[i], fs[i]
    return dict(params.preset_from_vec(best_v), **xcc_fixed), float(best_f)


def _scores(vecs, target, note, xcc_fixed):
    """A whole generation in one call -- CLAP costs the same for 13 clips as for 2, and scoring
    one at a time would make the re-fit arm an order of magnitude slower than the grid arm."""
    renders, live = [], []
    for i, v in enumerate(vecs):
        a = engine.render(dict(params.preset_from_vec(v), **xcc_fixed), note=note,
                          gate_s=WINDOW[0], tail_s=WINDOW[1])
        if np.sqrt(np.mean(a * a)) >= 1e-4:                 # silent patch: reject, do not embed it
            renders.append(a); live.append(i)
    out = np.full(len(vecs), float(search.SILENT))
    if renders:
        out[live] = loss_deep.dists(renders, target, a_sr=engine.SR, b_prepped=True)
    return out


def write_bank(rows):
    """Both re-fit arms as a browser bank, so the numbers can be checked by ear.

    `meta.targets` names the corpus module rather than letting build_previews.py infer it from the
    filename, which is how every other bank works -- this one is not fitted from a corpus called
    "xmodprobe" and would otherwise get no target clip to compare against, which is the entire
    point of auditioning it. The file is a probe artefact and is gitignored: it exists to be
    listened to once and then either promoted by a real re-fit or deleted."""
    # The bank name, not clean_name(): "E-Piano 1" and "E-Piano 2" both clean to "E-Piano", and
    # two slots with one name collide in every downstream dict -- the preview index would have
    # silently shown 18 rows for 20 presets, with one pair's clips overwritten by the other's.
    presets, alias = [], {}
    for r in rows:
        for arm, tag in (("preset_off", "off"), ("preset_x", "xmod")):
            slot = f"{r['name']} [{tag}]"
            presets.append({"name": slot, "category": r["category"], "values": r[arm]})
            alias[slot] = r["name"]
    out = os.path.abspath(os.path.join(HERE, "..", "webui", "presets_xmodprobe.json"))
    json.dump({"meta": {"targets": "soundfont", "probe": "xmod", "names": alias},
               "presets": presets}, open(out, "w"), indent=1)
    print(f"wrote {out}  ({len(presets)} presets, {len(rows)} pairs)")


def main():
    argv = sys.argv[1:]
    if "--bank-only" in argv:                 # re-package a finished run; the presets are in the
        rows = json.load(open(os.path.join(HERE, "xmod_probe.json")))["rows"]
        write_bank(rows)                      # JSON, so this costs nothing and re-fits nothing
        return
    budget = 0
    if "--refit" in argv:
        i = argv.index("--refit")
        budget = int(argv[i + 1])
        del argv[i:i + 2]
    backend = os.environ.get("LOSS", "clap+stft")
    loss_deep.select(backend)

    presets = {p["name"]: p for p in json.load(open(BANK))["presets"]}
    targets = {name: (path, note) for _, name, path, note in soundfont.list_targets(per_cat=16)}
    if argv:
        names = [n for n in argv if n in presets]
    else:                                   # first (base-note) occurrence of each metallic target
        names = []
        for want in METALLIC:
            # Exact first: clean_name() strips a trailing index, so "E-Piano 1" and "E-Piano 2"
            # both clean to "E-Piano" and a clean-only match would silently keep one of the two.
            hit = (want if want in presets and want in targets else
                   next((n for n in presets if clean_name(n) == want and n in targets), None))
            if hit:
                names.append(hit)
    print(f"LOSS={backend}  {len(names)} presets  {len(grid())-1} grid points each"
          + (f"  refit budget {budget}" if budget else ""))

    engine.render(engine._DEFAULTS, gate_s=WINDOW[0], tail_s=WINDOW[1])   # warm the JIT
    cands = grid()
    rows = []
    for name in names:
        p = presets[name]
        path, note = targets[name]
        assert note == note_of(name, p["category"]), f"{name}: note {note} != {note_of(name, p['category'])}"
        tgt = loss_deep.prep(*soundfont.load(path), window=WINDOW)
        f = loss_deep.dists(render_all(p["values"], note, cands), tgt,
                            a_sr=engine.SR, b_prepped=True)
        best = int(np.argmin(f))
        row = {"name": name, "category": p["category"], "note": note,
               "baseline": float(f[0]), "graft_best": float(f[best]),
               "graft_setting": cands[best][0],
               "graft_gain": float(f[0] - f[best])}
        # How much of the grid beats doing nothing? One winner in 96 is noise; a third of the grid
        # winning says the shipped preset is in the wrong region, not that one setting got lucky.
        row["grid_beating_baseline"] = int((f[1:] < f[0]).sum())
        if budget:
            x0 = params.vec_from_preset(p["values"])
            off, row["refit_off"] = refit(tgt, note, x0, xcc(0, 0, 0), budget)
            xon, row["refit_x"] = refit(tgt, note, x0, cands[best][1], budget)
            row["refit_gain"] = row["refit_off"] - row["refit_x"]
            # Kept, not just scored: a loss that moved is a claim about a sound, and the only way
            # to check the claim is to hear it. These go out as an auditionable bank below.
            row["preset_off"], row["preset_x"] = off, xon
        rows.append(row)
        tail = (f"   refit off {row['refit_off']:6.2f} / x-mod {row['refit_x']:6.2f}"
                f"  {'X-MOD' if row['refit_gain'] > 0 else 'off  '} by {abs(row['refit_gain']):5.2f}"
                if budget else "")
        print(f"  {clean_name(name):14} base {row['baseline']:6.2f} -> graft {row['graft_best']:6.2f}"
              f"  ({row['graft_setting']:>13}, {row['grid_beating_baseline']:2d}/{len(cands)-1} beat it)" + tail)

    out = {"loss": backend, "refit_budget": budget, "rows": rows}
    json.dump(out, open(os.path.join(HERE, "xmod_probe.json"), "w"), indent=1)
    if budget:
        write_bank(rows)
    g = [r["graft_gain"] for r in rows]
    print(f"\ngraft: {sum(x > 0 for x in g)}/{len(g)} improved, mean gain {np.mean(g):+.2f}")
    if budget:
        r = [x["refit_gain"] for x in rows]
        print(f"refit: {sum(x > 0 for x in r)}/{len(r)} improved, mean gain {np.mean(r):+.2f}")


if __name__ == "__main__":
    main()
