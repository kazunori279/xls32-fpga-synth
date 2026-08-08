"""Generate webui/static/demos.json — the DEMO player's song bank (4 songs).

demos.json is the live bank: the web UI's 💾 TONES button saves edited patches straight back
into it (M31: the browser writes the file itself, through the File System Access API; drop the
download into webui/static/ to keep it). Re-running this generator PRESERVES those edits — a
song's tones (parts + effect amounts) are carried over by name, and only the notes are
regenerated. Delete demos.json first if you want a clean re-bootstrap from the definitions here.

Each song is authored FOR this synth: 4 multitimbral parts (MIDI ch 0-3), each with its own
patch driving the synth's signature CCs (5 waveforms, PWM, sub, cross-osc ring/FM with 8
ratios, LP/HP/BP/notch filter, per-part LFO, filter env), plus the shared effects amounts.
Content is four complete public-domain classical pieces (notes in demo_scores.py), so it's clean
to ship in a public repo. Timbres are drawn from a broad INSTRUMENT LIBRARY.

Format (demos.json):
  { "songs": [ {
      "name","genre","bpm","bars",
      "parts": [patch0..patch3],   # each: { control-id: raw-CC-value }
      "notes": [[t_beats, dur_beats, ch, midi, vel], ...] } ] }
"""
import json, os
try:
    from . import demo_scores as SC        # note data for the four transcribed pieces
except ImportError:
    import demo_scores as SC

def w(v): return (v & 7) << 4      # CC70 wave / CC87 xratio  (3-bit field @ bit4)
def s(v): return (v & 3) << 5      # 2-bit selects
SINE, SAW, SQUARE, TRI, NOISE = w(0), w(1), w(2), w(3), w(4)
LP, HP, BP, NOTCH = s(0), s(1), s(2), s(3)
RING, FM, FMP = s(1), s(2), s(3)   # cross-osc modes

_DEF = dict(wave=SAW, pw=64, detune=s(0), sub=s(0), cutoff=90, reso=30, fmode=s(0),
            fatt=8, fdec=40, fsus=100, frel=40, fdepth=0, aatt=8, adec=40, asus=100, arel=40,
            lforate=40, lfodep=0, trem=s(0), unison=s(0), porta=s(0), xmode=s(0), xdepth=0, xratio=w(0))
def patch(**kw):
    p = dict(_DEF); p.update(kw); return p

