#!/usr/bin/env python3
"""Emit a reduced-voice-count copy of core/synth.x for the M21 spike.

The fallback ladder in TILIQUA_PORT.md §2.3 (32 -> 24 -> 16 voices) is only worth anything if
each rung has a number attached. But `core/synth.x` is the *shipping* Basys 3 gateware, and the
voice count is written into it as bare literals rather than a constant, so there is nothing to
override from the outside. Rather than parameterise production DSLX for a measurement, this
rewrites a throwaway copy.

Every site is enumerated below and the count of rewrites is asserted, so a future edit to
synth.x that moves one of them fails loudly instead of silently producing a 32-voice build
wearing a 16-voice filename.

Voices only. PARTS is deliberately not handled: `Part[4]:[...]` at the end of the proc lists
four struct literals by hand, which no regex should be pulling apart.
"""
import argparse, re, sys

ap = argparse.ArgumentParser()
ap.add_argument("--voices", type=int, required=True)
ap.add_argument("--src", default="core/synth.x")
ap.add_argument("--out", required=True)
a = ap.parse_args()

N = a.voices
if N < 2 or N > 32:
    sys.exit(f"voices must be 2..32, got {N}")
IDX_W = max(1, (N - 1).bit_length())     # bits needed to hold 0..N-1; 32 voices -> u5

src = open(a.src).read()

# (pattern, replacement, expected number of matches)
RULES = [
    # 1 struct field + 3 fns x (param, return) + 3 loop accumulators = 10.
    (r"Voice\[32\]",           f"Voice[{N}]",             10),
    # Literals before the bare type: `\bu5\b` matches the `u5` inside `u5:31` too, so rewriting
    # the type first would leave the index constants behind and the count check would not notice.
    # Literals before the bare type: `\bu5\b` matches the `u5` inside `u5:31` too. And the
    # replacement is written to a marker rather than straight to `u{IDX_W}`, because at 17..32
    # voices IDX_W is still 5 -- substituting in place would hand the next rule its own output
    # to match, and the count check would fire on a file that was already correct.
    (r"\bu5:31\b",             f"@IDX@:{N - 1}",           1),   # last-voice-of-ring compare
    (r"\bu5:0\b",              "@IDX@:0",                  1),   # ring wrap
    (r"\bu5:1\b",              "@IDX@:1",                  1),   # ring step
    (r"\bu5\b",                "@IDX@",                    1),   # vidx: u5
    (r"u32:0\.\.u32:32",       f"u32:0..u32:{N}",          2),   # apply_on / apply_off scans
    (r"u32:0\.\.u32:31",       f"u32:0..u32:{N - 1}",      1),   # rotate_in shift
]

for pat, rep, want in RULES:
    src, n = re.subn(pat, rep, src)
    if n != want:
        sys.exit(f"{a.src}: expected {want} match(es) for /{pat}/, found {n} -- "
                 "the voice count has moved; update voices_variant.py")

src = src.replace("@IDX@", f"u{IDX_W}")
assert "@IDX@" not in src

open(a.out, "w").write(src)
print(f"{a.out}: {N} voices, vidx u{IDX_W}")
