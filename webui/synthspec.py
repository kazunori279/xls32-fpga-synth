"""Single source of truth for the web UI: the synth's MIDI-CC control map and the
factory presets. Served to the browser as JSON via /api/spec, and reused by the host
tools. Values in presets are the *raw* CC values sent on the wire (for bit-packed
selects that's already the shifted value the firmware decodes).

Firmware CC map (see synth.x / top.v):
  1  vibrato depth (mod wheel, bits5:7)    74 cutoff (0..127)
  5  portamento (bits5:7)                  75 pulse width (0..127)
  20 amp attack     21 amp decay          76 LFO rate (0..127)
  22 amp sustain    23 amp release        77 LFO depth (0..127)
  24 filt attack    25 filt decay         78 detune (bits5:7)
  26 filt sustain   27 filt release       79 filter-env depth (0..127)
  70 waveform (bits4:7: 0..4)             80 unison (bits5:7)
  71 resonance (0..127)                   82/93/94/95 delay time / reverb wet / chorus / echo (shell)
  72 filter mode (bits5:7)                91 reverb size (bits5:7, shell-sniffed)
  73 sub-osc level (bits5:7)              92 tremolo depth (bits5:7)
  85 cross-osc mode (bits5:7)            86 cross-osc depth (0..127)
  87 cross-osc ratio (bits5:7)           0xE0 pitch bend (14-bit, live wheel)
"""

# Bit-packed select helpers: firmware reads evv[4:7] (wave/fx) or evv[5:7] (2-bit).
def _w(v):  return (v & 7) << 4    # 3-bit field @ bit4 (CC70 wave, CC83 fx)
def _s(v):  return (v & 3) << 5    # 2-bit field @ bit5 (mode/sub/detune/unison/...)

WAVE_OPTS   = [("Sine", _w(0)), ("Saw", _w(1)), ("Square", _w(2)), ("Triangle", _w(3)), ("Noise", _w(4))]
FMODE_OPTS  = [("LP", _s(0)), ("HP", _s(1)), ("BP", _s(2)), ("Notch", _s(3))]
SUB_OPTS    = [("Off", _s(0)), ("1/4", _s(1)), ("1/2", _s(2)), ("Full", _s(3))]
DETUNE_OPTS = [("Off", _s(0)), ("3¢", _s(1)), ("7¢", _s(2)), ("13¢", _s(3))]
UNISON_OPTS = [("Off", _s(0)), ("2", _s(1)), ("3", _s(2)), ("4", _s(3))]
PORTA_OPTS  = [("Off", _s(0)), ("Fast", _s(1)), ("Med", _s(2)), ("Slow", _s(3))]
# Effects (post-mix shell, stereo). Each is DEPTH-GATED — on when its knob > 0; there is NO mode
# selector (the old CC83 dry/chorus/delay/both byte is now unused). Chorus depth CC94, echo/delay
# depth CC95 + time CC82, reverb wet CC93 + room size CC91. Reverb is a full 8-comb + 4-all-pass
# per-channel Freeverb (serial send) whose comb-feedback multiply maps to a DSP48 on the Vivado
# backend (it railed under F4PGA soft multipliers -> was temporarily hidden before).
ROOM_OPTS   = [("Room", _s(0)), ("Hall", _s(1)), ("Large", _s(2)), ("Cathedral", _s(3))]
DEPTH_OPTS  = [("Off", _s(0)), ("Light", _s(1)), ("Med", _s(2)), ("Deep", _s(3))]
XMODE_OPTS  = [("Off", _s(0)), ("Ring", _s(1)), ("FM", _s(2)), ("FM+", _s(3))]
# xratio is a 3-bit field (CC87 evv[4:7]) -> 8 shift/add ratios incl. inharmonic FM ratios
XRATIO_OPTS = [("1:1", _w(0)), ("1.5:1", _w(1)), ("2:1", _w(2)), ("3:1", _w(3)),
               ("4:1", _w(4)), ("5:1", _w(5)), ("7:1", _w(6)), ("½:1", _w(7))]

