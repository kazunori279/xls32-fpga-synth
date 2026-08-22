#!/usr/bin/env python3
"""End-to-end hardware test runner for the XLS synth.

Reflashes the board, drives every test case over USB, grades the captured audio
(0-100), records each test to a .wav, assembles one captioned spectrogram .mp4, and
writes a scored report (Markdown + JSON).

  uv run python test/run_tests.py                 # full suite (reflash + all + video + report)
  uv run python test/run_tests.py --smoke         # fast subset (pipeline check)
  uv run python test/run_tests.py --only stress   # one category
  uv run python test/run_tests.py --board tiliqua # grade the other board
  uv run python test/run_tests.py --no-reflash --skip-video
"""
import os, sys, json, time, argparse, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "host"))
sys.path.insert(0, os.path.join(_ROOT, "webui"))

# --board has to be resolved before the first import below, not in main(): host/synth.py
# binds SR from the selected board at *import* time, and every threshold downstream is in
# samples. argparse runs far too late for that, so the flag is picked out of argv by hand
# and pushed into the environment the board registry reads.
def _preparse_board(argv):
    for i, a in enumerate(argv):
        if a == "--board" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--board="):
            return a.split("=", 1)[1]
    return None

_BOARD = _preparse_board(sys.argv[1:])
if _BOARD:
    os.environ["XLS32_BOARD"] = _BOARD

import harness as H            # noqa: E402
import analysis as A           # noqa: E402
import captions               # noqa: E402
import cases_basic, cases_integration, cases_stress   # noqa: E402
from boards import get_board, names as board_names    # noqa: E402
from synth import SR  # noqa: E402

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

    board = get_board()
    if not args.no_reflash:
        H.reflash(board)
    t = H.open_board(board)
    print(f"[{board.name}] running {len(cases)} tests at {SR} Hz over {board.transport}")
    H.warmup(t)

    # Grade nothing until the board's own clock has been checked. Every rate in the design
    # scales with it, so a wrong one does not fail a test or two -- it detunes the entire
    # instrument and returns 34 confidently-wrong scores. The warmup capture above is what
    # measured it; transports that cannot measure say nothing and are trusted.
    clock = t.clock_note() if hasattr(t, "clock_note") else None
    if clock:
        text, clock_ok = clock
        print(text)
        if not clock_ok:
            t.close()
            sys.exit("ABORT: the audio clock is wrong, so every score below would be too.\n"
                     "  Get the module into the bootloader -- power-cycle and touch the encoder\n"
                     "  within 5 seconds, or long-press it -- then load again. A power cycle on\n"
                     "  its own autoboots the last slot. See boards/tiliqua/board.py.")

    results = []
    gaps = []
    clocks = []
    missing = []
    t0 = time.time()
    for i, tc in enumerate(cases, 1):
        s, res = H.run_case(t, tc)
        if not s:
            s = [0] * int(0.5 * SR)
        # The measured USB frame gap rate. Published in every report even though it now sits
        # at ~0.001%: a report that only prints numbers when they are bad cannot be used to
        # notice when they go bad. The 2.5-5% this was built for turned out not to reproduce
        # (docs/TILIQUA_USB_DROPOUTS.md), which is exactly the failure an always-on
        # measurement would have caught months earlier.
        if getattr(t, "gap_rate", None) is not None:
            gaps.append(t.gap_rate)
        # And the other failure, which `gap_rate` is blind to by construction: frames that never
        # arrived leave no zeros behind, so the capture is simply short and every rate computed
        # from it still reads clean. #9 measured this reaching 89% of a capture with no flag from
        # PortAudio at all, so nothing but this number would say it had happened. Every capture
        # that can measure it is recorded, zeros included, for the same reason the gap rate is:
        # a number that only appears when it is bad cannot be watched.
        if getattr(t, "missing_frames", None) is not None:
            missing.append((tc.id, t.missing_frames))
        if getattr(t, "audio_clock_hz", None) is not None:
            clocks.append(t.audio_clock_hz)
        wav = os.path.join(OUT, "wav", f"{tc.id}.wav")
        H.save_wav(wav, s)
        res.extra["wav"] = wav
        results.append((tc, res))
        print(f"  [{i:2}/{len(cases)}] {res.verdict:4} {res.score:5.1f}  {tc.category:11} {tc.id:22} {res.metric}")
    t.close()
    print(f"captured {len(cases)} tests in {time.time()-t0:.0f}s")
    if gaps:
        print(f"USB frame gaps: mean {100*sum(gaps)/len(gaps):.2f}%, worst {100*max(gaps):.2f}%")
    if missing:
        hit = [m for m in missing if m[1]]
        worst_id, worst_n = max(missing, key=lambda m: m[1])
        print(f"frames that never arrived: {sum(n for _, n in missing)} over "
              f"{len(hit)} of {len(missing)} captures"
              + (f" (worst {worst_n} in {worst_id}) -- the host stalled; "
                 "these are not in the gap rate" if hit else ""))
    if clocks:
        print(f"audio clock: mean {sum(clocks)/len(clocks)/1e6:.3f} MHz over {len(clocks)} captures "
              f"(spread {(max(clocks)-min(clocks))/1e3:.1f} kHz)")

    overall, grade, counts = score_overall(results)
    write_reports(results, overall, grade, counts, args, board, gaps, clocks, missing)

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


