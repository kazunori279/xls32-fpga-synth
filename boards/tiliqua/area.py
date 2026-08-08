#!/usr/bin/env python3
"""Per-block area census of a Tiliqua build, read out of yosys' ``top.json``.

nextpnr prints one number for the whole device. That is the number that decides whether a
bitstream places at all, but when it says 102% -- as it did in M28 -- it says nothing about
*where* to look, and the answer to that question has twice now decided the shape of a milestone
(M28 split the design into two slots on the strength of ``core`` being 70.5% on its own).

    uv run boards/tiliqua/area.py                       # the cv variant
    uv run boards/tiliqua/area.py --variant fx
    uv run boards/tiliqua/area.py --top 5 --path build/tiliqua/build/xls32cv-r5/top.json

**What is being counted, and why it is a proxy.** ``top.json`` is yosys' output, so it predates
packing: there are no ``TRELLIS_COMB`` cells in it at all, only the ``LUT4`` / ``CCU2C`` /
``PFUMX`` / ``L6MUX21`` primitives nextpnr will later pack into slices. One ``CCU2C`` is two
carry halves and so two ``TRELLIS_COMB``; the two muxes usually fold into a slice that was going
to exist anyway, which is why this total runs ~1% over nextpnr's figure. Use nextpnr's total for
"does it fit" and this for "what is it spent on".

Hierarchy survives flattening in the cell *names* (``core.xls_engine...``), which is the whole
reason this works -- but not universally: small blocks are sometimes hoisted to top level with
their prefix dropped, so a block that reads as ~0 here has been absorbed, not removed. The
unattributed remainder is printed rather than hidden for exactly that reason.
"""

import argparse
import collections
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# What nextpnr will pack into TRELLIS_COMB, and how many each one costs.
COMB_WEIGHT = {"LUT4": 1, "CCU2C": 2, "PFUMX": 1, "L6MUX21": 1}

CELL_KEY = re.compile(r'^\s+"(.*)": \{\s*$')
CELL_TYPE = re.compile(r'^\s+"type": "([^"]+)"')


def census(path):
    """Stream the netlist and total each primitive against the block that owns it.

    Line-by-line rather than ``json.load`` because the file is 50 MB of mostly bit-vectors and
    none of it is needed: a cell is a key at some indent followed within a few lines by its type,
    and the two regexes above are the entire grammar this cares about.
    """
    comb = collections.Counter()
    ff = collections.Counter()
    other = collections.Counter()
    owner = ""

    with open(path) as f:
        for line in f:
            m = CELL_KEY.match(line)
            if m:
                owner = m.group(1).lstrip("\\").split(".")[0]
                continue
            m = CELL_TYPE.match(line)
            if not m:
                continue
            kind = m.group(1)
            if kind in COMB_WEIGHT:
                comb[owner] += COMB_WEIGHT[kind]
            elif kind == "TRELLIS_FF":
                ff[owner] += 1
            elif not kind.startswith("$"):
                other[kind] += 1

    return comb, ff, other


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default=os.environ.get("XLS32_VARIANT", "cv"),
                    help="which bitstream to read (default: $XLS32_VARIANT, else cv)")
    ap.add_argument("--path", help="an explicit top.json, overriding --variant")
    ap.add_argument("--top", type=int, default=12, help="how many blocks to name (default: 12)")
    ap.add_argument("--capacity", type=int, default=24288,
                    help="TRELLIS_COMB on the part (default: 24288, an LFE5U-25F)")
    args = ap.parse_args()

    # build.sh names the fx build directory after NAME=XLS32, not XLS32FX -- fx is the default
    # variant and predates there being a second one.
    subdir = "xls32-r5" if args.variant == "fx" else f"xls32{args.variant}-r5"
    path = Path(args.path) if args.path else (REPO / f"build/tiliqua/build/{subdir}/top.json")
    if not path.exists():
        sys.exit(f"no netlist at {path} -- build the {args.variant} variant first")

    comb, ff, other = census(path)
    total = sum(comb.values())

    print(f"{path.relative_to(REPO) if path.is_relative_to(REPO) else path}\n")
    print(f"{'block':<16}{'~COMB':>9}{'%dev':>8}{'FF':>9}")
    print("-" * 42)
    named = 0
    for block, n in comb.most_common(args.top):
        named += n
        print(f"{block:<16}{n:>9,}{n / args.capacity:>7.1%}{ff[block]:>9,}")
    rest = total - named
    if rest:
        print(f"{'(elsewhere)':<16}{rest:>9,}{rest / args.capacity:>7.1%}"
              f"{sum(ff.values()) - sum(ff[b] for b, _ in comb.most_common(args.top)):>9,}")
    print("-" * 42)
    print(f"{'total':<16}{total:>9,}{total / args.capacity:>7.1%}{sum(ff.values()):>9,}")
    print("\nhard blocks: " + ", ".join(f"{k} {v}" for k, v in sorted(other.items())))
    print("\nThis is a pre-pack estimate; nextpnr's own figure in top.tim is the one that decides "
          "whether the\nbitstream places, and runs about a percent under.")


if __name__ == "__main__":
    main()