# id, cc, label, section, kind, default, options
_CTRL = [
    ("wave",   70, "Wave",     "Oscillators", "select", _w(1), WAVE_OPTS),
    ("pw",     75, "Pulse W",  "Oscillators", "knob",   64,    None),
    ("detune", 78, "Detune",   "Oscillators", "select", _s(0), DETUNE_OPTS),
    ("sub",    73, "Sub Osc",  "Oscillators", "select", _s(0), SUB_OPTS),

    ("cutoff", 74, "Cutoff",   "Filter",      "knob",   90,    None),
    ("reso",   71, "Reso",     "Filter",      "knob",   30,    None),
    ("fmode",  72, "Mode",     "Filter",      "select", _s(0), FMODE_OPTS),

    # merged panel: filter-env (top row) + amp-env (bottom row), labels prefixed to disambiguate
    ("fatt",   24, "Flt Atk",  "Envelopes",   "knob",   8,     None),
    ("fdec",   25, "Flt Dec",  "Envelopes",   "knob",   40,    None),
    ("fsus",   26, "Flt Sus",  "Envelopes",   "knob",   100,   None),
    ("frel",   27, "Flt Rel",  "Envelopes",   "knob",   40,    None),
    ("fdepth", 79, "Flt Amt",  "Envelopes",   "knob",   0,     None),

    ("aatt",   20, "Amp Atk",  "Envelopes",   "knob",   8,     None),
    ("adec",   21, "Amp Dec",  "Envelopes",   "knob",   40,    None),
    ("asus",   22, "Amp Sus",  "Envelopes",   "knob",   100,   None),
    ("arel",   23, "Amp Rel",  "Envelopes",   "knob",   40,    None),
    ("volume",  7, "Volume",   "Envelopes",   "knob",   127,   None),   # per-part output level (CC7)

    ("lforate",76, "Rate",     "LFO",         "knob",   40,    None),
    ("lfodep", 77, "Depth",    "LFO",         "knob",   0,     None),
    ("trem",   92, "Tremolo",  "LFO",         "knob",   0,     None),   # continuous depth (CC92)

    ("unison", 80, "Voices",   "Unison",      "select", _s(0), UNISON_OPTS),
    ("porta",   5, "Glide",    "Unison",      "select", _s(0), PORTA_OPTS),

    ("xmode",  85, "X-Mod",    "Cross-Mod",   "select", _s(0), XMODE_OPTS),
    ("xdepth", 86, "X-Depth",  "Cross-Mod",   "knob",   0,     None),
    ("xratio", 87, "X-Ratio",  "Cross-Mod",   "select", _s(0), XRATIO_OPTS),

    # no mode selector — each effect is on when its knob > 0 (all default off/0)
    ("chorusd",94, "Chorus",   "Effects",     "knob",   0,     None),   # chorus depth/wet (CC94)
    ("echod",  95, "Delay",    "Effects",     "knob",   0,     None),   # delay(echo) depth/wet (CC95)
    ("dtime",  82, "Delay Time","Effects",    "knob",   63,    None),   # delay time (CC82) ~4..508ms
    ("reverb", 93, "Reverb",   "Effects",     "knob",   0,     None),   # reverb wet (CC93)
    ("room",   91, "Reverb Size","Effects",   "select", _s(2), ROOM_OPTS),  # reverb decay/size (CC91)
]

SECTIONS = ["Oscillators", "Filter", "Envelopes", "LFO", "Unison", "Cross-Mod", "Effects"]

# Shared by ALL parts, not per-part: the post-mix effects (CC82/91/93/94/95) live in the shell
# (one unit for the summed mix), so the UI treats them as one global setting. (LFO rate CC76 is
# now per-part — each timbre has its own LFO oscillator.)
GLOBAL_CTRL = {"reverb", "room", "chorusd", "echod", "dtime"}

def _control(id, cc, label, section, kind, default, options):
    c = {"id": id, "cc": cc, "label": label, "section": section, "kind": kind, "default": default}
    if id in GLOBAL_CTRL:
        c["global"] = True
    if options:
        c["options"] = [{"label": l, "value": v} for l, v in options]
    return c

CONTROLS = [_control(*c) for c in _CTRL]
DEFAULTS = {c[0]: c[5] for c in _CTRL}

def _preset(name, **over):
    vals = dict(DEFAULTS); vals.update(over)
    return {"name": name, "values": vals}

# ---- Factory bank: 128 presets across 8 categories (16 each), soft-synth style. ----
CATEGORIES = ["Bass", "Lead", "Pad", "Pluck", "Keys", "Brass", "Strings", "FX"]

def _clamp(v):  return max(0, min(127, int(v)))