# ============================ INSTRUMENT LIBRARY ============================
# Each returns a fresh patch. Grouped by role; genuinely varied waveforms/filters/FM.
# --- basses ---
def SUB():      return patch(wave=SQUARE, sub=s(3), cutoff=46, reso=16, aatt=0, adec=30, asus=100, arel=18)
def REESE():    return patch(wave=SAW, unison=s(3), detune=s(3), cutoff=52, reso=20, fdepth=30, fdec=40, aatt=0, adec=40, asus=92, arel=20, lforate=8, lfodep=12)
def FMBASS():   return patch(wave=SINE, xmode=FM, xdepth=74, xratio=w(2), cutoff=74, aatt=0, adec=36, asus=60, arel=16)
def ACID():     return patch(wave=SAW, cutoff=54, reso=8, fdepth=90, fdec=44, fsus=24, aatt=0, adec=40, asus=70, arel=16, porta=s(1))
def SQBASS():   return patch(wave=SQUARE, pw=40, sub=s(1), cutoff=60, reso=14, aatt=0, adec=34, asus=88, arel=16)
def PBASS():    return patch(wave=SAW, cutoff=64, reso=20, fdepth=50, fdec=26, aatt=0, adec=30, asus=22, arel=16)   # plucked bass
def UPRIGHT():  return patch(wave=TRI, sub=s(1), cutoff=56, reso=10, aatt=2, adec=44, asus=80, arel=44)             # warm acoustic-ish
# --- keys / pads ---
def EP():       return patch(wave=SINE, xmode=FM, xdepth=64, xratio=w(3), cutoff=100, aatt=1, adec=54, asus=44, arel=44)   # DX e-piano
def STRINGS():  return patch(wave=SAW, unison=s(3), detune=s(2), cutoff=72, reso=14, aatt=60, asus=114, arel=100, lforate=20, lfodep=16)
def WARMPAD():  return patch(wave=TRI, unison=s(2), detune=s(1), cutoff=78, reso=10, aatt=70, asus=112, arel=110, lforate=16, lfodep=14)
def ORGAN():    return patch(wave=SQUARE, pw=64, sub=s(2), cutoff=98, reso=8, aatt=2, adec=20, asus=118, arel=10)
def GLASSPAD(): return patch(wave=SINE, xmode=FMP, xdepth=80, xratio=w(4), cutoff=104, aatt=44, adec=90, asus=64, arel=110)
def CHOIR():    return patch(wave=SAW, unison=s(3), detune=s(2), cutoff=72, reso=34, fmode=BP, aatt=60, asus=110, arel=100)  # BP -> vowel-ish
def HARPSI():   return patch(wave=SQUARE, pw=56, cutoff=104, reso=16, aatt=0, adec=40, asus=68, arel=24)              # harpsichord
def BRASS():    return patch(wave=SAW, unison=s(2), detune=s(1), cutoff=70, reso=18, fdepth=60, fatt=18, fdec=50, fsus=80, aatt=14, adec=50, asus=100, arel=40)
# --- leads ---
def SAWLEAD():  return patch(wave=SAW, detune=s(1), cutoff=100, reso=24, aatt=2, adec=44, asus=100, arel=40)
def SQLEAD():   return patch(wave=SQUARE, pw=48, cutoff=98, reso=20, aatt=1, adec=40, asus=100, arel=30)              # chiptune
def PWMLEAD():  return patch(wave=SQUARE, pw=64, cutoff=96, reso=22, aatt=2, asus=100, arel=40, lforate=34, lfodep=26)  # animated PWM
def FLUTE():    return patch(wave=TRI, cutoff=90, reso=12, aatt=5, adec=50, asus=96, arel=50, lforate=42, lfodep=10)   # soft
def RINGLEAD(): return patch(wave=SAW, xmode=RING, xdepth=95, xratio=w(2), cutoff=100, reso=20, aatt=1, asus=100, arel=30)  # metallic
def FMLEAD():   return patch(wave=SINE, xmode=FM, xdepth=100, xratio=w(2), cutoff=104, aatt=1, adec=44, asus=80, arel=36)
def HOOVER():   return patch(wave=SAW, unison=s(3), detune=s(3), cutoff=88, reso=22, fdepth=30, fdec=40, aatt=2, asus=90, arel=30, porta=s(1))
# --- bells / plucks / arps ---
def BELL():     return patch(wave=SINE, xmode=FMP, xdepth=90, xratio=w(4), cutoff=110, aatt=1, adec=80, asus=0, arel=70)
def CLANG():    return patch(wave=SINE, xmode=FMP, xdepth=82, xratio=w(6), cutoff=112, aatt=0, adec=60, asus=0, arel=50)   # 7:1 inharmonic
def GLOCK():    return patch(wave=SINE, xmode=FM, xdepth=70, xratio=w(5), cutoff=112, aatt=0, adec=30, asus=0, arel=24)
def PLUCK():    return patch(wave=SAW, cutoff=92, reso=24, fdepth=40, fdec=30, aatt=0, adec=34, asus=30, arel=22)
def MARIMBA():  return patch(wave=TRI, cutoff=100, reso=14, fmode=HP, aatt=0, adec=28, asus=8, arel=20)                # HP -> woody
def HARP():     return patch(wave=TRI, cutoff=96, reso=10, aatt=0, adec=50, asus=20, arel=60)
def STAB():     return patch(wave=SAW, unison=s(3), detune=s(2), cutoff=86, reso=20, aatt=0, adec=30, asus=60, arel=22)
# --- percussion / texture (noise) ---
def HAT():      return patch(wave=NOISE, cutoff=122, reso=20, fmode=HP, aatt=0, adec=8, asus=0, arel=6)
def WIND():     return patch(wave=NOISE, cutoff=68, reso=10, fmode=BP, aatt=90, asus=90, arel=115)                    # airy drone
# --- ambient-tuned (long env) ---
def APAD():     return patch(wave=SAW, unison=s(3), detune=s(2), cutoff=64, reso=14, aatt=110, asus=118, arel=122, lforate=18, lfodep=26)
def APADW():    return patch(wave=TRI, unison=s(2), detune=s(1), cutoff=66, reso=10, aatt=118, asus=116, arel=125, lforate=14, lfodep=22)
def AGLASS():   return patch(wave=SINE, xmode=FMP, xdepth=78, xratio=w(4), cutoff=100, aatt=60, adec=110, asus=60, arel=120)
def ACHOIR():   return patch(wave=SAW, unison=s(3), detune=s(2), cutoff=70, reso=30, fmode=BP, aatt=110, asus=112, arel=122)
def ABELL():    return patch(wave=SINE, xmode=FM, xdepth=66, xratio=w(3), cutoff=100, aatt=3, adec=110, asus=16, arel=120)
def ACLANG():   return patch(wave=SINE, xmode=FMP, xdepth=60, xratio=w(6), cutoff=112, aatt=6, adec=100, asus=0, arel=110)
def ADRONE():   return patch(wave=SQUARE, sub=s(3), cutoff=42, reso=10, aatt=80, asus=110, arel=125)
def ASPARK():   return patch(wave=SINE, xmode=FMP, xdepth=54, xratio=w(5), cutoff=112, aatt=6, adec=90, asus=0, arel=95, lforate=22, lfodep=30)

