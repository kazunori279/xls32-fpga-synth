"""Hand-designed FM / ring-mod showcase bank (M19 cross-osc). The inverse-synthesis search
can't discover these — the magnitude-spectrogram loss doesn't reward FM/ring even for bells
(subtractive approximates the magnitude 'well enough'; the residual is the decay, not the
missing inharmonic partials). So, like every real FM synth, these are voiced by ear from FM
theory: sine carrier, mod:carrier ratio + depth set for the target timbre.

Writes webui/presets_fm.json (source 'fm' -> its own browser tab). Run to (re)generate;
prints a sim sanity-check (RMS + how many spectral peaks the cross-mod adds vs the same patch
with X-Mod off).
"""
import os, sys, json
import numpy as np, numpy.fft as fft
sys.path.insert(0, os.path.dirname(__file__))
import engine
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "webui")))
import synthspec

def _w(v): return (v & 7) << 4
def _s(v): return (v & 3) << 5
OFF, RING, FM, FMP = _s(0), _s(1), _s(2), _s(3)          # xmode
RA = [_w(i) for i in range(8)]                           # xratio: [1,1.5,2,3,4,5,7,0.5]
SINE = _w(0)

# name, category, overrides (raw CC; unset -> synthspec DEFAULTS). Strong-FM voicing: FM index
# now scales with depth*modsig, so bells want high xdepth + an inharmonic ratio (idx 3-6).
PRESETS = [
    # --- Pluck: bells & mallets (strong FM/ring make the inharmonic partials subtractive can't) ---
    ("FM Glockenspiel", "Pluck", dict(wave=SINE, xmode=FMP, xdepth=95,  xratio=RA[6], cutoff=118, reso=8, aatt=0, adec=40, asus=6,  arel=44, fx=_w(2))),
    ("Tubular Bells",   "Pluck", dict(wave=SINE, xmode=FMP, xdepth=82,  xratio=RA[3], cutoff=105, aatt=0, adec=80, asus=20, arel=120, fx=_w(4), room=_s(2))),
    ("FM Vibraphone",   "Pluck", dict(wave=SINE, xmode=FM,  xdepth=64,  xratio=RA[2], aatt=0, adec=62, asus=45, arel=66, trem=_s(1), fx=_w(4))),
    ("Music Box",       "Pluck", dict(wave=SINE, xmode=FMP, xdepth=72,  xratio=RA[5], cutoff=122, aatt=0, adec=34, asus=4, arel=28)),
    ("Ring Bells",      "Pluck", dict(wave=SINE, xmode=RING, xdepth=100, xratio=RA[3], aatt=0, adec=45, asus=15, arel=44, fx=_w(4))),
    # --- Keys: FM electric pianos / clav ---
    ("DX E-Piano",      "Keys",  dict(wave=SINE, xmode=FM,  xdepth=60, xratio=RA[0], aatt=2, adec=58, asus=68, arel=48, cutoff=105, fx=_w(1))),
    ("FM Clav",         "Keys",  dict(wave=SINE, xmode=FMP, xdepth=78, xratio=RA[2], aatt=0, adec=40, asus=28, arel=30, cutoff=98)),
    ("Bell Keys",       "Keys",  dict(wave=SINE, xmode=FM,  xdepth=66, xratio=RA[3], aatt=2, adec=60, asus=58, arel=62, fx=_w(1))),
    # --- Brass: FM / ring horns ---
    ("FM Brass",        "Brass", dict(wave=SINE, xmode=FM,  xdepth=52, xratio=RA[0], cutoff=80, fdepth=55, fatt=22, aatt=14, adec=55, asus=105, arel=40, unison=_s(1))),
    ("Ring Horn",       "Brass", dict(wave=_w(2), xmode=RING, xdepth=72, xratio=RA[2], cutoff=85, aatt=8, asus=110, arel=40)),
    # --- FX: metallic / clangorous / robotic ---
    ("Clangor",         "FX",    dict(wave=SINE, xmode=RING, xdepth=112, xratio=RA[6], asus=122, arel=90, fx=_w(4), room=_s(3))),
    ("Metallic Drone",  "FX",    dict(wave=SINE, xmode=FMP,  xdepth=120, xratio=RA[6], asus=127, cutoff=95, fx=_w(4), room=_s(3))),
    ("Robotic",         "FX",    dict(wave=SINE, xmode=RING, xdepth=84,  xratio=RA[7], lforate=60, lfodep=30, asus=120)),
    ("FM Sweep",        "FX",    dict(wave=SINE, xmode=FMP,  xdepth=104, xratio=RA[2], fdepth=80, fatt=40, asus=110, fx=_w(4))),
    # --- Bass ---
    ("FM Bass",         "Bass",  dict(wave=SINE, xmode=FM,  xdepth=44, xratio=RA[0], cutoff=64, sub=_s(2), aatt=0, adec=52, asus=82, arel=30)),
    ("Ring Bass",       "Bass",  dict(wave=SINE, xmode=RING, xdepth=64, xratio=RA[2], cutoff=56, sub=_s(1), asus=85, arel=30)),
    # --- Lead ---
    ("FM Lead",         "Lead",  dict(wave=SINE, xmode=FM,  xdepth=70, xratio=RA[2], cutoff=102, asus=112, unison=_s(1), detune=_s(1))),
    ("Ring Lead",       "Lead",  dict(wave=SINE, xmode=RING, xdepth=80, xratio=RA[3], cutoff=105, asus=110)),
]


def logmag(sig, sr):
    W = sig[int(0.03*sr):int(0.03*sr)+8192]
    if len(W) < 8192: W = np.pad(W, (0, 8192 - len(W)))
    m = np.abs(fft.rfft(W * np.hanning(len(W))))
    return np.log1p(m / (m.max() + 1e-9))


def main():
    out = []
    print(f"{'name':18} {'cat':6} peak   specdiff  verdict")
    for name, cat, over in PRESETS:
        vals = dict(synthspec.DEFAULTS); vals.update(over)
        a = engine.render(vals, note=72, gate_s=1.0, tail_s=0.4)
        off = dict(vals); off['xmode'] = OFF
        b = engine.render(off, note=72, gate_s=1.0, tail_s=0.4)
        peak = float(np.max(np.abs(a)))
        sd = float(np.sqrt(np.mean((logmag(a, engine.SR) - logmag(b, engine.SR)) ** 2)))
        verdict = "SILENT" if peak < 0.12 else ("weak-xmod" if sd < 0.03 else "OK")
        print(f"{name:18} {cat:6} {peak:.3f}  {sd:.3f}    {verdict}")
        out.append({"name": name, "category": cat, "values": vals})
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "webui", "presets_fm.json"))
    json.dump({"presets": out}, open(path, "w"))
    print(f"\nwrote {path}: {len(out)} presets")


if __name__ == "__main__":
    main()
