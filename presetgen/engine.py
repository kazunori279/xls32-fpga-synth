"""Sample-accurate software model of the XLS FPGA synth (core/synth.x + the shared effects).

Renders (preset CC dict, note, gate) -> mono float audio at 32 kHz, reproducing the engine's
exact arithmetic (naive aliasing oscillators, Chamberlin SVF, dual ADSR, unison, LFO, VCA,
Freeverb-style effects) so an offline parameter search matches what the board actually does.
numba JITs the recursive per-sample kernels so a CMA-ES search is fast enough.

Constants/formulas verified against synth.x (SINE/BASE_INC/TIME_INC, svf, adsr, process_voice,
scale_mix) and the effects RTL. CC decode mirrors the firmware's bit-packing.

32 kHz on both boards. That is the *engine* rate, not the interface rate: the Basys 3 divides
100 MHz by 3125 to stream at 32 kHz, the Tiliqua runs its UAC2 link at 48 kHz and scales the
effects constants by 3/2 to compensate (boards/tiliqua/gateware/fx.py), but the phase increments
in synth.x are 32 kHz on both. Until M27 this file said 28 kHz and carried a matching BASE_INC —
left over from the ÷4/soft-multiplier era that synth.x:8-11 describes. Because the rate and the
table were consistently stale, rendered *pitch* was right and everything measured per sample was
not: envelopes, LFO and the SVF corner all sat 14% away from the hardware they were fitted for.

The board is 4-part multitimbral (one patch per MIDI channel 0-3); this model renders ONE part
(the per-voice DSP is identical across parts), which is exactly what the per-preset CMA-ES search
needs. Any multi-part integration render belongs in test/.
"""
import numpy as np
from numba import njit

SR = 32000
MASK = 0xFFFFFFFF

# 256-entry sine LUT, s16 range ±2047 (synth.x line 1 = round(2047*sin(2πi/256))).
SINE = np.round(2047 * np.sin(2 * np.pi * np.arange(256) / 256)).astype(np.int64)
# Phase increment for the lowest octave; note_inc(n) = BASE_INC[n%12] << (n//12).
# Verbatim from synth.x:12 — do not recompute it here, or the two drift again.
BASE_INC = np.array([1097338, 1162588, 1231719, 1304961, 1382558, 1464769,
                     1551869, 1644148, 1741914, 1845494, 1955232, 2071497], dtype=np.int64)
TIME_INC = [640, 200, 90, 45, 20, 9, 4, 1]           # ADSR A/D/R increment LUT (index = cc>>4)


