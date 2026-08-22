"""Re-grade the stored captures with the analysis code as it stands, no board attached.

    uv run python test/regrade.py                 # against the published report.json
    uv run python test/regrade.py --clean         # force pick_window(clean=True) too

`run_tests.py` saves every graded take to `test/out/wav/<id>.wav`, and `check(samples)` is
a pure function of those samples. So the question "what would this analysis change do to
the published scores?" does not need a board day -- it needs the wavs, which are already
on disk. That was the stated blocker on #11 and it was never true.

**Two things the wavs are not, and both matter when reading the delta.** `save_wav` writes
`normalize(s)`, peak-scaled to 30000, so any check with an absolute threshold reads
slightly differently offline: `_chk_preset` counts glitches over 9000 and clip over 32300,
which is why the five factory presets reproduce to within ~1.4 points rather than exactly.
And the wav is the *best* take of up to five retries, not the run -- so this re-grades the
take that was published, not the capture session that produced it.

What survives both caveats is the **delta**: two analysis variants over the same samples,
where everything except the thing being changed is identical.
"""
import argparse
import os
import struct
import sys
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "host"))
sys.path.insert(0, os.path.join(_ROOT, "webui"))
sys.path.insert(0, _ROOT)
os.environ.setdefault("XLS32_BOARD", "tiliqua")

import analyze_fft                                                      # noqa: E402
import harness as H                                                     # noqa: E402
import cases_basic, cases_integration, cases_stress                     # noqa: E402

OUT = os.path.join(_HERE, "out")
_PICK = analyze_fft.pick_window


def load_wav(path):
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        return list(struct.unpack(f"<{n}h", w.readframes(n)))


def grade(tc, s):
    """The case's own score for these samples, or the exception as a string.

    `run_case` swallows check errors into a 0 and a metric; here they are worth seeing,
    because a check that throws offline usually means it reached for something the wav
    does not carry rather than that the board misbehaved."""
    try:
        return tc.check(s).score
    except Exception as e:                      # noqa: BLE001 -- reporting, not handling
        return f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clean", action="store_true",
                    help="also grade with pick_window(clean=True) and show the delta")
    args = ap.parse_args()

    cases = {c.id: c for c in
             cases_basic.CASES + cases_integration.CASES + cases_stress.CASES}
    published = {}
    rp = os.path.join(OUT, "report.json")
    if os.path.exists(rp):
        import json
        published = {r["id"]: r["score"] for r in json.load(open(rp))["results"]}

    rows, absent = [], []
    for cid, tc in cases.items():
        p = os.path.join(OUT, "wav", f"{cid}.wav")
        if not os.path.exists(p):
            absent.append(cid)
            continue
        s = load_wav(p)
        analyze_fft.pick_window = _PICK
        now = grade(tc, s)
        alt = None
        if args.clean:
            analyze_fft.pick_window = lambda x, W=2048, clean=True: _PICK(x, W, clean=True)
            alt = grade(tc, s)
            analyze_fft.pick_window = _PICK
        rows.append((cid, published.get(cid), now, alt))

    def n(x):
        return isinstance(x, (int, float))

    def delta(a, b):
        return b - a if n(a) and n(b) else None

    moved = [(c, p, w, a) for c, p, w, a in rows
             if (d := delta(p, w)) is None or abs(d) >= 0.05]
    print(f"{len(rows)} captures re-graded"
          + (f", {len(absent)} cases have no wav" if absent else ""))
    print(f"\nagainst the published report: {len(moved)} differ")
    for cid, pub, now, _ in moved:
        p = f"{pub:6.1f}" if n(pub) else "     -"
        w = f"{now:6.1f}" if n(now) else str(now)
        d = delta(pub, now)
        print(f"  {cid:26} published {p}  now {w}" + (f"  ({d:+.1f})" if d else ""))

    if args.clean:
        shifted = [(c, w, a) for c, _, w, a in rows if (d := delta(w, a)) and abs(d) >= 0.05]
        print(f"\npick_window(clean=True): {len(shifted)} of {len(rows)} move")
        for cid, now, alt in shifted:
            print(f"  {cid:26} {now:6.1f} -> {alt:6.1f}  ({alt - now:+.1f})")
        vals = [(w, a) for _, _, w, a in rows if n(w) and n(a)]
        if vals:
            print(f"  mean {sum(w for w, _ in vals)/len(vals):.2f} -> "
                  f"{sum(a for _, a in vals)/len(vals):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
