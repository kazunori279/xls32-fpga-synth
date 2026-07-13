"""Generate webui/static/demos.json — the DEMO player's song bank (4 songs, one per genre).

NOTE: demos.json is the SINGLE SOURCE OF TRUTH for the demo bank. The web UI's 💾 TONES
button saves edited songs straight back into it (via /api/demo_save), so re-running this
generator OVERWRITES those edits — regenerate only to re-bootstrap, then re-tune in the UI.

Each song is authored FOR this synth: 4 multitimbral parts (MIDI ch 0-3), each with its own
patch driving the synth's signature CCs (5 waveforms, PWM, sub, cross-osc ring/FM with 8
ratios, LP/HP/BP/notch filter, per-part LFO, filter env), plus the shared effects amounts.
Content is one public-domain classical theme + one procedural song per other genre, so it's
clean to ship in a public repo. Timbres are drawn from a broad INSTRUMENT LIBRARY.

Format (demos.json):
  { "songs": [ {
      "name","genre","bpm","bars","fx": <raw CC83>,
      "parts": [patch0..patch3],   # each: { control-id: raw-CC-value }
      "notes": [[t_beats, dur_beats, ch, midi, vel], ...] } ] }
"""
import json, os

def w(v): return (v & 7) << 4      # CC70 wave / CC83 fx / CC87 xratio  (3-bit @ bit4)
def s(v): return (v & 3) << 5      # 2-bit selects
SINE, SAW, SQUARE, TRI, NOISE = w(0), w(1), w(2), w(3), w(4)
DRY, CHORUS, ECHO, BOTH = w(0), w(1), w(2), w(3)
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
def pulse(out, ch, midi, t, count, step, dur=None, vel=100):
    for i in range(count): out.append([round(t+i*step,4), round(dur or step*0.5,4), ch, midi, vel])

songs = []
def song(name, genre, bpm, bars, fx, parts, notes):
    songs.append({"name": name, "genre": genre, "bpm": bpm, "bars": bars, "fx": fx, "parts": parts, "notes": notes})
def mk(name, bpm, bars, fx, parts, notes):   # pure: RETURN a Classical song dict (reused by make_random)
    return {"name": name, "genre": "Classical", "bpm": bpm, "bars": bars, "fx": fx, "parts": parts, "notes": notes}
def prog_song(name, genre, bpm, fx, parts, prog, build):
    out = []
    for bari, (root, qual) in enumerate(prog): build(out, bari, root, qual, bari * 4.0)
    song(name, genre, bpm, len(prog), fx, parts, out)
def bass_root(out, ch, r, octv, t, mode, vel=104):
    if mode == "8th":   pulse(out, ch, n(r,octv), t, 8, 0.5, dur=0.42, vel=vel)
    elif mode == "4":   pulse(out, ch, n(r,octv), t, 4, 1.0, dur=0.7, vel=vel)
    elif mode == "16":  pulse(out, ch, n(r,octv), t, 16, 0.25, dur=0.2, vel=vel)
    elif mode == "off": [out.append([round(t+b+0.5,4), 0.4, ch, n(r,octv), vel]) for b in range(4)]
    elif mode == "hold": hold(out, ch, [n(r,octv)], t, 4.0, vel)
    elif mode == "walk":
        for i, m in enumerate([n(r,octv), n(r,octv), n(r,octv)+7, n(r,octv)]): out.append([round(t+i,4), 0.9, ch, m, vel])


# ==================== CLASSICAL (8) ====================  parts = [ch0, ch1, ch2, ch3]
def bach_prelude():
    bars = [[n("C",4),n("E",4),n("G",4),n("C",5),n("E",5)],[n("C",4),n("D",4),n("A",4),n("D",5),n("F",5)],
            [n("B",3),n("D",4),n("G",4),n("D",5),n("F",5)],[n("C",4),n("E",4),n("G",4),n("C",5),n("E",5)],
            [n("C",4),n("E",4),n("A",4),n("E",5),n("A",5)],[n("C",4),n("D",4),n("F#",4),n("A",4),n("D",5)],
            [n("B",3),n("D",4),n("G",4),n("D",5),n("G",5)],[n("C",4),n("E",4),n("G",4),n("C",5),n("E",5)]]
    out = []
    for bi, notes in enumerate(bars):
        t = bi * 4.0
        # walking quarter-note bass: root + a chord tone (an octave down) instead of one whole note
        lo, mid = notes[0]-12, notes[2]-12
        for bq, (bp, bv) in enumerate([(lo,80),(mid,60),(lo,74),(mid,62)]):
            out.append([round(t+bq,4), 0.92, 2, bp, bv])
        hold(out, 1, notes[1:3], t, 4.0, 54)
        fig = [notes[0],notes[2],notes[3],notes[4],notes[3],notes[2],notes[3],notes[4]]
        for half in range(2):
            for i, m in enumerate(fig): out.append([round(t+half*2+i*0.25,4), 0.24, 0, m, 82])
        hold(out, 3, [notes[4]], t, 4.0, 55)         # bell rings the chord's top note every bar
    return mk("Bach · Prelude in C", 76, 8, CHORUS, [HARPSI(), STRINGS(), UPRIGHT(), BELL()], out)

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
    return mk("Beethoven · Ode to Joy", 92, 8, CHORUS, [BRASS(), CHOIR(), SUB(), GLOCK()], out)