def note_inc(note):
    n = note & 0x7f
    return int(BASE_INC[n % 12]) << (n // 12)


# synthspec control defaults (raw CC values) so a partial preset dict still renders.
# The effects five (reverb/chorusd/echod/dtime/room) default to the shell's own reset values,
# so a preset that names none of them renders dry — which is what the banks, none of which carry
# an effects key, actually sound like on the board.
_DEFAULTS = dict(wave=16, pw=64, detune=0, sub=0, cutoff=90, reso=30, fmode=0,
                 fatt=8, fdec=40, fsus=100, frel=40, fdepth=0, aatt=8, adec=40, asus=100,
                 arel=40, lforate=40, lfodep=0, trem=0, unison=0, porta=0,
                 reverb=0, chorusd=0, echod=0, dtime=63, room=64,
                 xmode=0, xdepth=0, xratio=0)


def decode(preset):
    """Raw CC dict (synthspec values) -> engine scalar params (mirrors synth.x CC routing)."""
    p = dict(_DEFAULTS); p.update({k: int(v) for k, v in preset.items() if k in _DEFAULTS})
    rate = lambda cc: TIME_INC[(cc >> 4) & 7]
    return dict(
        wave=(p['wave'] >> 4) & 7,
        cutoff_base=p['cutoff'] * 39,
        reso=max(800, 4000 - p['reso'] * 25),
        fdepth=p['fdepth'],
        lfo_rate=p['lforate'] * 16000,
        lfo_depth=p['lfodep'],
        fmode=(p['fmode'] >> 5) & 3,
        subsel=(p['sub'] >> 5) & 3,
        pw=p['pw'],
        detsel=(p['detune'] >> 5) & 3,
        portsel=(p['porta'] >> 5) & 3,
        tdsel=(p['trem'] >> 5) & 3,
        unison=(p['unison'] >> 5) & 3,
        xmode=(p['xmode'] >> 5) & 3, xdepth=p['xdepth'], xratio=(p['xratio'] >> 4) & 7,   # 3-bit ratio
        a_att=rate(p['aatt']), a_dec=rate(p['adec']), a_sus=p['asus'] << 9, a_rel=rate(p['arel']),
        f_att=rate(p['fatt']), f_dec=rate(p['fdec']), f_sus=p['fsus'] << 9, f_rel=rate(p['frel']),
        # Effects are depth-gated, not mode-selected: each is on iff its own depth is nonzero
        # (top.v:210-211). CC83 "fx" used to pick one of five modes and is dead on both boards.
        revwet=p['reverb'], chdep=p['chorusd'], echodep=p['echod'], dtime=p['dtime'],
        rsize=(p['room'] >> 5) & 3,
    )


@njit(cache=True, fastmath=False)
def _adsr(env, st, att, dec, sus, rel):
    if st == 1:                                        # ATTACK
        if env + att >= 65535:
            return 65535, 2
        return env + att, 1
    elif st == 2:                                      # DECAY
        if env <= sus + dec:
            return sus, 3
        return env - dec, 2
    elif st == 3:                                      # SUSTAIN
        return env, 3
    elif st == 4:                                      # RELEASE
        if env <= rel:
            return 0, 0
        return env - rel, 4
    return 0, 0                                        # OFF


@njit(cache=True, fastmath=False)
def _voice_wave(wave, t, noise, pwthr, sine):
    """One oscillator sample, mirroring core/synth.x:voice_wave (a u3 selector over FIVE waves).

    Kept as a function, and called for the detune oscillator too, because the two used to be
    written out separately and drifted: `wave` selects only 0..4, and the RTL's catch-all `_` is
    SINE, so indices 5-7 (CC70 >= 80) are sine on the board. The model returned noise there.
    """
    if wave == 0:
        return sine[t]
    elif wave == 1:
        return t * 16 - 2048
    elif wave == 2:
        return 2047 if t < pwthr else -2047
    elif wave == 3:
        ff = t if t < 128 else 255 - t
        return ff * 32 - 2048
    elif wave == 4:
        return noise
    return sine[t]                                     # 5..7 fall through to sine, as in the RTL


@njit(cache=True, fastmath=False)
def _core(n, gate, note, vel, ph, ph2, uni, tgt, portsel,
          wave, pwbase, subsel, detsel, cutoff_base, reso, fdepth, fmode,
          lfo_rate, lfo_depth, tdsel, xmode, xdepth, xratio,
          a_att, a_dec, a_sus, a_rel, f_att, f_dec, f_sus, f_rel, comp, sine):
    nv = ph.shape[0]
    out = np.zeros(n, dtype=np.float64)
    env = np.zeros(nv, dtype=np.int64); env_st = np.ones(nv, dtype=np.int64)
    fenv = np.zeros(nv, dtype=np.int64); fenv_st = np.ones(nv, dtype=np.int64)
    flo = np.zeros(nv); fbnd = np.zeros(nv)
    subhi = np.zeros(nv, dtype=np.int64)
    cinc = np.empty(nv, dtype=np.int64)
    for v in range(nv):
        cinc[v] = tgt[v] if portsel == 0 else 0
    lfsr = 0xACE1
    lfo_ph = 0
    ktrack = note * 16
    for t in range(n):
        lfo_raw = sine[(lfo_ph >> 24) & 255]
        lfo_mod = (lfo_raw * lfo_depth) >> 8
        lfoU = (lfo_raw >> 6) + 32
        if tdsel == 0:
            tg = 64
        elif tdsel == 1:
            tg = 64 - ((64 - lfoU) >> 2)
        elif tdsel == 2:
            tg = 64 - ((64 - lfoU) >> 1)
        else:
            tg = lfoU
        if tg < 0:
            tg = 0
        elif tg > 64:
            tg = 64
        pwthr = (pwbase << 1) + (lfo_mod >> 4)
        if pwthr < 12:
            pwthr = 12
        elif pwthr > 244:
            pwthr = 244
        released = t >= gate
        acc = 0.0
        for v in range(nv):
            if released and 1 <= env_st[v] <= 3:
                env_st[v] = 4
            if released and 1 <= fenv_st[v] <= 3:
                fenv_st[v] = 4
            env[v], env_st[v] = _adsr(env[v], env_st[v], a_att, a_dec, a_sus, a_rel)
            fenv[v], fenv_st[v] = _adsr(fenv[v], fenv_st[v], f_att, f_dec, f_sus, f_rel)
            if portsel == 0:
                ci = tgt[v]
            else:
                pk = 9 if portsel == 1 else (11 if portsel == 2 else 13)
                ci = cinc[v] + ((tgt[v] - cinc[v]) >> pk)
            cinc[v] = ci
            inc = (ci << 6) & MASK
            inc = (inc + ((inc >> 9) * uni[v])) & MASK
            newph = (ph[v] + inc) & MASK
            if newph < ph[v]:
                subhi[v] ^= 1
            ph[v] = newph
            # 2nd-osc accumulator: DETUNE saw (xmode 0) or cross-osc MODULATOR (xmode>0).
            doff = 0 if detsel == 0 else (inc >> 9 if detsel == 1 else (inc >> 8 if detsel == 2 else inc >> 7))
            # modulator ratio (mod:carrier) via shifts/adds — 8 options incl. inharmonic FM ratios
            if xratio == 0:   mstep = inc                       # 1
            elif xratio == 1: mstep = (inc + (inc >> 1)) & MASK # 1.5
            elif xratio == 2: mstep = (inc << 1) & MASK         # 2
            elif xratio == 3: mstep = ((inc << 1) + inc) & MASK # 3
            elif xratio == 4: mstep = (inc << 2) & MASK         # 4
            elif xratio == 5: mstep = ((inc << 2) + inc) & MASK # 5
            elif xratio == 6: mstep = ((inc << 3) - inc) & MASK # 7
            else:             mstep = inc >> 1                  # 0.5
            nph2 = (ph2[v] + ((inc + doff) if xmode == 0 else mstep)) & MASK
            ph2[v] = nph2
            modsig = sine[(nph2 >> 24) & 255]                  # FM/ring modulator, ±2047
            # STRONG FM: index = modsig*xdepth (one multiply) scaled into the phase. FM (xmode 2)
            # reaches β~1.5 rad at full depth; FM+ (xmode 3) ~π. M19's shift index (β~0.1) was
            # far too weak to voice bells; this is the fix.
            if xmode >= 2:
                fmoff = ((modsig * xdepth) << (12 if xmode == 2 else 13)) & MASK
            else:
                fmoff = 0
            tt = ((newph + fmoff) & MASK) >> 24 & 255
            noise = (lfsr & 0xFFF) - 2048
            main = _voice_wave(wave, tt, noise, pwthr, sine)
            ring = (main * modsig) >> 11                        # ring product, ±2047
            # DETUNE 2nd osc uses the SAME waveform as the main. This was a hardcoded saw here,
            # which turned e.g. sine+detune into sine+saw -- core/synth.x:264 fixed it and the
            # model kept the old behaviour, so every preset with detune>0 over a non-saw wave was
            # scored against a sound the board has never made.
            det2 = _voice_wave(wave, (nph2 >> 24) & 255, noise, pwthr, sine)
            if xmode == 0:
                o12 = main if detsel == 0 else (main + det2) >> 1
            elif xmode == 1:                                    # ring: blend dry->ring by depth
                xb = (xdepth >> 5) & 3
                if xb == 0:
                    o12 = main
                elif xb == 1:
                    o12 = main - (main >> 2) + (ring >> 2)
                elif xb == 2:
                    o12 = (main >> 1) + (ring >> 1)
                else:
                    o12 = ring
            else:                                              # FM: modulation already in `main`
                o12 = main
            sub = 1800 if subhi[v] == 1 else -1800
            if subsel == 0:
                subm = 0
            elif subsel == 1:
                subm = sub >> 2
            elif subsel == 2:
                subm = sub >> 1
            else:
                subm = sub
            w = o12 + subm
            e7 = env[v] >> 9
            g7 = (e7 * vel) >> 7
            g7t = (g7 * tg) >> 6
            amp = w * g7t
            fmod = ((fenv[v] >> 6) * fdepth) >> 7
            fsum = cutoff_base + ktrack + fmod + lfo_mod
            f = 60 if fsum < 60 else (4095 if fsum > 4095 else fsum)   # 12-bit cap (see synth.x)
            x = amp / 4.0
            low = flo[v]; band = fbnd[v]
            low1 = low + (f * band) / 8192.0
            low1 = 131072.0 if low1 > 131072 else (-131072.0 if low1 < -131072 else low1)
            high = x - low1 - (reso * band) / 8192.0
            high = 180000.0 if high > 180000 else (-180000.0 if high < -180000 else high)
            band1 = band + (f * high) / 8192.0
            band1 = 131072.0 if band1 > 131072 else (-131072.0 if band1 < -131072 else band1)
            low2 = low1 - (low1 / 128.0)          # DE-LATCH leak (mirror RTL >>7 / >>6): keeps the
            band2 = band1 - (band1 / 64.0)        # fixed-point filter from sustaining a full-scale latch
            flo[v] = low2; fbnd[v] = band2
            if fmode == 0:
                filt = low2
            elif fmode == 1:
                filt = high
            elif fmode == 2:
                filt = band2
            else:
                filt = low2 + high
            acc += (filt * 4.0 * comp) / 256.0
            lfsr = (lfsr >> 1) ^ (0xB400 if (lfsr & 1) else 0)
        lfo_ph = (lfo_ph + lfo_rate) & MASK
        s = acc / 32.0
        s = 32767.0 if s > 32767 else (-32767.0 if s < -32767 else s)
        out[t] = s / 32768.0
    return out


# ---- effects -------------------------------------------------------------------------------
# A third transcription of the same block: top.v:159-400 (Basys 3, 32 kHz) and
# boards/tiliqua/gateware/fx_model.py (Tiliqua, the same constants x3/2 for 48 kHz) are the other
# two. The constants below are the 32 kHz originals, which is this model's rate, so the Tiliqua's
# scaling cancels out — same RT60, same chorus rate, same echo time, by construction.
#
# Rewritten in M27. What was here modelled a synth that shipped years ago: four combs where the
# hardware has eight, two all-pass where it has four, and a CC83 mode selector that both boards
# now ignore. Every preset in every bank was fitted against it.
#
# Integer-exact, following fx_model.py truncation for truncation, rather than the float
# approximation it replaces: the input is quantised to 16 bits at the boundary and the arithmetic
# from there is the gateware's. Stereo, because the ping-pong echo and the anti-phase chorus are
# only meaningful across two channels -- render() returns channel 0, which is the channel the
# graded suite and validate_hw.py capture.
CL = np.array([810, 878, 940, 1012, 1066, 1122, 1176, 1230], dtype=np.int64)   # 8 comb delays
AL = np.array([403, 320, 247, 163], dtype=np.int64)                            # 4 all-pass delays
SPREAD = 23                                        # R delay lengths = L + SPREAD (stereo image)
DELAYS = np.concatenate((CL, AL))
NCOMB, NREG = len(CL), len(DELAYS)
_LEN = DELAYS + SPREAD                             # each region is as long as its R-channel delay
REGION = np.concatenate((np.zeros(1, dtype=np.int64), np.cumsum(_LEN)[:-1]))
TANK_WORDS = int(_LEN.sum())

CH_BASE, CH_SWEEP, CH_WORDS = 2400, 2048, 1024     # Q3 chorus tap: 300.0 .. 556.0 samples
LFO_PERIOD = CH_SWEEP * 16                         # 32768 samples -> 0.977 Hz at 32 kHz
ECHO_MAX = 16384                                   # dmemL/dmemR depth in boards/basys3/rtl/top.v
RVG = np.array([22000, 26000, 29000, 31200], dtype=np.int64)   # room/hall/large/cathedral


@njit(cache=True)
def _sat(x):
    return 32767.0 if x > 32767 else (-32768.0 if x < -32768 else x)


@njit(cache=True)
def _sat16(x):
    return -32768 if x < -32768 else (32767 if x > 32767 else x)


@njit(cache=True)
def _wrap16(x):
    """Assignment into a 16-bit signed target: truncate, do not clamp."""
    x = x & 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


@njit(cache=True)
def _fx(dry, revwet, chdep, echodep, dtime, rvg, delays, region, tank_words):
    """Stereo effects chain -> channel 0. Mirrors fx_model.FxModel.step, one sample at a time."""
    n = dry.shape[0]
    out = np.empty(n, dtype=np.float64)
    echo_on = echodep != 0
    chorus_on = chdep != 0
    # boards/basys3/rtl/top.v:170 is `edly = {dtime, 7'd0} | 14'd128`, 14 bits wide -- an OR, not
    # the addition this used to be. Bit 7 of `dtime<<7` is dtime's bit 0, so the floor only lands
    # on EVEN dtime; for odd dtime the OR does nothing and the delay is 128 samples (4 ms) shorter
    # than the model made it. That is why the sweep flagged dtime 85 and 127 and passed 0 and 42.
    edly = ((dtime << 7) | 128) & 0x3FFF
    wetgn = revwet << 8
    chdep_q15 = chdep << 8
    echdep_q15 = echodep << 8

    echo = np.zeros((2, ECHO_MAX), dtype=np.int64)
    tank = np.zeros((2, tank_words), dtype=np.int64)
    chor = np.zeros((2, CH_WORDS), dtype=np.int64)
    cp = np.zeros((2, NREG), dtype=np.int64)           # per-region rotating pointers
    dlp = np.zeros((2, NCOMB), dtype=np.int64)         # comb damping state
    ctap = np.zeros(2, dtype=np.int64)
    echod = np.zeros(2, dtype=np.int64)
    chint = np.zeros(2, dtype=np.int64)
    ecw = np.zeros(2, dtype=np.int64)
    revw = np.zeros(2, dtype=np.int64)
    wp = 0
    cwaddr = 0
    lfo = 0

    for t in range(n):
        raws = _sat16(int(dry[t] * 32768.0))           # back to the engine's 16-bit domain

        # --- chorus LFO (advanced on sample intake, as the FSM does in IDLE) ---
        lfo = 0 if lfo == LFO_PERIOD - 1 else lfo + 1
        fold = lfo if lfo < LFO_PERIOD // 2 else LFO_PERIOD - 1 - lfo
        ctap[0] = CH_BASE + (fold >> 3)
        ctap[1] = CH_BASE + CH_SWEEP - 1 - (fold >> 3)     # anti-phase, for width

        for c in range(2):
            echod[c] = echo[c, (wp - edly) % ECHO_MAX]
            # chorus tap, interpolated at quarter-sample resolution
            cti = ctap[c] >> 3
            cfr = (ctap[c] & 7) >> 1
            s0 = chor[c, (cwaddr - cti) % CH_WORDS]
            s1 = chor[c, (cwaddr - cti - 1) % CH_WORDS]
            d = s1 - s0
            if cfr == 0:
                cble = 0
            elif cfr == 1:
                cble = d >> 2
            elif cfr == 2:
                cble = d >> 1
            else:
                cble = (d >> 1) + (d >> 2)
            chint[c] = _wrap16(s0 + cble)

        # --- ping-pong history write: L stores dry + half of what R just read ---
        for c in range(2):
            wr = _sat16(raws + ((echod[1 - c] >> 1) if echo_on else 0))
            echo[c, wp] = wr
            chor[c, cwaddr] = wr
        wp = (wp + 1) % ECHO_MAX
        cwaddr = (cwaddr + 1) % CH_WORDS

        for c in range(2):
            ew = _wrap16((echdep_q15 * echod[c]) >> 15) if echo_on else 0
            cw = _wrap16((chdep_q15 * chint[c]) >> 15) if chorus_on else 0
            ecw[c] = _sat16(raws + ew + cw)

        # --- Freeverb tank: 8 combs then 4 all-pass, L then R ---
        # Runs unconditionally, as the gateware does: with revwet == 0 the wet multiply zeroes it
        # out anyway, and running it keeps the tank primed for when the knob comes up.
        rin = _wrap16((ecw[0] + ecw[1]) >> 6)
        for c in range(2):
            acc = 0
            for i in range(NCOMB):
                a = region[i] + cp[c, i]
                drd = tank[c, a]
                nlp = _wrap16(dlp[c, i] + ((drd - dlp[c, i] + 1) >> 1))
                dlp[c, i] = nlp
                cbn = _sat16(rin + _wrap16((rvg * nlp + 16384) >> 15))
                tank[c, a] = cbn
                acc += cbn
            csr = _sat16(acc >> 2)
            apy = 0
            for j in range(NCOMB, NREG):
                a = region[j] + cp[c, j]
                drd = tank[c, a]
                apin = csr if j == NCOMB else apy
                tank[c, a] = _sat16(apin + (drd >> 1))
                apy = _sat16(drd - (apin >> 1))
            revw[c] = apy

        for c in range(2):
            for i in range(NREG):
                ln = delays[i] + (SPREAD if c else 0)
                cp[c, i] = 0 if cp[c, i] == ln - 1 else cp[c, i] + 1

        out[t] = _sat16(ecw[0] + _wrap16((wetgn * revw[0]) >> 15)) / 32768.0
    return out


def render(preset, note=60, gate_s=1.2, tail_s=1.0, vel=100, fx=True):
    """Render a preset (raw-CC dict) to mono float audio at SR. Returns np.float32 in [-1,1]."""
    d = decode(preset)
    n = int((gate_s + tail_s) * SR)
    gate = int(gate_s * SR)
    nvoices = d['unison'] + 1
    comp = (256, 181, 148, 128)[d['unison']]
    tgt0 = note_inc(note) >> 6
    ph = np.empty(nvoices, dtype=np.int64); ph2 = np.empty(nvoices, dtype=np.int64)
    uni = np.empty(nvoices, dtype=np.int64); tgt = np.empty(nvoices, dtype=np.int64)
    lfsr = 0xACE1
    for cnt in range(nvoices):
        seed = ((lfsr << 16) ^ (cnt << 29)) & MASK
        ph[cnt] = seed
        ph2[cnt] = seed ^ 0x5a5a5a5a
        uni[cnt] = cnt * 2 - (nvoices - 1)
        tgt[cnt] = tgt0
    dry = _core(n, gate, note, vel, ph, ph2, uni, tgt, d['portsel'],
                d['wave'], d['pw'], d['subsel'], d['detsel'], d['cutoff_base'], d['reso'],
                d['fdepth'], d['fmode'], d['lfo_rate'], d['lfo_depth'], d['tdsel'],
                d['xmode'], d['xdepth'], d['xratio'],
                d['a_att'], d['a_dec'], d['a_sus'], d['a_rel'],
                d['f_att'], d['f_dec'], d['f_sus'], d['f_rel'], comp, SINE)
    # Skipping a fully-dry chain is not just an optimisation: the tank is 12 regions x 2 channels
    # per sample, and every bank preset is dry, so this is the common path.
    if fx and (d['revwet'] or d['chdep'] or d['echodep']):
        dry = _fx(dry, d['revwet'], d['chdep'], d['echodep'], d['dtime'],
                  int(RVG[d['rsize']]), DELAYS, REGION, TANK_WORDS)
    return dry.astype(np.float32)


if __name__ == "__main__":
    import numpy.fft as fft
    def w(**kw):
        from webui import synthspec  # not needed; build a raw dict directly
    def _s(v): return (v & 3) << 5
    def _w(v): return (v & 7) << 4
    def peaks(sig, top=6):
        W = sig[int(0.2*SR):int(0.2*SR)+8192]
        if len(W) < 8192: return []
        mag = np.abs(fft.rfft(W * np.hanning(len(W))))
        fr = fft.rfftfreq(len(W), 1/SR)
        idx = np.argsort(mag)[::-1]
        got = []
        for i in idx:
            if fr[i] < 60: continue
            if all(abs(fr[i]-g) > 30 for g in got): got.append(round(fr[i]))
            if len(got) >= top: break
        return sorted(got)
    print("SR", SR, "SINE peak", int(SINE.max()), "note_inc(69)=", note_inc(69),
          " -> Hz", round(note_inc(69)/2**32*SR, 1))
    for name, wv in [("sine", 0), ("saw", 1), ("square", 2), ("tri", 3)]:
        a = render({'wave': _w(wv), 'cutoff': 127, 'reso': 0, 'asus': 127, 'aatt': 0}, note=69)
        print(f"  {name:6} A4 peaks: {peaks(a)}   rms={np.sqrt(np.mean(a**2)):.3f}")
    # cutoff sweep on saw: brightness should drop as cutoff drops
    for cc in (127, 60, 20):
        a = render({'wave': _w(1), 'cutoff': cc, 'reso': 0, 'asus': 127, 'aatt': 0}, note=57)
        W = a[int(0.2*SR):int(0.2*SR)+8192]
        mag = np.abs(fft.rfft(W*np.hanning(len(W)))); fr = fft.rfftfreq(len(W), 1/SR)
        cen = (fr*mag).sum()/mag.sum()
        print(f"  saw cutoff CC={cc:3}: spectral centroid={cen:6.0f} Hz")
    # ADSR attack time: CC20 -> time to 50%
    for cc in (0, 64, 120):
        a = render({'wave': _w(1), 'cutoff': 100, 'aatt': cc, 'asus': 127}, note=60, gate_s=2.5, tail_s=0.1)
        env = np.abs(a); pk = env.max()
        t50 = np.argmax(env > 0.5*pk)/SR*1000 if pk > 0 else -1
        print(f"  amp attack CC20={cc:3}: ~{t50:.0f} ms to 50%")
