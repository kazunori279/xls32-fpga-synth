#!/usr/bin/env python3
"""Pull one row of the M21 table out of a nextpnr-ecp5 run.

Utilisation comes from --report JSON; Fmax only exists in the log, so that gets parsed too.
A failed run still prints a row -- "did not fit" and "fit but missed timing" are both results,
and silently dropping them would make the sweep look better than it is.
"""
import argparse, json, os, re, sys

CELLS = ("TRELLIS_COMB", "TRELLIS_FF", "DP16KD", "MULT18X18D")

ap = argparse.ArgumentParser()
ap.add_argument("--stages", type=int, required=True)
ap.add_argument("--wct", type=int, required=True)
ap.add_argument("--freq", type=float, required=True)
ap.add_argument("--report", required=True)
ap.add_argument("--log", required=True)
ap.add_argument("--yosys-log", help="fallback counts when PnR never got far enough to report")
ap.add_argument("--rc", type=int, default=0)
ap.add_argument("--tsv", action="store_true", help="print a bare TSV row instead of JSON")
a = ap.parse_args()

used = dict.fromkeys(CELLS, 0)
avail = dict.fromkeys(CELLS, 0)
if os.path.exists(a.report):
    with open(a.report) as f:
        rep = json.load(f)
    for name, d in (rep.get("utilization") or {}).items():
        if name in used:
            used[name] = d.get("used", 0)
            avail[name] = d.get("available", 0)

counts_from = "nextpnr"
if not any(used.values()) and a.yosys_log and os.path.exists(a.yosys_log):
    # A run that never placed leaves no report, and a row of zeros would read as "costs nothing"
    # -- the opposite of the truth. Fall back to the yosys cell count so the row still says why
    # it did not fit. TRELLIS_FF and MULT18X18D pass through packing unchanged; the comb figure
    # is the pre-pack LUT4 count, which nextpnr would pack *down* a few percent, so it is an
    # upper bound and labelled as a different key.
    ystat = open(a.yosys_log, errors="replace").read()
    block = re.split(r"=== design hierarchy ===", ystat)[0]
    for cell, key in (("TRELLIS_FF", "TRELLIS_FF"), ("MULT18X18D", "MULT18X18D"),
                      ("DP16KD", "DP16KD"), ("LUT4", "TRELLIS_COMB")):
        m = None
        for m in re.finditer(rf"^\s*(\d+)\s+{cell}\s*$", block, re.M):
            pass
        if m:
            used[key] = int(m.group(1))
    avail = {"TRELLIS_COMB": 24288, "TRELLIS_FF": 24288, "DP16KD": 56, "MULT18X18D": 28}
    counts_from = "yosys (pre-pack)"

log = open(a.log, errors="replace").read() if os.path.exists(a.log) else ""

# "Info: Max frequency for clock '$glbnet$clk': 66.49 MHz (PASS at 60.00 MHz)"
# Routing runs the analysis more than once; the last one is the post-route truth.
fmax, verdict = None, None
for m in re.finditer(r"Max frequency for clock\s+'([^']+)':\s+([\d.]+)\s*MHz\s+\((PASS|FAIL)", log):
    fmax, verdict = float(m.group(2)), m.group(3)

if a.rc != 0 and verdict is None:
    # Distinguish "no room on the die" from any other tool failure -- they mean different things
    # for the decision gate.
    # The exact wording is "Unable to find legal placement for all cells, design is probably at
    # utilisation limit." -- and under yowasp that ERROR then surfaces as a wasm trap, so the
    # traceback at the end of the log says nothing useful. Match the sentence, not the trap.
    verdict = "NOFIT" if re.search(
        r"(unable to (find legal )?place|failed to place|not enough|utilisation limit|"
        r"unable to route)", log, re.I) else "ERROR"

row = {
    "stages": a.stages, "wct": a.wct, "target_mhz": a.freq,
    "fmax_mhz": fmax, "verdict": verdict or ("PASS" if a.rc == 0 else "ERROR"),
    "rc": a.rc, "counts_from": counts_from,
    **{c.lower(): used[c] for c in CELLS},
    **{c.lower() + "_avail": avail[c] for c in CELLS},
}

if a.tsv:
    print("\t".join(str(row[k]) for k in
                    ("stages", "wct", "target_mhz", "trellis_comb", "trellis_ff",
                     "dp16kd", "mult18x18d", "fmax_mhz", "verdict")))
else:
    print(json.dumps(row))

sys.exit(0)