# ---- note / music helpers ----
NAMES = {n: i for i, n in enumerate(["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"])}
NAMES.update({"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10})
def n(name, octv):  return 12 * (octv + 1) + NAMES[name]
CH = {"maj": [0,4,7], "min": [0,3,7], "maj7": [0,4,7,11], "min7": [0,3,7,10],
      "dom7": [0,4,7,10], "sus4": [0,5,7], "sus2": [0,2,7], "min9": [0,3,7,10,14],
      "add9": [0,4,7,14], "maj9": [0,4,7,11,14], "dim": [0,3,6], "5": [0,7], "6": [0,4,7,9]}
def chord(root, quality, octv=4):  return [n(root, octv) + iv for iv in CH[quality]]

def hold(out, ch, notes, t, dur, vel=90):
    for m in notes: out.append([round(t,4), round(dur,4), ch, m, vel])
def arp(out, ch, notes, t, count, step, dur=None, vel=90, updown=False):
    seq = notes + notes[-2:0:-1] if updown else notes
    for i in range(count): out.append([round(t+i*step,4), round(dur or step*0.9,4), ch, seq[i % len(seq)], vel])
def line(out, ch, seq, t, step, vel=90):
    for m, d in seq:
        if m is not None: out.append([round(t,4), round(d*step*0.95,4), ch, m, vel])
        t += d * step

# M31 removed four more helpers from here -- `pulse`, `song`, `prog_song`, `bass_root`. They were
# the scaffolding the procedural genre composers stood on (drive a chord progression bar by bar,
# append straight into `songs`), and nothing transcribed by hand ever used them.

songs = []
def mk(name, bpm, bars, parts, notes):   # pure: RETURN a Classical song dict rather than append
    return {"name": name, "genre": "Classical", "bpm": bpm, "bars": bars, "parts": parts, "notes": notes}
def fx_amounts(sg, **kw):                    # override the genre's default effect amounts
    sg.update(kw); return sg


# ==================== CLASSICAL (12) ====================  parts = [ch0, ch1, ch2, ch3]
def note_name(tok):                     # "C#4" / "Bb3" -> midi
    return n(tok[:-1], int(tok[-1]))

def bach_prelude():
    """BWV 846 complete, all 35 bars. Nothing but the one broken-chord figure for 32 bars,
    a two-bar written-out arpeggio, then the final chord — the whole piece is a chord chart."""
    out = []
    for bi, spell in enumerate(SC.BWV846):
        t, notes = bi * 4.0, [note_name(x) for x in spell.split()]
        # walking quarter-note bass: root + a chord tone (an octave down) instead of one whole note
        lo, mid = notes[0]-12, notes[2]-12
        for bq, (bp, bv) in enumerate([(lo,80),(mid,60),(lo,74),(mid,62)]):
            out.append([round(t+bq,4), 0.92, 2, bp, bv])
        hold(out, 1, notes[1:3], t, 4.0, 54)
        fig = notes + notes[2:]                      # n1 n2 n3 n4 n5 n3 n4 n5, as Bach wrote it
        for half in range(2):
            for i, m in enumerate(fig): out.append([round(t+half*2+i*0.25,4), 0.24, 0, m, 82])
        hold(out, 3, [notes[4]], t, 4.0, 55)         # bell rings the chord's top note every bar
    for k, spell in enumerate(SC.BWV846_CODA):       # bars 33-34: the figure dissolves, C pedal below
        t, notes = (32 + k) * 4.0, [note_name(x) for x in spell.split()]
        for i, m in enumerate(notes): out.append([round(t+i*0.25,4), 0.24, 0, m, 82])
        hold(out, 2, [notes[0]-12], t, 4.0, 78)
        hold(out, 1, notes[2:4], t, 4.0, 54)
        hold(out, 3, [notes[5]], t, 4.0, 55)
    t = 34 * 4.0                                     # bar 35: the C major chord, let it ring
    hold(out, 0, [n("C",3)] + chord("C", "maj", 4) + [n("C",5), n("E",5)], t, 4.0, 76)
    hold(out, 1, chord("C", "maj", 4), t, 4.0, 54)
    hold(out, 2, [n("C",2)], t, 4.0, 82)
    hold(out, 3, [n("C",5)], t, 4.0, 58)
    return mk("Bach · Prelude in C", 76, 35, [HARPSI(), STRINGS(), UPRIGHT(), BELL()], out)

def goldberg_aria():
    """Aria from the Goldberg Variations, first half (16 bars of 3/4). `bars` counts 4-beat
    bars because that's what the web player loops on, so 48 beats -> 12."""
    out = SC.unpack(SC.GOLDBERG_MEL, 0, 84, transpose=-12)
    for bi, (root, qual) in enumerate(SC.GOLDBERG_HARM):
        t = bi * 3.0
        hold(out, 1, chord(root, qual, 3)[:3], t, 2.9, 44)          # pad on the ground-bass harmony
        hold(out, 2, [note_name(SC.GOLDBERG_BASS[bi])], t, 1.9, 80)  # the bass the 30 variations share
        out.append([round(t+2,4), 0.9, 2, note_name(SC.GOLDBERG_BASS[bi]) + 12, 58])
        out.append([round(t+1,4), 1.0, 3, n(root, 5), 34])           # sarabande stress on beat 2
    sg = mk("Bach · Goldberg Aria", 58, 12, [HARPSI(), WARMPAD(), UPRIGHT(), GLOCK()], out)
    return fx_amounts(sg, reverb=96, room=s(2), chorusd=34)      # small room, a little shimmer

def the_swan():
    """Le cygne, 26 bars of 6/4 = 156 beats -> 39 player-bars. The cello sings from bar 2 over
    the piano's rippling sixteenths, which is the whole point of the piece."""
    bass = SC.unpack(SC.SWAN_BASS, 2, 66)
    out  = SC.unpack(SC.SWAN_MEL, 0, 92)
    out += SC.unpack(SC.SWAN_ARP, 1, 46)             # the water
    out += bass
    seen = set()                                     # pad: hold each bar's bass downbeat under it,
    for t, _dur, _ch, midi, _vel in sorted(bass):    # except where the LH climbs out of the bass
        bar = int(t // 6)                            # register (last bar) and there's nothing to hold
        if bar not in seen:
            seen.add(bar)
            if midi <= n("G", 3): out.append([round(bar * 6.0, 4), 6.0, 3, midi, 36])
    sg = mk("Saint-Saëns · Le Cygne", 70, 39, [FLUTE(), HARP(), UPRIGHT(), WARMPAD()], out)
    return fx_amounts(sg, reverb=118, room=s(3), chorusd=58, echod=44, dtime=80)   # big wet hall

def vivaldi_winter():
    """L'inverno II. Largo, all 18 bars. Solo violin over pizzicato rain — the pizzicato is a
    double stop in the score, so it goes out as two voices (dropped an octave to clear the solo)."""
    out  = SC.unpack(SC.WINTER_MEL, 0, 96)
    out += SC.unpack(SC.WINTER_PIZ, 1, 62, transpose=-12)
    out += SC.unpack(SC.WINTER_BASS, 2, 72)
    out += SC.unpack(SC.WINTER_PIZ2, 3, 54, transpose=-12)
    sg = mk("Vivaldi · Winter (Largo)", 44, 18, [SAWLEAD(), PLUCK(), UPRIGHT(), HARP()], out)
    return fx_amounts(sg, reverb=104, room=s(2), chorusd=48)     # ensemble spread on the strings

def ode_to_joy():
    mel = ["E","E","F","G","G","F","E","D","C","C","D","E","E","D","D",None,
           "E","E","F","G","G","F","E","D","C","C","D","E","D","C","C",None]
    prog = [("C","maj"),("G","maj"),("C","maj"),("G","maj"),("C","maj"),("G","maj"),("C","maj"),("G","maj")]
    out = []
    for i, nm in enumerate(mel):
        if nm is not None: out.append([round(i*1.0,4), 0.9, 0, n(nm,5), 88])
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 1, chord(r,q,4), t, 4.0, 52); hold(out, 2, [n(r,3)], t, 4.0, 82)
        arp(out, 3, chord(r,q,5), t, 8, 0.5, dur=0.4, vel=46)
    return mk("Beethoven · Ode to Joy", 92, 8, [BRASS(), CHOIR(), SUB(), GLOCK()], out)

def canon_in_d():
    prog = [("D","maj"),("A","maj"),("B","min"),("F#","min"),("G","maj"),("D","maj"),("G","maj"),("A","maj")]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 1, chord(r,q,4), t, 4.0, 52); hold(out, 2, [n(r,3)], t, 4.0, 78)
        arp(out, 0, chord(r,q,5), t, 8, 0.5, dur=0.45, vel=80)
        if bari >= 4: arp(out, 3, chord(r,q,6), t, 16, 0.25, dur=0.2, vel=44)
    return mk("Pachelbel · Canon in D", 68, 8, [HARP(), STRINGS(), UPRIGHT(), BELL()], out)

def fur_elise():
    m = "E5 D#5 E5 D#5 E5 B4 D5 C5 A4 . C4 E4 A4 B4 . E4 G#4 B4 C5 . E4 E5 D#5 E5 D#5 E5 B4 D5 C5 A4 . ."
    seq = [(None, 0.5) if t == "." else (n(t[:-1], int(t[-1])), 0.5) for t in m.split()]
    out = []; line(out, 0, seq, 0.0, 1.0, vel=86)
    # harmony ALIGNED to the melody's phrases: Am, then E under the E-major (G#-B-C) phrase, then Am.
    # (the old fixed Am/E-per-bar put G# over an Am bar -> clash.)
    for start, dur, r, q in [(0.0, 7.5, "A", "min"), (7.5, 2.0, "E", "maj"), (9.5, 6.5, "A", "min")]:
        hold(out, 2, [n(r, 3)], start, dur, 72)                                   # left-hand bass
        arp(out, 1, chord(r, q, 3), start, int(round(dur * 2)), 0.5, dur=0.45, vel=42)  # broken-chord accomp
    return mk("Beethoven · Für Elise", 80, 4, [EP(), HARP(), UPRIGHT(), GLOCK()], out)

def baroque_air():
    # chord-derived (melody = chord tones) so it is always consonant — replaces a mis-metered tune
    prog = [("G","maj"),("E","min"),("C","maj"),("D","maj"),("G","maj"),("C","maj"),("D","maj"),("G","maj")]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 2, [n(r,3)], t, 4.0, 72); hold(out, 1, chord(r,q,4), t, 4.0, 44)
        arp(out, 0, chord(r,q,5), t, 8, 0.5, dur=0.45, vel=80, updown=True)      # flowing melodic line
        arp(out, 3, chord(r,q,6), t, 4, 1.0, dur=0.8, vel=40)                    # gentle upper voice
    return mk("Baroque Air", 72, 8, [FLUTE(), STRINGS(), UPRIGHT(), HARP()], out)

