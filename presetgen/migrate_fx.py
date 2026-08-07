"""One-shot (M27): rewrite the dead `fx` key in the shipped preset banks into live controls.

Every preset in webui/presets_{nsynth,fm,soundfont}.json carries an `fx` value from when CC83
selected one of five effect modes. Both boards now gate each effect on its own depth --
`echo_on = (echodep != 0)`, `chorus_on = (chdep != 0)` in top.v:210-211 -- and synthspec has no
CC83 control at all, so the key is inert: app.js `setValue('fx', 32)` writes a phantom entry and
sends nothing. The banks were fitted against presetgen/engine.py's old `_fx()`, which *did* render
those modes, so the patches have been playing dry since the depth gating landed.

The translation is the old model's own mode semantics, at the depth it hardcoded (`wet/2` -> 64):

    packed  mode              chorusd  echod  reverb
    0       dry                     0      0       0
    16      chorus                 64      0       0
    32      echo                    0     64       0
    48      chorus + echo          64     64       0
    64      reverb                  0      0      96

All three keys are written on every preset, zeros included. They have to be: chorusd/echod/reverb
are GLOBAL controls (synthspec.py:91), shared by all four parts, so a preset that merely omits
them inherits whatever the last one set -- pick a chorus patch, then a dry one, and the dry one
sings. `fx` never had that problem because it never did anything.

Left alone: `dtime` (only audible when echod > 0, and the shell's 63 is what the old fixed-tap
model approximated) and `room` (already live on CC91, already absent from all three banks).

Reverb wet is 96, deliberately under the 110-120 that the graded reverb / reverb_size /
stress_fx_tail cases drive, so migrated presets do not compete with the cases that measure the
tank. No shipped bank actually uses mode 4, so that row never fires here -- it is in the table for
make_fm_bank.py, whose source table does use it.

Idempotent: a bank with no `fx` left is reported unchanged. Run:  uv run presetgen/migrate_fx.py
"""
import json, os, sys

BANKS = ["presets_nsynth.json", "presets_fm.json", "presets_soundfont.json"]
WEBUI = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "webui"))

# packed CC83 value -> the live depths that reproduce it
FX_MAP = {
    0:  dict(chorusd=0,  echod=0,  reverb=0),
    16: dict(chorusd=64, echod=0,  reverb=0),
    32: dict(chorusd=0,  echod=64, reverb=0),
    48: dict(chorusd=64, echod=64, reverb=0),
    64: dict(chorusd=0,  echod=0,  reverb=96),
}


def migrate_values(vals):
    """Replace `fx` in place (preserving key order) with the three depths. Returns the packed
    mode that was found, or None if the preset was already migrated."""
    if "fx" not in vals:
        return None
    packed = vals["fx"]
    if packed not in FX_MAP:
        raise ValueError(f"unknown packed fx value {packed!r} -- mode {(packed >> 4) & 7}")
    out = {}
    for k, v in vals.items():
        if k == "fx":
            out.update(FX_MAP[packed])       # three keys land where the one used to be
        else:
            out[k] = v
    vals.clear(); vals.update(out)
    return packed


def main():
    total = 0
    for fname in BANKS:
        path = os.path.join(WEBUI, fname)
        doc = json.load(open(path))
        hist = {}
        for p in doc["presets"]:
            packed = migrate_values(p["values"])
            if packed is not None:
                hist[packed] = hist.get(packed, 0) + 1
        n = sum(hist.values())
        total += n
        if n:
            with open(path, "w") as f:
                json.dump(doc, f, indent=2)   # matches the checked-in formatting: indent 2, no final \n
        modes = ", ".join(f"{k}->{'+'.join(kk for kk, vv in FX_MAP[k].items() if vv) or 'dry'} x{v}"
                          for k, v in sorted(hist.items()))
        print(f"{fname:26} {n:3d}/{len(doc['presets']):3d} migrated  {modes}")
    print(f"\n{total} presets migrated")
    return 0 if total else 1        # nonzero when there was nothing to do, so a re-run is visible


if __name__ == "__main__":
    sys.exit(main())
