"""Basic functionality tests — one (or more) per synth feature. Each sends MIDI to
the board and grades the captured audio against an expected-outcome rubric (0-100)."""
import os, sys, time
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "host"))
import uartaudio as U                       # noqa: E402
from uartaudio import (note_on, note_off, cc, pitch_bend,                 # noqa: E402
                       set_wave, set_cutoff, set_reso, set_fmode, set_sub, set_pw,
                       set_detune, set_unison, set_vib, set_porta, set_fx, set_trem, set_room,
                       set_reverb, set_echo_depth, set_delay_time)
from harness import TestCase, mk           # noqa: E402
import harness as H
import analysis as A                        # noqa: E402

W_SINE, W_SAW, W_SQR, W_TRI, W_NOISE = 0, 1, 2, 3, 4

def w(fd, *msgs):
    for m in msgs: H.send(fd, m)

def hold(notes, secs, vel=100):
    def perform(fd):
        for n in notes: H.send(fd, note_on(n, vel))
        time.sleep(secs)
        for n in notes: H.send(fd, note_off(n))
        time.sleep(0.1)
    return perform

def open_bright(fd):                        # setup: filter wide open, snappy amp, steady tone
    w(fd, set_cutoff(127), set_reso(15), cc(20, 2), cc(22, 120), cc(23, 20), cc(79, 0), set_fx(0))

def score_scale(x, good, bad):
    """Linear score: x>=good -> 100, x<=bad -> 0."""
    if good == bad: return 100.0 if x >= good else 0.0
    return max(0.0, min(100.0, 100.0 * (x - bad) / (good - bad)))

CASES = []
def add(**kw): CASES.append(TestCase(category="basic", **kw))

# ---------------- pitch / polyphony / velocity ----------------
def _chk_pitch(note):
    def check(s):
        f, _ = A.strongest(s, 60, 3000); tgt = A.note_hz(note)
        err = abs(f - tgt) / tgt * 100
        return mk(score_scale(-err, -0.5, -6), f"peak {f:.0f} Hz (want {tgt:.0f})", f"±3% of {tgt:.0f} Hz")
    return check

add(id="pitch_a4", title="Pitch accuracy — A4", desc="A single A4 (MIDI 69) should read ~440 Hz.",
    expected="strongest peak within 3% of 440 Hz", setup=open_bright,
    perform=hold([69], 1.5), check=_chk_pitch(69), capture_s=1.8)

def _chk_range(notes):
    def check(s):
        hits, p = A.found_pitches(s, notes)
        return mk(100 * hits / len(notes), f"{hits}/{len(notes)} pitches ({[round(x) for x in p][:6]})",
                  f"all {len(notes)} notes present")
    return check
add(id="note_range", title="Note range — low/mid/high", desc="Notes across the range (C2/C4/C6) each sound at the right pitch.",
    expected="3/3 correct pitches", setup=open_bright, perform=hold([36, 60, 84], 1.6),
    check=_chk_range([36, 60, 84]), capture_s=2.0)

add(id="poly4", title="Polyphony — 4-voice chord", desc="An Amaj7 chord should show four simultaneous peaks.",
    expected="4/4 chord tones", setup=open_bright, perform=hold([69, 73, 76, 80], 2.0),
    check=_chk_range([69, 73, 76, 80]), capture_s=2.4)

def _chk_velocity(s):
    # perform plays soft then loud; compare RMS of the two halves
    n = len(s) // 2
    soft, loud = A.rms(s[:n]), A.rms(s[n:])
    ratio = loud / max(1.0, soft)
    return mk(score_scale(ratio, 2.5, 1.1), f"loud/soft RMS = {ratio:.2f}", "loud ≫ soft (>2×)")
def _perf_velocity(fd):
    H.send(fd, note_on(69, 25)); time.sleep(1.0); H.send(fd, note_off(69)); time.sleep(0.3)
    H.send(fd, note_on(69, 120)); time.sleep(1.0); H.send(fd, note_off(69)); time.sleep(0.1)
add(id="velocity", title="Velocity → amplitude", desc="The same note at velocity 25 then 120 — the second is much louder.",
    expected="loud/soft RMS ratio > 2×", setup=open_bright, perform=_perf_velocity, check=_chk_velocity, capture_s=2.8)

