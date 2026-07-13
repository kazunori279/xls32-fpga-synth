#!/usr/bin/env python3
"""End-to-end hardware test runner for the XLS synth.

Reflashes the board, drives every test case over USB, grades the captured audio
(0-100), records each test to a .wav, assembles one captioned spectrogram .mp4, and
writes a scored report (Markdown + JSON).

  uv run python test/run_tests.py                 # full suite (reflash + all + video + report)
  uv run python test/run_tests.py --smoke         # fast subset (pipeline check)
  uv run python test/run_tests.py --only stress   # one category
  uv run python test/run_tests.py --no-reflash --skip-video
"""
import os, sys, json, time, argparse, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "host"))
sys.path.insert(0, os.path.join(_ROOT, "webui"))

import harness as H            # noqa: E402
import analysis as A           # noqa: E402
import captions               # noqa: E402
import cases_basic, cases_integration, cases_stress   # noqa: E402
from uartaudio import SR       # noqa: E402

OUT = os.path.join(_HERE, "out")
SMOKE_IDS = {"pitch_a4", "wave_saw", "filter_lp_closed", "amp_release", "combo_lead", "stress_retrigger"}


def all_cases():
    return cases_basic.CASES + cases_integration.CASES + cases_stress.CASES


def run(args):
    cases = all_cases()
    if args.only:
        cases = [c for c in cases if c.category == args.only]
    if args.smoke:
        cases = [c for c in cases if c.id in SMOKE_IDS]
    if not cases:
        sys.exit("no test cases selected")

    for d in ("wav", "cards", "seg"):
        os.makedirs(os.path.join(OUT, d), exist_ok=True)

    if not args.no_reflash:
        H.reflash()
    dev, fd = H.open_board()
    print(f"[{dev}] running {len(cases)} tests")
    H.warmup(fd)

    results = []
    t0 = time.time()
    for i, tc in enumerate(cases, 1):
        s, res = H.run_case(fd, tc)
        if not s:
            s = [0] * int(0.5 * SR)
        wav = os.path.join(OUT, "wav", f"{tc.id}.wav")
        H.save_wav(wav, s)
        res.extra["wav"] = wav
        results.append((tc, res))
        print(f"  [{i:2}/{len(cases)}] {res.verdict:4} {res.score:5.1f}  {tc.category:11} {tc.id:22} {res.metric}")
    os.close(fd)
    print(f"captured {len(cases)} tests in {time.time()-t0:.0f}s")

    overall, grade, counts = score_overall(results)
    write_reports(results, overall, grade, counts, args)

    if not args.skip_video:
        build_video(results, overall, grade, counts)

    print(f"\nOVERALL {overall:.1f}/100 ({grade}) — "
          f"{counts['PASS']} pass / {counts['WARN']} warn / {counts['FAIL']} fail")
    print(f"report: {os.path.join(OUT,'report.md')}")
    return 1 if counts["FAIL"] else 0


def score_overall(results):
    tw = sum(tc.weight for tc, _ in results) or 1
    overall = sum(r.score * tc.weight for tc, r in results) / tw
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for _, r in results:
        counts[r.verdict] += 1
    return overall, H.grade(overall), counts


def _table(results, category):
    rows = [f"| {tc.id} | {r.score:.0f} | {r.verdict} | {r.metric} | {r.expected} |"
            for tc, r in results if tc.category == category]
    if not rows:
        return ""
    head = "\n| Test | Score | Verdict | Metric | Expected |\n|---|---:|---|---|---|\n"
    return head + "\n".join(rows) + "\n"


def write_reports(results, overall, grade, counts, args):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    md = [f"# XLS-5 synth — e2e test report",
          f"",
          f"_{now} · {len(results)} tests · board: hardware over USB_",
          f"",
          f"## Overall: **{overall:.0f}/100 ({grade})** — {counts['PASS']} PASS · {counts['WARN']} WARN · {counts['FAIL']} FAIL",
          f"",
          f"Captioned spectrogram video: [`report.mp4`](report.mp4)",
          f"",
          f"## 1. Basic functionality", _table(results, "basic"),
          f"## 2. Integration (typical combinations)", _table(results, "integration"),
          f"## 3. Stress (strict: glitches / clipping / latches)", _table(results, "stress"),
          f"## Stress findings"]
    for tc, r in results:
        if tc.category == "stress":
            md.append(f"- **{tc.title}** — {r.verdict} ({r.score:.0f}): {r.metric}")
    md.append("")
    with open(os.path.join(OUT, "report.md"), "w") as f:
        f.write("\n".join(md))

    js = {"generated": now, "overall": round(overall, 1), "grade": grade, "counts": counts,
          "results": [{"id": tc.id, "category": tc.category, "title": tc.title,
                       "score": round(r.score, 1), "verdict": r.verdict, "metric": r.metric,
                       "expected": r.expected, "wav": os.path.basename(r.extra.get("wav", ""))}
                      for tc, r in results]}
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(js, f, indent=2)


def build_video(results, overall, grade, counts):
    import video
    entries = []
    intro = os.path.join(OUT, "cards", "_intro.png")
    captions.render_intro(intro, "XLS-5 Synth — E2E Test Run",
                          f"{len(results)} tests over USB · basic · integration · stress")
    entries.append({"kind": "card", "id": "intro", "png": intro, "secs": 4.0})
    for i, (tc, r) in enumerate(results, 1):
        png = os.path.join(OUT, "cards", f"{tc.id}.png")
        captions.render_card(png, tc.category, tc.title, tc.desc, tc.expected,
                             index=i, total=len(results), verdict=r.verdict,
                             score=r.score, metric=r.metric)
        entries.append({"kind": "test", "id": tc.id, "png": png, "wav": r.extra["wav"], "secs": 3.0})
    summ = os.path.join(OUT, "cards", "_summary.png")
    captions.render_summary(summ, [f"PASS {counts['PASS']} · WARN {counts['WARN']} · FAIL {counts['FAIL']}",
                                   f"Basic: {sum(1 for tc,_ in results if tc.category=='basic')} tests",
                                   f"Integration: {sum(1 for tc,_ in results if tc.category=='integration')} tests",
                                   f"Stress: {sum(1 for tc,_ in results if tc.category=='stress')} tests"],
                            grade, overall)
    entries.append({"kind": "card", "id": "summary", "png": summ, "secs": 5.0})
    out = os.path.join(OUT, "report.mp4")
    print("building captioned video …")
    video.build(os.path.join(OUT, "seg"), entries, out)
    print(f"video: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-reflash", action="store_true")
    ap.add_argument("--skip-video", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--only", choices=["basic", "integration", "stress"])
    sys.exit(run(ap.parse_args()))
