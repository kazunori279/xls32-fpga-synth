"""Freesound CC0 analog-synth samples as ground-truth targets.

Searches Freesound for Creative-Commons-0 single-note analog-synth sounds per category,
uses the AudioCommons descriptors (ac_note_midi, ac_single_event, ac_tonality) to keep tonal
one-shots and to know each sample's pitch (so the sim renders at the matching note), and
downloads the HQ mp3 preview. Needs a free API token in $FREESOUND_API_TOKEN
(get one at https://freesound.org/apiv2/apply/).

Interface matches nsynth.py: list_targets(per_cat) -> [(category, name, path, note)], load(path).
"""
import os, json, re, time, urllib.request, urllib.parse
import numpy as np
from pedalboard.io import AudioFile

TOKEN = os.environ.get("FREESOUND_API_TOKEN", "")
if not TOKEN:
    _tf = os.path.join(os.path.dirname(__file__), ".freesound_token")   # gitignored
    if os.path.exists(_tf):
        TOKEN = open(_tf).read().strip()
API = "https://freesound.org/apiv2/search/text/"
CACHE = os.path.join(os.path.dirname(__file__), "targets_freesound")

CAT_QUERIES = {
    "Bass":    ["analog synth bass", "moog bass", "juno bass"],
    "Lead":    ["analog synth lead", "moog lead", "sawtooth lead"],
    "Pad":     ["analog synth pad", "juno pad", "warm synth pad"],
    "Pluck":   ["synth pluck", "analog pluck", "synth blip"],
    "Keys":    ["analog synth keys", "synth electric piano", "juno keys"],
    "Brass":   ["synth brass", "analog brass", "oberheim brass"],
    "Strings": ["synth strings", "juno strings", "analog string machine"],
    "FX":      ["analog synth sweep", "synth fx", "synth riser"],
}
NOTE = {"Bass": 48}
_CLEAN = re.compile(r"[^A-Za-z0-9 ]+")


_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) presetgen/1.0"
_THROTTLE = 2.0                       # seconds between API calls (stay under Freesound limits)


def _req(url, auth=True):
    h = {"User-Agent": _UA}
    if auth:
        h["Authorization"] = f"Token {TOKEN}"
    return urllib.request.Request(url, headers=h)


def _get(url):
    time.sleep(_THROTTLE)
    with urllib.request.urlopen(_req(url), timeout=30) as r:
        return json.load(r)


def _search(query, page_size=40):
    params = {
        "query": query,
        "filter": 'license:"Creative Commons 0" duration:[0.3 TO 6.0]',   # ac_* filtered in code
        "fields": "id,name,previews,license,duration,ac_analysis",
        "sort": "score", "page_size": str(page_size),
    }
    return _get(API + "?" + urllib.parse.urlencode(params)).get("results", [])


def _download(url, path):
    if not os.path.exists(path):
        time.sleep(0.4)
        with urllib.request.urlopen(_req(url, auth=False), timeout=60) as r, open(path, "wb") as f:
            f.write(r.read())
    return path


def list_targets(per_cat=16):
    if not TOKEN:
        raise SystemExit("set FREESOUND_API_TOKEN (get one at https://freesound.org/apiv2/apply/)")
    out = []
    for cat, queries in CAT_QUERIES.items():
        cdir = os.path.join(CACHE, cat); os.makedirs(cdir, exist_ok=True)
        picked, seen = [], set()
        for q in queries:
            if len(picked) >= per_cat:
                break
            try:
                results = _search(q)
            except Exception as e:
                print(f"  search '{q}' failed: {repr(e)[:80]}"); continue
            for r in results:
                if len(picked) >= per_cat:
                    break
                ac = r.get("ac_analysis") or {}
                midi = ac.get("ac_note_midi")
                prev = (r.get("previews") or {}).get("preview-hq-mp3")
                if midi is None or prev is None or r["id"] in seen:
                    continue
                if ac.get("ac_single_event") is False:      # keep tonal one-shots (True/None ok)
                    continue
                note = int(round(midi))
                if not (28 <= note <= 96):
                    continue
                seen.add(r["id"])
                name = (cat + " " + _CLEAN.sub("", r["name"]).strip())[:28]
                path = os.path.join(cdir, f"{r['id']}.mp3")
                try:
                    _download(prev, path)
                except Exception as e:
                    print(f"  dl {r['id']} failed: {repr(e)[:60]}"); continue
                picked.append((cat, name, path, note))
                time.sleep(0.05)
        out.extend(picked)
        print(f"  {cat:8} {len(picked)} targets")
    return out


def load(path):
    with AudioFile(path) as f:
        a = f.read(f.frames); sr = f.samplerate
    a = a.mean(axis=0) if a.ndim > 1 else a
    return a.astype(np.float32).flatten(), sr


if __name__ == "__main__":
    ts = list_targets(per_cat=int(os.environ.get("PER", "4")))
    from collections import Counter
    print("total:", len(ts), dict(Counter(t[0] for t in ts)))
    for t in ts[:6]:
        print("  ", t[0], t[1], "note", t[3], os.path.basename(t[2]))
