#!/usr/bin/env python3
"""M23 exit check: does the Tiliqua audio path carry the engine's pitch faithfully?

Two captures of the same boot patch:

  * ``build/tiliqua/ref32.txt`` -- the bare engine under iverilog, one signed sample per line,
    at the engine's own rate (``boards/tiliqua/sim/tb_boot.v``).
  * ``build/tiliqua/out0.txt``  -- out0 of the Tiliqua gateware under Verilator, after the CDC,
    the 3/2 resampler and the codec (``SIM=1 bash boards/tiliqua/build.sh``).

The comparison is in *cycles per sample*, not hertz. That is deliberate. Neither simulation runs
at a physically exact clock -- the SDK's harness advances time in whole nanoseconds, so its
12.288 MHz mclk is really 12.5 MHz -- and hertz would fold that error into the result. Cycles
per sample is immune to it: whatever the clock, resampling 32 kHz to 48 kHz must divide the
normalised frequency by exactly 3/2, so

    f_norm(out0) / f_norm(ref) == 2/3

is a statement about the audio path alone. A resampler that dropped or doubled samples, a CDC
that lost a word, or a codec fed at the wrong rate would all move this ratio.

This says nothing about whether the synth is in tune at A4=440 -- that is the separate,
pre-existing ``pitch_a4`` question, and mixing the two would make neither answerable.

    uv run boards/tiliqua/check_pitch.py
"""

import argparse
import sys

import numpy as np

# Ratio the resampler is configured for in xls_core.py (n_up=3, m_down=2). Normalised frequency
# scales by the inverse.
N_UP, M_DOWN = 3, 2
EXPECTED = M_DOWN / N_UP

# Skip the ADSR attack and decay -- the envelope smears the spectrum badly enough during the
# first ~80 ms that the peak wanders by several bins.
SKIP_FRACTION = 0.35


def peak_norm_freq(x):
    """Peak frequency of ``x`` in cycles per sample, with parabolic interpolation.

    Interpolation matters here: the two captures have different lengths and different sample
    rates, so their FFT bins do not line up, and comparing raw bin indices would show a
    disagreement that is entirely an artefact of binning.
    """
    x = np.asarray(x, dtype=float)
    x = x[int(len(x) * SKIP_FRACTION):]
    x = x - x.mean()
    n = len(x)
    spec = np.abs(np.fft.rfft(x * np.hanning(n)))
    k = int(spec.argmax())
    if k == 0 or k == len(spec) - 1:
        return k / n, spec
    a, b, c = spec[k - 1], spec[k], spec[k + 1]
    denom = a - 2 * b + c
    delta = 0.0 if denom == 0 else 0.5 * (a - c) / denom
    return (k + delta) / n, spec


def load(path):
    try:
        data = np.loadtxt(path)
    except OSError as exc:
        sys.exit(f"{exc}\nrun the two simulations first (see the module docstring)")
    if data.size < 1024:
        sys.exit(f"{path}: only {data.size} samples, too few to resolve a pitch")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="build/tiliqua/ref32.txt")
    ap.add_argument("--out", default="build/tiliqua/out0.txt")
    ap.add_argument("--engine-fs", type=float, default=32000.0,
                    help="nominal engine rate, used only to print a frequency in Hz")
    ap.add_argument("--tol", type=float, default=0.01,
                    help="allowed relative error on the 2/3 ratio (default 1%%)")
    args = ap.parse_args()

    ref = load(args.ref)
    out = load(args.out)

    f_ref, _ = peak_norm_freq(ref)
    f_out, _ = peak_norm_freq(out)
    ratio = f_out / f_ref
    err = abs(ratio / EXPECTED - 1.0)

    fs_out = args.engine_fs * N_UP / M_DOWN
    print(f"engine reference : {len(ref):6d} samples, peak {f_ref:.6f} cyc/sample"
          f"  ({f_ref * args.engine_fs:8.2f} Hz at {args.engine_fs / 1000:g} kHz)")
    print(f"tiliqua out0     : {len(out):6d} samples, peak {f_out:.6f} cyc/sample"
          f"  ({f_out * fs_out:8.2f} Hz at {fs_out / 1000:g} kHz)")
    print(f"ratio            : {ratio:.6f}  (expected {EXPECTED:.6f}, "
          f"error {err * 100:.3f}%, tolerance {args.tol * 100:g}%)")
    print(f"peak level       : ref {np.abs(ref).max():.0f}, out0 {np.abs(out).max():.0f} "
          f"of 32768 full scale")

    if err > args.tol:
        print("FAIL: the audio path does not preserve pitch")
        return 1
    print("PASS: the Tiliqua audio path carries the engine's pitch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