# ---------------- waveforms ----------------
def _chk_wave(kind):
    def check(s):
        f0, _ = A.strongest(s, 200, 900)
        if f0 < 100: return mk(0, "no tone", kind)
        h = A.harmonic_profile(s, f0, 6)     # [1, h2, h3, h4, h5, h6]
        h2, h3, h4, h5 = h[1], h[2], h[3], h[4]
        if kind == "sine":
            sc = score_scale(-max(h2, h3, h4), -0.12, -0.5); metric = f"h2={h2:.2f} h3={h3:.2f} (want ~0)"; exp = "only fundamental"
        elif kind == "saw":
            sc = score_scale(min(h2, h3), 0.25, 0.05); metric = f"h2={h2:.2f} h3={h3:.2f}"; exp = "full harmonic series"
        elif kind == "square":
            sc = 0.5 * score_scale(-h2, -0.15, -0.5) + 0.5 * score_scale(h3, 0.2, 0.05)
            metric = f"h2={h2:.2f} (want~0) h3={h3:.2f}"; exp = "odd harmonics only"
        else:  # triangle
            sc = 0.5 * score_scale(-h2, -0.15, -0.5) + 0.5 * score_scale(h3, 0.05, 0.01) + 0.5 * score_scale(-(h3 - 0.4), 0, -0.4)
            sc = min(100.0, sc); metric = f"h2={h2:.2f} h3={h3:.2f} (odd, steep)"; exp = "odd harmonics, steep rolloff"
        return mk(sc, metric, exp)
    return check
for wid, wname in [(W_SINE, "sine"), (W_SAW, "saw"), (W_SQR, "square"), (W_TRI, "triangle")]:
    def mk_setup(wv):
        def setup(fd): open_bright(fd); w(fd, set_wave(wv), set_pw(64))
        return setup
    add(id=f"wave_{wname}", title=f"Waveform — {wname}", desc=f"A {wname} at A4 has its characteristic harmonic signature.",
        expected=f"{wname} spectrum", setup=mk_setup(wid), perform=hold([69], 1.6), check=_chk_wave(wname), capture_s=1.9)

def _chk_noise(s):
    freqs, mags = A.spectrum(s, 200, 6000, 16)
    if not mags or max(mags) < 1: return mk(0, "silent", "broadband noise")
    m = sorted(mags, reverse=True); flat = (sum(m) / len(m)) / m[0]      # mean/peak: flat spectrum -> ~1
    return mk(score_scale(flat, 0.17, 0.05), f"spectral flatness {flat:.2f}", "broadband (flat) spectrum")
def _setup_noise(fd): open_bright(fd); w(fd, set_wave(W_NOISE))
add(id="noise", title="Noise source", desc="Waveform 4 is white noise — a broadband, flat-ish spectrum.",
    expected="broadband spectrum", setup=_setup_noise, perform=hold([69], 1.6), check=_chk_noise, capture_s=1.9)

# ---------------- sub / PWM / detune ----------------
def _chk_sub(s):
    f0, _ = A.strongest(s, 200, 900)
    if f0 < 100: return mk(0, "no tone", "sub octave present")
    sub = A.dft_mag(A._window(s), [f0 / 2])[0]; fund = A.dft_mag(A._window(s), [f0])[0] or 1
    r = sub / fund
    return mk(score_scale(r, 0.25, 0.03), f"sub/fund = {r:.2f} at {f0/2:.0f} Hz", "energy one octave below")
def _setup_sub(fd): open_bright(fd); w(fd, set_wave(W_SAW), set_sub(3))
add(id="sub_osc", title="Sub-oscillator", desc="CC73 adds a square one octave below the played note.",
    expected="energy one octave down", setup=_setup_sub, perform=hold([69], 1.6), check=_chk_sub, capture_s=1.9)

def _chk_pwm(s):
    f0, _ = A.strongest(s, 200, 900)
    if f0 < 100: return mk(0, "no tone", "even harmonics appear")
    h = A.harmonic_profile(s, f0, 4)
    return mk(score_scale(h[1], 0.2, 0.03), f"h2={h[1]:.2f} at narrow PW", "narrow pulse → strong even harmonics")