def moonlight():
    prog = [("C#","min"),("C#","min"),("A","maj"),("F#","min"),("G#","maj"),("C#","min"),("G#","maj"),("C#","min")]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0; tones = chord(r,q,4); trip = [tones[0], tones[1%len(tones)], tones[2%len(tones)]]
        for k in range(12): out.append([round(t+k*(4.0/12),4), 0.3, 0, trip[k%3], 56])
        hold(out, 2, [n(r,3)], t, 4.0, 70); hold(out, 1, [chord(r,q,5)[0]], t, 4.0, 44)
    return mk("Beethoven · Moonlight", 54, 8, [EP(), ABELL(), ADRONE(), WARMPAD()], out)

def gymnopedie():
    prog = [("G","maj7"),("D","maj7"),("G","maj7"),("D","maj7")]
    mel = [n("F#",5),n("A",5),n("B",5),n("A",5),n("G",5),n("F#",5),n("D",5),n("E",5)]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 2, [n(r,3)], t, 1.0, 66); hold(out, 1, chord(r,q,4), t+2.0, 1.6, 46)
        out.append([round(t+1.0,4), 2.6, 0, mel[(bari*2)%len(mel)], 62])
        out.append([round(t+3.0,4), 0.9, 0, mel[(bari*2+1)%len(mel)], 56])
    return mk("Satie · Gymnopédie", 66, 4, [FLUTE(), WARMPAD(), UPRIGHT(), GLOCK()], out)