def canon_in_d():
    prog = [("D","maj"),("A","maj"),("B","min"),("F#","min"),("G","maj"),("D","maj"),("G","maj"),("A","maj")]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 1, chord(r,q,4), t, 4.0, 52); hold(out, 2, [n(r,3)], t, 4.0, 78)
        arp(out, 0, chord(r,q,5), t, 8, 0.5, dur=0.45, vel=80)
        if bari >= 4: arp(out, 3, chord(r,q,6), t, 16, 0.25, dur=0.2, vel=44)
    return mk("Pachelbel · Canon in D", 68, 8, CHORUS, [HARP(), STRINGS(), UPRIGHT(), BELL()], out)

def fur_elise():
    m = "E5 D#5 E5 D#5 E5 B4 D5 C5 A4 . C4 E4 A4 B4 . E4 G#4 B4 C5 . E4 E5 D#5 E5 D#5 E5 B4 D5 C5 A4 . ."
    seq = [(None, 0.5) if t == "." else (n(t[:-1], int(t[-1])), 0.5) for t in m.split()]
    out = []; line(out, 0, seq, 0.0, 1.0, vel=86)
    # harmony ALIGNED to the melody's phrases: Am, then E under the E-major (G#-B-C) phrase, then Am.
    # (the old fixed Am/E-per-bar put G# over an Am bar -> clash.)
    for start, dur, r, q in [(0.0, 7.5, "A", "min"), (7.5, 2.0, "E", "maj"), (9.5, 6.5, "A", "min")]:
        hold(out, 2, [n(r, 3)], start, dur, 72)                                   # left-hand bass
        arp(out, 1, chord(r, q, 3), start, int(round(dur * 2)), 0.5, dur=0.45, vel=42)  # broken-chord accomp
    return mk("Beethoven · Für Elise", 80, 4, CHORUS, [EP(), HARP(), UPRIGHT(), GLOCK()], out)

def baroque_air():
    # chord-derived (melody = chord tones) so it is always consonant — replaces a mis-metered tune
    prog = [("G","maj"),("E","min"),("C","maj"),("D","maj"),("G","maj"),("C","maj"),("D","maj"),("G","maj")]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 2, [n(r,3)], t, 4.0, 72); hold(out, 1, chord(r,q,4), t, 4.0, 44)
        arp(out, 0, chord(r,q,5), t, 8, 0.5, dur=0.45, vel=80, updown=True)      # flowing melodic line
        arp(out, 3, chord(r,q,6), t, 4, 1.0, dur=0.8, vel=40)                    # gentle upper voice
    return mk("Baroque Air", 72, 8, CHORUS, [FLUTE(), STRINGS(), UPRIGHT(), HARP()], out)

def moonlight():
    prog = [("C#","min"),("C#","min"),("A","maj"),("F#","min"),("G#","maj"),("C#","min"),("G#","maj"),("C#","min")]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0; tones = chord(r,q,4); trip = [tones[0], tones[1%len(tones)], tones[2%len(tones)]]
        for k in range(12): out.append([round(t+k*(4.0/12),4), 0.3, 0, trip[k%3], 56])
        hold(out, 2, [n(r,3)], t, 4.0, 70); hold(out, 1, [chord(r,q,5)[0]], t, 4.0, 44)
    return mk("Beethoven · Moonlight", 54, 8, BOTH, [EP(), ABELL(), ADRONE(), WARMPAD()], out)

