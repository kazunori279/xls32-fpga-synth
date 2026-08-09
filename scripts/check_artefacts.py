#!/usr/bin/env python3
"""Check the committed bitstreams against the sources they were built from.

Nothing else in this repo does. The Pages workflow deploys the web UI and never builds
gateware; the test suite needs a board on the desk. So an artefact under `boards/*/firmware/`
can quietly fall behind `core/synth.x`, and the only symptom is that someone downloads it,
flashes it, hears the wrong synth, and goes looking at their cables.

This builds nothing and needs no toolchain. It records the SHA-256 of every source that feeds
each artefact and compares them on demand -- cheap enough to run on any commit, and unlike
`git log` it also catches drift from edits you have not committed yet.

    uv run --no-project python scripts/check_artefacts.py            # check; exit 1 if stale
    uv run --no-project python scripts/check_artefacts.py --update   # re-record, after a rebuild

Run `--update` only when the artefact sitting in the tree was *just built from the tree as it
stands*. The record is a claim about provenance; updating it on a dirty or unrelated tree turns
that claim into a false one, which is worse than having no check at all.
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "scripts" / "artefact_hashes.json"

# Each artefact names the sources that actually reach the bitstream -- nothing more. A source
# set that is too wide is as bad as one that is too narrow: it cries stale over a comment in a
# test harness, and then nobody reads the output.
ARTEFACTS = {
    "basys3": {
        "artefact": "boards/basys3/firmware/top.bit",
        "doc": "boards/basys3/firmware/top.bit.md",
        # The shipped build is Vivado at STAGES=48. These are exactly the files
        # boards/basys3/scripts/remote_build.sh uploads for that backend. It also uploads
        # basys3_nextpnr.xdc, vmbuild.sh and vmbuild_nextpnr.sh, but only the F4PGA and
        # openXC7 backends read those, so they stay out.
        "params": {"STAGES": "48", "WCT": "48", "backend": "vivado"},
        "sources": [
            "core/synth.x",
            "core/codegen.sh",
            "core/fix_verilog.py",
            "boards/basys3/rtl/top.v",
            "boards/basys3/rtl/basys3.xdc",
            "boards/basys3/rtl/build_vivado.tcl",
            "boards/basys3/scripts/vmbuild_vivado.sh",
        ],
    },
    "tiliqua": {
        "artefact": "boards/tiliqua/firmware/xls32-r5.tar.gz",
        "doc": "boards/tiliqua/firmware/README.md",
        "params": {"STAGES": "12", "WCT": "12", "hw_rev": "5"},
        # What boards/tiliqua/build.sh runs and what gateware/top.py imports. fx_model.py is
        # the NumPy reference, test_*.py are the Amaranth/Verilator harnesses, and
        # sim_xls_core.cpp is simulation-only -- none of them reach the bitstream. Neither
        # does core/gen_lut.py, which no build script calls.
        #
        # NOT covered: the Tiliqua SDK checkout ($TILIQUA_SDK) that build.sh builds against.
        # It lives outside this repo and cannot be hashed from here, so an SDK bump will not
        # show up below. That is a real hole; it is just not one this script can close.
        "sources": [
            "core/synth.x",
            "core/codegen.sh",
            "core/fix_verilog.py",
            "boards/tiliqua/build.sh",
            "boards/tiliqua/gateware/top.py",
            "boards/tiliqua/gateware/xls_core.py",
            "boards/tiliqua/gateware/usb_iface.py",
            "boards/tiliqua/gateware/midi_filter.py",
            "boards/tiliqua/gateware/midi_arb.py",
            "boards/tiliqua/gateware/fx.py",
            "boards/tiliqua/gateware/viz.py",
        ],
    },
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args):
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


def head_commit():
    """Short HEAD, with a trailing '-' if the tree is dirty -- the convention the Tiliqua
    bootloader manifest already uses for its `tag` field."""
    h = git("rev-parse", "--short", "HEAD") or "unknown"
    return h + ("-" if git("status", "--porcelain") else "")


def changed_since(commit, sources):
    """Commits after `commit` that touched any of `sources`. Explains a stale verdict in the
    terms the author will recognise, and is the only signal available when no hash was ever
    recorded."""
    if not commit:
        return []
    log = git("log", "--oneline", f"{commit.rstrip('-')}..HEAD", "--", *sources)
    return log.split("\n") if log else []


def measure(spec):
    return {src: sha256(ROOT / src) for src in spec["sources"]}


def check(name, spec, record):
    """Return (status, lines). status is one of ok / stale / unrecorded / missing."""
    artefact = ROOT / spec["artefact"]
    if not artefact.exists():
        return "missing", [f"{spec['artefact']} is not in the tree"]

    now = measure(spec)
    lines = []

    if record is None or record.get("sources") is None:
        built = record.get("built_from_commit") if record else None
        lines.append("no source hashes have ever been recorded for this artefact")
        if record and record.get("note"):
            lines.append(record["note"])
        for c in changed_since(built, spec["sources"]):
            lines.append(f"  {c}")
        return "unrecorded", lines

    was = record["sources"]
    for src in sorted(set(was) | set(now)):
        if src not in was:
            lines.append(f"added since the build:   {src}")
        elif src not in now:
            lines.append(f"dropped since the build: {src}")
        elif was[src] != now[src]:
            lines.append(f"changed since the build: {src}")

    if record.get("params") != spec["params"]:
        lines.append(f"build parameters changed: {record.get('params')} -> {spec['params']}")

    # A different artefact with the same recorded hashes means someone dropped in a new
    # bitstream and never re-recorded. The record is then describing the previous file.
    if record.get("artefact_sha256") != sha256(artefact):
        lines.insert(0, "the artefact itself differs from the one that was recorded")
        lines.append("run --update if this is a fresh build of the current tree")
        return "stale", lines

    if lines:
        for c in changed_since(record.get("built_from_commit"), spec["sources"]):
            lines.append(f"  {c}")
        return "stale", lines

    built = record.get("built_from_commit", "?")
    on = record.get("built_on", "?")
    return "ok", [f"matches the recorded build {built} ({on}), {len(now)} sources"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--update", action="store_true", help="re-record, after a rebuild")
    ap.add_argument("only", nargs="?", choices=sorted(ARTEFACTS), help="just this one")
    args = ap.parse_args()

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    names = [args.only] if args.only else list(ARTEFACTS)

    if args.update:
        for name in names:
            spec = ARTEFACTS[name]
            state[name] = {
                "artefact": spec["artefact"],
                "artefact_sha256": sha256(ROOT / spec["artefact"]),
                "built_from_commit": head_commit(),
                "built_on": datetime.date.today().isoformat(),
                "params": spec["params"],
                "sources": measure(spec),
            }
            print(f"recorded {name} at {state[name]['built_from_commit']}")
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        print(f"wrote {STATE.relative_to(ROOT)}")
        return 0

    worst = 0
    for name in names:
        spec = ARTEFACTS[name]
        status, lines = check(name, spec, state.get(name))
        print(f"{name:9} {spec['artefact']}")
        for i, line in enumerate(lines):
            head = f"  {status:11} " if i == 0 else " " * 14
            # Indented lines are the git log; leave them alone. Prose gets wrapped, because
            # the one thing worse than no explanation is one that runs off the terminal.
            if line.startswith("  "):
                print(head + line)
            else:
                print(textwrap.fill(line, 92, initial_indent=head, subsequent_indent=" " * 14))
        print()
        if status != "ok":
            worst = 1

    if worst:
        print("A stale artefact is one that will be flashed and will misbehave. Rebuild it")
        print("(README §3), then re-record with --update.")
    return worst


if __name__ == "__main__":
    sys.exit(main())
