# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Watch every USB hub port and write down each change, so a capture can be alibied.

    uv run host/usb_watch.py --out /tmp/usb_watch.log &     # leave it running, always
    uv run host/usb_watch.py --hub 0-1 --interval 0.5       # narrow, once you know the hub

Needs the `uhubctl` binary (`brew install uhubctl`); no Python dependencies.

WHY THIS EXISTS. A short capture has two explanations that look identical in one
device's numbers, and this is what tells them apart after the fact. Any USB
device re-enumerating anywhere on the machine makes PortAudio's callback run
late and the board's ring overwrite itself, so the suite reports frame loss that
no hardware caused (#49 -- three hardware explanations were written up and
retracted in a day before anyone read a log). And one hub *port* can eat frames
with any module and any cable plugged into it, which reads exactly like a broken
module (#51). Both are gone by the time the run finishes. A witness started after
the symptom explains nothing, so this runs permanently and the rule in
`test/README.md` is to read its log for the run's window *before* forming any
hardware hypothesis.

WHAT THE BITS MEAN, because the whole argument turns on them:

  power enable connect   normal.
  power connect          VBUS is out there and nothing is pulling D+ up.
                         `connect` is the hub reporting a pull-up, which is a
                         purely electrical property of the far end of the cable
                         - no amount of traffic, from this port or a neighbour,
                         can clear it. So the link dropped: the device took its
                         own pull-up down, or lost the volts to hold one up.
  connect []             empty brackets where a descriptor belongs: the hub sees
                         something electrically but the device never answered.
                         Paired with `0111 power reset connect []` repeating,
                         that is a port refusing to enumerate at all (#51).
  (no power)             somebody cycling the port. If nothing was run by hand,
                         the hub dropped VBUS on its own.

Only transitions are logged, plus a heartbeat every 60 s, so a gap in the file
means the watcher stopped rather than the tree being quiet -- the distinction
matters, because "quiet" is the evidence. Timestamps are local wall clock, to
line up against a run log and `log show` on the Mac.

**Check the log covers the hub under test.** Bus numbering is per machine and a
hub only appears once it is plugged in; four "USB tree quiet" reports during #51
were read off a log that had never seen the Tiliqua hub. `host/hub_ports.py`
prints which socket each module is actually on.

Originally written for the sibling `fpga-open-vocab` project and copied here
verbatim apart from this docstring, because the rule that depends on it is in
this repo's test protocol.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PORT_RE = re.compile(r"^\s*Port (\d+):\s+([0-9a-fA-F]{4})\s*(.*?)\s*$")
HUB_RE = re.compile(r"^\s*Current status for hub (\S+)")

# uhubctl is quick, but a wedged USB stack can make it sit there. Time it out
# well inside the poll interval's patience so a stuck call shows up as its own
# logged event rather than as silence.
UHUBCTL_TIMEOUT_S = 10.0


def read_ports() -> dict[str, str] | str:
    """Every port uhubctl can see, as {"2-1:1": "0103 power enable connect ..."}.

    Returns a string instead of a dict if uhubctl could not be asked, so that
    losing the instrument is itself an event worth a line in the log.
    """
    try:
        r = subprocess.run(["uhubctl"], capture_output=True, text=True,
                           timeout=UHUBCTL_TIMEOUT_S, check=False)
    except FileNotFoundError:
        return "uhubctl not installed"
    except subprocess.TimeoutExpired:
        return f"uhubctl did not answer in {UHUBCTL_TIMEOUT_S:g}s"
    except subprocess.SubprocessError as e:
        return f"uhubctl failed: {e}"
    if r.returncode != 0:
        return f"uhubctl exit {r.returncode}: {r.stderr.strip() or 'no message'}"

    ports: dict[str, str] = {}
    hub = None
    for line in r.stdout.splitlines():
        m = HUB_RE.match(line)
        if m:
            hub = m.group(1)
            continue
        m = PORT_RE.match(line)
        if m and hub:
            flags, rest = m.group(2), m.group(3)
            ports[f"{hub}:{m.group(1)}"] = f"{flags} {rest}".strip()
    return ports


def wanted(key: str, sel: str | None) -> bool:
    """Does port `key` ("2-1:1") match a --hub of "2-1" or "2-1:1" or None?"""
    if sel is None:
        return True
    if ":" in sel:
        return key == sel
    return key.startswith(sel + ":")


def stamp() -> str:
    # .astimezone() only to make the local zone explicit - the format string
    # prints no offset, so the stamp is byte-for-byte what it always was.
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hub", metavar="HUB[:PORT]",
                    help="narrow to one hub or one port; default is everything "
                         "uhubctl can see, which also catches a neighbour "
                         "blinking at the same moment")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="seconds between polls (default 1.0)")
    ap.add_argument("--heartbeat", type=float, default=60.0,
                    help="seconds between 'still here' lines, so a gap in the "
                         "log is unambiguous (default 60; 0 to disable)")
    ap.add_argument("--out", type=Path,
                    help="also append to this file (stdout always gets it)")
    args = ap.parse_args()

    out = args.out.open("a", buffering=1) if args.out else None

    def say(text: str) -> None:
        line = f"{stamp()}  {text}"
        print(line, flush=True)
        if out:
            out.write(line + "\n")

    prev: dict[str, str] | str | None = None
    # Start the clock now, not at zero: the opening state dump is already a
    # full picture, and a heartbeat one line under it says nothing.
    last_beat = time.monotonic()
    say(f"watching {args.hub or 'every port uhubctl can see'} "
        f"every {args.interval:g}s")

    try:
        while True:
            now = read_ports()

            if isinstance(now, str):
                if now != prev:
                    say(f"INSTRUMENT  {now}")
            else:
                now = {k: v for k, v in now.items() if wanted(k, args.hub)}
                if prev is None:
                    for k in sorted(now):
                        say(f"{k}  {now[k]}")
                elif isinstance(prev, str):
                    say("INSTRUMENT  uhubctl is answering again")
                    for k in sorted(now):
                        say(f"{k}  {now[k]}")
                else:
                    for k in sorted(set(prev) | set(now)):
                        was, is_ = prev.get(k, "(not in the tree)"), \
                                   now.get(k, "(not in the tree)")
                        if was != is_:
                            say(f"{k}  {was}   ->   {is_}")

            prev = now

            if args.heartbeat and time.monotonic() - last_beat >= args.heartbeat:
                last_beat = time.monotonic()
                if isinstance(now, dict):
                    say("still here: " + " | ".join(
                        f"{k} {now[k]}" for k in sorted(now)))
                else:
                    say(f"still here, but {now}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        say("stopped")
        return 0
    finally:
        if out:
            out.close()


if __name__ == "__main__":
    sys.exit(main())