def gymnopedie():
    prog = [("G","maj7"),("D","maj7"),("G","maj7"),("D","maj7")]
    mel = [n("F#",5),n("A",5),n("B",5),n("A",5),n("G",5),n("F#",5),n("D",5),n("E",5)]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 2, [n(r,3)], t, 1.0, 66); hold(out, 1, chord(r,q,4), t+2.0, 1.6, 46)
        out.append([round(t+1.0,4), 2.6, 0, mel[(bari*2)%len(mel)], 62])
        out.append([round(t+3.0,4), 0.9, 0, mel[(bari*2+1)%len(mel)], 56])
    return mk("Satie · Gymnopédie", 66, 4, BOTH, [FLUTE(), WARMPAD(), UPRIGHT(), GLOCK()], out)

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
    return mk("Aria in A minor", 84, 8, CHORUS, [EP(), HARP(), UPRIGHT(), GLOCK()], out)

# --- more public-domain themes (melody on ch0 + harmony ALIGNED to the melody -> consonant) ---
def pd_mel(name, bpm, bars, fx, parts, mel, changes):
    out = []; t = 0.0
    for m, d in mel:
        if m is not None: out.append([round(t,4), round(d*0.92,4), 0, m, 84])
        t += d
    for start, dur, r, q in changes:
        hold(out, 2, [n(r,3)], start, dur, 70)
        arp(out, 1, chord(r,q,3), start, max(1,int(round(dur*2))), 0.5, dur=0.45, vel=40)
        arp(out, 3, chord(r,q,5), start, max(1,int(round(dur))), 1.0, dur=0.8, vel=34)
    return mk(name, bpm, bars, fx, parts, out)

def twinkle():
    N = lambda s: n(s, 4)
    mel = [(N("C"),1),(N("C"),1),(N("G"),1),(N("G"),1), (N("A"),1),(N("A"),1),(N("G"),2),
           (N("F"),1),(N("F"),1),(N("E"),1),(N("E"),1), (N("D"),1),(N("D"),1),(N("C"),2),
           (N("G"),1),(N("G"),1),(N("F"),1),(N("F"),1), (N("E"),1),(N("E"),1),(N("D"),2),
           (N("G"),1),(N("G"),1),(N("F"),1),(N("F"),1), (N("E"),1),(N("E"),1),(N("D"),2)]
    ch = [(0,4,"C","maj"),(4,4,"C","maj"),(8,4,"F","maj"),(12,4,"C","maj"),
          (16,4,"G","maj"),(20,4,"C","maj"),(24,4,"G","maj"),(28,4,"C","maj")]
    return pd_mel("Mozart · Twinkle", 108, 8, CHORUS, [HARPSI(), STRINGS(), UPRIGHT(), GLOCK()], mel, ch)

def beethoven5():
    G,Eb,F,D = n("G",4), n("Eb",4), n("F",4), n("D",4)
    mel = [(G,0.5),(G,0.5),(G,0.5),(None,0.5),(Eb,2.0),
           (F,0.5),(F,0.5),(F,0.5),(None,0.5),(D,2.0)]
    ch = [(0,4,"C","min"),(4,4,"G","maj")]     # Cm (Eb,G) then V (F,D) -> consonant
    return pd_mel("Beethoven · Symphony No.5", 108, 2, BOTH, [BRASS(), STRINGS(), UPRIGHT(), GLOCK()], mel, ch)

def eine_kleine():
    # G/D arpeggio texture (the piece is broken chords) -> derived, always consonant
    prog = [("G","maj"),("D","dom7"),("G","maj"),("D","dom7"),("G","maj"),("C","maj"),("D","dom7"),("G","maj")]
    out = []
    for bari, (r, q) in enumerate(prog):
        t = bari * 4.0
        hold(out, 2, [n(r,3)], t, 4.0, 72); hold(out, 1, chord(r,q,4), t, 4.0, 44)
        arp(out, 0, chord(r,q,5), t, 8, 0.5, dur=0.42, vel=82, updown=True)
        arp(out, 3, chord(r,q,6), t, 4, 1.0, dur=0.8, vel=38)
    return mk("Mozart · Eine kleine Nachtmusik", 132, 8, CHORUS, [SAWLEAD(), STRINGS(), UPRIGHT(), HARP()], out)

# the public-domain rotation the classical "replace" pulls from (all verified consonant)
CLASSICAL_PD = [bach_prelude, ode_to_joy, canon_in_d, fur_elise, moonlight, gymnopedie,
                twinkle, beethoven5, eine_kleine]


