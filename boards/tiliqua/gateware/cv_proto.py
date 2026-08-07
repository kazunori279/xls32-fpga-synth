# The three constants the host and the gateware have to agree on, in a module with no imports.
#
# check_cv.py has to know which CC drives the test ramp, how many counts a step of it is worth, and
# what note 0 V lands on -- and nothing else out of cvin.py. Importing them from there costs it
# `from amaranth import *`, which is not in the repo's uv environment at all: amaranth lives in the
# Tiliqua SDK venv that build.sh reaches for (build.sh:44), and the host scripts run under `uv run`
# from the repo root. So a host script that wants one integer out of a gateware module cannot have
# it. This file is that integer, and its two neighbours.
#
# Keeping them here rather than duplicating them in check_cv.py matters because two of the three are
# a wire protocol: if RAMP_STEP moves in the gateware and not in the host, the sweep silently
# measures the wrong volts and still reports a clean 1 V/oct fit, because the slope error would be
# in the axis rather than in the data.

BASE_NOTE = 36                 # 0 V -> C2
CC_RAMP = 102                  # undefined in the MIDI spec, and unused by synth.x
RAMP_STEP = 160                # counts per step: 0..127 spans 0..20320 counts = 0..5.08 V
