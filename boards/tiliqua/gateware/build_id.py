# The build stamp the board reports over USB. Issue #27.
#
# The question this answers is "which build is on the board in front of me?", and until now nothing
# on the wire could answer it. `webui/static/firmware.json` says what *this repo ships*, which is a
# different question and the wrong one whenever the module was last flashed a month ago.
#
# The obvious mechanism -- a SysEx identity reply -- was costed and rejected, twice. It needs a
# device-to-host USB-MIDI endpoint that does not exist (EP 3 is OUT only), a packetiser, a request
# matcher upstream of `MidiSysexFilter`, and a CDC back into the `usb` domain. Against that the
# design has **559 TRELLIS_COMB left** of 24,288 (23,729 placed, 97%) and a router that only
# converges on a hand-picked seed -- see the `--seed 3` note in build.sh. A few hundred cells for a
# diagnostic is not a trade this board can make, and it would re-draw the seed lottery.
#
# So the stamp goes where the USB stack already has room for it: a string descriptor. No new
# endpoint, no SysEx permission, and *zero* gateware cells.
#
# Three USB strings were candidates and two were rejected on evidence:
#
#   * `iProduct` is what the *audio* device list shows -- `enumerateDevices()` returns
#     "Tiliqua XLS32 (1209:aa62)" -- so a stamp there lands in the macOS sound picker and in the
#     panel's own OUT picker. That is a visible regression to a feature two commits old.
#   * `iSerialNumber` is semantically the right field, but CoreAudio builds a device's persistent
#     UID from vendor/product/serial. A serial that changes on every flash makes every reflash a
#     new device to macOS and to Chrome's permission store, and strands the remembered `sinkId`.
#     It stays "beta-0000".
#   * `iManufacturer` is keyed on by nothing and shown by nothing, which is what makes it free.
#
# **Reading it back does not go through Web MIDI, and that was not free.** `MIDIPort.manufacturer`
# was the intended path -- the panel already holds the MIDI permission -- but on macOS it is not
# the device talking. CoreMIDI caches every string it has seen for a device in
# `~/Library/Preferences/ByHost/com.apple.MIDI.<uuid>.plist`, keyed on USBLocationID +
# USBVendorProduct + SerialNumber, all three of which are pinned above *on purpose*. So a reflash
# never invalidates the entry and Web MIDI keeps reporting the build you just replaced -- which is
# worse than reporting nothing, since "did my flash take?" is the only question this exists for.
# The panel reads `USBDevice.manufacturerName` over WebUSB instead, which comes from IOKit and does
# follow a reflash; see the note in `webui/static/transport.js` and the side-by-side in
# `webui/usb_check.html`. None of that changes anything on this side of the wire, but it is the
# reason the field cannot be quietly renamed: two read paths now depend on the format.
#
# The format keeps "apf.audio" first so the field is still true, and puts the stamp behind a token
# the parser can key on:
#
#     apf.audio XLS32/2026-08-17T09:31Z-974552b
#     apf.audio XLS32/2026-08-17T09:31Z-974552b+     <- built from a dirty tree
#     apf.audio                                      <- built without a stamp (see below)
#
# `webui/static/transport.js` has the matching parser, and the two formats have to agree.
#
# **Keep the stamp a fixed width.** A string descriptor is a ROM, and on a design sitting at 97%
# TRELLIS_COMB with a hand-picked router seed, anything that moves the netlist is expensive -- the
# seed has to be drawn again, and at that utilisation about half the seeds lose. Measured on this
# design: the *length* of the stamp moves the netlist and its *characters* do not. A 42-character
# stamp placed at 23,679 cells; two different 41-character stamps, thirty-four minutes and a
# different commit apart, both placed at 23,792 and produced byte-identical placements down to the
# router's first-iteration wire count. So the format below is deliberately fixed-width -- 17 for
# the timestamp, 7 for the abbreviated commit -- and a build that changes neither the width nor
# the source keeps the seed that is written down in build.sh.
#
# The one thing that does change the width is the '+' on a dirty tree, which re-draws the lottery.
# That is the right way round: a bitstream built over uncommitted edits is a development build and
# is already a different netlist for other reasons. Commit before the build you intend to ship.

import os
import subprocess

# What the field says when there is no stamp: the descriptor goes back to exactly what it was
# before this file existed. That matters -- an unstamped build must be *indistinguishable* from a
# pre-#27 one, so the panel's "this board predates the stamp" row is the honest answer for both
# rather than a third state nobody can act on.
MANUFACTURER = "apf.audio"

# The token that separates the vendor from the stamp. Deliberately not a bare separator: this string
# is user-visible in Audio MIDI Setup, and "XLS32/" reads as what it is.
TAG = "XLS32/"


def build_stamp():
    """
    `<utc>-<commit>`, or None when the build was not given one.

    Both halves come from the environment, because the build script is the only thing that knows
    them and Amaranth elaboration may run from a cache or a container where `git` is not the repo's
    git. `build.sh` sets them; a bare `python top.py build` does not, and gets None.
    """

    utc = os.environ.get("XLS32_BUILD_UTC", "").strip()
    commit = os.environ.get("XLS32_BUILD_COMMIT", "").strip()
    if not utc or not commit:
        return None
    return f"{utc}-{commit}"


def usb_manufacturer():
    """ iManufacturer, with the stamp appended when there is one. """

    stamp = build_stamp()
    return f"{MANUFACTURER} {TAG}{stamp}" if stamp else MANUFACTURER


def env_from_git(repo_root, now_utc):
    """
    The two variables `build.sh` exports, computed here so the rule lives in one place.

    `now_utc` is the build instant -- passed in rather than read, so a caller that wants a
    bit-reproducible build can pin it. It is the clock rather than HEAD's commit date because the
    question is when this bitstream was made, and because it costs nothing to be the clock: the
    format is fixed-width, so the netlist does not move (see the note above).

    A dirty tree gets a trailing '+' on the commit: a bitstream built over uncommitted edits is not
    the commit it names, and the one character says so wherever the stamp is displayed.
    """

    def git(*args):
        try:
            return subprocess.run(("git", "-C", repo_root, *args), capture_output=True,
                                  text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    # --short=7, not --short: git widens abbreviations as a repo grows, and a stamp that changes
    # width silently is exactly what the fixed-width rule above is guarding against.
    commit = git("rev-parse", "--short=7", "HEAD") or "unknown"
    if commit != "unknown" and git("status", "--porcelain") != "":
        commit += "+"

    # A value already in the environment wins. build.sh consumes this through `eval`, which would
    # otherwise re-export over the top of whatever the caller pinned -- so `XLS32_BUILD_UTC=... \
    # ./build.sh` silently got the clock anyway, and the documented way to make a reproducible
    # build did nothing. The override belongs here rather than in the shell for the usual reason:
    # this file is the one place that knows what the variables mean.
    return {"XLS32_BUILD_UTC": os.environ.get("XLS32_BUILD_UTC") or now_utc,
            "XLS32_BUILD_COMMIT": os.environ.get("XLS32_BUILD_COMMIT") or commit}


if __name__ == "__main__":
    # `eval "$(python build_id.py)"` in build.sh -- one implementation, not two.
    import datetime
    import pathlib
    import shlex

    root = pathlib.Path(__file__).resolve().parents[3]
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    for k, v in env_from_git(str(root), now).items():
        print(f"export {k}={shlex.quote(v)}")