# ==================== TECHNO (8) ====================
def acid_drive():
    prog = [("A","min"),("A","min"),("C","maj"),("G","min")]
    def build(out, bari, r, q, t):
        base = n(r,2); pulse(out, 0, base, t, 8, 0.5, dur=0.22, vel=118)
        seq16 = [0,0,12,0,7,0,10,0,0,12,0,3,7,0,10,15]
        for i, iv in enumerate(seq16): out.append([round(t+i*0.25,4), 0.2, 1, base+12+iv, 120 if i%4==0 else 90])
        if bari % 2 == 1:
            hold(out, 2, chord(r,"min",4), t, 0.5, 110); hold(out, 2, chord(r,"min",4), t+2, 0.5, 110)
        arp(out, 3, chord(r,"min",5), t, 16, 0.25, dur=0.12, vel=70)
    prog_song("Acid Drive", "Techno", 130, ECHO, [SUB(), ACID(), STAB(), HAT()], prog, build)

def warehouse():
    prog = [("F","min"),("F","min"),("Db","maj"),("Eb","maj")]
    def build(out, bari, r, q, t):
        pulse(out, 0, n(r,2), t, 4, 1.0, dur=0.7, vel=115)
        for b in range(4): hold(out, 1, chord(r,q,4), t+b+0.5, 0.35, 100)
        for i, iv in enumerate([0,3,7,10,7,3,0,3]): out.append([round(t+i*0.5,4), 0.4, 2, n(r,4)+iv, 96])
        arp(out, 3, chord(r,q,5), t, 16, 0.25, dur=0.14, vel=64, updown=True)
    prog_song("Warehouse", "Techno", 126, ECHO, [SUB(), STAB(), RINGLEAD(), CLANG()], prog, build)

def _techno(name, bpm, prog, key_oct, bassmode, leadseq, arp_oct, parts, fx=ECHO, stab_every=2):
    def build(out, bari, r, q, t):
        bass_root(out, 0, r, key_oct, t, bassmode, vel=116)
        if bari % stab_every == (stab_every-1):
            for b in range(4): hold(out, 2, chord(r,q,4), t+b+0.5, 0.3, 98)
        base = n(r,4)
        for i, iv in enumerate(leadseq):
            out.append([round(t+i*(4.0/len(leadseq)),4), (4.0/len(leadseq))*0.8, 1, base+iv, 96 if i%2==0 else 78])
        arp(out, 3, chord(r,q,arp_oct), t, 16, 0.25, dur=0.13, vel=62, updown=(bari%2==0))
    prog_song(name, "Techno", bpm, fx, parts, prog, build)

def techno_extra():
    _techno("Detroit", 128, [("A","min7"),("A","min7"),("D","min7"),("E","min7")], 2, "8th",
            [0,7,12,7,3,7,10,7], 5, [REESE(), EP(), STAB(), BELL()])
    _techno("Hardwire", 134, [("E","min"),("E","min"),("G","min"),("D","min")], 2, "16",
            [0,0,12,0,10,0,7,0,3,0,12,0,7,3,0,7], 5, [SUB(), ACID(), HOOVER(), HAT()], stab_every=1)
    _techno("Dub Chamber", 120, [("C","min9"),("C","min9"),("Ab","maj7"),("G","min7")], 2, "4",
            [0,3,7,3], 5, [SUB(), EP(), STRINGS(), BELL()], fx=BOTH, stab_every=1)
    _techno("Rave Signal", 132, [("A","min"),("F","maj"),("G","maj"),("A","min")], 3, "off",
            [0,12,7,12,3,12,7,12], 5, [SUB(), HOOVER(), STAB(), CLANG()])
    _techno("Minimal Pulse", 124, [("D","min"),("D","min"),("D","min"),("A","min")], 2, "8th",
            [0,0,10,0,7,0,0,0], 5, [SUB(), ACID(), ORGAN(), GLOCK()], stab_every=4)
    _techno("Uplift", 128, [("A","min"),("C","maj"),("G","maj"),("F","maj")], 3, "8th",
            [0,4,7,12,7,4,7,12], 5, [SUB(), PLUCK(), STAB(), BELL()], fx=BOTH)


# ==================== POP (8) ====================
def synthwave():
    prog = [("A","min"),("F","maj"),("C","maj"),("G","maj")]
    def build(out, bari, r, q, t):
        pulse(out, 0, n(r,3), t, 8, 0.5, dur=0.42, vel=104); hold(out, 1, chord(r,q,4), t, 4.0, 66)
        mels = {0:[("E",5,2),("A",5,2)],1:[("F",5,2),("C",6,2)],2:[("E",5,2),("G",5,2)],3:[("D",5,2),("B",5,2)]}
        tt = t
        for nm, ov, d in mels[bari]: out.append([round(tt,4), d*0.95, 2, n(nm,ov), 92]); tt += d
        arp(out, 3, chord(r,q,5), t, 8, 0.5, dur=0.4, vel=58, updown=True)
    prog_song("Neon Drive", "Pop", 118, CHORUS, [REESE(), STRINGS(), SAWLEAD(), BELL()], prog, build)