def aria_am():
    # chord-derived aria (melody from chord tones) — always consonant
    prog = [("A","min"),("D","min"),("E","maj"),("A","min"),("F","maj"),("C","maj"),("E","maj"),("A","min")]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 2, [n(r,3)], t, 4.0, 72); arp(out, 1, chord(r,q,3), t, 8, 0.5, dur=0.45, vel=42)
        tones = chord(r, q, 5)
        out.append([round(t,4), 1.8, 0, tones[-1], 78])                          # two-note chord-tone melody
        out.append([round(t+2,4), 1.8, 0, tones[max(0,len(tones)-2)], 72])
        arp(out, 3, chord(r,q,6), t, 4, 1.0, dur=0.8, vel=38)
    return mk("Aria in A minor", 84, 8, [EP(), HARP(), UPRIGHT(), GLOCK()], out)

# --- more public-domain themes (melody on ch0 + harmony ALIGNED to the melody -> consonant) ---
def pd_mel(name, bpm, bars, parts, mel, changes):
    out = []; t = 0.0
    for m, d in mel:
        if m is not None: out.append([round(t,4), round(d*0.92,4), 0, m, 84])
        t += d
    for start, dur, r, q in changes:
        hold(out, 2, [n(r,3)], start, dur, 70)
        arp(out, 1, chord(r,q,3), start, max(1,int(round(dur*2))), 0.5, dur=0.45, vel=40)
        arp(out, 3, chord(r,q,5), start, max(1,int(round(dur))), 1.0, dur=0.8, vel=34)
    return mk(name, bpm, bars, parts, out)

