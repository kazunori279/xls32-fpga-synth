#!/usr/bin/env python3
"""Bake webui/static/spec.json — the control map and preset bank the browser reads at boot.

Until M31 the browser fetched this from the Python bridge's /api/spec, which called
`synthspec.spec()` live. There is no bridge any more (the UI drives the board directly over
Web MIDI / Web Serial), so the same call runs here and the result is committed as a static file.

`synthspec.py` stays the single source of truth. Re-run this after editing it, or after
`build_presets.py` rewrites any `webui/presets_*.json`:

    uv run presetgen/build_spec.py

What is NOT in the file: `sr`. The old endpoint attached it from the open transport, because
the sample rate is a property of the board that happens to be plugged in (32 kHz Basys 3,
48 kHz Tiliqua) and not of the control map. The browser now takes it from whichever transport
it connected to, which is the same fact from a closer source.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "webui"))

import synthspec                                        # noqa: E402

OUT = os.path.join(REPO, "webui", "static", "spec.json")


def main():
    spec = synthspec.spec()
    with open(OUT, "w") as f:
        json.dump(spec, f, separators=(",", ":"))       # minified: it ships over the wire
    size = os.path.getsize(OUT)
    by = {}
    for p in spec["factory"]:
        by[p["source"]] = by.get(p["source"], 0) + 1
    print(f"wrote {os.path.relpath(OUT, REPO)}  {size / 1024:.0f} KB")
    print(f"  {len(spec['controls'])} controls, {len(spec['factory'])} presets  {by}")


if __name__ == "__main__":
    main()
