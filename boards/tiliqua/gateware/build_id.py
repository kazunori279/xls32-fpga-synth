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
# So the stamp goes where the USB stack already has room for it: a string descriptor. Measured on
# Chrome 150 / macOS, `MIDIOutput` exposes iManufacturer **verbatim** as `.manufacturer` --
#
#     {id: "759255568", name: "Tiliqua XLS32", manufacturer: "apf.audio", version: ""}
#
# -- so the panel reads it with the MIDI permission it already holds: no SysEx permission, no
# WebUSB picker, no new endpoint, and *zero* gateware cells. Per port, so four boards give four
# stamps, which is exactly the table #27 asks for.
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
#   * `iManufacturer` is keyed on by nothing, shown by nothing, and read verbatim by Web MIDI.
#
# The format keeps "apf.audio" first so the field is still true, and puts the stamp behind a token
# the parser can key on:
#
#     apf.audio XLS32/2026-08-17T09:31Z-974552b
#     apf.audio XLS32/2026-08-17T09:31Z-974552b+     <- built from a dirty tree
#     apf.audio                                      <- built without a stamp (see below)
#
# `webui/static/transport.js` has the matching parser, and the two formats have to agree.

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

    `now_utc` is passed in rather than read, so a caller that wants a reproducible build can pin it.
    A dirty tree gets a trailing '+' on the commit: a bitstream built over uncommitted edits is not
    the commit it names, and the one character says so wherever the stamp is displayed.
    """

    def git(*args):
        try:
            return subprocess.run(("git", "-C", repo_root, *args), capture_output=True,
                                  text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    commit = git("rev-parse", "--short=7", "HEAD") or "unknown"
    if commit != "unknown" and git("status", "--porcelain") != "":
        commit += "+"
    return {"XLS32_BUILD_UTC": now_utc, "XLS32_BUILD_COMMIT": commit}


if __name__ == "__main__":
    # `eval "$(python build_id.py)"` in build.sh -- one implementation, not two.
    import datetime
    import pathlib
    import shlex

    root = pathlib.Path(__file__).resolve().parents[3]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    for k, v in env_from_git(str(root), stamp).items():
        print(f"export {k}={shlex.quote(v)}")
