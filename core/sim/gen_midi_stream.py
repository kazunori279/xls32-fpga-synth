#!/usr/bin/env python3
"""Emit the byte-for-byte MIDI stream `validate_hw.capture()` sends for one preset, as a
$readmemh file for core/sim/tb_preset_rail.v.

M28a's simulation of the shell showed the Basys 3 drops no MIDI bytes, which killed the
dropped-byte explanation for the 6/274 railing presets. The question that replaced it -- is the
rail reproducible off the board at all? -- needs the *exact* stimulus the validator uses,
including its 3 ms inter-CC pacing and the `recover()` preamble, because "the preset's CCs" alone
is not what the hardware sees. Generating it here keeps one definition of that stimulus instead of
transcribing 24 CC values into Verilog by hand for each preset under suspicion.

Word format (32-bit hex, one per line):
    0000_00BB   send byte BB on the FT2232 line at 2 Mbaud, no gap
    0001_NNNN   idle for NNNN microseconds
    0002_0000   start capturing samples
    0003_0000   stop and report

`--prime` chains a second preset in front of the one under test, because `validate_hw.py` does not
reset the board between presets and `recover()` does not clear the reverb tank: it sets CC93 = 0,
and with `revwet == 0` top.v skips the whole reverb FSM (`dst` 5-28), so the tank *freezes* holding
the previous preset's audio instead of decaying. Every simulation starts from a cleared tank; every
hardware take starts from whatever the last preset left behind. That is the one state difference
between the rig and the board still standing after M28a, and it is history-dependent -- the right
shape for a fault that hits 30-60% of takes.

    uv run python core/sim/gen_midi_stream.py Brightness > /tmp/stream.hex
    uv run python core/sim/gen_midi_stream.py --prime prev Brightness > /tmp/stream.hex
    uv run python core/sim/gen_midi_stream.py --bank nsynth --list
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "presetgen"))
from calibrate import CC_MAP, NOTE  # noqa: E402  (path set above)

CAP_S = 1.3          # validate_hw.CAP
VEL = 100            # validate_hw.capture() -> u.note_on(note, 100)

out = []
def byte(b):  out.append(0x000000 | (b & 0xFF))
def delay_us(us): out.append(0x00010000 | min(us, 0xFFFF))
def delay_ms(ms):
    while ms > 60:                       # the delay field tops out at 65.5 ms
        delay_us(60000); ms -= 60
    delay_us(int(ms * 1000))
def msg(bs):
    for b in bs: byte(b)
def note_off(n): msg([0x80, n & 0x7F, 0])
def note_on(n, v): msg([0x90, n & 0x7F, v & 0x7F])
def cc(c, v): msg([0xB0, c & 0x7F, v & 0x7F])


def recover():
    """validate_hw.recover(): all notes off, every effect depth zeroed, a mild filter, then settle.
    Skipping this would start the capture from a different state than the board ever starts from.
    Note what it does NOT do: clear the delay lines or the reverb tank."""
    for n in range(128): note_off(n)
    for c in (93, 94, 95): cc(c, 0)
    cc(71, 40); cc(74, 64)
    delay_ms(300)


def setup(vals, pace_ms):
    """validate_hw.capture()'s preamble: note-offs unpaced, the preset's CCs at 3 ms, 40 ms settle.
    Kept separate from hold() because the capture window opens between the two -- widening it to
    include the CC sweep would change the peak/jump statistic and break comparison with the
    unprimed baseline."""
    for n in range(128): note_off(n)
    for cid, num in CC_MAP:
        if cid in vals:
            cc(num, vals[cid] & 0x7F)
            delay_ms(pace_ms)
    delay_ms(40)


def hold(note, secs):
    note_on(note, VEL)
    delay_ms(secs * 1000)
    note_off(note)
    delay_ms(40)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset", nargs="?", help="preset name (exact, as in the bank JSON)")
    ap.add_argument("--bank", default="soundfont")
    ap.add_argument("--pace-ms", type=float, default=3.0, help="inter-CC gap (validate_hw uses 3)")
    ap.add_argument("--note", type=int, default=NOTE)
    ap.add_argument("--secs", type=float, default=CAP_S)
    ap.add_argument("--prime", metavar="NAME",
                    help="play this preset first, uncaptured, to leave its audio in the reverb "
                         "tank -- as the board does. 'prev' uses the bank-order predecessor, "
                         "which is what validate_hw.py actually leaves behind.")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    bank = json.load(open(os.path.join(ROOT, "webui", f"presets_{a.bank}.json")))["presets"]
    if a.list:
        for p in bank: print(p["name"])
        return
    idx = [i for i, p in enumerate(bank) if p["name"] == a.preset]
    if not idx:
        sys.exit(f"no preset named {a.preset!r} in presets_{a.bank}.json (try --list)")
    vals = bank[idx[0]]["values"]

    primer = None
    if a.prime:
        if a.prime == "prev":
            if idx[0] == 0:
                sys.exit(f"{a.preset!r} is first in the bank -- nothing precedes it, pass a name")
            primer = bank[idx[0] - 1]
        else:
            phit = [p for p in bank if p["name"] == a.prime]
            if not phit:
                sys.exit(f"no preset named {a.prime!r} to prime with (try --list)")
            primer = phit[0]
        # Uncaptured, and deliberately NOT followed by anything that drains the tank.
        recover()
        setup(primer["values"], a.pace_ms)
        hold(a.note, a.secs)

    recover()
    setup(vals, a.pace_ms)
    out.append(0x00020000)
    hold(a.note, a.secs)
    out.append(0x00030000)

    tag = f", primed with {primer['name']!r}" if primer else ""
    print(f"// {a.bank}/{a.preset}: note {a.note} vel {VEL}, {a.secs}s, "
          f"pace {a.pace_ms}ms{tag}")
    for w in out: print(f"{w:08x}")


if __name__ == "__main__":
    main()
