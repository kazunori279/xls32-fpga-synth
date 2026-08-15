"""Per-preset audition clips for the browser: what the target sounded like, and what we made of it.

The preset browser can select a patch and push it to a board, which is the right thing when there
is a board. It cannot answer the question that keeps coming up while listening -- "how far is this
from the sample it was fitted to?" -- because the target has never been anywhere near the browser.

So this renders two clips per preset into webui/preview/:

  ours    engine.py's render of the preset, at the note it was fitted at. This is the offline model
          the search optimises against, NOT the board. It is what the fit actually produced, which
          is what you want when judging the fit; when a board is connected the two can be compared
          in turn, and a difference between them is a finding about the RTL, not about the preset.
  target  the corpus sample the preset was fitted to, where the corpus is still on this machine.
          nsynth's is a 4.6 GB download and usually is not, so its presets get `ours` only and the
          browser hides the target button rather than offering a 404. presets_fm.json is voiced by
          ear and has no target by construction.

Both are peak-normalised to -3 dBFS, for the same reason ab_render.py does it: a preset that
happened to fit louder should not sound better.

    uv run python presetgen/build_previews.py [source ...]      # default: every bank in webui/
"""
import importlib
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine                                                              # noqa: E402
import search                                                              # noqa: E402
from ab_render import write_wav                                            # noqa: E402
from name_audit import note_of                                             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEBUI = os.path.abspath(os.path.join(HERE, "..", "webui"))
# Under static/, not beside the banks: index.html lives in static/ and the README serves the app
# with `-d webui/static`, so anything outside that directory is simply not on the web at all.
# Relative to index.html the path is "preview/..." either way, so serving `-d webui` also works.
OUT = os.path.join(WEBUI, "static", "preview")


def slug(name):
    """A filename that survives every preset name we ship -- spaces, '#', '/' and all."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "x"


def targets_for(source):
    """{preset name -> corpus path}, or {} when the corpus is not on this machine. Missing corpora
    are normal (nsynth) and are not an error: the bank still gets its `ours` clips."""
    try:
        ns = importlib.import_module(source)
        return ns, {name: path for _, name, path, _ in ns.list_targets(per_cat=16)}
    except Exception as e:
        print(f"  {source}: no targets ({type(e).__name__}: {str(e)[:60]}) -- rendering ours only")
        return None, {}


def main():
    sources = sys.argv[1:] or [os.path.basename(p)[len("presets_"):-len(".json")]
                               for p in sorted(os.listdir(WEBUI))
                               if re.fullmatch(r"presets_\w+\.json", p)]
    os.makedirs(OUT, exist_ok=True)
    engine.render(engine._DEFAULTS, gate_s=search.GATE_S, tail_s=search.TAIL_S)

    index = {}
    for source in sources:
        path = os.path.join(WEBUI, f"presets_{source}.json")
        if not os.path.exists(path):
            print(f"  {source}: no bank file, skipped")
            continue
        presets = json.load(open(path))["presets"]
        ns, targets = targets_for(source)
        d = os.path.join(OUT, source)
        os.makedirs(d, exist_ok=True)
        n_t = 0
        for p in presets:
            s = slug(p["name"])
            w = engine.render(p["values"], note=note_of(p["name"], p["category"]),
                              gate_s=search.GATE_S, tail_s=search.TAIL_S)
            write_wav(os.path.join(d, f"{s}_ours.wav"), w, engine.SR)
            has_t = p["name"] in targets
            if has_t:
                audio, sr = ns.load(targets[p["name"]])
                write_wav(os.path.join(d, f"{s}_target.wav"), audio, sr)
                n_t += 1
            index.setdefault(source, {})[p["name"]] = {"slug": s, "target": has_t}
        print(f"  {source}: {len(presets)} ours, {n_t} target")

    json.dump(index, open(os.path.join(OUT, "index.json"), "w"))
    mb = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(OUT) for f in fs) / 1e6
    print(f"\nwrote {OUT}  ({mb:.0f} MB)")


if __name__ == "__main__":
    main()