# per-category base character (overrides on DEFAULTS); the spread below varies each of the 16.
_TMPL = {
    "Bass":    dict(wave=_w(1), sub=_s(2), cutoff=58, reso=34, aatt=2, adec=46, asus=80,
                    arel=30, fdepth=40, fatt=0, fdec=42, fsus=30, frel=28, fx=_w(0)),
    "Lead":    dict(wave=_w(1), detune=_s(1), cutoff=92, reso=40, aatt=4, adec=44, asus=104,
                    arel=44, lforate=48, fx=_w(2)),
    "Pad":     dict(wave=_w(1), unison=_s(2), detune=_s(2), cutoff=74, reso=24, aatt=96,
                    adec=70, asus=118, arel=110, lforate=20, lfodep=16, fx=_w(1)),
    "Pluck":   dict(wave=_w(1), cutoff=48, reso=60, fdepth=100, fatt=0, fdec=40, fsus=24,
                    frel=36, aatt=2, adec=40, asus=40, arel=34, fx=_w(2)),
    "Keys":    dict(wave=_w(3), detune=_s(1), cutoff=90, reso=22, aatt=4, adec=54, asus=96,
                    arel=48, fx=_w(1)),
    "Brass":   dict(wave=_w(1), unison=_s(1), cutoff=70, reso=30, fdepth=60, fatt=30, fdec=50,
                    fsus=80, frel=40, aatt=18, adec=50, asus=100, arel=44, fx=_w(1)),
    "Strings": dict(wave=_w(1), unison=_s(3), detune=_s(2), cutoff=80, reso=20, aatt=90,
                    adec=70, asus=120, arel=100, lforate=24, lfodep=14, fx=_w(1)),
    "FX":      dict(wave=_w(4), cutoff=70, reso=90, fdepth=90, fatt=10, fdec=50, fsus=40,
                    frel=60, lforate=80, lfodep=80, fx=_w(1)),
}

_NAMES = {
    "Bass":    ["Deep Sub", "Reese Wide", "Acid Growl", "808 Boom", "FM Punch", "Rubber",
                "Moog Round", "Dark Cellar", "Square Grind", "Sub Drop", "Talkbox", "Detroit",
                "Warehouse", "Fat Stack", "Analog Low", "Neuro"],
    "Lead":    ["Solar Lead", "Super Saw", "Echo Lead", "Screamer", "Glass Lead", "Hard Edge",
                "Retro Mono", "Cutting", "Vintage Lead", "Portamento", "Bright Blade",
                "Pulse Lead", "Nebula", "Ravebird", "Saw King", "Comet"],
    "Pad":     ["Cathedral Pad", "Warm Analog", "Glass Pad", "Aurora", "Drifting", "Choir Air",
                "Deep Space", "Halo", "Velvet", "Slow Bloom", "Ambient Wash", "Frost",
                "Evolving", "Nimbus", "Ocean", "Ether"],
    "Pluck":   ["Auto-Wah Pluck", "Crystal", "Koto", "Music Box", "Ping", "Blip Stack",
                "Marimba", "Plink", "Dew Drop", "Toy Piano", "Harp", "Bell Pluck", "Staccato",
                "Pixel", "Zither", "Spark"],
    "Keys":    ["E-Piano", "Clav", "Bright Rhodes", "DX Keys", "Toy Organ", "Wurli",
                "Glass Keys", "Vintage EP", "Soft Keys", "Chime Keys", "Bell EP",
                "Digital Keys", "Warm Rhodes", "Tine", "Mallet", "Celeste"],
    "Brass":   ["Analog Brass", "Synth Horn", "Fanfare", "Big Band", "Trumpet Stab",
                "Section", "Bold Brass", "Swell Horn", "Retro Brass", "Mono Horn",
                "Wide Brass", "Power Stab", "OB Brass", "Jump Brass", "Regal", "Blare"],
    "Strings": ["Analog Strings", "Ensemble", "Cinematic", "Solo Bow", "Warm Section",
                "Marcato", "Lush Strings", "Tremolo Str", "Film Score", "Baroque",
                "Wide Ensemble", "Slow Strings", "Octave Str", "Velvet Str", "Adagio",
                "Sostenuto"],
    "FX":      ["Riser", "Downlifter", "Noise Sweep", "Sci-Fi", "Alien Talk", "Wind",
                "Impact", "Glitch", "Drone", "Radio", "Vortex", "Metal Hit", "Siren",
                "Static", "Warp", "Abyss"],
}

