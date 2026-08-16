"""The note-on/note-off protocol a target and a render have to agree on before they are compared.

This used to be three numbers in three files that did not match. `search.py` rendered a 1.6 s gate
with a 0.3 s tail; `soundfont.py` rendered its targets with a 1.5 s gate and a 0.5 s tail; NSynth
ships 4 s clips that hold for about 3 s. `loss.py` truncates to the shorter of the two signals, so
nothing crashed and nothing said anything -- the soundfont bank was fitted with the target's
release starting 100 ms before the render's, and the NSynth bank was fitted with 0.3 s of *our
release* scored against 0.3 s of *the target still sustaining*.

Under a loss that averages every frame equally that is a smear. Under a loss with a release
segment it is a straight bug: the segment boundaries would be in different places in the two
signals being compared. So the window becomes one declaration, per corpus, that both sides read.

A corpus declares WINDOW = (gate_s, tail_s):

    gate_s   note-on to note-off. The target must actually be held this long.
    tail_s   note-off to end of the comparison. 0.0 means the corpus cannot show a release inside
             a window worth rendering -- NSynth releases at ~3 s, and stretching every render to
             3.4 s to catch it would cost 1.8x on every candidate in every search for one segment.
             Those corpora are compared over the sustain only, and SEGMENTS drops the R term
             rather than inventing one.

`nsynth` is the one that pays for this: its targets get cropped to a held note and the release is
simply not part of the fit. That is a real limitation of that bank and is better stated here than
discovered later in a spectrogram.
"""

# The default, and what `soundfont` uses: long enough that a pad reaches its sustain, short enough
# that a search is affordable. Not tuned -- inherited from search.py, kept so the shipped bank's
# numbers stay comparable to the ones in DEVELOPMENT.md.
GATE_S, TAIL_S = 1.6, 0.3

# Attack/decay boundary. There is no principled way to find the D->S knee from timing alone (a
# decaying target never reaches a sustain at all), and taking it from the *candidate's* aatt/adec
# would let the optimizer move the goalposts: a patch could score well by placing its own segment
# boundary where its error is small. So it is a constant, and it is the target's job to be a real
# note. 250 ms covers a struck attack and the first part of the decay on everything we fit.
AD_S = 0.25


def window(source):
    """(gate_s, tail_s) for a corpus module, by name or by module. Unknown -> the default."""
    if isinstance(source, str):
        import importlib
        try:
            source = importlib.import_module(source)
        except Exception:
            return GATE_S, TAIL_S
    return tuple(getattr(source, "WINDOW", (GATE_S, TAIL_S)))


def segments(gate_s=GATE_S, tail_s=TAIL_S, ad_s=AD_S):
    """[(name, t0, t1)] in seconds. R is dropped when the corpus has no release in the window."""
    segs = [("AD", 0.0, min(ad_s, gate_s)), ("S", min(ad_s, gate_s), gate_s)]
    if tail_s > 0:
        segs.append(("R", gate_s, gate_s + tail_s))
    return segs
