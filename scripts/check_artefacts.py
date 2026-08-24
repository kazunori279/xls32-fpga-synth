#!/usr/bin/env python3
"""Check the committed bitstreams against the sources they were built from.

Nothing else in this repo does. The Pages workflow deploys the web UI and never builds
gateware; the test suite needs a board on the desk. So an artefact under `boards/*/firmware/`
can quietly fall behind `core/synth.x`, and the only symptom is that someone downloads it,
flashes it, hears the wrong synth, and goes looking at their cables.

`.github/workflows/artefacts.yml` runs this on every push and PR (#10). It could not, until the
`known_stale` waiver existed: the Basys 3 blob is stale for a reason that is written down and
cannot currently be fixed, so an unconditional check would have been red from its first run and
ignored by its second. The waiver is deliberately narrow -- it names the sources it covers and
lapses if anything outside them changes -- because a blanket skip would hide the drift nobody has
seen yet along with the drift everybody knows about.

This builds nothing and needs no toolchain. It records the SHA-256 of every source that feeds
each artefact and compares them on demand -- cheap enough to run on any commit, and unlike
`git log` it also catches drift from edits you have not committed yet.

The hash is of the source with its comments and docstrings removed (see `normalize`), because a
bitstream does not change when a comment does, and a check that fails on prose is a check people
learn to skip.

    uv run --no-project python scripts/check_artefacts.py             # check; exit 1 if stale
    uv run --no-project python scripts/check_artefacts.py --strict    # ...and on known-stale too
    uv run --no-project python scripts/check_artefacts.py --update    # re-record, after a rebuild
    uv run --no-project python scripts/check_artefacts.py --self-test # the stripping's own check

Run `--update` only when the artefact sitting in the tree was *just built from the tree as it
stands*. The record is a claim about provenance; updating it on a dirty or unrelated tree turns
that claim into a false one, which is worse than having no check at all. It takes an artefact
name for that reason, and skips any whose bytes have not changed since they were recorded --
a bare `--update` after rebuilding one board would otherwise stamp today's commit onto every
other one and drop their waivers.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "scripts" / "artefact_hashes.json"


def pinned_seed(voices):
    """The placer seed `boards/tiliqua/build.sh` pins for this voice count.

    Read out of the script rather than written down again here. It was written down again here
    until M41, and it drifted: the record said the 24-voice archive was seed 3 for two milestones
    after the board started running seed 7. Nothing caught it, because both the record and the
    expectation came from the same stale literal and so agreed with each other.
    """
    case = re.search(r"case \"\$VOICES\" in(.*?)esac", (ROOT / "boards/tiliqua/build.sh").read_text(),
                     re.S)
    pins = dict(re.findall(r"(\d+|\*)\)\s*SEED=\"\$\{SEED:-(\d+)\}\"", case.group(1) if case else ""))
    seed = pins.get(str(voices), pins.get("*"))
    if seed is None:
        raise SystemExit(f"no seed pin for VOICES={voices} in boards/tiliqua/build.sh")
    return seed

# Bumped whenever `normalize` changes, so old records are reported as needing a re-record instead
# of showing up as every source having changed at once.
SCHEME = "normalized-v1"

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
    # Two Tiliqua bitstreams since M36: 24 voices is the formal build (slot 7) and 32 is kept as
    # experimental (slot 6). See boards/tiliqua/firmware/README.md.
    #
    # They are built from nearly the same committed sources, and the difference between them is
    # not in the tree at all: the voice count is rewritten into a throwaway copy of core/synth.x by
    # voices_variant.py at build time. So `voices` is a build param here, exactly like STAGES. Left
    # out, the two records would differ only by their artefact hash, and this script would report a
    # 24-voice archive as matching a 32-voice source set -- while --self-test still printed 0 blind,
    # because no line of any tracked file would move either one. `SEED` is here for a weaker version
    # of the same reason: it does not change the netlist, but it decides which bitstream comes out
    # of it, and at this occupancy the two seeds are not interchangeable.
    **{
        f"tiliqua-{voices}": {
            "artefact": f"boards/tiliqua/firmware/xls{voices}-r5.tar.gz",
            "doc": "boards/tiliqua/firmware/README.md",
            "params": {"STAGES": "12", "WCT": "12", "hw_rev": "5",
                       "voices": str(voices), "SEED": pinned_seed(voices)},
            # Half of this bitstream is the vendor's: luna, luna-soc and the tiliqua gateware,
            # linked in from $TILIQUA_SDK. See sdk_state() for why it is a commit and not hashes.
            # The Basys 3 has no equivalent -- Vivado reads only the files listed under it.
            "sdk": True,
            # What boards/tiliqua/build.sh runs and what gateware/top.py imports. fx_model.py is
            # the NumPy reference, test_*.py are the Amaranth/Verilator harnesses, and
            # sim_xls_core.cpp is simulation-only -- none of them reach the bitstream. Neither
            # does core/gen_lut.py, which no build script calls.
            #
            # The Tiliqua SDK checkout ($TILIQUA_SDK) that build.sh builds against is not in this
            # list and cannot be -- it lives outside this repo. It is covered separately, by
            # commit rather than by hash; see the "sdk" flag below and sdk_state().
            "sources": [
                "core/synth.x",
                # Only for the reduced-voice build, because only that one runs it: at VOICES=32
                # build.sh hands core/synth.x straight to codegen and never calls the generator.
                # "sources that actually reach the bitstream, nothing more" -- listing it for 32
                # would mean an edit to a script that build could not have executed reporting the
                # 32-voice archive stale.
                *(["boards/tiliqua/spike/voices_variant.py"] if voices != 32 else []),
                "core/codegen.sh",
                "core/fix_verilog.py",
                "boards/tiliqua/build.sh",
                "boards/tiliqua/gateware/top.py",
                "boards/tiliqua/gateware/xls_core.py",
                "boards/tiliqua/gateware/usb_iface.py",
                # Reaches the bitstream through iManufacturer -- one string descriptor, no logic.
                # Its *output* changes on every build by design (it is a timestamp), which this
                # check does not see and should not: the record is about whether the sources
                # drifted, and a bitstream that differs only in the minute it names has not.
                "boards/tiliqua/gateware/build_id.py",
                "boards/tiliqua/gateware/midi_filter.py",
                "boards/tiliqua/gateware/midi_arb.py",
                "boards/tiliqua/gateware/fx.py",
                "boards/tiliqua/gateware/dc_block.py",
                "boards/tiliqua/gateware/viz.py",
                # One line of arithmetic and three constants, but they are the constants that
                # decide how many tiles get drawn and what the bootloader's slot list says. Both
                # of the files above import it (M37, #40/#43), so an edit here reaches the
                # bitstream without touching either of them.
                "boards/tiliqua/gateware/voices.py",
            ],
        }
        for voices in (24, 32)
    },
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_python(data):
    """Python tokens, minus comments and docstrings.

    Through the real lexer, not a regex: a `#` inside a string has to survive, and `tokenize` is
    the only thing that reliably knows which is which. INDENT and DEDENT are kept as structure but
    not as the whitespace that expresses them, so a re-indent is not a rebuild. A bare string
    opening a logical line is a docstring -- prose, and prose does not reach a bitstream.
    """
    parts, fresh = [], True
    for tok in tokenize.generate_tokens(io.StringIO(data.decode()).readline):
        name, s = tokenize.tok_name[tok.type], tok.string
        if name in ("COMMENT", "NL"):
            continue
        if name == "STRING" and fresh:
            fresh = False
            continue
        parts.append(name if name in ("INDENT", "DEDENT") else f"{name}:{s}")
        fresh = name in ("NEWLINE", "INDENT", "DEDENT")
    return "\n".join(parts).encode()


def _strip_cstyle(data):
    """Verilog / DSLX minus `//` and `/* */`, with each line's whitespace collapsed.

    A scanner rather than a regex, for the same reason as above: `//` inside a string literal is
    not a comment. Newlines are kept -- collapsing them too would let genuinely different files
    normalise together, and a false clean is the one failure this script must not have.
    """
    s = data.decode()
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            out.append(s[i:j+1]); i = j + 1
        elif s.startswith("//", i):
            j = s.find("\n", i)
            if j < 0:
                break
            i = j                                  # leave the newline; only the comment goes
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            out.append(" "); i = n if j < 0 else j + 2
        else:
            out.append(s[i]); i += 1
    lines = ("".join(out)).split("\n")
    return "\n".join(" ".join(ln.split()) for ln in lines if ln.strip()).encode()


def normalize(path, data):
    """Drop what cannot reach the bitstream, so the check does not cry stale over a comment.

    That was not hypothetical: `afda87e` corrected a wrong comment in
    `boards/tiliqua/gateware/top.py` -- eighteen lines of prose about which clock the tee FIFO
    drops against -- and this script called the Tiliqua archive stale and told the reader it
    "will be flashed and will misbehave". It will not. A checker that fails on documentation
    teaches people to ignore it, which costs more than the check was ever worth.

    Only languages with a lexer simple enough to scan exactly are normalised. `.sh`, `.tcl` and
    `.xdc` keep their raw bytes: `#` there can sit inside a string, and mistaking one for a
    comment would drop real content and report a stale artefact as clean.
    """
    suffix = pathlib.Path(path).suffix
    try:
        if suffix == ".py":
            return _strip_python(data)
        if suffix in (".v", ".sv", ".x"):
            return _strip_cstyle(data)
    except (SyntaxError, UnicodeDecodeError, tokenize.TokenError):
        return data                                # unparseable: hash it as it lies
    return data


def source_sha256(path, data=None):
    """SHA-256 of a source after `normalize`. The artefact itself is still hashed raw."""
    data = (ROOT / path).read_bytes() if data is None else data
    return hashlib.sha256(normalize(path, data)).hexdigest()


def _prose_lines(src, text):
    """Line numbers holding nothing but comment or docstring, read a second and separate way.

    Deliberately not `normalize`: this is what `--self-test` checks `normalize` against, and a
    check that shares its subject's code checks nothing.
    """
    if src.endswith(".py"):
        code, fresh = set(), True
        for t in tokenize.generate_tokens(io.StringIO(text).readline):
            n = tokenize.tok_name[t.type]
            if n in ("COMMENT", "NL", "NEWLINE", "INDENT", "DEDENT", "ENDMARKER"):
                fresh = fresh or n == "NEWLINE"
                continue
            if not (n == "STRING" and fresh):
                code.update(range(t.start[0], t.end[0] + 1))
            fresh = False
        return {i for i in range(1, text.count("\n") + 2) if i not in code}
    out, inblk = set(), False
    for i, ln in enumerate(text.split("\n"), 1):
        t, was = ln.strip(), inblk
        if "/*" in t and "*/" not in t.split("/*", 1)[1]:
            inblk = True
        if was and "*/" in t:
            inblk = False
            if not t.split("*/", 1)[1].strip():
                out.add(i)
            continue
        if was or not t or t.startswith("//"):
            out.add(i)
    return out


def self_test():
    """Delete every line of every tracked source, one at a time, and see if the hash notices.

    Dropping comments buys a check nobody ignores, but it buys it by making the hash blind to
    something -- and the failure that matters here is the blind spot growing past its brief and
    swallowing a real edit. A false stale wastes an afternoon; a false clean ships a bitstream
    that does not match its sources and says so in writing. So: every line that carries code must
    change the hash when it goes, and this asserts it over the actual tree rather than a fixture.
    """
    bad = tested = 0
    for src in sorted({s for spec in ARTEFACTS.values() for s in spec["sources"]}):
        if not src.endswith((".py", ".v", ".sv", ".x")):
            print(f"{'raw':>6}  {src}")
            continue
        data = (ROOT / src).read_bytes()
        base, lines = source_sha256(src, data), data.split(b"\n")
        prose = _prose_lines(src, data.decode())
        blind = [i + 1 for i, ln in enumerate(lines) if ln.strip()
                 and source_sha256(src, b"\n".join(lines[:i] + lines[i + 1:])) == base]
        code = [n for n in blind if n not in prose]
        tested += sum(1 for ln in lines if ln.strip())
        bad += len(code)
        print(f"{'ok' if not code else 'FAIL':>6}  {src:44} {len(blind):4} deletions unseen, "
              f"{len(code)} carrying code")
        for n in code[:5]:
            print(f"          line {n}: {lines[n - 1].decode(errors='replace')[:78]}")
    print(f"\n{tested} lines deleted one at a time; {bad} carrying code went unnoticed")
    return 1 if bad else 0


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
    bootloader manifest already uses for its `tag` field.

    The files `--update` is itself about to write do not count as dirt. This is the only caller,
    and at the moment it runs, the artefact just copied in and this script's own state file are
    both necessarily uncommitted -- so counting them made the '-' fire on every correct run, which
    is a marker that means nothing. What it is for is the *other* case: recording an artefact
    against sources that have been edited since it was built.
    """
    h = git("rev-parse", "--short", "HEAD") or "unknown"
    expected = {STATE.relative_to(ROOT).as_posix()}
    expected |= {spec["artefact"] for spec in ARTEFACTS.values()}
    # Lines are 'XY path', or 'R  old -> new' for a rename. Not sliced at a fixed offset: `git()`
    # strips the whole output, which eats the leading space of the *first* line only, so ln[3:]
    # silently ate a character of one path and nothing else -- the kind of off-by-one that reads
    # as a mystery rather than a bug. Split on the flags instead.
    dirt = set()
    for ln in (git("status", "--porcelain") or "").splitlines():
        parts = ln.strip().split(maxsplit=1)
        if len(parts) == 2:
            dirt.add(parts[1].split(" -> ")[-1].strip('"'))
    return h + ("-" if dirt - expected else "")