def twinkle():
    N = lambda s: n(s, 4)
    mel = [(N("C"),1),(N("C"),1),(N("G"),1),(N("G"),1), (N("A"),1),(N("A"),1),(N("G"),2),
           (N("F"),1),(N("F"),1),(N("E"),1),(N("E"),1), (N("D"),1),(N("D"),1),(N("C"),2),
           (N("G"),1),(N("G"),1),(N("F"),1),(N("F"),1), (N("E"),1),(N("E"),1),(N("D"),2),
           (N("G"),1),(N("G"),1),(N("F"),1),(N("F"),1), (N("E"),1),(N("E"),1),(N("D"),2)]
    ch = [(0,4,"C","maj"),(4,4,"C","maj"),(8,4,"F","maj"),(12,4,"C","maj"),
          (16,4,"G","maj"),(20,4,"C","maj"),(24,4,"G","maj"),(28,4,"C","maj")]
    return pd_mel("Mozart · Twinkle", 108, 8, [HARPSI(), STRINGS(), UPRIGHT(), GLOCK()], mel, ch)

def beethoven5():
    G,Eb,F,D = n("G",4), n("Eb",4), n("F",4), n("D",4)
    mel = [(G,0.5),(G,0.5),(G,0.5),(None,0.5),(Eb,2.0),
           (F,0.5),(F,0.5),(F,0.5),(None,0.5),(D,2.0)]
    ch = [(0,4,"C","min"),(4,4,"G","maj")]     # Cm (Eb,G) then V (F,D) -> consonant
    return pd_mel("Beethoven · Symphony No.5", 108, 2, [BRASS(), STRINGS(), UPRIGHT(), GLOCK()], mel, ch)