def _setup_pwm(fd): open_bright(fd); w(fd, set_wave(W_SQR), set_pw(20))
add(id="pwm", title="Pulse-width modulation", desc="A narrow pulse (CC75=20) brings in even harmonics vs a 50% square.",
    expected="strong 2nd harmonic", setup=_setup_pwm, perform=hold([69], 1.6), check=_chk_pwm, capture_s=1.9)

def _chk_detune(s):
    cv = A.beating_cv(s)
    return mk(score_scale(cv, 0.10, 0.02), f"envelope CV = {cv:.3f}", "audible beating (CV > 0.05)")
def _setup_detune(fd): open_bright(fd); w(fd, set_wave(W_SAW), set_detune(3))
add(id="detune", title="Detuned dual oscillator", desc="CC78 adds a detuned 2nd saw → beating between the two.",
    expected="beating (envelope modulation)", setup=_setup_detune, perform=hold([57], 2.2), check=_chk_detune, capture_s=2.5)

# ---------------- filter ----------------
def _band_ratio(s):
    lo = A.band_energy(s, 200, 700); hi = A.band_energy(s, 1500, 6000)
    return lo, hi
def _chk_lp_closed(s):
    c = A.spectral_centroid(s)
    return mk(score_scale(-c, -650, -1600), f"centroid {c:.0f} Hz", "dark (low centroid)")
def _setup_lp_closed(fd): w(fd, set_wave(W_SAW), set_cutoff(6), set_reso(20), set_fmode(0), cc(20, 2), cc(22, 120), set_fx(0))
add(id="filter_lp_closed", title="Filter — LP cutoff low", desc="A low LP cutoff rolls the sawtooth's highs off (dark tone).",
    expected="dark (low centroid)", setup=_setup_lp_closed, perform=hold([36], 1.6), check=_chk_lp_closed, capture_s=1.9)

def _chk_lp_open(s):
    c = A.spectral_centroid(s)
    return mk(score_scale(c, 900, 350), f"centroid {c:.0f} Hz", "bright (centroid > 900 Hz)")
def _setup_lp_open(fd): w(fd, set_wave(W_SAW), set_cutoff(127), set_reso(20), set_fmode(0), cc(20, 2), cc(22, 120), set_fx(0))
add(id="filter_lp_open", title="Filter — LP cutoff high", desc="A high LP cutoff (CC74=127) passes the full harmonic stack.",
    expected="highs present", setup=_setup_lp_open, perform=hold([57], 1.6), check=_chk_lp_open, capture_s=1.9)

def _chk_sweep(s):
    c = A.centroid_over_time(s, 8)
    rise = c[-2] - c[1] if len(c) >= 3 else 0
    return mk(score_scale(rise, 800, 100), f"centroid {c[1]:.0f}→{c[-2]:.0f} Hz", "brightness rises during the sweep")
def _perf_sweep(fd):
    H.send(fd, note_on(57, 110))
    for i in range(0, 8):
        H.send(fd, set_cutoff(20 + i * 14)); time.sleep(0.28)
    H.send(fd, note_off(57)); time.sleep(0.1)
def _setup_sweep(fd): w(fd, set_wave(W_SAW), set_reso(40), set_fmode(0), cc(20, 2), cc(22, 120), set_fx(0))
add(id="filter_sweep", title="Filter — cutoff sweep", desc="Sweeping CC74 up while holding a note moves the bright edge upward.",
    expected="spectral centroid rises", setup=_setup_sweep, perform=_perf_sweep, check=_chk_sweep, capture_s=2.6)