def sdk_state():
    """The Tiliqua SDK checkout's commit, or None if it is not on this machine.

    The other half of a Tiliqua bitstream comes from `$TILIQUA_SDK` -- luna, luna-soc and the
    vendor's own gateware -- and it is not in this repo, so `sources` cannot reach it. Hashing it
    file-by-file would not travel anyway: CI has no checkout, and a hash set that only one machine
    can compute is not a record, it is a local opinion. A commit is the thing both ends can agree
    on, so that is what gets written down. It reaches further than it looks, too: luna and luna-soc
    are pinned by the `pdm.lock` inside that same checkout, so the one sha covers all three.

    Returns `{"commit": <short sha, '-' suffixed if dirty>, "path": <where>}`. `build.sh` treats
    the checkout as read-only, so a dirty one means someone was editing the vendor's tree, and
    that belongs in the record more than almost anything else here.
    """
    path = os.environ.get("TILIQUA_SDK") or os.path.expanduser(
        "~/Documents/GitHub/tiliqua/gateware")
    # `$TILIQUA_SDK` points at `<repo>/gateware` by convention (build.sh:56), but walk up for the
    # repo root rather than assuming one level: the variable is the user's to set, and a wrong
    # guess here would silently report "no checkout" on a machine that plainly has one.
    if not os.path.isdir(path):
        return None
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path,
                          capture_output=True, text=True)
    if root.returncode != 0 or not root.stdout.strip():
        return None
    root = root.stdout.strip()
    def g(*a):
        out = subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else None
    h = g("rev-parse", "--short", "HEAD")
    if not h:
        return None
    return {"commit": h + ("-" if g("status", "--porcelain") else ""), "path": root}


