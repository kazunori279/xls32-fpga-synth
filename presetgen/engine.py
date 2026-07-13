"""Sample-accurate software model of the XLS FPGA synth (rtl/synth.x + rtl/top.v effects).

Renders (preset CC dict, note, gate) -> mono float audio at 28 kHz, reproducing the engine's
exact arithmetic (naive aliasing oscillators, Chamberlin SVF, dual ADSR, unison, LFO, VCA,
Freeverb-style effects) so an offline parameter search matches what the board actually does.
numba JITs the recursive per-sample kernels so a CMA-ES search is fast enough.

Constants/formulas verified against synth.x (SINE/BASE_INC/TIME_INC, svf, adsr, process_voice,
scale_mix) and top.v (chorus/echo/reverb). CC decode mirrors the firmware's bit-packing.

The board is 4-part multitimbral (one patch per MIDI channel 0-3); this model renders ONE part
(the per-voice DSP is identical across parts), which is exactly what the per-preset CMA-ES search
needs. Any multi-part integration render belongs in test/.
"""
import numpy as np
from numba import njit

SR = 28000
MASK = 0xFFFFFFFF

# 256-entry sine LUT, s16 range ±2047 (synth.x line 1 = round(2047*sin(2πi/256))).
SINE = np.round(2047 * np.sin(2 * np.pi * np.arange(256) / 256)).astype(np.int64)
# phase increment for the lowest octave at 28 kHz; note_inc(n) = BASE_INC[n%12] << (n//12).
BASE_INC = np.array([1254100, 1328672, 1407679, 1491384, 1580066, 1674022,
                     1773565, 1879026, 1990759, 2109136, 2234551, 2367425], dtype=np.int64)
TIME_INC = [640, 200, 90, 45, 20, 9, 4, 1]           # ADSR A/D/R increment LUT (index = cc>>4)