def eine_kleine():
    # G/D arpeggio texture (the piece is broken chords) -> derived, always consonant
    prog = [("G","maj"),("D","dom7"),("G","maj"),("D","dom7"),("G","maj"),("C","maj"),("D","dom7"),("G","maj")]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 2, [n(r,3)], t, 4.0, 72); hold(out, 1, chord(r,q,4), t, 4.0, 44)
        arp(out, 0, chord(r,q,5), t, 8, 0.5, dur=0.42, vel=82, updown=True)
        arp(out, 3, chord(r,q,6), t, 4, 1.0, dur=0.8, vel=38)
    return mk("Mozart · Eine kleine Nachtmusik", 132, 8, [SAWLEAD(), STRINGS(), UPRIGHT(), HARP()], out)

# the four full transcriptions, and the whole bank. Until M31 there were three more, generated
# rather than transcribed -- one Techno, one Pop, one Ambient, each the first draw from a seeded
# random composer that the UI's per-song "replace" button could re-roll. The standalone UI has no
# server to re-roll them on, and four hand-checked pieces demo the engine better than seven of
# which three are dice, so the composer and its genres went out together. See DEVELOPMENT.md.
CLASSICAL_BANK = [bach_prelude, goldberg_aria, the_swan, vivaldi_winter]


# --- per-genre effect amounts (raw CC values) so each demo shows off the effects -------------
# reverb wet (93), room size (91), chorus depth (94), delay depth (95), delay time (82).
# Attached to every song by genre so the demo player restores the full effect state.
# (There is no mode byte: the old CC83 dry/chorus/delay/both select is gone — see synthspec.py.)
# Only Classical ships now; the table stays a table because a song is free to declare any genre
# and _DEFAULT_FX is a poorer answer than a named one.
FX_BY_GENRE = {
    "Classical": {"reverb": 88,  "room": s(1), "chorusd": 0,  "echod": 0,   "dtime": 63},  # hall tail
}
_DEFAULT_FX = {"reverb": 60, "room": s(1), "chorusd": 40, "echod": 40, "dtime": 63}
def add_effects(sg):
    """Attach the genre's effect amounts to a song (without clobbering any it already set)."""
    for k, v in FX_BY_GENRE.get(sg.get("genre"), _DEFAULT_FX).items():
        sg.setdefault(k, v)
    return sg

# what the web UI's 💾 TONES button owns: the sound, not the score. Re-running the generator
# keeps these as they are in demos.json and refreshes everything else.
TONE_KEYS = ("parts", "reverb", "room", "chorusd", "echod", "dtime")
def keep_tones(songs, path):
    """Carry hand-tuned tones over from the existing demos.json, matched by song name."""
    if not os.path.exists(path): return songs
    old = {s_["name"]: s_ for s_ in json.load(open(path)).get("songs", [])}
    for sg in songs:
        prev = old.get(sg["name"])
        if prev: sg.update({k: prev[k] for k in TONE_KEYS if k in prev})
    return songs

if __name__ == "__main__":
    # The bank: the four full classical transcriptions, in order.
    for _f in CLASSICAL_BANK:
        songs.append(_f())
    for _sg in songs: add_effects(_sg)                       # attach per-genre effect amounts
    out_path = os.path.join(os.path.dirname(__file__), "..", "webui", "static", "demos.json")
    keep_tones(songs, out_path)
    for _sg in songs:                                        # round(t,4) yields floats; the UI's
        for _nt in _sg["notes"]:                             # own save writes 6, not 6.0 — match it
            _nt[0], _nt[1] = (int(x) if float(x).is_integer() else x for x in _nt[:2])
    with open(out_path, "w") as f:
        json.dump({"songs": songs}, f, indent=1)
    by = {}
    for s_ in songs: by[s_["genre"]] = by.get(s_["genre"], 0) + 1
    print(f"wrote {len(songs)} demos -> {os.path.relpath(out_path)}   {by}")
    allp = [(p["wave"], p["xmode"], p["fmode"]) for s_ in songs for p in s_["parts"]]
    print(f"distinct part-timbres used: {len(set(allp))} across {len(allp)} part-slots")
