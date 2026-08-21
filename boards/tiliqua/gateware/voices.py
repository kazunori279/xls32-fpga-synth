# How many voices this build has, in one place.
#
# The number is decided in DSLX -- `spike/voices_variant.py` rewrites the ring length before XLS
# compiles the engine -- and until #40 the Python side had it written down twice more, both times
# as the literal 32: once in viz.py's tile grid and once in the `brief` string the bootloader
# prints. The 24-voice bitstream that ships therefore drew 32 tiles, eight of which could never
# light, and announced itself as the 32-voice build.
#
# `$VOICES` is exported by boards/tiliqua/build.sh, which is the only thing that sets it. A bare
# `python top.py build` with no environment gets 24, matching that script's own default, so the
# two cannot drift apart silently.

import os

N_VOICE = int(os.environ.get("VOICES", "24"))

# The tile grid for M29's renderer. Both dimensions have to divide the 720x720 active area
# exactly (viz.py asserts it), and the product has to be the voice count, or the screen would
# either clip a voice or draw a tile nothing ever addresses. Four rows throughout: it keeps the
# tiles as close to square as this panel allows and it makes the three ladders visibly the same
# picture at different widths.
GRIDS = {16: (4, 4), 24: (6, 4), 32: (8, 4)}

if N_VOICE not in GRIDS:
    raise ValueError(f"VOICES={N_VOICE} has no tile grid; add one to voices.GRIDS")

COLS, ROWS = GRIDS[N_VOICE]