def future_pop():
    prog = [("C","maj7"),("A","min7"),("F","maj7"),("G","dom7")]
    def build(out, bari, r, q, t):
        hold(out, 0, [n(r,3)], t, 2.0, 96); hold(out, 0, [n(r,3)], t+2, 2.0, 96)
        for b8 in range(8):
            if b8 % 2 == 0: hold(out, 1, chord(r,q,4), t+b8*0.5, 0.4, 80)
        lead = [("G",5,1.5),("E",5,0.5),("F",5,1),("G",5,1)]; tt = t
        for nm, ov, d in lead: out.append([round(tt,4), d*0.9, 2, n(nm,ov), 90]); tt += d
        arp(out, 3, chord(r,q,5), t, 16, 0.25, dur=0.16, vel=54)
    prog_song("Future Pop", "Pop", 100, BOTH, [FMBASS(), PLUCK(), SAWLEAD(), GLOCK()], prog, build)

def _pop(name, bpm, prog, fx, bassmode, lead, parts, arp_oct=5, chord_oct=4, arpv=54):
    def build(out, bari, r, q, t):
        bass_root(out, 0, r, 3, t, bassmode, vel=100); hold(out, 1, chord(r,q,chord_oct), t, 4.0, 64)
        tt = t
        for iv, d in lead[bari % len(lead)]: out.append([round(tt,4), d*0.9, 2, n(r,5)+iv, 92]); tt += d
        arp(out, 3, chord(r,q,arp_oct), t, 8, 0.5, dur=0.4, vel=arpv, updown=True)
    prog_song(name, "Pop", bpm, fx, parts, prog, build)

def pop_extra():
    _pop("Sunset Pop", 112, [("F","maj7"),("A","min7"),("Bb","maj7"),("C","dom7")], CHORUS, "8th",
         [[(0,2),(4,2)],[(-1,2),(2,2)],[(2,2),(5,2)],[(4,1),(2,1),(0,2)]], [PBASS(), EP(), FLUTE(), HARP()])
    _pop("Dance Floor", 122, [("C","maj"),("G","maj"),("A","min"),("F","maj")], BOTH, "4",
         [[(7,1),(4,1),(0,2)],[(2,2),(-1,2)],[(0,1),(3,1),(7,2)],[(5,2),(2,2)]], [SUB(), STAB(), SQLEAD(), BELL()])
    _pop("Heartlight (Ballad)", 76, [("C","maj"),("G","maj"),("A","min7"),("F","maj7")], BOTH, "hold",
         [[(4,4)],[(2,4)],[(0,2),(3,2)],[(0,4)]], [UPRIGHT(), WARMPAD(), EP(), GLOCK()], arpv=44)
    _pop("City Pop", 104, [("D","maj7"),("C#","min7"),("B","min7"),("E","dom7")], CHORUS, "walk",
         [[(0,1),(2,1),(4,2)],[(-1,2),(2,2)],[(-3,2),(0,2)],[(4,1),(2,1),(-1,2)]], [FMBASS(), EP(), FLUTE(), HARP()])
    _pop("Bright Side (K-pop)", 120, [("Eb","maj"),("Bb","maj"),("C","min"),("Ab","maj")], BOTH, "8th",
         [[(7,1),(9,1),(12,2)],[(7,2),(4,2)],[(0,1),(3,1),(7,2)],[(4,2),(0,2)]], [SUB(), STAB(), SAWLEAD(), GLOCK()], arpv=58)
    _pop("Midnight (Funk)", 108, [("E","min7"),("A","dom7"),("D","maj7"),("C#","min7")], CHORUS, "off",
         [[(0,1),(3,1),(0,1),(7,1)],[(4,1),(0,1),(4,2)],[(2,1),(5,1),(2,2)],[(0,2),(3,2)]], [PBASS(), ORGAN(), SQLEAD(), GLOCK()])