def sdk_lines(record):
    """How the recorded SDK compares with the one on this machine, as (stale?, lines).

    Three outcomes, and only one of them is a failure:

    * recorded and matching, or recorded and unverifiable here -- nothing to say, or a note.
    * recorded and different -- the bitstream was built against another vendor tree. Stale.
    * never recorded -- every archive built before this check existed. Informational, because
      calling those stale would be inventing a fact: nobody wrote down what they were built
      against, and that is not the same as knowing they drifted.

    Deliberately not waivable. `known_stale` names *sources*, and an SDK bump is exactly the kind
    of drift a source-level waiver should not quietly cover.
    """
    was = record.get("sdk")
    now = sdk_state()
    if not was:
        return False, ["the SDK checkout was not recorded when this was built -- an SDK bump "
                       "between then and now would not be visible here (recorded from the next "
                       "build onwards)"]
    if not now:
        return False, [f"SDK recorded at {was['commit']}; no checkout on this machine, so it "
                       "could not be verified"]
    if now["commit"] != was["commit"]:
        return True, [f"SDK checkout changed since the build: {was['commit']} -> {now['commit']}"]
    return False, []


def _at(commit, src):
    """`src` as of `commit`, normalised. None if it was not there."""
    try:
        out = subprocess.run(["git", "show", f"{commit}:{src}"], cwd=ROOT,
                             capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return hashlib.sha256(normalize(src, out.stdout)).hexdigest()


def changed_since(commit, sources):
    """Commits after `commit` that changed any of `sources` in a way the hash can see.

    Explains a stale verdict in the terms the author will recognise, and is the only signal
    available when no hash was ever recorded. Commits that only moved comments are counted, not
    listed: naming one as the cause of a stale verdict sends the reader to a diff that cannot
    have caused it, which is how the old whole-file check misled in the first place.
    """
    if not commit:
        return []
    log = git("log", "--oneline", f"{commit.rstrip('-')}..HEAD", "--", *sources)
    if not log:
        return []
    lines, prose = [], 0
    for entry in log.split("\n"):
        sha = entry.split(" ", 1)[0]
        touched = (git("show", "--pretty=", "--name-only", sha) or "").split("\n")
        hit = [s for s in sources if s in touched]
        if any(_at(sha, s) != _at(f"{sha}~1", s) for s in hit) or not hit:
            lines.append(entry)
        else:
            prose += 1
    if prose:
        lines.append(f"({prose} further commit{'s' * (prose > 1)} touched these sources, "
                     "but only their comments)")
    return lines


def measure(spec):
    return {src: source_sha256(src) for src in spec["sources"]}


def waived(record, stale_sources):
    """Is this exact staleness already known and accepted? Returns the reason, or None.

    A blanket "ignore this artefact" flag would be worse than no automation at all: the Basys 3
    bitstream is stale for a reason we have written down and cannot currently fix (#41 -- Vivado
    needs an x86 machine and a licence), but it could *also* go stale for a reason nobody has seen,
    and a flag that hides the first hides the second too. So the waiver names the sources it
    covers, and only holds while the stale set stays inside them. One new changed file and the
    waiver stops applying, which is the whole point of it.
    """
    known = record.get("known_stale")
    if not known:
        return None
    covered = set(known.get("sources", ()))
    return known.get("reason", "known stale") if set(stale_sources) <= covered else None


def check(name, spec, record):
    """Return (status, lines). status is one of ok / stale / known-stale / unrecorded / missing."""
    artefact = ROOT / spec["artefact"]
    if not artefact.exists():
        return "missing", [f"{spec['artefact']} is not in the tree"]

    now = measure(spec)
    lines = []
    sdk_stale, sdk_notes = (False, []) if not spec.get("sdk") else sdk_lines(record or {})

    # A record written under an older `normalize` would show every source as changed at once,
    # which reads as catastrophe and means only that the recipe moved. Say so instead.
    if record is not None and record.get("sources") is not None:
        if record.get("hash_scheme") != SCHEME:
            got = record.get("hash_scheme", "raw bytes (pre-2026-08-10)")
            return "unrecorded", [
                f"recorded under hash scheme {got}, this script computes {SCHEME}",
                "the sources cannot be compared across schemes; re-record from the recorded "
                "commit, or --update if the artefact is a fresh build of this tree",
            ]

    if record is None or record.get("sources") is None:
        built = record.get("built_from_commit") if record else None
        lines.append("no source hashes have ever been recorded for this artefact")
        if record and record.get("note"):
            lines.append(record["note"])
        for c in changed_since(built, spec["sources"]):
            lines.append(f"  {c}")
        return "unrecorded", lines

    was = record["sources"]
    stale_sources = []
    for src in sorted(set(was) | set(now)):
        if src not in was:
            lines.append(f"added since the build:   {src}")
            stale_sources.append(src)
        elif src not in now:
            lines.append(f"dropped since the build: {src}")
            stale_sources.append(src)
        elif was[src] != now[src]:
            lines.append(f"changed since the build: {src}")
            stale_sources.append(src)

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
        # Only source drift can be waived. A params change or a swapped artefact returns above,
        # before this point, because neither is the situation a waiver was written to describe.
        reason = waived(record, stale_sources)
        if reason and not sdk_stale:
            lines.append(f"accepted: {reason}")
            return "known-stale", lines + sdk_notes
        return "stale", lines + sdk_notes

    built = record.get("built_from_commit", "?")
    on = record.get("built_on", "?")
    head = f"matches the recorded build {built} ({on}), {len(now)} sources"
    if sdk_stale:
        return "stale", [f"the repo sources match the recorded build {built} ({on})"] + sdk_notes
    return "ok", [head] + sdk_notes


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--update", action="store_true", help="re-record, after a rebuild")
    ap.add_argument("--force", action="store_true",
                    help="with --update, record even artefacts whose bytes have not changed")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the comment-stripping is not blind to real edits")
    ap.add_argument("--strict", action="store_true",
                    help="fail on known-stale artefacts too (they pass by default, so this can "
                         "run in CI without being permanently red)")
    # A prefix selects a family, so `tiliqua` still means "the Tiliqua ones" now that there are
    # two of them, and every call site that predates the split keeps working.
    ap.add_argument("only", nargs="?", metavar="ARTEFACT",
                    help=f"just this one, or a prefix ({', '.join(sorted(ARTEFACTS))})")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    names = list(ARTEFACTS)
    if args.only:
        names = [n for n in names if n == args.only or n.startswith(args.only + "-")]
        if not names:
            ap.error(f"unknown artefact {args.only!r}; known: {', '.join(sorted(ARTEFACTS))}")

    if args.update:
        for name in names:
            spec = ARTEFACTS[name]
            digest = sha256(ROOT / spec["artefact"])
            # An artefact whose bytes have not moved was not rebuilt, whatever the sources did.
            # Recording it anyway is the exact failure the docstring above warns about, and a bare
            # `--update` after rebuilding one board hits every other one: it stamps today's commit
            # onto a bitstream built weeks ago and drops any `known_stale` waiver explaining why it
            # is behind. Skipping is the safe default because a real rebuild always changes the
            # bytes -- the build stamp alone guarantees it. --force is for re-recording sources
            # against an artefact you know is current.
            if not args.force and state.get(name, {}).get("artefact_sha256") == digest:
                print(f"skipped {name}: unchanged since it was recorded at "
                      f"{state[name].get('built_from_commit', '?')}, so it was not rebuilt "
                      "(--force to record anyway)")
                continue
            state[name] = {
                "artefact": spec["artefact"],
                "artefact_sha256": digest,
                "built_from_commit": head_commit(),
                "built_on": datetime.date.today().isoformat(),
                "hash_scheme": SCHEME,
                "params": spec["params"],
                "sources": measure(spec),
            }
            sdk = sdk_state() if spec.get("sdk") else None
            if sdk:
                state[name]["sdk"] = sdk
            print(f"recorded {name} at {state[name]['built_from_commit']}"
                  + (f", SDK {sdk['commit']}" if sdk else ""))
            if spec.get("sdk") and not sdk:
                # Silence here would write a record that looks complete and is not. --update is a
                # provenance claim; the one thing it must never do is quietly omit half of it.
                print(f"  warning: no SDK checkout found (set $TILIQUA_SDK), so {name} is being "
                      "recorded without one -- an SDK bump will not be detected for this build")
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        print(f"wrote {STATE.relative_to(ROOT)}")
        return 0

    seen = set()
    for name in names:
        spec = ARTEFACTS[name]
        status, lines = check(name, spec, state.get(name))
        seen.add(status)
        print(f"{name:11} {spec['artefact']}")
        for i, line in enumerate(lines):
            head = f"  {status:11} " if i == 0 else " " * 14
            # Indented lines are the git log; leave them alone. Prose gets wrapped, because
            # the one thing worse than no explanation is one that runs off the terminal.
            if line.startswith("  "):
                print(head + line)
            else:
                print(textwrap.fill(line, 92, initial_indent=head, subsequent_indent=" " * 14))
        print()

    # Only a stale verdict means the bitstream is wrong. An unrecorded one means the record is,
    # and telling someone to rebuild an FPGA image because a JSON field is missing is the kind of
    # advice that gets a tool uninstalled.
    if "stale" in seen or "missing" in seen:
        print("A stale artefact is one that will be flashed and will misbehave. Rebuild it")
        print("(README §3), then re-record with --update.")
    if "known-stale" in seen and not args.strict:
        print("known-stale artefacts are stale for a reason already written down; the waiver")
        print("names the sources it covers and lapses if anything else changes. --strict fails")
        print("on them anyway.")

    # `known-stale` passes by default so this can run unattended -- see #10. It is the difference
    # between a check that runs on every push and one that is permanently red and therefore
    # ignored, which is the failure mode that let the Basys 3 bitstream sit stale for four months.
    ok = {"ok"} if args.strict else {"ok", "known-stale"}
    return 0 if seen <= ok else 1


if __name__ == "__main__":
    sys.exit(main())