def note_inc(note):
    n = note & 0x7f
    return int(BASE_INC[n % 12]) << (n // 12)


# synthspec control defaults (raw CC values) so a partial preset dict still renders.
_DEFAULTS = dict(wave=16, pw=64, detune=0, sub=0, cutoff=90, reso=30, fmode=0,
                 fatt=8, fdec=40, fsus=100, frel=40, fdepth=0, aatt=8, adec=40, asus=100,
                 arel=40, lforate=40, lfodep=0, trem=0, unison=0, porta=0, fx=0, room=96,
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
        fx=(p['fx'] >> 4) & 7, room=(p['room'] >> 5) & 3,
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
            if wave == 0:
                main = sine[tt]
            elif wave == 1:
                main = tt * 16 - 2048
            elif wave == 2:
                main = 2047 if tt < pwthr else -2047
            elif wave == 3:
                ff = tt if tt < 128 else 255 - tt
                main = ff * 32 - 2048
            else:
                main = (lfsr & 0xFFF) - 2048
            ring = (main * modsig) >> 11                        # ring product, ±2047
            saw2 = ((nph2 >> 24) & 255) * 16 - 2048             # detune saw (xmode 0)
            if xmode == 0:
                o12 = main if detsel == 0 else (main + saw2) >> 1
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


# ---- effects (mono; top.v constants; ping-pong echo collapses to plain feedback in mono) ----
CL = np.array([810, 878, 940, 1012], dtype=np.int64)   # comb delays
AL = np.array([348, 116], dtype=np.int64)              # all-pass delays
EDLY = 8000
RVG = {0: 22000, 1: 26000, 2: 29000, 3: 31200}


@njit(cache=True)
def _sat(x):
    return 32767.0 if x > 32767 else (-32768.0 if x < -32768 else x)


@njit(cache=True)
def _fx(dry, fx, rvg):
    n = dry.shape[0]
    out = np.empty(n, dtype=np.float64)
    if fx == 0:
        return dry.copy()
    echo_on = (fx == 2) or (fx == 3)
    chorus_on = (fx == 1) or (fx == 3)
    if fx != 4:                                        # chorus / echo / both
        buf = np.zeros(16384)
        wp = 0
        clfo = 0
        for t in range(n):
            d = dry[t] * 32768.0                        # back to ±32k scale
            tri = (2047 - ((clfo >> 3) & 2047)) if (clfo >> 14) & 1 else ((clfo >> 3) & 2047)
            tapq = 2400 + tri; ti = tapq >> 3; fr2 = (tapq >> 1) & 3    # tap + quarter-sample fraction
            echod = buf[(wp - EDLY) % 16384]
            s0 = buf[(wp - ti) % 16384]; s1 = buf[(wp - ti - 1) % 16384]
            chor = s0 + (s1 - s0) * fr2 / 4.0                           # LINEAR INTERP: no tap-jump zipper
            wet = (echod / 2.0 if echo_on else 0.0) + (chor / 2.0 if chorus_on else 0.0)
            buf[wp] = _sat(d + (echod / 2.0 if echo_on else 0.0))
            wp = (wp + 1) % 16384
            clfo += 1
            out[t] = _sat(d + wet) / 32768.0
        return out
    # reverb: 4 combs + 2 all-pass (Freeverb)
    c0 = np.zeros(CL[0]); c1 = np.zeros(CL[1]); c2 = np.zeros(CL[2]); c3 = np.zeros(CL[3])
    dlp = np.zeros(4)
    a0 = np.zeros(AL[0]); a1 = np.zeros(AL[1])
    cp = np.zeros(4, dtype=np.int64); ap0 = 0; ap1 = 0
    for t in range(n):
        d = dry[t] * 32768.0
        rin = d / 8.0
        acc = 0.0
        for i in range(4):
            if i == 0:
                drd = c0[cp[0]]
            elif i == 1:
                drd = c1[cp[1]]
            elif i == 2:
                drd = c2[cp[2]]
            else:
                drd = c3[cp[3]]
            nlp = dlp[i] + (drd - dlp[i]) / 2.0
            dlp[i] = nlp
            cbn = _sat(rin + (rvg * nlp) / 32768.0)
            if i == 0:
                c0[cp[0]] = cbn
            elif i == 1:
                c1[cp[1]] = cbn
            elif i == 2:
                c2[cp[2]] = cbn
            else:
                c3[cp[3]] = cbn
            acc += cbn
        csr = acc / 4.0
        av0 = a0[ap0]; y0 = av0 - csr / 2.0
        a0[ap0] = _sat(csr + av0 / 2.0)
        av1 = a1[ap1]
        a1[ap1] = _sat(y0 + av1 / 2.0)
        wet = (av1 - y0 / 2.0) / 2.0
        out[t] = _sat(d + wet) / 32768.0
        for i in range(4):
            cp[i] = (cp[i] + 1) % CL[i]
        ap0 = (ap0 + 1) % AL[0]; ap1 = (ap1 + 1) % AL[1]
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
    if fx and d['fx'] != 0:
        dry = _fx(dry, d['fx'], RVG[d['room']])
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
        a = render({'wave': _w(wv), 'cutoff': 127, 'reso': 0, 'fx': 0, 'asus': 127, 'aatt': 0}, note=69)
        print(f"  {name:6} A4 peaks: {peaks(a)}   rms={np.sqrt(np.mean(a**2)):.3f}")
    # cutoff sweep on saw: brightness should drop as cutoff drops
    for cc in (127, 60, 20):
        a = render({'wave': _w(1), 'cutoff': cc, 'reso': 0, 'fx': 0, 'asus': 127, 'aatt': 0}, note=57)
        W = a[int(0.2*SR):int(0.2*SR)+8192]
        mag = np.abs(fft.rfft(W*np.hanning(len(W)))); fr = fft.rfftfreq(len(W), 1/SR)
        cen = (fr*mag).sum()/mag.sum()
        print(f"  saw cutoff CC={cc:3}: spectral centroid={cen:6.0f} Hz")
    # ADSR attack time: CC20 -> time to 50%
    for cc in (0, 64, 120):
        a = render({'wave': _w(1), 'cutoff': 100, 'aatt': cc, 'asus': 127, 'fx': 0}, note=60, gate_s=2.5, tail_s=0.1)
        env = np.abs(a); pk = env.max()
        t50 = np.argmax(env > 0.5*pk)/SR*1000 if pk > 0 else -1
        print(f"  amp attack CC20={cc:3}: ~{t50:.0f} ms to 50%")