# ==================== AMBIENT (8) ====================
def _ambient(name, bpm, prog, parts, bell_oct=5, spark_oct=6, drone_oct=2):
    def build(out, bari, r, q, t):
        hold(out, 0, chord(r,q,3), t, 4.0, 58); hold(out, 2, [n(r,drone_oct)], t, 4.0, 72)
        for i, iv in enumerate(CH[q]): out.append([round(t+i*1.0,4), 2.5, 1, n(r,bell_oct)+iv, 50])
        arp(out, 3, chord(r,q,spark_oct), t, 8, 0.5, dur=0.6, vel=38, updown=True)
    prog_song(name, "Ambient", bpm, BOTH, parts, prog, build)

def ambient_all():
    _ambient("Aurora", 72, [("D","maj9"),("A","add9"),("B","min9"),("G","maj7")], [APAD(), ABELL(), ADRONE(), ASPARK()])
    _ambient("Drift", 66, [("E","min9"),("C","maj7"),("G","add9"),("D","sus4")], [APADW(), ACLANG(), ADRONE(), ABELL()])
    _ambient("Deep Space", 60, [("C","min9"),("Ab","maj7"),("Eb","maj9"),("Bb","sus2")], [AGLASS(), ABELL(), WIND(), ASPARK()], bell_oct=4)
    _ambient("Glacier", 64, [("F#","min9"),("D","maj7"),("A","add9"),("E","sus4")], [ACHOIR(), ASPARK(), ADRONE(), ACLANG()])
    _ambient("Meditation", 58, [("C","maj9"),("G","add9"),("C","maj9"),("F","maj7")], [APADW(), ABELL(), ADRONE(), HARP()])
    _ambient("Nebula", 70, [("A","min9"),("F","maj7"),("C","maj9"),("G","add9")], [AGLASS(), ABELL(), WIND(), ACLANG()])
    _ambient("Rainfall", 62, [("D","min9"),("Bb","maj7"),("F","maj9"),("C","add9")], [APAD(), HARP(), ADRONE(), ASPARK()], spark_oct=6)
    def dawn():
        motif = [n("Bb",5),n("C",6),n("D",6),n("F",6),n("D",6),n("C",6),n("Bb",5),n("C",6)]
        prog = [("Bb","maj9"),("Gb","maj7"),("Bb","maj9"),("Db","maj7")]
        out = []
        for bari, (r, q) in enumerate(prog):
            t = bari * 4.0; hold(out, 0, chord(r,q,3), t, 4.0, 54); hold(out, 2, [n(r,2)], t, 4.0, 70)
            out.append([round(t,4), 2.0, 1, motif[(bari*2)%len(motif)], 56])
            out.append([round(t+2,4), 2.0, 1, motif[(bari*2+1)%len(motif)], 52])
            arp(out, 3, chord(r,q,6), t, 8, 0.5, dur=0.6, vel=36, updown=True)
        song("Grieg · Morning Mood", "Ambient", 68, 4, BOTH, [APADW(), FLUTE(), ADRONE(), HARP()], out)
    dawn()


# ==================== RANDOM GENERATOR (for the UI's per-song "replace") ====================
import random
ROOTNAMES = ["C","C#","D","Eb","E","F","F#","G","Ab","A","Bb","B"]
_ADJ = ["Neon","Crystal","Velvet","Solar","Midnight","Electric","Golden","Lunar","Azure","Ember",
        "Frost","Amber","Cobalt","Scarlet","Ivory","Jade","Coral","Onyx","Aurora","Nova"]
_NOUN = ["Drift","Pulse","Motion","Skyline","Echoes","Voyage","Mirage","Cascade","Horizon",
         "Nocturne","Signal","Current","Bloom","Orbit","Ridge","Tide","Prism","Glow","Vапor","Trace"]
# relative progressions per genre: (semitone offset from key root, chord quality)
RP = {
 "Pop":[[(9,"min"),(5,"maj"),(0,"maj"),(7,"maj")],[(0,"maj"),(7,"maj"),(9,"min"),(5,"maj")],
        [(0,"maj7"),(9,"min7"),(5,"maj7"),(7,"dom7")],[(5,"maj"),(0,"maj"),(7,"maj"),(9,"min")]],
 "Techno":[[(9,"min"),(9,"min"),(0,"maj"),(7,"min")],[(0,"min"),(0,"min"),(10,"maj"),(8,"maj")],
        [(9,"min7"),(9,"min7"),(2,"min7"),(4,"min7")],[(0,"min"),(5,"maj"),(7,"maj"),(0,"min")]],
 "Classical":[[(0,"maj"),(9,"min"),(5,"maj"),(7,"maj")],[(0,"maj"),(4,"min"),(5,"maj"),(7,"maj")],
        [(9,"min"),(2,"min"),(4,"maj"),(9,"min")],[(0,"maj"),(7,"maj"),(9,"min"),(4,"min")]],
 "Ambient":[[(2,"maj9"),(9,"add9"),(11,"min9"),(7,"maj7")],[(4,"min9"),(0,"maj7"),(7,"add9"),(2,"sus4")],
        [(0,"min9"),(8,"maj7"),(3,"maj9"),(10,"sus2")],[(9,"min9"),(5,"maj7"),(0,"maj9"),(7,"add9")]],
}
def _render(prog, build):
    out = []
    for bari, (r, q) in enumerate(prog): build(out, bari, r, q, bari * 4.0)
    return out

