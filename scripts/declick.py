#!/usr/bin/env python3
"""Conceal the step discontinuities a USB capture leaves behind, without changing its length.

As of M33 this is a repair tool for old takes, not part of the recording path. The tee that feeds
the USB capture is a 16-deep FIFO written off the board's audio clock and read off the host's, and
it is required to drop rather than stall the codec (`boards/tiliqua/gateware/top.py:310`); it used
to have no rate control between the two clocks, and 110-123 ppm of difference ate the FIFO every
10 s. The UAC2 capture endpoint now paces itself from the FIFO's own level, and a 120 s take
measures 0 lost frames in 0 events, so a take made on a current bitstream has nothing here to fix.
Everything below describes the takes made before it, which is what this still exists for.

`scripts/rec_audio.py` counts the frames a capture loses and refuses a take over 0.1 %. What it
could not do was make the loss zero. Nothing here
reaches the jacks -- `out0`/`out1` get every sample -- but the recording did not. The drops land on
a grid: over a 122 s take, **9.66 to 10.39 s** apart, and twice at exactly double that -- which is
the arithmetic agreeing with itself, because the counter scored the same take at 12 events and this
finds 10. About a millisecond each time, 0.011 % of the audio, which is nothing, except that each
one is a *step* in a sustained tone and a step is a click. Ten of them were plainly audible in a
take the counter had already passed.

**Where** they are is not guessed at. The Tiliqua's tee is four channels and two of them are that
counter, so a recording made through `rec_audio.py` says which sample the tee dropped a run after;
this repairs those and nothing else. It used to look for the steps in the waveform instead,
and that is a worse question than it sounds: a MIDI CC is 7 bits, so a knob dragged across a filter
sweep moves the sound in 1/128 jumps at the pointer's ~50 Hz, and a burst of small steps 20 ms apart
is the same shape as a dropped buffer. On a take where the panel was played while the demo ran, the
waveform detector found 50 seams and only ~12 were the clock -- it spent the other 38 rebuilding the
performance, and the bridge is not free: measured on this material it lands about 0.18 away from
what was really there, so on a 0.0078 knob step the cure ran twenty times the disease. The counter
does not have an opinion about any of that. `--heuristic` keeps the old path for takes without one.

The excision is what it is; this hides the seam. For each step the waveform is continued across it
by an AR model fitted to the 40 ms before it, then cross-faded into the material that follows over
5 ms. The result is the same number of samples in and out -- important, because the audio is being
muxed against a video track that has not moved.

This conceals; it does not restore. The ~1 ms that never arrived is still gone, and the two sides
of a seam are not in phase, so the cross-fade thins the tone for those 5 ms. That is inaudible
where a click is not.

`--audit` is the check that says which of those two things a bridge would be doing (#12). It only
works because the counter is ground truth sitting in the same file as the music: run both detectors
over one take, match them up, and every waveform hit with no counter event under it is a knob move
the heuristic was about to rebuild. It exits 1 if it finds any, which makes "would `--heuristic`
damage this take?" a question with an answer rather than something to listen for. Misses -- real
dropouts the waveform walked past -- are reported but do not fail, because a miss leaves a click
that was already there while a spurious bridge replaces material that was fine.

Usage:
    uv run scripts/declick.py in.wav out.wav
    uv run scripts/declick.py in.wav --dry-run          # locate the seams, write nothing
    uv run scripts/declick.py in.wav --audit            # score the heuristic against the counter
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rec_audio import counter_loss                                       # noqa: E402

# Only used when the take has no counter on it. A seam is a sample-to-sample jump far outside what
# the music itself does *there*. The ratio is
# taken against a local median of |diff| rather than a global one, because a global figure is set
# by whatever dominates the take: the raw USB tee carries the pulse wave's DC and most of its
# energy below 5 Hz, which is smooth, so the global median collapses and ordinary note attacks
# clear any fixed multiple of it. 20x the local median separates the two populations cleanly --
# the loudest musical transient in *Prelude in C* reaches 8x, the quietest real seam 34x.
THRESH_RATIO = 20.0
BASELINE_S = 0.100       # window the local median of |diff| is taken over
MERGE_S = 0.020          # two detections closer than this are one seam
# 5 ms of cross-fade, bridged by an order-48 AR model fitted to the 40 ms before the seam. Short
# enough that the thinning where the two sides disagree in phase passes as nothing; long enough
# that what is left of the step is spread below the ear's click threshold.
XFADE_S = 0.005
LPC_ORDER = 48
# A seam is not always one sample wide -- the largest jump can sit two or three samples inside the
# damage, and repairing from the largest one outward leaves the rest of it standing. Backing the
# repair up by a few samples costs nothing and puts the whole disturbance inside the fade. Measured:
# without this the residual peak lands at -1 to -3 samples, i.e. just outside what was rebuilt.
GUARD = 8


def counter_seams(path):
    """Sample indices where the board says frames went missing, or None if it cannot say.

    This is the whole answer when the take came off the Tiliqua's tee: ch2/3 carry a 12.288 MHz
    counter, so the recording knows exactly which sample the tee stopped writing after. Nothing has
    to be inferred and nothing else gets touched.
    """
    a, _ = sf.read(path, dtype="int16", always_2d=True)
    res = counter_loss(a)
    return None if res is None else [int(i) for i in res[3]]


def find_seams(mono, sr, thresh=THRESH_RATIO):
    """Indices i where the step falls between sample i and i+1."""
    d = np.abs(np.diff(mono))
    if len(d) < 2:
        return []
    # Block medians, then one value per sample by nearest-block lookup. A sliding median over five
    # million samples is not worth the minutes it costs; the baseline only has to track the take's
    # loudness, which does not move inside 100 ms.
    b = max(1, int(BASELINE_S * sr))
    nb = len(d) // b
    if nb < 1:
        return []
    blocks = np.median(d[:nb * b].reshape(nb, b), axis=1)
    # Widen each block to the loudest of itself and its neighbours. Without this, the first note of
    # the take is always a "seam": the block before it is the silence ahead of the downbeat, so the
    # baseline there is a noise floor and any attack clears 20x of it. Taking the neighbour's level
    # too means an onset is judged against the music it starts, which is the honest comparison.
    blocks = np.maximum.reduce([blocks, np.roll(blocks, 1), np.roll(blocks, -1)])
    blocks = np.maximum(blocks, 1e-9)
    base = np.repeat(blocks, b)
    if len(base) < len(d):
        base = np.concatenate([base, np.full(len(d) - len(base), blocks[-1])])
    hits = np.flatnonzero(d / base > thresh)
    seams, merge = [], int(MERGE_S * sr)
    for i in hits:
        if seams and i - seams[-1] <= merge:
            continue                      # same seam, already taken
        seams.append(int(i))
    return seams


def _lpc(seg, order):
    """AR coefficients by autocorrelation + Levinson-Durbin. Returns a[1..order], or None."""
    seg = seg - seg.mean()
    r = np.correlate(seg, seg, mode="full")[len(seg) - 1:][: order + 1]
    if r[0] <= 0:
        return None
    r = r / r[0]
    r[0] += 1e-6                                   # ridge: keeps a near-silent window invertible
    a = np.zeros(order + 1)
    a[0], e = 1.0, r[0]
    for m in range(1, order + 1):
        k = -(a[:m] @ r[m:0:-1]) / e
        a[1:m + 1] += k * a[m - 1::-1]
        e *= 1 - k * k
        if e <= 0:
            return None
    return a[1:]


def _extrapolate(seg, order, n):
    """Continue `seg` forward by `n` samples under an AR model fitted to it."""
    # The model is fitted to the segment with its mean taken out, so it has to be *driven* that way
    # too. Off the Tiliqua's USB tee that is not a detail: the raw capture carries the pulse wave's
    # DC at around +0.29, and feeding a zero-mean model a history sitting on that offset makes the
    # first predicted sample miss by more than the click being repaired.
    mu = seg.mean()
    seg = seg - mu
    a = _lpc(seg, order)
    if a is None:
        return None
    hist = list(seg[-order:])
    out = []
    for _ in range(n):
        nxt = -float(np.dot(a, hist[::-1]))
        out.append(nxt)
        hist = hist[1:] + [nxt]
    return np.array(out) + mu


def audit(counter, heuristic, sr, tol_s=MERGE_S):
    """Match what the waveform detector found against what the board says actually happened.

    This is the check #12 asked for, and the reason it can exist at all is that the Tiliqua's tee
    carries ground truth alongside the music: ch2/3 are a 12.288 MHz counter, so for any take made
    through `rec_audio.py` there is no need to *infer* which seams were dropouts. Running both
    detectors over the same file turns "is this bridge repairing damage or repairing the
    performance?" from a listening judgement into a matching problem.

    A heuristic hit with no counter event under it is a knob move -- or an attack, or anything else
    the music did -- and bridging it replaces real material with an AR guess that measures ~0.18
    away from what was there, against a CC step of 0.0078. A counter event with no heuristic hit is
    the opposite failure: a real dropout the waveform detector walked past.

    Returns (matched, false_positives, missed), each a list of sample indices, all in heuristic
    coordinates except `missed` which is in counter coordinates. Tolerance is `MERGE_S`, the same
    window the detector already uses to decide two hits are one seam -- a counter event and the
    step it causes are the same sample give or take the resampler's smear.
    """
    tol = int(tol_s * sr)
    c = sorted(counter)
    matched, false_positives, hit = [], [], set()
    for i in heuristic:
        # Nearest counter event, by scan. Both lists are tens of entries on a two-minute take;
        # a bisect here would be faster and harder to read for no measurable gain.
        near = [j for j in c if abs(j - i) <= tol]
        if near:
            matched.append(i)
            hit.add(min(near, key=lambda j: abs(j - i)))
        else:
            false_positives.append(i)
    return matched, false_positives, [j for j in c if j not in hit]


def repair(x, sr, seams, xfade_s=XFADE_S, order=LPC_ORDER):
    """Bridge each seam with an LPC continuation cross-faded into what follows.

    A pitch-period splice is not enough here: the music is four independent parts, so there is no
    one period to splice on, and bridging on the strongest one leaves a quarter of the step behind.
    An AR model fitted to the 40 ms before the seam carries all four voices across it, and the
    cross-fade only has to cover the phase mismatch, not the waveform.
    """
    out = x.copy()
    w = int(xfade_s * sr)
    fit = 4 * order
    done, skipped = [], []
    # Raised cosine rather than linear: at a seam the two sides are phase-incoherent, and a linear
    # ramp leaves a corner in the envelope at each end of the fade -- a smaller click in place of
    # the big one.
    a = (0.5 - 0.5 * np.cos(np.pi * np.arange(1, w + 1) / w))[:, None]
    for i in seams:
        s = i - GUARD                     # rebuild from here, not from the largest jump
        if s - fit < 0 or s + 1 + w > len(x):
            skipped.append(i)
            continue
        cols = [_extrapolate(x[s + 1 - fit:s + 1, c], order, w) for c in range(x.shape[1])]
        if any(c is None for c in cols):
            skipped.append(i)
            continue
        bridge = np.stack(cols, axis=1)
        # An AR continuation can run away when the fit window is degenerate. If it leaves the range
        # the music is actually in, it is not a continuation of anything -- leave the seam alone
        # rather than replace a click with a swoop.
        ceiling = 2.0 * np.abs(x[s + 1 - fit:s + 1 + w]).max()
        if not np.isfinite(bridge).all() or np.abs(bridge).max() > ceiling:
            skipped.append(i)
            continue
        out[s + 1:s + 1 + w] = (1 - a) * bridge + a * x[s + 1:s + 1 + w]
        done.append(i)
    return out, done, skipped


def run_audit(args, x, sub, sr):
    """--audit: report where the waveform detector and the counter disagree. Returns an exit code.

    Deliberately refuses to guess when there is no counter. An audit against nothing is the same
    listening judgement this is meant to replace, dressed up as a number.
    """
    counter = counter_seams(args.infile)
    if counter is None:
        print(f"{args.infile}: no counter on this take, so there is nothing to audit against.\n"
              "  ch2/3 must carry rec_audio.py's 12.288 MHz counter. Without it the only\n"
              "  available answer is the waveform detector's own, which is the thing being\n"
              "  checked.", file=sys.stderr)
        return 2

    found = find_seams(sub.mean(axis=1), sr, args.thresh)
    matched, fp, missed = audit(counter, found, sr)
    print(f"{args.infile}: {len(x) / sr:.3f}s, counter says {len(counter)} dropout(s), "
          f"the waveform detector finds {len(found)} seam(s)", file=sys.stderr)
    # Chronological, not grouped by verdict. Someone reading this has the take open next to it and
    # wants to scrub to a timestamp; three separate ascending lists make that harder than one.
    rows = ([(i, "real    ", "dropout, corroborated") for i in matched]
            + [(i, "SPURIOUS", "no dropout here — a knob move or an attack") for i in fp]
            + [(j, "MISSED  ", "real dropout the waveform detector walked past") for j in missed])
    for i, verdict, why in sorted(rows):
        print(f"  {i / sr:8.3f}s  {verdict} {why}", file=sys.stderr)

    pct = 100.0 * len(fp) / len(found) if found else 0.0
    print(f"\n  {len(matched)} corroborated, {len(fp)} spurious ({pct:.0f}% of what --heuristic "
          f"would bridge), {len(missed)} missed", file=sys.stderr)
    # Only spurious hits fail. The asymmetry is deliberate: a spurious bridge *damages* the take --
    # on this material the AR continuation lands ~0.18 from what was there against a 7-bit CC step
    # of 0.0078, so the repair is about twenty times louder than the thing it was aimed at. A miss
    # merely leaves a click that was already there. One is worse than the other, and a check that
    # scores them the same gives no reason to prefer the counter path.
    if fp:
        print("  --heuristic on this take would rebuild the performance, not the damage.",
              file=sys.stderr)
        return 1
    if missed:
        print("  no spurious bridges, but the waveform detector is not finding everything;",
              file=sys.stderr)
        print("  the counter path repairs the misses too. Not a failure: a miss leaves a click,",
              file=sys.stderr)
        print("  where a spurious bridge would replace good material.", file=sys.stderr)
        return 0
    print("  every seam the waveform detector found is a real dropout, and it found them all.",
          file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("infile")
    ap.add_argument("outfile", nargs="?")
    ap.add_argument("--heuristic", action="store_true",
                    help="find the seams in the waveform even if the take has a counter on it")
    ap.add_argument("--thresh", type=float, default=THRESH_RATIO,
                    help=f"threshold for --heuristic, |diff| over its local median "
                         f"(default {THRESH_RATIO})")
    ap.add_argument("--channels", help="comma-separated channels to detect on and repair "
                                       "(default: all). The Tiliqua's capture is 4ch -- pass 0,1 "
                                       "to leave the counter on ch2/3 alone, and out of the "
                                       "detection, where its sawtooth would swamp the music")
    ap.add_argument("--dry-run", action="store_true", help="report the seams, write nothing")
    ap.add_argument("--audit", action="store_true",
                    help="score the waveform detector against the board's counter and write "
                         "nothing. Exits 1 if any heuristic seam has no dropout under it, i.e. "
                         "if --heuristic on this take would bridge over the performance")
    args = ap.parse_args()

    x, sr = sf.read(args.infile, dtype="float64", always_2d=True)
    chans = ([int(c) for c in args.channels.split(",")] if args.channels
             else list(range(x.shape[1])))
    if any(c < 0 or c >= x.shape[1] for c in chans):
        sys.exit(f"{args.infile} has {x.shape[1]} channels; "
                 f"--channels {args.channels} is out of range")
    if not args.outfile and not (args.dry_run or args.audit):
        sys.exit("outfile is required unless --dry-run or --audit is given")

    sub = x[:, chans]
    if args.audit:
        return run_audit(args, x, sub, sr)

    seams, how = counter_seams(args.infile), "the board's counter"
    if seams is None or args.heuristic:
        seams, how = find_seams(sub.mean(axis=1), sr, args.thresh), "the waveform"
    print(f"{args.infile}: {len(x) / sr:.3f}s, {len(seams)} seam(s) from {how}", file=sys.stderr)
    for i in seams:
        print(f"  {i / sr:8.3f}s  step {np.abs(sub[i + 1] - sub[i]).max():.4f}", file=sys.stderr)
    if args.dry_run:
        return 0

    out = x.copy()
    out[:, chans], done, skipped = repair(sub, sr, seams)
    sf.write(args.outfile, out, sr, subtype="FLOAT")
    print(f"wrote {args.outfile} — bridged {len(done)}"
          + (f", skipped {len(skipped)} (no usable fit)" if skipped else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    # main() returns a code rather than printing and falling off the end, because --audit is a
    # check and a check that cannot fail is decoration.
    sys.exit(main())
