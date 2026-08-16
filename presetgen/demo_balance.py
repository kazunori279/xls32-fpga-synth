"""Measure — and optionally set — the per-part VOLUME (CC7) of a demo song.

The four parts of a demo song are mixed by addition on the board (`synth.x:422`, one `mixacc`
for all 32 voices) and hard-clipped at the end of it (`scale_mix`), so the only thing standing
between a four-part song and a mush is CC7. Three of the four shipped songs got their levels set
by ear through the panel's 💾 TONES button; this measures what that ear did, so the fourth can be
made to match without guessing.

The measurement is an offline mix: every note of the song is rendered through `engine.render`
(the same model the search fits against), summed into a per-part track at its own onset, and
scored as A-weighted loudness. Two numbers per part, because they answer different questions:

  `while playing`  the 90th-percentile frame loudness -- how loud the part is *when it sounds*.
                   This is the one a mix engineer sets, and the one --apply equalises.
  `over the song`  mean power across the whole song, silence included -- the part's share of the
                   total energy. A sparse part is quiet here and loud in the other column, which
                   is correct and is why balancing on this one would shout.

Rendered dry (`fx=False`): reverb/chorus/delay are shared by the four parts and applied after the
sum, so they scale everything alike and cannot change the balance. They do add to the peak, which
is why the reported headroom is a lower bound on the clipping risk, not an upper one.

    uv run python presetgen/demo_balance.py                    # measure all four songs
    uv run python presetgen/demo_balance.py Goldberg           # measure one (substring match)
    uv run python presetgen/demo_balance.py Goldberg --apply   # ...and write the new CC7s back
"""
import json, os, sys
import numpy as np
import librosa

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine

DEMOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webui", "static", "demos.json")
EFFECT_IDS = ["reverb", "room", "chorusd", "echod", "dtime"]   # app.js:32 -- shared, live on the song
PPB = 4
TAIL_S = 1.5          # release tail past the gate; longer than any bank release at these rates
NFFT, HOP = 2048, 512

_FREQ = librosa.fft_frequencies(sr=engine.SR, n_fft=NFFT)
_AW = 10.0 ** (librosa.A_weighting(np.maximum(_FREQ, 1e-6)) / 10.0)     # dB -> power weight


def _frame_power(x):
    """A-weighted power per STFT frame. Absolute scale is fixed by _REF below."""
    S = np.abs(librosa.stft(x, n_fft=NFFT, hop_length=HOP, center=False)) ** 2
    return (S * _AW[:, None]).sum(0)


# 0 dBA := a full-scale 1 kHz sine, so the numbers read like dBFS for a mid-band tone.
_REF = float(np.mean(_frame_power(np.sin(2 * np.pi * 1000 *
                                         np.arange(engine.SR) / engine.SR).astype(np.float32))))


def _db(p):
    return -np.inf if p <= 0 else 10.0 * np.log10(p / _REF)


def render_parts(song):
    """The song's four parts as separate dry float tracks, at CC7 = 127 (i.e. unscaled)."""
    beat = 60.0 / song["bpm"]
    fx = {k: song.get(k, 0) for k in EFFECT_IDS}
    n = int((song["bars"] * 4 * beat + TAIL_S + 1.0) * engine.SR)
    tracks = [np.zeros(n, dtype=np.float32) for _ in range(PPB)]
    cache = {}
    for t, dur, ch, note, vel in song["notes"]:
        if ch >= PPB:
            continue
        gate = round(dur * beat, 3)
        key = (ch, note, gate, vel)
        if key not in cache:
            # `fx=False` on purpose -- see the module docstring. The song's effect state still has
            # to be in the dict decode() sees, or `room`/`dtime` would read as their defaults.
            cache[key] = engine.render({**song["parts"][ch], **fx}, note=note, gate_s=gate,
                                       tail_s=TAIL_S, vel=vel, fx=False)
        sig = cache[key]
        i = int(round(t * beat * engine.SR))
        tracks[ch][i:i + len(sig)] += sig[:max(0, n - i)]
    return tracks


