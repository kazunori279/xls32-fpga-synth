#!/usr/bin/env python3
"""Write `webui/static/firmware.json` -- when the bitstreams this repo ships were built.

    uv run --no-project python scripts/build_firmware_json.py            # write it
    uv run --no-project python scripts/build_firmware_json.py --check    # exit 1 if stale

Issue #27. The panel's SETTINGS ▸ Firmware block answers two different questions and has to keep
them apart, because the answers routinely differ:

  * **what is flashed** -- the board's own stamp, read off USB. Only the Tiliqua can say, and only
    a bitstream built since #27; see `boards/tiliqua/gateware/build_id.py`.
  * **what this repo ships** -- this file. It is what you would flash if you flashed now, and it is
    the only answer available for the Basys 3 and for any board that predates the stamp.

Every field below is read out of the committed artefact itself, never from a file mtime. A fresh
`git clone` stamps every mtime with the checkout time, so an mtime-derived date would be a
confident lie on any machine but the one that built it. What is committed and therefore honest:

  * **Basys 3** -- Vivado writes a header into `top.bit`: design name, part, date and time. That
    is the build machine's *local* clock with no zone recorded, which is why `tz` is null and the
    panel does not append a Z. It is still the real build instant, and it is inside the file.
  * **Tiliqua** -- the flash archive is a tarball, and a tar entry carries the mtime of the file
    that went in. `top.bit`'s entry is therefore the moment nextpnr finished, independent of when
    the archive was copied into `boards/tiliqua/firmware/`. Tar mtimes are epoch seconds, so this
    one is genuinely UTC.
  * The commit each artefact was built from comes from `scripts/artefact_hashes.json`, which is
    already the repo's record of that and must not be duplicated here. If that file is stale this
    one inherits the staleness, which is the right coupling: #10 is about nothing running
    `check_artefacts.py` automatically, and this file should not paper over it.

`--check` exists so the staleness is visible without a rebuild: the generated content is
deterministic given the artefacts, so a diff means the artefacts moved and the page did not.
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "webui" / "static" / "firmware.json"
HASHES = ROOT / "scripts" / "artefact_hashes.json"

BASYS3_BIT = "boards/basys3/firmware/top.bit"
TILIQUA_TAR = "boards/tiliqua/firmware/xls32-r5.tar.gz"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def bit_header(path):
    """The key/value fields Xilinx writes ahead of the configuration data.

    Format (undocumented by Xilinx, stable for twenty years): a 2-byte big-endian length and 9
    magic bytes, then a run of records, each a single ASCII key byte, a 2-byte big-endian length,
    and that many bytes of NUL-terminated string. 'a' is the design name, 'b' the part, 'c' the
    date, 'd' the time; 'e' introduces the bitstream body and is a 4-byte length instead, which is
    where this stops.
    """

    with open(path, "rb") as f:
        head = f.read(256)
    (n,) = int.from_bytes(head[0:2], "big"),
    i = 2 + n + 2                              # the magic, then the 2-byte length of what follows
    fields = {}
    while i < len(head):
        key = chr(head[i])
        if key == "e":                         # the body's length is 4 bytes, and we want none of it
            break
        length = int.from_bytes(head[i + 1:i + 3], "big")
        fields[key] = head[i + 3:i + 3 + length].rstrip(b"\x00").decode("ascii", "replace")
        i += 3 + length
    return fields


def basys3(commit):
    path = ROOT / BASYS3_BIT
    f = bit_header(path)
    date, time = f.get("c", ""), f.get("d", "")
    # "2026/08/10" + "23:10:36" -> "2026-08-10 23:10", to the minute. The seconds are in the file
    # and are noise: nothing anyone compares two builds by.
    built = f"{date.replace('/', '-')} {time[:5]}" if date and time else None
    # The design record reads "top;UserID=0XFFFFFFFF;Version=2024.2" -- the toolchain version is
    # worth surfacing, the UserID is a default nobody set.
    version = next((p.split("=", 1)[1] for p in f.get("a", "").split(";") if p.startswith("Version=")), None)
    return {
        "board": "Basys 3",
        "artefact": BASYS3_BIT,
        "built": built,
        "tz": None,                            # Vivado records local time and not which local
        "source": "Vivado bitstream header",
        "part": f.get("b"),
        "toolchain": f"Vivado {version}" if version else None,
        "bytes": path.stat().st_size,
        "sha256": sha256(path)[:12],
        "commit": commit,
        # The board cannot be asked. See the note in transport.js's Basys3Transport.
        "reports_over_usb": False,
    }


def tiliqua(commit):
    path = ROOT / TILIQUA_TAR
    built, tag = None, None
    with tarfile.open(path, "r:gz") as tf:
        for m in tf.getmembers():
            if m.name.endswith("top.bit"):
                built = datetime.datetime.fromtimestamp(
                    m.mtime, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
            elif m.name.endswith("manifest.json"):
                tag = json.load(tf.extractfile(m)).get("tag")
    return {
        "board": "Tiliqua",
        "artefact": TILIQUA_TAR,
        "built": built,
        "tz": "UTC",                           # tar mtimes are epoch seconds
        "source": "tar entry mtime of the inner top.bit",
        "part": "LFE5U-25F",
        "toolchain": "yosys + nextpnr-ecp5",
        "manifest_tag": tag,
        "bytes": path.stat().st_size,
        "sha256": sha256(path)[:12],
        "commit": commit,
        # Since #27: iManufacturer carries "<utc>-<commit>". A board flashed with an older archive
        # answers nothing, and the panel says which of the two it is looking at.
        "reports_over_usb": True,
    }


def generate():
    hashes = json.loads(HASHES.read_text())
    boards = [tiliqua(hashes["tiliqua"]["built_from_commit"]),
              basys3(hashes["basys3"]["built_from_commit"])]
    return {
        "_comment": ("What this repo SHIPS, not what is flashed on your board. Generated by "
                     "scripts/build_firmware_json.py; do not edit."),
        "boards": boards,
        # Which commit of the page this file was generated at, so a deploy that fell behind the
        # artefacts is visible from the panel rather than only from `git log`.
        "generated_at_commit": subprocess.run(
            ("git", "-C", str(ROOT), "rev-parse", "--short=7", "HEAD"),
            capture_output=True, text=True).stdout.strip() or "unknown",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if the committed file is not what would be written")
    args = ap.parse_args()

    fresh = generate()
    text = json.dumps(fresh, indent=2) + "\n"
    if not args.check:
        OUT.write_text(text)
        for b in fresh["boards"]:
            print(f"{b['board']:8} {b['built']} {b['tz'] or 'local'}  {b['commit']}  "
                  f"{b['sha256']}")
        print(f"wrote {OUT.relative_to(ROOT)}")
        return 0

    # The generated-at commit moves on every commit and says nothing about the artefacts, so it is
    # not what staleness means here.
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    if old.get("boards") == fresh["boards"]:
        print(f"{OUT.relative_to(ROOT)} is current")
        return 0
    print(f"{OUT.relative_to(ROOT)} is stale -- re-run without --check", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
