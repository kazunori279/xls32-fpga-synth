"""Which physical hub socket is behind each PortAudio index?

A probe result names a device index, and an index names a *socket*, not a module -- every module
reports the same USB serial (#50) and the indices move whenever anything is re-plugged. So when
`probe_capture.py` says audio[3] is losing frames, this is what turns that into "port 3 of the hub",
which is the form the answer has to be in before it can be tested by moving a plug (#51).

    uv run python host/hub_ports.py

The mapping goes PortAudio index -> CoreAudio UID -> `locationID`. macOS embeds the location in the
UID, so a module whose UID fragment is already a location needs no guessing; one still carrying the
stock `beta-0000` string is resolved by elimination against what `ioreg` sees, which only works when
exactly one is unresolved. Two stock modules on the same hub print the candidates instead of
choosing.

`0x1N0000` is port N of the hub on bus 1; uhubctl calls the same socket `0-1:N`, which is how
`usb_watch.log` labels its transitions. Cross-reference the two before blaming a module.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sounddevice as sd                                              # noqa: E402

from transport.usbaudio import _ca_uids                               # noqa: E402


def kernel_locations():
    """Every Tiliqua `locationID` the kernel can see, as hex, in ioreg order."""
    ior = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w0"],
                         capture_output=True, text=True).stdout
    locs, pending = [], False
    for line in ior.splitlines():
        if "Tiliqua XLS32" in line:
            pending = True
        elif pending and "locationID" in line:
            n = int(re.search(r"= (\d+)", line).group(1))
            locs.append(f"{n:x}")
            pending = False
    return locs


def main():
    locs = kernel_locations()
    uids = _ca_uids()
    rows = []
    for i, d in enumerate(sd.query_devices()):
        if "Tiliqua" in d["name"] and d["max_input_channels"] >= 4:
            u = uids.get(i, "")
            frag = u.split(":")[-2] if u.count(":") >= 2 else "?"
            rows.append((i, frag))

    named = {f for _, f in rows if f != "beta-0000"}
    spare = [l for l in locs if l not in named]
    print(f"\nkernel sees Tiliqua at hub ports: {', '.join(locs) or '(none)'}\n")
    print("  audio  UID fragment   hub port")
    for i, frag in rows:
        port = frag if frag != "beta-0000" else (spare[0] if len(spare) == 1 else f"? {spare}")
        print(f"  [{i}]    {frag:<13}  {port}")
    print()


if __name__ == "__main__":
    main()
