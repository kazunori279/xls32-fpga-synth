"""Several Tiliqua modules playing together: notes over USB, audio out of the jacks.

One USB cable per module carries MIDI only. No board's audio comes back, and the mix
happens outside the computer, on whatever the jacks are plugged into. Issue #52 has the
measurements behind that; the short version is three reasons:

- The only failure measured with three modules attached is on the capture side (#51).
  Not opening an input stream removes it rather than routing around it.
- USB loss cannot reach the jacks. ``usb_tee`` copies into its FIFO only when there is
  room and drops otherwise, so there is no ``ready`` to stall the codec with
  (``ARCHITECTURE_tiliqua.md`` B4).
- MIDI is a bulk OUT endpoint (``boards/tiliqua/gateware/usb_iface.py:265``), and bulk
  transfers are checked and retried by the host controller where isochronous ones are
  not. A marginal link degrades capture long before it degrades notes.

**Parts, not channels.** The engine folds the MIDI channel onto a part with
``ch = ps[0:2]`` (``core/synth.x``), so every board answers to channels 1-4 of its own
cable and nothing else. A rig therefore counts *parts*: part ``p`` lives on board
``p // 4`` as that board's channel ``p % 4``, exactly as the web UI stacks 4 boards into
16 parts (README, "Several boards at once"). ``send_part`` is the one place that knows
it; every caller writes a part number into the status byte as if there were one board.

The two routing modes issue #52 asked for are both here, per message rather than as a
switch: ``send_part`` is the split -- board N owns parts 4N..4N+3 -- and ``send_all`` is
the broadcast, which is what the shell-level effects want, since reverb, chorus and
delay sit after each board's own mix.

**Which box is which.** Nothing in the descriptors names a module: same VID, same PID,
same ``iProduct``, and ``iSerialNumber`` is a constant (#50). With no USB audio there is
nothing to verify a guess against either, so board order is rtmidi's enumeration order
and means nothing beyond being stable for an unchanged set of cables. ``--identify``
plays an arpeggio on one board and lets the ear answer; pin the result by writing the
port indices into ``XLS32_RIG``.

    uv run python host/rig.py                     # what is attached, in board order
    uv run python host/rig.py --identify          # arpeggio on each board in turn
    uv run python host/rig.py --play 60 64 67 72  # one note per part, spread over the rig
    uv run python host/rig.py --panic             # every part of every board silent

    XLS32_RIG=2,0,1 uv run python host/rig.py     # pin board order to those rtmidi ports
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "host")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
# The underscored three are borrowed rather than reimplemented: the rig and the transport have
# to agree on what a board is called, which backend sends the bytes, and how an index or a name
# fragment resolves, or a board reachable from one is unreachable from the other.
from transport.usbaudio import (                                      # noqa: E402
    DEFAULT_MATCH, _import_mido, _import_rtmidi, _match, find_midi_port,
)

#: Parts per board. Fixed by the engine, not a preference: `ch = ps[0:2]` in core/synth.x
#: reads two bits of the channel, so channels 5-16 alias onto 1-4 rather than being ignored.
PPB = 4
#: Notes the identify arpeggio plays, and the seconds between them. Long enough to be
#: unmistakable across a room, short enough that three boards take under five seconds.
IDENT_NOTES = (60, 64, 67, 72)
IDENT_STEP = 0.14


def rig_ports(spec=None):
    """rtmidi port indices for the rig, in board order.

    `spec` (or ``$XLS32_RIG``) is a comma-separated list, each item an rtmidi port index
    or a name fragment -- the same two forms `find_midi_port` takes, and resolved by it,
    so the error messages are the ones the transport already prints. Without a spec,
    every destination matching `DEFAULT_MATCH`, in rtmidi's order.
    """
    spec = spec if spec is not None else os.environ.get("XLS32_RIG")
    if not spec:
        names = _import_rtmidi().MidiOut().get_ports()
        hits = _match(DEFAULT_MATCH, names)
        if not hits:
            find_midi_port()             # attached nothing: borrow its "here is what I see"
        return hits

    ports = [find_midi_port(item.strip(), var="XLS32_RIG")
             for item in spec.split(",") if item.strip()]
    if not ports:
        raise SystemExit(f"XLS32_RIG={spec!r} names no boards.")
    dupes = {p for p in ports if ports.count(p) > 1}
    if dupes:
        # Two board numbers pointing at one module is not a rig, it is a silent misconfiguration:
        # the parts of the shadowed board go nowhere and nothing about the sound says so.
        raise SystemExit(f"XLS32_RIG={spec!r} names port(s) {sorted(dupes)} more than once.\n"
                         "  one board per entry; board order is the order they are written.")
    return ports


class Rig:
    """Open MIDI destinations for N boards, addressed by part.

    Deliberately not a `Transport`: that contract is half capture, and a rig captures
    nothing. What it shares is `send_midi`, so a helper that only sends will take either.
    """

    def __init__(self, ports=None):
        self._spec = ports
        self.ports = []                  # rtmidi indices, board order
        self._outs = []
        self._parser = None
        #: What `open()` bound to, one line per board. See `_announce`.
        self.assignment = None

    # ---- lifecycle ----
    @property
    def nboards(self):
        return len(self.ports)

    @property
    def nparts(self):
        return len(self.ports) * PPB

    def open(self):
        """Acquire every board's MIDI destination. Idempotent; returns self."""
        if self._outs:
            return self
        rtmidi = _import_rtmidi()
        self.ports = (rig_ports(self._spec) if self._spec is None or isinstance(self._spec, str)
                      else list(self._spec))
        for p in self.ports:
            out = rtmidi.MidiOut()
            out.open_port(p)
            self._outs.append(out)
        self._parser = _import_mido().Parser()
        self._announce()
        return self

    def close(self):
        """Release every destination, silencing the boards first.

        The panic is not politeness. A held note on a board whose audio never comes back
        over USB is a note nobody on this end can hear stop.
        """
        if self._outs:
            try:
                self.panic()
            except Exception:
                pass
            for out in self._outs:
                out.close_port()
        self._outs = []

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

    def _announce(self):
        """Print the board-to-port assignment, once, on stderr.

        #50 was a silent binding: with three modules attached the MIDI side picked one and
        told nobody. A rig multiplies that -- a wrong order plays the right notes on the
        wrong instruments, which sounds like a bad arrangement rather than a bug -- so the
        assignment is stated before a single note goes out.
        """
        names = _import_rtmidi().MidiOut().get_ports()
        lines = [f"  board {b + 1}: midi[{p}] {names[p]!r}  parts {b * PPB + 1}-{(b + 1) * PPB}"
                 for b, p in enumerate(self.ports)]
        self.assignment = "\n".join(lines)
        print(f"[rig] {self.nboards} board(s), {self.nparts} parts "
              f"(XLS32_RIG={','.join(str(p) for p in self.ports)})",
              file=sys.stderr)
        print(self.assignment, file=sys.stderr)

    # ---- routing ----
    def send_board(self, b, data):
        """Raw bytes to one board, channel untouched."""
        if not 0 <= b < self.nboards:
            raise ValueError(f"board {b} out of range (rig has {self.nboards})")
        for msg in self._split(data):
            self._outs[b].send_message(msg)

    def send_all(self, data):
        """The same bytes to every board, verbatim -- the broadcast half of #52.

        For the shell-level effects, which are one setting per board and not per part:
        reverb, chorus, delay and delay time sit after each board's own mix, and
        `fx.py`'s CC sniffer matches `(b & 0xF0) == 0xB0` without looking at the channel.
        """
        for msg in self._split(data):
            for out in self._outs:
                out.send_message(msg)

    def send_part(self, p, data):
        """Bytes to global part `p`, rewriting the channel nibble -- the split half of #52.

        The caller writes the part number as if there were one board of `nparts` parts;
        this is the only place that knows part `p` is board `p // PPB` channel `p % PPB`.
        Non-channel messages (0xF0 and up) have no nibble to rewrite and go to that board
        as they stand.
        """
        if not 0 <= p < self.nparts:
            raise ValueError(f"part {p} out of range (rig has {self.nparts})")
        b, ch = divmod(p, PPB)
        for msg in self._split(data):
            if 0x80 <= msg[0] < 0xF0:
                msg = bytes([(msg[0] & 0xF0) | ch]) + bytes(msg[1:])
            self._outs[b].send_message(msg)

    def send_midi(self, data):
        """`Transport.send_midi` by another name: every board, verbatim. See `send_all`."""
        self.send_all(data)

    def _split(self, data):
        """`data` as a list of complete MIDI messages.

        rtmidi's `send_message` takes one message, and callers hand over whatever
        `host/synth.py` built -- `panic()` alone is four concatenated CCs. mido's parser
        does the splitting and is pure Python; no backend is loaded and none is needed.
        """
        self._parser.feed(data)
        return [m.bytes() for m in self._parser]

    # ---- operations ----
    def panic(self):
        """Every part of every board silent. See `synth.panic` for why CC120 alone."""
        from synth import panic as _panic

        self.send_all(_panic(PPB))

    def identify(self, b, notes=IDENT_NOTES, step=IDENT_STEP):
        """Play an arpeggio on board `b`'s first part and nothing else.

        The only way to learn which box is which. There is no serial number to key on and
        no USB audio to check a guess against, so the answer arrives by ear, from the
        module that sounds and the visualiser that moves.
        """
        p = b * PPB
        for n in notes:
            self.send_part(p, bytes([0x90, n, 100]))
            time.sleep(step)
            self.send_part(p, bytes([0x80, n, 0]))


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(
        description="Drive several Tiliqua modules over USB MIDI; audio comes out of the jacks.")
    ap.add_argument("--rig", help="comma-separated rtmidi ports in board order "
                                  "(default $XLS32_RIG, else every 'Tiliqua XLS32' destination)")
    ap.add_argument("--identify", nargs="?", const=-1, type=int, metavar="BOARD",
                    help="arpeggio on board N (1-based), or every board in turn")
    ap.add_argument("--play", nargs="+", type=int, metavar="NOTE",
                    help="one note per part, spread over the rig: note k -> part k")
    ap.add_argument("--secs", type=float, default=2.0, help="how long --play holds (default 2)")
    ap.add_argument("--panic", action="store_true", help="silence every part and exit")
    args = ap.parse_args(argv)

    with Rig(args.rig) as rig:
        if args.panic:
            rig.panic()
            return 0
        if args.identify is not None:
            boards = range(rig.nboards) if args.identify < 0 else [args.identify - 1]
            for b in boards:
                if not 0 <= b < rig.nboards:
                    raise SystemExit(f"--identify {b + 1}: the rig has {rig.nboards} board(s).")
                print(f"board {b + 1} (midi[{rig.ports[b]}]) -- listen", file=sys.stderr)
                rig.identify(b)
                time.sleep(0.4)
            return 0
        if args.play:
            from synth import note_off, note_on

            if len(args.play) > rig.nparts:
                # Silently folding the extras onto part 0 would stack notes on one voice
                # allocator and sound like the rig, not like a rig missing a board.
                raise SystemExit(f"{len(args.play)} notes but only {rig.nparts} parts "
                                 f"({rig.nboards} board(s) x {PPB}).")
            for p, n in enumerate(args.play):
                print(f"  part {p + 1} (board {p // PPB + 1} ch {p % PPB + 1}): note {n}",
                      file=sys.stderr)
                rig.send_part(p, note_on(n))
            time.sleep(args.secs)
            for p, n in enumerate(args.play):
                rig.send_part(p, note_off(n))
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