# ---- midigen (theory-aware) composer for non-classical: real melody + voice-led extended harmony ----
MG_KEYS = {"major": ["C","G","D","F","A","Bb","Eb"], "minor": ["A","E","D","C","F","G","B"]}
MG_CFG = {
 "Pop":     {"mode":"major", "fx":CHORUS, "bpm":[100,108,112,118,122],
   "progs":[["ii7","V7","Imaj7","vi7"],["Imaj7","vi7","IV","V7"],["vi7","IV","Imaj7","V7"],["Imaj7","IV","ii7","V7"]]},
 "Techno":  {"mode":"minor", "fx":ECHO, "bpm":[124,126,128,130,132],
   "progs":[["i","VI","VII","i"],["i7","iv7","VII","V7"],["i","VII","VI","VII"],["i","iv7","V7","i"]]},
 "Ambient": {"mode":"major", "fx":BOTH, "bpm":[60,66,70,74],
   "progs":[["Imaj7","IV","vi7","ii7"],["Imaj7","V7","vi7","IV"],["ii7","V7","Imaj7","IV"],["vi7","IV","Imaj7","V7"]]},
}
MG_PATCH = {
 "Techno":  {"bass":[SUB,ACID,SQBASS],   "chords":[STAB,ORGAN],              "lead":[RINGLEAD,SQLEAD,SAWLEAD], "arp":[GLOCK,BELL,CLANG]},
 "Pop":     {"bass":[REESE,FMBASS,PBASS], "chords":[STRINGS,EP,STAB],         "lead":[SAWLEAD,SQLEAD,FLUTE],    "arp":[BELL,GLOCK,HARP]},
 "Ambient": {"bass":[ADRONE,SUB],         "chords":[APAD,APADW,AGLASS,ACHOIR],"lead":[ABELL,GLASSPAD,FLUTE],    "arp":[ASPARK,HARP,GLOCK]},
}