def write_reports(results, overall, grade, counts, args, board, gaps=(), clocks=(), missing=()):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    caveat = ""
    if gaps:
        # Say what the number means, but only claim the repair happened when it did. At a
        # correct audio clock the rate is ~0.001%, and a report reading "0.00% -- dropped
        # device-side and interpolated back" invites the reader to distrust one half or the
        # other. The 2.5-5% §1.1 used to quote has since been withdrawn outright.
        worst = max(gaps)
        detail = ("interpolated back by the host transport" if worst >= 5e-5
                  else "nothing material to repair")
        caveat = (f"\n_USB frame gaps: mean {100*sum(gaps)/len(gaps):.3f}%, "
                  f"worst {100*worst:.3f}% — {detail}; see ARCHITECTURE_tiliqua.md._\n")
    if missing:
        # The other loss, and the one no rate can see: a frame that never arrived leaves no
        # zeros behind, so the capture is simply short and reads clean. Only the board's own
        # frame counter witnesses it (host/transport/usbaudio.py, `_measure_clock`), and
        # boards/tiliqua/probe/probe_discard.py measured PortAudio raising no flag at all
        # while 89% of a capture went missing. So it is stated on every report, at zero too.
        lost = sum(n for _, n in missing)
        hit = [m for m in missing if m[1]]
        detail = (f"over {len(hit)} of {len(missing)} captures — the host stalled; not in the "
                  "gap rate above" if hit else f"across {len(missing)} captures")
        caveat += f"\n_Frames that never arrived: {lost} {detail}._\n"
    if clocks:
        # The clock the run was graded at, not just the clock it was supposed to be graded
        # at. Pitch is measured in cents against a fixed reference, so a report that does not
        # record this cannot be re-read later: the same numbers mean different things.
        caveat += (f"\n_Audio clock: {sum(clocks)/len(clocks)/1e6:.3f} MHz measured at the "
                   f"board, over {len(clocks)} captures._\n")
    md = [f"# XLS-5 synth — e2e test report",
          f"",
          f"_{now} · {len(results)} tests · board: {board.name} ({board.fpga}) "
          f"over {board.transport} at {board.sr} Hz_",
          caveat,]
    md += [
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

    js = {"generated": now, "board": board.name, "sr": board.sr,
          "transport": board.transport,
          "gap_rate_mean": round(sum(gaps) / len(gaps), 5) if gaps else None,
          "gap_rate_worst": round(max(gaps), 5) if gaps else None,
          # None means the transport cannot measure it; 0 means it measured none.
          "missing_frames_total": sum(n for _, n in missing) if missing else None,
          "missing_frames_captures": sum(1 for _, n in missing if n) if missing else None,
          "audio_clock_hz": round(sum(clocks) / len(clocks)) if clocks else None,
          "overall": round(overall, 1), "grade": grade, "counts": counts,
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
    # Already consumed by _preparse_board above; declared here so it shows in --help and
    # so an unknown value is rejected before the board registry raises on it.
    ap.add_argument("--board", choices=board_names(),
                    help="board to grade (default: $XLS32_BOARD, else basys3)")
    ap.add_argument("--no-reflash", action="store_true")
    ap.add_argument("--skip-video", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--only", choices=["basic", "integration", "stress"])
    sys.exit(run(ap.parse_args()))