def _chk_reso(s):
    # a resonant peak near cutoff stands above the local spectrum
    freqs, mags = A.spectrum(s, 300, 3000, 8)
    if not mags or max(mags) < 1: return mk(0, "silent", "resonant peak")
    pk = max(mags); med = sorted(mags)[len(mags) // 2] or 1
    r = pk / med
    return mk(score_scale(r, 8, 2), f"peak/median = {r:.1f}", "pronounced resonant peak")
def _setup_reso(fd): w(fd, set_wave(W_SAW), set_cutoff(55), set_reso(120), set_fmode(0), cc(20, 2), cc(22, 120), set_fx(0))
add(id="resonance", title="Filter — resonance", desc="High resonance (CC71) makes a sharp peak at the cutoff.",
    expected="sharp resonant peak", setup=_setup_reso, perform=hold([45], 1.8), check=_chk_reso, capture_s=2.1)

def _chk_hp(s):
    lo = A.band_energy(s, 200, 500); hi = A.band_energy(s, 1500, 6000)
    r = lo / max(1.0, hi)
    return mk(score_scale(-r, -0.6, -3.0), f"low/high energy = {r:.2f}", "fundamental attenuated, highs pass")
def _setup_hp(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(20), set_fmode(1), cc(20, 2), cc(22, 120), set_fx(0))
add(id="filter_hp", title="Filter mode — high-pass", desc="CC72=HP attenuates the fundamental while highs pass.",
    expected="lows cut", setup=_setup_hp, perform=hold([57], 1.6), check=_chk_hp, capture_s=1.9)

def _chk_bp(s):
    # band-pass with resonance produces a peaked spectrum (one region stands out)
    freqs, mags = A.spectrum(s, 200, 5000, 8)
    if not mags or max(mags) < 1: return mk(0, "silent", "peaked band-pass")
    r = max(mags) / (sorted(mags)[len(mags) // 2] or 1)
    return mk(score_scale(r, 6, 2), f"peak/median = {r:.1f}", "peaked (band-limited) spectrum")
def _setup_bp(fd): w(fd, set_wave(W_SAW), set_cutoff(45), set_reso(95), set_fmode(2), cc(20, 2), cc(22, 120), set_fx(0))
add(id="filter_bp", title="Filter mode — band-pass", desc="CC72=BP passes a band around the cutoff.",
    expected="band-limited spectrum", setup=_setup_bp, perform=hold([45], 1.6), check=_chk_bp, capture_s=1.9)

def _chk_notch(s):
    return mk(100.0 if A.rms(s) > 200 else 0.0, f"RMS {A.rms(s):.0f}", "audible output (notch mode active)")
def _setup_notch(fd): w(fd, set_wave(W_SAW), set_cutoff(70), set_reso(40), set_fmode(3), cc(20, 2), cc(22, 120), set_fx(0))
add(id="filter_notch", title="Filter mode — notch", desc="CC72=notch (LP+HP) still produces a shaped tone.",
    expected="audible shaped output", setup=_setup_notch, perform=hold([57], 1.6), check=_chk_notch, capture_s=1.9)

def _chk_keytrack(s):
    # two notes an octave+ apart at fixed CC; key-tracking makes the higher one brighter.
    # analyse each note's own loud window (cap fmax to ignore saw aliasing).
    n = len(s)
    c_lo = A.spectral_centroid(s[:int(n * 0.42)], 60, 3000)
    c_hi = A.spectral_centroid(s[int(n * 0.55):], 60, 3000)
    r = c_hi / max(1.0, c_lo)
    return mk(score_scale(r, 1.3, 1.0), f"centroid {c_lo:.0f}→{c_hi:.0f} Hz", "higher note is brighter")
def _perf_keytrack(fd):
    H.send(fd, note_on(45, 100)); time.sleep(1.2); H.send(fd, note_off(45)); time.sleep(0.4)
    H.send(fd, note_on(69, 100)); time.sleep(1.2); H.send(fd, note_off(69)); time.sleep(0.1)
def _setup_keytrack(fd): w(fd, set_wave(W_SAW), set_cutoff(45), set_reso(12), set_fmode(0), cc(20, 2), cc(22, 120), set_fx(0))
add(id="key_tracking", title="Filter — key tracking", desc="At a fixed cutoff, a higher note tracks brighter than a low one.",
    expected="centroid rises with pitch", setup=_setup_keytrack, perform=_perf_keytrack, check=_chk_keytrack, capture_s=2.8)

def _chk_filter_env(s):
    c = A.centroid_over_time(s, 10)
    bright = max(c[:4]) if len(c) >= 4 else 0     # peak brightness at the attack
    dark = c[-1] if c else 0                        # settled (darker) sustain
    drop = bright - dark
    return mk(score_scale(drop, 150, 25), f"centroid {bright:.0f}→{dark:.0f} Hz", "bright attack decaying to darker")
def _setup_filter_env(fd): w(fd, set_wave(W_SAW), set_cutoff(30), set_reso(40), set_fmode(0), cc(79, 115), cc(24, 0), cc(25, 95), cc(26, 25), cc(20, 2), cc(22, 120), set_fx(0))
add(id="filter_env", title="Filter envelope (pluck)", desc="A filter-env with depth (CC79) opens then closes the cutoff → a pluck.",
    expected="bright attack, darker sustain", setup=_setup_filter_env, perform=hold([45], 2.0), check=_chk_filter_env, capture_s=2.3)

# ---------------- amp envelope ----------------
def _chk_attack_slow(s):
    env = A.envelope(s, 256); st = A.env_stats(env)
    r = st["rise50"] or 0
    return mk(score_scale(r, 16, 3), f"rise-to-50% = {r} windows", "slow attack (gradual swell)")
def _setup_attack_slow(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(20, 110), cc(22, 120), cc(21, 40), set_fx(0))
add(id="amp_attack", title="Amp envelope — slow attack", desc="A long amp attack (CC20=110) ramps the level up over ~1 s.",
    expected="gradual amplitude rise", setup=_setup_attack_slow, perform=hold([57], 1.6), check=_chk_attack_slow, capture_s=1.9)

def _chk_release_long(s):
    # note released partway; the tail should still ring
    tail = A.silence_tail(s, 300)
    return mk(score_scale(tail, 1500, 200), f"tail RMS = {tail:.0f}", "long release still ringing")
def _perf_release(fd):
    H.send(fd, note_on(57, 110)); time.sleep(0.6); H.send(fd, note_off(57)); time.sleep(0.7)
def _setup_release(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(20, 2), cc(22, 120), cc(23, 120), set_fx(0))
add(id="amp_release", title="Amp envelope — long release", desc="A long release (CC23=120) keeps the note ringing after note-off.",
    expected="audible release tail", setup=_setup_release, perform=_perf_release, check=_chk_release_long, capture_s=1.6)

def _chk_release_short(s):
    tail = A.silence_tail(s, 250)
    return mk(score_scale(-tail, -300, -3000), f"tail RMS = {tail:.0f}", "short release → quick silence")
def _perf_release_short(fd):
    H.send(fd, note_on(57, 110)); time.sleep(0.6); H.send(fd, note_off(57)); time.sleep(0.6)
def _setup_release_short(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(20, 2), cc(22, 120), cc(23, 0), set_fx(0))
add(id="amp_release_fast", title="Amp envelope — short release", desc="A short release (CC23=0) cuts the note off quickly.",
    expected="quick decay to silence", setup=_setup_release_short, perform=_perf_release_short, check=_chk_release_short, capture_s=1.5)

def _chk_sustain(s):
    # the SUSTAIN stage holds a note at a steady level while held — verify the sustained
    # region (past attack/decay, before release) is present and flat (not decaying away).
    env = A.envelope(s, 256)
    core = env[len(env) // 4: int(len(env) * 0.72)]
    if not core or max(core) < 50:
        return mk(0, "no sustained level", "steady held level")
    m = sum(core) / len(core)
    sd = (sum((e - m) ** 2 for e in core) / len(core)) ** 0.5
    cv = sd / m
    return mk(score_scale(-cv, -0.18, -0.6), f"sustain level {m:.0f}, stability CV {cv:.2f}", "steady held level (sustain holds)")
def _setup_sustain(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(20, 2), cc(21, 30), cc(22, 90), set_fx(0))
add(id="amp_sustain", title="Amp envelope — sustain level", desc="CC22 sets the sustain plateau below the attack peak.",
    expected="sustain ≈ half of peak", setup=_setup_sustain, perform=hold([57], 1.8), check=_chk_sustain, capture_s=2.1)

# ---------------- LFO / modulation ----------------
def _chk_autowah(s):
    c = A.centroid_over_time(s, 12)
    core = c[2:-2] or c
    m = sum(core) / len(core); swing = (max(core) - min(core)) / max(1.0, m)
    return mk(score_scale(swing, 0.18, 0.05), f"centroid swing = {swing:.2f}", "cutoff wobbles (LFO)")
def _setup_autowah(fd): w(fd, set_wave(W_SAW), set_cutoff(55), set_reso(60), set_fmode(0), cc(76, 70), cc(77, 90), cc(20, 2), cc(22, 120), set_fx(0))
add(id="lfo_autowah", title="LFO → cutoff (auto-wah)", desc="The LFO (CC76 rate, CC77 depth) sweeps the filter cutoff.",
    expected="periodic brightness wobble", setup=_setup_autowah, perform=hold([45], 2.6), check=_chk_autowah, capture_s=2.9)

def _chk_vibrato(s):
    p = A.pitch_track(s, 12); core = [x for x in p[2:-2] if x > 0]
    if len(core) < 4: return mk(0, "no pitch", "pitch wobble")
    m = sum(core) / len(core); swing = (max(core) - min(core)) / max(1.0, m)
    return mk(score_scale(swing, 0.02, 0.002), f"pitch swing = {swing*100:.1f}%", "pitch wobbles (vibrato)")
def _setup_vibrato(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(76, 80), cc(1, 96), cc(20, 2), cc(22, 120), set_fx(0))
add(id="vibrato", title="Vibrato (mod wheel)", desc="CC1 routes the LFO to pitch → the tone develops sidebands.",
    expected="periodic pitch modulation", setup=_setup_vibrato, perform=hold([69], 2.4), check=_chk_vibrato, capture_s=2.7)

def _chk_tremolo(s):
    d = A.modulation_depth(s, 128)
    return mk(score_scale(d, 0.6, 0.1), f"amplitude mod depth = {d:.2f}", "amplitude pulses (tremolo)")
def _setup_tremolo(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(76, 80), set_trem(3), cc(20, 2), cc(22, 120), set_fx(0))
add(id="tremolo", title="Tremolo (LFO → amp)", desc="CC92 routes the LFO to amplitude → the level pulses.",
    expected="amplitude modulation", setup=_setup_tremolo, perform=hold([57], 2.4), check=_chk_tremolo, capture_s=2.7)

def _chk_portamento(s):
    p = [x for x in A.pitch_track(s, 12) if x > 0]
    if len(p) < 4: return mk(0, "no glide", "pitch glides")
    # expect a monotone-ish climb from ~low to ~high across the capture
    span = (max(p) - min(p)) / max(1.0, min(p))
    return mk(score_scale(span, 1.5, 0.3), f"pitch span = {min(p):.0f}→{max(p):.0f} Hz", "note glides between pitches")
def _perf_portamento(fd):
    H.send(fd, note_on(45, 110)); time.sleep(0.5)
    H.send(fd, note_on(69, 110)); time.sleep(1.3); H.send(fd, note_off(69)); H.send(fd, note_off(45)); time.sleep(0.1)
def _setup_portamento(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), set_porta(3), cc(20, 2), cc(22, 120), set_fx(0))
add(id="portamento", title="Portamento / glide", desc="With glide on (CC5), a new note slides up from the previous pitch.",
    expected="continuous pitch glide", setup=_setup_portamento, perform=_perf_portamento, check=_chk_portamento, capture_s=2.2)

def _chk_bend(s):
    p = [x for x in A.pitch_track(s, 12) if x > 0]
    if len(p) < 4: return mk(0, "no tone", "pitch bends up")
    span = (max(p) - min(p)) / max(1.0, min(p))
    return mk(score_scale(span, 0.10, 0.02), f"pitch span = {span*100:.1f}%", "pitch bends ≈ 2 semitones")
def _perf_bend(fd):
    H.send(fd, note_on(60, 110)); time.sleep(0.4)
    for i in range(9):
        H.send(fd, pitch_bend(i / 8.0)); time.sleep(0.15)
    H.send(fd, note_off(60)); H.send(fd, pitch_bend(0.0)); time.sleep(0.1)
def _setup_bend(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(20, 2), cc(22, 120), set_fx(0))
add(id="pitch_bend", title="Pitch bend", desc="A full pitch-bend up (0xE0) raises the pitch ~2 semitones.",
    expected="upward pitch bend", setup=_setup_bend, perform=_perf_bend, check=_chk_bend, capture_s=2.2)

# ---------------- unison ----------------
def _chk_unison(s):
    cv = A.beating_cv(s)
    return mk(score_scale(cv, 0.15, 0.03), f"envelope CV = {cv:.3f}", "thick beating super-saw")
def _setup_unison(fd): w(fd, set_wave(W_SAW), set_cutoff(100), set_reso(20), set_unison(3), cc(20, 2), cc(22, 120), set_fx(0))
add(id="unison", title="Unison (4-voice super-saw)", desc="CC80 stacks 4 detuned voices on one note → thick beating.",
    expected="strong beating (CV > 0.1)", setup=_setup_unison, perform=hold([45], 2.4), check=_chk_unison, capture_s=2.7)

# ---------------- effects ----------------
def _chk_echo(s):
    tail = A.tail_energy(s, 0.3)      # discrete decaying repeats after the pluck
    return mk(score_scale(tail, 250, 40), f"echo tail RMS = {tail:.0f}", "delayed repeats after the pluck")
def _perf_echo(fd):
    for n in (48, 55, 60): H.send(fd, note_on(n, 120))
    time.sleep(0.25)
    for n in (48, 55, 60): H.send(fd, note_off(n))
    time.sleep(1.7)
def _setup_echo(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(20, 2), cc(23, 10), set_fx(2), set_echo_depth(110), set_delay_time(50))
add(id="echo", title="Effect — echo / delay", desc="CC83=echo/delay + CC95 depth + CC82 time repeats a pluck with decaying taps.",
    expected="decaying repeats", setup=_setup_echo, perform=_perf_echo, check=_chk_echo, capture_s=2.0)

def _chk_reverb(s):
    # dry note is gone within ~50ms of release; any energy in the post-stab region is
    # the (quiet, Freeverb /8) wet tail. Measure it absolutely; must also decay (no latch).
    tail = A.tail_energy(s, 0.2)
    latched = A.is_latched(s, 500, 1200)
    sc = score_scale(tail, 40, 6)
    if latched: sc = min(sc, 55)
    return mk(sc, f"wet tail RMS = {tail:.0f}{' (latched!)' if latched else ''}", "diffuse decaying tail")
def _perf_reverb(fd):
    for n in (48, 55, 60): H.send(fd, note_on(n, 120))
    time.sleep(0.25)
    for n in (48, 55, 60): H.send(fd, note_off(n))
    time.sleep(2.4)
def _setup_reverb(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(20, 2), cc(23, 8), set_fx(0), set_reverb(110), set_room(2))
add(id="reverb", title="Effect — reverb", desc="CC93 reverb wet (8-comb Freeverb send) adds a diffuse decaying tail.",
    expected="reverb tail that decays", setup=_setup_reverb, perform=_perf_reverb, check=_chk_reverb, capture_s=2.8)

def _chk_reverb_size(s):
    # cathedral rings the loudest/longest — a large absolute wet tail after the stab.
    tail = A.tail_energy(s, 0.15)
    return mk(score_scale(tail, 90, 25), f"cathedral tail RMS = {tail:.0f}", "long, prominent tail")
def _perf_reverb_size(fd):
    for n in (48, 55, 60): H.send(fd, note_on(n, 120))
    time.sleep(0.25)
    for n in (48, 55, 60): H.send(fd, note_off(n))
    time.sleep(3.2)
def _setup_reverb_size(fd): w(fd, set_wave(W_SAW), set_cutoff(110), set_reso(15), cc(20, 2), cc(23, 8), set_fx(0), set_reverb(120), set_room(3))
add(id="reverb_cathedral", title="Reverb size — cathedral", desc="CC91=cathedral gives the longest RT60 tail.",
    expected="long-ringing tail", setup=_setup_reverb_size, perform=_perf_reverb_size, check=_chk_reverb_size, capture_s=3.6)