def _mg_song(genre, seed):
    """midigen theory composer: melody via in-scale random walk (snapped to the bar's chord on
    beats), harmony via voice-led Roman-numeral chords (get_chord_pitches). Coherent by design."""
    import midigen as _mg
    rng = random.Random(seed)
    cfg = MG_CFG[genre]; mode = cfg["mode"]
    kname = rng.choice(MG_KEYS[mode]); prog = rng.choice(cfg["progs"]); bars = len(prog)
    base = n(kname, 4)
    scfn = _mg.Scale.major if mode == "major" else _mg.Scale.minor
    scl = scfn(base) + scfn(base + 12)
    def cp(num, octv=4):
        try: return _mg.get_chord_pitches(kname, mode, num, octv)
        except Exception: return [base, base + 4, base + 7]
    def croot(num):
        try: deg = _mg.parse_roman_numeral(num).degree
        except Exception: deg = 1
        return scfn(base)[(deg - 1) % 7]
    out = []
    mel = _mg.Melody.random_walk(start_pitch=base + 7, length=bars * 8, scale=scl,
                                 max_interval=4, duration=240, seed=seed).get_notes()
    for nt in mel:
        t = nt.time / 480.0
        if t >= bars * 4: break
        if rng.random() < 0.25: continue                                  # phrasing rests
        p = nt.pitch
        if abs(t - round(t)) < 0.12:                                      # on a beat -> snap to chord tone
            pcs = {x % 12 for x in cp(prog[int(t // 4) % bars])}
            for dd in range(6):
                if (p - dd) % 12 in pcs: p -= dd; break
                if (p + dd) % 12 in pcs: p += dd; break
        out.append([round(t, 4), round(min(nt.duration / 480.0, 1.5) * 0.9, 4), 2, max(55, min(84, p)), 92])
    for bari, num in enumerate(prog):
        t = bari * 4.0; rn = ROOTNAMES[croot(num) % 12]; chd = cp(num, 4)
        if genre == "Techno":
            bass_root(out, 0, rn, 2, t, rng.choice(["8th","4","16"]), vel=114)
            if bari % 2 == 1:
                for b in range(4): hold(out, 1, chd, t + b + 0.5, 0.3, 92)
            arp(out, 3, cp(num, 5), t, 16, 0.25, dur=0.13, vel=58, updown=(bari % 2 == 0))
        elif genre == "Pop":
            bass_root(out, 0, rn, 3, t, rng.choice(["8th","4","walk"]), vel=100)
            hold(out, 1, chd, t, 4.0, 60)
            arp(out, 3, cp(num, 5), t, 8, 0.5, dur=0.4, vel=50, updown=True)
        else:  # Ambient
            hold(out, 0, [croot(num) - 24], t, 4.0, 70)                    # drone
            hold(out, 1, cp(num, 3), t, 4.0, 56)
            arp(out, 3, cp(num, 6), t, 8, 0.5, dur=0.6, vel=38, updown=True)
    parts = [rng.choice(MG_PATCH[genre]["bass"])(), rng.choice(MG_PATCH[genre]["chords"])(),
             rng.choice(MG_PATCH[genre]["lead"])(), rng.choice(MG_PATCH[genre]["arp"])()]
    return {"name": rng.choice(_ADJ) + " " + rng.choice(_NOUN), "genre": genre, "bpm": rng.choice(cfg["bpm"]),
            "bars": bars, "fx": cfg["fx"], "parts": parts, "notes": out}

def make_random(genre, seed=0):
    """Classical -> public-domain rotation; others -> midigen theory composer."""
    rng = random.Random(seed)
    if genre == "Classical":
        return add_effects(rng.choice(CLASSICAL_PD)())
    if genre not in MG_CFG: genre = "Pop"
    return add_effects(_mg_song(genre, seed))


# --- per-genre effect amounts (raw CC values) so each demo shows off the effects -------------
# The fx MODE (CC83) is already per-song; these are the AMOUNTS: reverb wet (93), room size (91),
# chorus depth (94), delay depth (95), delay time (82). Attached to every song by genre so the
# demo player restores the full effect state, not just the mode.
FX_BY_GENRE = {
    "Classical": {"reverb": 88,  "room": s(1), "chorusd": 0,  "echod": 0,   "dtime": 63},  # hall tail
    "Pop":       {"reverb": 52,  "room": s(0), "chorusd": 85, "echod": 0,   "dtime": 63},  # chorus + a little room
    "Techno":    {"reverb": 40,  "room": s(2), "chorusd": 0,  "echod": 100, "dtime": 48},  # delay + reverb
    "Ambient":   {"reverb": 115, "room": s(3), "chorusd": 90, "echod": 70,  "dtime": 85},  # lush: big verb+chorus+delay
}
_DEFAULT_FX = {"reverb": 60, "room": s(1), "chorusd": 40, "echod": 40, "dtime": 63}
def add_effects(sg):
    """Attach the genre's effect amounts to a song (without clobbering any it already set)."""
    for k, v in FX_BY_GENRE.get(sg.get("genre"), _DEFAULT_FX).items():
        sg.setdefault(k, v)
    return sg

if __name__ == "__main__":
    # 4 first-of-genre demos: one public-domain classical theme + the first procedural song per
    # other genre. The seeds (5000/5050/5100) deterministically reproduce Frost Tide / Neon
    # Nocturne / Ivory Orbit (_mg_song uses a per-call random.Random(seed)).
    songs.append(bach_prelude())
    for _gi, _g in enumerate(("Techno", "Pop", "Ambient")):
        songs.append(_mg_song(_g, 5000 + _gi * 50))
    for _sg in songs: add_effects(_sg)                       # attach per-genre effect amounts
    out_path = os.path.join(os.path.dirname(__file__), "..", "webui", "static", "demos.json")
    with open(out_path, "w") as f:
        json.dump({"songs": songs}, f, indent=1)
    by = {}
    for s_ in songs: by[s_["genre"]] = by.get(s_["genre"], 0) + 1
    print(f"wrote {len(songs)} demos -> {os.path.relpath(out_path)}   {by}")
    allp = [(p["wave"], p["xmode"], p["fmode"]) for s_ in songs for p in s_["parts"]]
    print(f"distinct part-timbres used: {len(set(allp))} across {len(allp)} part-slots")
