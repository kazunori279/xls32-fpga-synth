# The USB strings, checked without a board. Issue #27.
#
# Three of the module's descriptor fields carry promises that are kept somewhere else, and none of
# the three is exercised by any other check:
#
#   * `iManufacturer` carries the build stamp, and `webui/static/transport.js` parses it with a
#     regex. Two files have to agree on a format; nothing but this check says they do.
#   * `iProduct` is what the host transport matches on and what the macOS sound picker shows, and
#     `iSerialNumber` is half of the UID CoreAudio remembers the device by. Both must stay put.
#     A one-character edit to either is a silent regression -- the board still enumerates, it is
#     just a different device to every host that had remembered it.
#   * The stamp must stay **fixed-width**. Its length moves the netlist, and the netlist decides
#     whether the hand-picked router seed in build.sh still routes at 97% utilisation. A format
#     change that widened the stamp would not fail here or in synthesis; it would fail an
#     afternoon later, in nextpnr, as a runaway ripup cascade. See build_id.py.
#
# It builds the descriptor collection in-process and reads the bytes back, so it is a few hundred
# milliseconds and needs no hardware. Run it from this directory with the Tiliqua SDK's Python:
#
#     .../tiliqua/gateware/.venv/bin/python check_descriptors.py
#
# What it *cannot* say is that the bitstream on your board carries these strings; only plugging the
# module in can. `ioreg -p IOUSB -l -w 0 | grep -i "USB Vendor Name"` on macOS is the read-out, and
# `webui/usb_check.html` is the same read-out from the browser -- worth preferring, because it also
# shows what Web MIDI thinks, which on macOS is a stale CoreMIDI cache entry rather than the board.

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_id import MANUFACTURER, TAG, usb_manufacturer  # noqa: E402

# Pinned rather than read from git: the point is to check the *format*, and a check whose expected
# value moves with the repo cannot fail.
STAMP_UTC = "2026-08-17T03:57Z"
STAMP_COMMIT = "890d4be"

# The parser in webui/static/transport.js, transcribed. Kept as a literal copy rather than an
# import because it lives in another language -- if you change one, this line is the reminder.
JS_FW_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z)-([0-9a-f]{7}|unknown)(\+?)$")

EXPECTED = {
    1: f"{MANUFACTURER} {TAG}{STAMP_UTC}-{STAMP_COMMIT}",
    2: "Tiliqua XLS32",
    3: "beta-0000",
}

# 17 for the timestamp, 1 for the '-', 7 for the abbreviated commit. Not a magic number: it is the
# width build.sh's `--seed` was drawn against.
STAMP_WIDTH = 25


def strings_from_descriptors():
    """ The device's string descriptors, index -> text, built the way the gateware builds them. """

    os.environ["XLS32_BUILD_UTC"] = STAMP_UTC
    os.environ["XLS32_BUILD_COMMIT"] = STAMP_COMMIT

    from usb_iface import XlsUsbInterface

    # `create_descriptors` is a pure function of the interface's shape, so a bare instance with the
    # handful of attributes the UAC2 parent reads is enough -- elaborating the real thing would
    # drag in the whole design for three strings.
    iface = XlsUsbInterface.__new__(XlsUsbInterface)
    for k, v in dict(nr_channels=4, max_packet_size=512, sample_rate=48000).items():
        setattr(iface, k, v)

    collection = XlsUsbInterface.create_descriptors(iface)
    out = {}
    for index in EXPECTED:
        raw = bytes(collection.get_descriptor_bytes(3, index))
        out[index] = raw[2:].decode("utf-16-le")
    return out


def main():
    fails = []

    got = strings_from_descriptors()
    for index, want in EXPECTED.items():
        state = "ok" if got[index] == want else "FAIL"
        print(f"  [{state}] string {index}: {got[index]!r}")
        if got[index] != want:
            fails.append(f"string {index}: expected {want!r}")

    stamp = got[1][len(f"{MANUFACTURER} {TAG}"):]
    if len(stamp) != STAMP_WIDTH:
        fails.append(f"stamp is {len(stamp)} characters, not {STAMP_WIDTH} -- this re-draws the "
                     f"router seed lottery; see build.sh")
    if not JS_FW_RE.match(stamp):
        fails.append(f"stamp {stamp!r} does not match the parser in webui/static/transport.js")

    # A dirty tree is the one legitimate width change, and the panel has to be able to show it.
    dirty = f"{STAMP_UTC}-{STAMP_COMMIT}+"
    if not JS_FW_RE.match(dirty):
        fails.append(f"the dirty-tree form {dirty!r} does not parse")

    # No stamp at all must be indistinguishable from a pre-#27 bitstream, so that the panel's
    # "this board predates the stamp" row is the honest answer for both.
    for var in ("XLS32_BUILD_UTC", "XLS32_BUILD_COMMIT"):
        os.environ.pop(var, None)
    bare = usb_manufacturer()
    if bare != MANUFACTURER:
        fails.append(f"unstamped build reports {bare!r}, not the bare {MANUFACTURER!r}")
    print(f"  [{'ok' if bare == MANUFACTURER else 'FAIL'}] unstamped: {bare!r}")

    if fails:
        print("\nFAIL")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
