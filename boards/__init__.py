"""Board registry.

A *board* is everything that is not DSP: pins, clock rate, how the host talks to it,
how a bitstream gets loaded. The engine itself (``core/synth.x``) never appears here
and never learns which board it is running on.

Pick a board with ``$XLS32_BOARD``; the default is ``basys3``, so every command that
worked before the boards/ split still works unchanged.

    from boards import get_board
    b = get_board()          # honours $XLS32_BOARD
    b.sr                     # 32000
    b.transport              # "uart"

``host/synth.py`` binds ``SR`` at import time, so the variable has to be set in the
environment before python starts — not from an argparse flag halfway down a main().
``test/run_tests.py`` gained a ``--board`` flag in M25 and works around that the only way
it can: it scans ``sys.argv`` by hand and writes ``$XLS32_BOARD`` *above* its own
``import harness`` line. Anything reading this registry from argparse is already too late.
"""
import os
from dataclasses import dataclass, field

DEFAULT = "basys3"


@dataclass(frozen=True)
class Board:
    """What the host needs to know to drive a board. No gateware details."""

    name: str
    fpga: str
    #: Audio sample rate the gateware actually runs at.
    sr: int
    #: Which host/transport/ implementation to use: "uart" | "usbaudio".
    transport: str
    #: Keyword arguments for that transport's constructor.
    transport_opts: dict = field(default_factory=dict)
    #: True when the capture stream is L,R interleaved (see samples_from_bytes).
    stereo: bool = True
    #: Shell command that loads build/top.bit onto the board, for docs and scripts.
    load_cmd: str = ""
    #: Directory holding this board's gateware and scripts, relative to the repo root.
    root: str = ""
    #: Set when the board is declared but not yet buildable, with the reason.
    unsupported: str = ""

    def require_supported(self):
        if self.unsupported:
            raise NotImplementedError(f"board {self.name!r}: {self.unsupported}")
        return self


def _registry():
    from boards.basys3.board import BOARD as basys3
    from boards.tiliqua.board import BOARD as tiliqua

    return {b.name: b for b in (basys3, tiliqua)}


def names():
    return sorted(_registry())


def get_board(name=None):
    """Resolve a board by name, then $XLS32_BOARD, then the default."""
    name = name or os.environ.get("XLS32_BOARD") or DEFAULT
    try:
        return _registry()[name]
    except KeyError:
        raise SystemExit(
            f"unknown board {name!r}; known boards: {', '.join(names())}"
        ) from None