# The original 5 curated presets, spliced into their categories (name + exact overrides).
_CURATED = {
    ("Bass", 0):  ("Sub Bass", dict(wave=_w(2), sub=_s(3), cutoff=52, reso=32, aatt=2, adec=48,
                    asus=78, arel=28, fdepth=45, fatt=0, fdec=40, fsus=36, frel=28, fx=_w(0))),
    ("Lead", 1):  ("Super Saw", dict(wave=_w(1), unison=_s(3), detune=_s(3), cutoff=100, reso=40,
                    aatt=20, adec=55, asus=112, arel=60, fx=_w(1))),
    ("Lead", 2):  ("Echo Lead", dict(wave=_w(2), pw=40, cutoff=96, reso=34, detune=_s(1),
                    aatt=4, adec=42, asus=106, arel=46, lforate=72, fx=_w(2))),
    ("Pad", 0):   ("Cathedral Pad", dict(wave=_w(1), cutoff=72, reso=24, aatt=104, adec=70,
                    asus=120, arel=118, unison=_s(1), detune=_s(2), lforate=22, lfodep=18,
                    fx=_w(1))),
    ("Pluck", 0): ("Auto-Wah Pluck", dict(wave=_w(1), cutoff=38, reso=96, fdepth=115, fatt=0,
                    fdec=58, fsus=28, frel=42, aatt=2, adec=60, asus=92, arel=44, fx=_w(2))),
}

def _factory():
    out = []
    for c in CATEGORIES:
        base = _TMPL[c]
        for i in range(16):
            if (c, i) in _CURATED:
                name, over = _CURATED[(c, i)]
                v = dict(DEFAULTS); v.update(over)
                out.append({"name": name, "category": c, "values": v})
                continue
            v = dict(DEFAULTS); v.update(base)
            v["cutoff"] = _clamp(base.get("cutoff", 90) + (i - 8) * 4)   # brightness sweep
            v["reso"]   = _clamp(base.get("reso", 28) + (i % 4) * 9)
            if c in ("Pad", "Strings"):
                v["unison"] = _s(1 + (i % 3))
            elif c in ("Lead", "Keys"):
                v["detune"] = _s(i % 4); v["fx"] = _w([0, 1, 2, 2][i % 4])
            elif c == "Bass":
                v["sub"] = _s(1 + (i % 3)); v["fx"] = _w([0, 0, 1, 2][i % 4])
            elif c == "Pluck":
                v["fdepth"] = _clamp(80 + (i - 8) * 5); v["fx"] = _w([0, 2, 1, 2][i % 4])
            elif c == "Brass":
                v["fatt"] = _clamp(10 + (i % 4) * 10); v["unison"] = _s(i % 3)
            elif c == "FX":
                v["wave"] = _w(4 if i % 2 else 2); v["lfodep"] = _clamp((i * 8) % 128)
                v["fx"] = _w([2, 1, 3, 1][i % 4])
            out.append({"name": _NAMES[c][i], "category": c, "values": v})
    return out

FACTORY = _factory()

import os as _os, json as _json, glob as _glob

def _factory_bank():
    """Concatenate every matched source bank (webui/presets_*.json, e.g. presets_nsynth.json)
    written by presetgen/build_presets.py; fall back to the template FACTORY if none exist.
    Each preset is tagged with its `source` (the file stem after 'presets_') and its values are
    filled from DEFAULTS for any missing id."""
    out = []
    for path in sorted(_glob.glob(_os.path.join(_os.path.dirname(__file__), "presets_*.json"))):
        source = _os.path.basename(path)[len("presets_"):-len(".json")]
        try:
            d = _json.load(open(path))
            for p in (d.get("presets") if isinstance(d, dict) else d):
                vals = dict(DEFAULTS); vals.update(p["values"])
                out.append({"name": p["name"], "category": p["category"],
                            "source": source, "values": vals})
        except Exception:
            pass
    if not out:
        return [dict(p, source="factory") for p in FACTORY]
    return out

SOURCE_ORDER = ["soundfont", "fm", "nsynth"]   # preset-browser tab order (others appended after)

def _sources(bank):
    seen = []
    for p in bank:
        if p["source"] not in seen:
            seen.append(p["source"])
    return sorted(seen, key=lambda s: SOURCE_ORDER.index(s) if s in SOURCE_ORDER else len(SOURCE_ORDER))

def spec():
    bank = _factory_bank()
    return {"controls": CONTROLS, "sections": SECTIONS, "defaults": DEFAULTS,
            "categories": CATEGORIES, "factory": bank, "sources": _sources(bank)}