def measure(song, tracks=None):
    """Per-part loudness at the song's current CC7, plus the summed mix."""
    tracks = render_parts(song) if tracks is None else tracks
    vols = [song["parts"][ch].get("volume", 127) for ch in range(PPB)]
    out = []
    for ch, tr in enumerate(tracks):
        p = _frame_power(tr * (vols[ch] / 127.0))
        live = p[p > p.max() * 1e-6] if p.max() > 0 else p        # frames where the part sounds
        out.append(dict(vol=vols[ch], playing=_db(np.percentile(live, 90) if len(live) else 0),
                        song=_db(p.mean()), duty=float(len(live)) / max(1, len(p))))
    mix = sum(tr * (v / 127.0) for tr, v in zip(tracks, vols))
    return out, dict(playing=_db(np.percentile(_frame_power(mix), 90)),
                     song=_db(_frame_power(mix).mean()), peak=float(np.abs(mix).max()))


def at_unity(tracks):
    """Each part's `while playing` loudness with CC7 out of the way (127 = unity)."""
    out = []
    for tr in tracks:
        p = _frame_power(tr)
        live = p[p > p.max() * 1e-6] if p.max() > 0 else p
        out.append(_db(np.percentile(live, 90) if len(live) else 0))
    return out


def balance(tracks, target=None):
    """CC7s that put every part at the same loudness while it sounds.

    The target defaults to the quietest part, and it has to: CC7 is a downward gain (`vol/127` at
    synth.x:412, 127 = unity), so the only way to level four parts is to bring the loud ones down
    to the quiet one. Asking for anything above that just pins the quiet part at 127 and tilts the
    mix the other way.
    """
    lv = at_unity(tracks)
    t = min(lv) if target is None else target
    return [int(min(127, max(1, round(127 * 10.0 ** ((t - l) / 20.0))))) for l in lv]


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in sys.argv
    demos = json.load(open(DEMOS))
    picked = [s for s in demos["songs"] if not argv or argv[0].lower() in s["name"].lower()]
    if not picked:
        sys.exit(f"no song matching {argv[0]!r}")
    if apply_ and len(picked) != 1:
        sys.exit("--apply wants exactly one song")

    # Levelling is per song and not across the bank. The three hand-mixed songs sit 17 dBA apart
    # from each other -- that spread is the ear choosing how loud each piece should be, and there
    # is no bank-wide number hiding behind it to normalise to.
    target = None
    for a in sys.argv[1:]:
        if a.startswith("--target="):
            target = float(a.split("=", 1)[1])

    for s in picked:
        tracks = render_parts(s)
        parts, mix = measure(s, tracks)
        print(f"{s['name']}   {s['bars'] * 4 * 60 / s['bpm']:.0f}s")
        print("        vol   playing   over song   duty")
        for ch, p in enumerate(parts):
            print(f"  part {ch}  {p['vol']:3d}   {p['playing']:+6.1f}      {p['song']:+6.1f}   "
                  f"{p['duty'] * 100:4.0f}%")
        print(f"  mix          {mix['playing']:+6.1f}      {mix['song']:+6.1f}   "
              f"peak {mix['peak']:.2f}{'  CLIPS' if mix['peak'] > 1.0 else ''}")
        new = balance(tracks, target)
        print(f"  balanced ->  {new}")
        if apply_:
            for ch in range(PPB):
                s["parts"][ch]["volume"] = new[ch]
            _, mix2 = measure(s, tracks)
            print(f"  after        {mix2['playing']:+6.1f}      {mix2['song']:+6.1f}   "
                  f"peak {mix2['peak']:.2f}{'  CLIPS' if mix2['peak'] > 1.0 else ''}")
            with open(DEMOS, "w") as f:
                f.write(json.dumps(demos, indent=1) + "\n")
            print(f"  wrote {os.path.normpath(DEMOS)}")
        print()


if __name__ == "__main__":
    main()
