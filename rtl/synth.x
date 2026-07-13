const SINE: s16[256] = s16[256]:[0, 50, 100, 151, 201, 251, 300, 350, 399, 449, 497, 546, 594, 642, 690, 737, 783, 830, 875, 920, 965, 1009, 1052, 1095, 1137, 1179, 1219, 1259, 1299, 1337, 1375, 1411, 1447, 1483, 1517, 1550, 1582, 1614, 1644, 1674, 1702, 1729, 1756, 1781, 1805, 1828, 1850, 1871, 1891, 1910, 1927, 1944, 1959, 1973, 1986, 1997, 2008, 2017, 2025, 2032, 2037, 2041, 2045, 2046, 2047, 2046, 2045, 2041, 2037, 2032, 2025, 2017, 2008, 1997, 1986, 1973, 1959, 1944, 1927, 1910, 1891, 1871, 1850, 1828, 1805, 1781, 1756, 1729, 1702, 1674, 1644, 1614, 1582, 1550, 1517, 1483, 1447, 1411, 1375, 1337, 1299, 1259, 1219, 1179, 1137, 1095, 1052, 1009, 965, 920, 875, 830, 783, 737, 690, 642, 594, 546, 497, 449, 399, 350, 300, 251, 201, 151, 100, 50, 0, -50, -100, -151, -201, -251, -300, -350, -399, -449, -497, -546, -594, -642, -690, -737, -783, -830, -875, -920, -965, -1009, -1052, -1095, -1137, -1179, -1219, -1259, -1299, -1337, -1375, -1411, -1447, -1483, -1517, -1550, -1582, -1614, -1644, -1674, -1702, -1729, -1756, -1781, -1805, -1828, -1850, -1871, -1891, -1910, -1927, -1944, -1959, -1973, -1986, -1997, -2008, -2017, -2025, -2032, -2037, -2041, -2045, -2046, -2047, -2046, -2045, -2041, -2037, -2032, -2025, -2017, -2008, -1997, -1986, -1973, -1959, -1944, -1927, -1910, -1891, -1871, -1850, -1828, -1805, -1781, -1756, -1729, -1702, -1674, -1644, -1614, -1582, -1550, -1517, -1483, -1447, -1411, -1375, -1337, -1299, -1259, -1219, -1179, -1137, -1095, -1052, -1009, -965, -920, -875, -830, -783, -737, -690, -642, -594, -546, -497, -449, -399, -350, -300, -251, -201, -151, -100, -50];
// Phase increment per MIDI note. Octave structure: inc(n+12) == 2*inc(n), so we keep
// only the lowest octave (12 entries) and shift by the octave. This replaces a 128:1
// 32-bit LUT mux (~15ns) with a 12:1 mux + barrel shift (F4PGA/VPR array-index is an
// atomic one-stage op, so a wide LUT can't be pipeline-split). Rounding vs the exact
// 128-entry table is <30 ULP on inc -> inaudible.
// Increments for a 32 kHz sample rate (SAMPDIV 3125). With DSP48 multipliers (Vivado backend)
// the engine critical path drops to ~19.5 ns; ÷2 (20 ns) latched the SVF under stress, so it
// runs at ÷3 (30 ns) and still sustains a true 32 kHz stream in real time — the earlier 28 kHz
// values were a ÷4/soft-multiplier compromise. (The filter/reverb constants were always
// 32 kHz-native, so they become correct here too.)
const BASE_INC: u32[12] = u32[12]:[1097338, 1162588, 1231719, 1304961, 1382558, 1464769,
                                    1551869, 1644148, 1741914, 1845494, 1955232, 2071497];
fn note_inc(note: u8) -> u32 {
    let n = note & u8:0x7f;
    BASE_INC[(n % u8:12) as u32] << ((n / u8:12) as u32)
}
// Default ADSR (used to seed the engine so behavior is unchanged until a CC arrives).
const ATT_INC: u16 = u16:51;
const DEC_INC: u16 = u16:26;
const SUS_LEVEL: u16 = u16:40000;
const REL_INC: u16 = u16:20;
// ADSR is now MIDI-CC controllable (CC20-27). A/D/R knobs (7-bit) map to a per-sample
// increment via a small 8-entry LUT (higher knob = longer time = smaller increment),
// spanning ~3 ms .. ~2 s at 32 kHz. Sustain maps linearly to a 16-bit level. Only an
// 8:1 mux (index = cc>>4) and no multiply, so it's cheap for F4PGA/VPR packing. These
// params are engine-level (added once, not per-voice), so the Voice ring width is
// untouched. att/env are never 0 (min inc 1), so the attack always completes.
const TIME_INC: u16[8] = u16[8]:[640, 200, 90, 45, 20, 9, 4, 1];
fn adsr_rate(cc7: u8) -> u16 { TIME_INC[(cc7 >> u8:4) as u32] }   // 3.2ms .. 2.05s
fn adsr_sus(cc7: u8) -> u16 { (cc7 as u16) << u16:9 }             // 0 .. 65024

pub enum Env : u3 { OFF=0, ATTACK=1, DECAY=2, SUSTAIN=3, RELEASE=4 }
// flo/fbnd = per-voice Chamberlin SVF state; fenv/fenv_st = per-voice FILTER envelope
// (a 2nd ADSR that modulates this voice's cutoff -> a "pluck"). Each voice filters its
// own oscillator before the mix, so filtering is truly per-voice (M6b).
// Ring-state is the F4PGA packing budget, so widths are tight: `inc` isn't stored (it's
// recomputed from `note`); the filter state flo/fbnd is s19 (it only ever holds the
// SVF clamp, +-131072); `cinc` is the portamento-glided increment stored as inc>>6 (u26).
pub struct Voice { phase: u32, env: u16, env_st: Env, note: u8, vel: u8,
                   flo: s19, fbnd: s19, fenv: u16, fenv_st: Env, subhi: u1, ph2: u32, cinc: u26,
                   uni: s4, part: u2 }   // uni: unison slot; part: which timbre (MIDI channel 0-3)
// One timbre's patch (MULTITIMBRAL: 4 of these, one per MIDI channel 0-3). All sound-shaping
// Everything is per-part now, including the LFO *oscillator* (lfo_ph/lfo_rate) so each timbre
// has its own LFO speed & phase (CC76). Only the noise LFSR stays shared (in Eng).
pub struct Part { wave: u3, cutoff: u16, reso: u16, fdepth: u16,   // wave(70) cutoff(74) reso(71) fdepth(79)
                  fmode: u2, subsel: u2, pw: u16, detsel: u2,      // fmode(72) sub(73) pw(75) detune(78)
                  vibsel: u2, bend: s16, portsel: u2, trdep: u8,   // vib(CC1) bend(0xE0) porta(5) trem depth(92)
                  unison: u2, lfo_depth: u16, vol: u8,              // unison(80) LFO depth(77) volume(CC7)
                  lfo_ph: u32, lfo_rate: u32,                       // per-part LFO oscillator (CC76 rate)
                  xmode: u2, xdepth: u16, xratio: u3,               // cross-osc: CC85/86/87
                  a_att: u16, a_dec: u16, a_sus: u16, a_rel: u16,   // amp ADSR (CC20-23)
                  f_att: u16, f_dec: u16, f_sus: u16, f_rel: u16 }  // filter-env ADSR (CC24-27)
const DEFAULT_PART = Part { cutoff: u16:3000, reso: u16:2200, fdepth: u16:70, pw: u16:64,
                            lfo_rate: u32:670000, vol: u8:127,
                            a_att: ATT_INC, a_dec: DEC_INC, a_sus: SUS_LEVEL, a_rel: REL_INC,
                            f_att: ATT_INC, f_dec: DEC_INC, f_sus: SUS_LEVEL, f_rel: REL_INC,
                            ..zero!<Part>() };
pub struct Eng { voices: Voice[32], vidx: u5, mixacc: s32,
                 parts: Part[4],                              // the 4 timbres (MIDI ch 0-3)
                 lfsr: u16,                                   // shared white-noise generator
                 p_status: u8, p_data1: u8, p_cnt: u2 }

fn clampx(x: s32, lim: s32) -> s32 { if x > lim { lim } else if x < s32:0 - lim { s32:0 - lim } else { x } }

// Chamberlin state-variable filter, coefficients in Q13. State is clamped to ~18 bits
// so the f*band / q*band products stay ~13x18 (narrow soft-multipliers -- no DSP48 in
// F4PGA). Returns (low', band', lp, hp, bp) -- all four responses fall out for free
// (multimode); notch = lp + hp.
fn svf(low: s32, band: s32, x: s32, f: s32, q: s32) -> (s32, s32, s32, s32, s32) {
    let low1  = clampx(low + ((f * band) >> u32:13), s32:131072);
    let high  = clampx(x - low1 - ((q * band) >> u32:13), s32:180000);
    let band1 = clampx(band + ((f * high) >> u32:13), s32:131072);
    // DE-LATCH: leak the integrator state a hair each sample so a fixed-point overflow limit
    // cycle can't sustain full-scale (the clamp alone latches). Poles pulled just inside the
    // unit circle -> any self-oscillation decays. >>6/>>7 is ~1.5%/0.8%/sample: inaudible on
    // the frequency response but kills the latch. (Bright polyphonic patches were railing.)
    let low2  = low1  - (low1  >> u32:7);
    let band2 = band1 - (band1 >> u32:6);
    (low2, band2, low2, high, band2)
}

// Parametrized ADSR step (att/dec/rel = per-sample increments, sus = target level).
// Compares are done in u32 so a high sustain (~65024) plus a large decay step can't
// wrap u16. Shared by the amp envelope and the per-voice filter envelope, each fed its
// own params (CC20-23 amp, CC24-27 filter).
fn adsr(env: u16, st: Env, att: u16, dec: u16, sus: u16, rel: u16) -> (u16, Env) {
    match st {
        Env::ATTACK => if (env as u32) + (att as u32) >= u32:65535 { (u16:65535, Env::DECAY) } else { (env + att, Env::ATTACK) },
        Env::DECAY  => if (env as u32) <= (sus as u32) + (dec as u32) { (sus, Env::SUSTAIN) } else { (env - dec, Env::DECAY) },
        Env::SUSTAIN => (env, Env::SUSTAIN),
        Env::RELEASE => if env <= rel { (u16:0, Env::OFF) } else { (env - rel, Env::RELEASE) },
        _ => (u16:0, Env::OFF),
    }
}
// Returns a 12-bit-ish sample in s16 (|v| <= 2048). The narrow return type gives the
// optimizer a tight range bound so the downstream amp multiply narrows to ~16x7 bits
// instead of a full 32x32 soft-multiplier (F4PGA/VPR does not infer DSP48 blocks).
fn voice_wave(wave: u3, phase: u32, noise: s16, pw: u8) -> s16 {
    let t = phase[24:32];
    match wave {
        u3:0 => SINE[t],
        u3:1 => ((t as s16) * s16:16) - s16:2048,
        u3:2 => if t < pw { s16:2047 } else { s16:0 - s16:2047 },   // pulse: pw=duty (PWM)
        u3:3 => { let f = if t < u8:128 { t } else { u8:255 - t }; ((f as s16) * s16:32) - s16:2048 },
        u3:4 => noise,                                          // white noise (LFSR)
        _    => SINE[t],
    }
}
fn parse(status: u8, data1: u8, cnt: u2, mb: u8) -> (u8, u8, u2, u3, u8, u8) {
    if mb >= u8:0x80 { (mb, data1, u2:1, u3:0, u8:0, u8:0) }
    else if cnt == u2:1 { (status, mb, u2:2, u3:0, u8:0, u8:0) }
    else if cnt == u2:2 {
        let hi = status & u8:0xF0;
        let is_on = (hi == u8:0x90) && (mb != u8:0);
        let is_off = (hi == u8:0x80) || ((hi == u8:0x90) && (mb == u8:0));
        let is_cc = hi == u8:0xB0;
        let is_bend = hi == u8:0xE0;                // pitch bend: data1=lsb, mb=msb
        let k = if is_on { u3:1 } else if is_off { u3:2 } else if is_cc { u3:3 }
                else if is_bend { u3:4 } else { u3:0 };
        (status, data1, u2:1, k, data1, mb)
    } else { (status, data1, cnt, u3:0, u8:0, u8:0) }
}
// UNISON: allocate `un` free voices to one note (voice-stacking), not just one. Each gets
// a symmetric detune slot 2*cnt-(un-1) -> {-1,+1} / {-2,0,+2} / {-3,-1,+1,+3} (applied to
// the increment in process_voice) and a DECORRELATED start phase seeded from the LFSR
// (distinct per stack index) so the copies don't sum coherently at the attack (no N* spike
// -> headroom) and thicken immediately. un=1 -> slot 0 -> identical to the old behavior.
fn apply_on(voices: Voice[32], note: u8, vel: u8, porta: u1, un: u3, lfsr: u16, part: u2) -> Voice[32] {
    let res = for (i, acc): (u32, (Voice[32], u3)) in u32:0..u32:32 {
        let (vs, cnt) = acc;
        if cnt < un && vs[i].env_st == Env::OFF {
            // portamento: start the glided increment from this voice's PREVIOUS pitch
            // (glide from there to the new note); otherwise snap straight to the target.
            let cinc0 = if porta == u1:1 { vs[i].cinc } else { (note_inc(note) >> u32:6) as u26 };
            let slot = ((cnt as s8) * s8:2 - ((un as s8) - s8:1)) as s4;
            let seed = ((lfsr as u32) << u32:16) ^ ((cnt as u32) << u32:29);
            (update(vs, i, Voice { phase: seed, env: u16:0, env_st: Env::ATTACK, note: note, vel: vel, flo: s19:0, fbnd: s19:0, fenv: u16:0, fenv_st: Env::ATTACK, subhi: u1:0, ph2: seed ^ u32:0x5a5a5a5a, cinc: cinc0, uni: slot, part: part }), cnt + u3:1)
        } else { (vs, cnt) }
    }((voices, u3:0));
    let (vs, _) = res; vs
}
fn apply_off(voices: Voice[32], note: u8, part: u2) -> Voice[32] {
    for (i, vs): (u32, Voice[32]) in u32:0..u32:32 {
        let v = vs[i];
        if v.note == note && v.part == part && v.env_st != Env::OFF && v.env_st != Env::RELEASE { update(vs, i, Voice { env_st: Env::RELEASE, fenv_st: Env::RELEASE, ..v }) } else { vs }
    }(voices)
}
// Apply one CC to a single part's patch (per-part CC routing). The effects (83/91) are global
// and handled by the shell. Bend (0xE0) is handled in next().
fn apply_cc(p: Part, evn: u8, evv: u8) -> Part {
    let r = s32:4000 - (evv as s32) * s32:25;                  // CC71 reso: higher CC -> more resonance
    let reso_v = (if r < s32:800 { s32:800 } else { r }) as u16;
    match evn {
        u8:70 => Part { wave: evv[4:7], ..p },
        u8:74 => Part { cutoff: (evv as u16) * u16:39, ..p },
        u8:71 => Part { reso: reso_v, ..p },
        u8:79 => Part { fdepth: evv as u16, ..p },
        u8:77 => Part { lfo_depth: evv as u16, ..p },
        u8:76 => Part { lfo_rate: (evv as u32) * u32:16000, ..p },   // per-part LFO rate (~0.1..15 Hz)
        u8:72 => Part { fmode: evv[5:7], ..p },
        u8:73 => Part { subsel: evv[5:7], ..p },
        u8:75 => Part { pw: evv as u16, ..p },
        u8:78 => Part { detsel: evv[5:7], ..p },
        u8:1  => Part { vibsel: evv[5:7], ..p },
        u8:5  => Part { portsel: evv[5:7], ..p },
        u8:92 => Part { trdep: evv, ..p },                          // tremolo depth (continuous 0..127)
        u8:7  => Part { vol: evv, ..p },                            // CC7 per-part output volume
        u8:80 => Part { unison: evv[5:7], ..p },
        u8:85 => Part { xmode: evv[5:7], ..p },
        u8:86 => Part { xdepth: evv as u16, ..p },
        u8:87 => Part { xratio: evv[4:7], ..p },
        u8:20 => Part { a_att: adsr_rate(evv), ..p },
        u8:21 => Part { a_dec: adsr_rate(evv), ..p },
        u8:22 => Part { a_sus: adsr_sus(evv),  ..p },
        u8:23 => Part { a_rel: adsr_rate(evv), ..p },
        u8:24 => Part { f_att: adsr_rate(evv), ..p },
        u8:25 => Part { f_dec: adsr_rate(evv), ..p },
        u8:26 => Part { f_sus: adsr_sus(evv),  ..p },
        u8:27 => Part { f_rel: adsr_rate(evv), ..p },
        _ => p,
    }
}
fn process_voice(v: Voice, wave: u3, cutoff: u16, reso: u16, lfo_mod: s32, fdepth: u16,
                 noise: s16, fmode: u2, subsel: u2, pw: u8, detsel: u2, pmod: s32, portsel: u2,
                 xmode: u2, xdepth: u16, xratio: u3,
                 tg: u8, a_att: u16, a_dec: u16, a_sus: u16, a_rel: u16,
                 f_att: u16, f_dec: u16, f_sus: u16, f_rel: u16) -> (Voice, s32) {
    let (env_n, est_n) = adsr(v.env, v.env_st, a_att, a_dec, a_sus, a_rel);
    let (fenv_n, fest_n) = adsr(v.fenv, v.fenv_st, f_att, f_dec, f_sus, f_rel);  // per-voice filter env
    // Portamento: glide cinc (stored as inc>>6) toward the target note exponentially
    // (subtract + arithmetic-shift + add -- no multiply). portsel=0 snaps.
    let tgt = note_inc(v.note) >> u32:6;                       // target increment / 64
    let cinc_n = if portsel == u2:0 { tgt as u26 } else {
        let pk = match portsel { u2:1 => u32:9, u2:2 => u32:11, _ => u32:13 };
        ((v.cinc as s32) + (((tgt as s32) - (v.cinc as s32)) >> pk)) as u26
    };
    let inc0 = (cinc_n as u32) << u32:6;                       // effective base increment
    // pitch modulation (vibrato/bend): inc*(1 + pmod/4096), done as inc + (inc>>12)*pmod
    // so the multiply is ~19x10 bits (no 32x32 / no u64 overflow).
    // (pmod as s16) forces XLS to a 16-bit operand so this multiply is one DSP48 (pmod is clamped
    // to +-2047 in next(), so the truncation is lossless).
    let inc = (inc0 as s32 + (((inc0 >> u32:12) as s32) * (pmod as s16 as s32))) as u32;
    // unison detune: shift this voice's pitch by its stack slot (~3.4 cents/unit, inc>>9).
    // The slot grows with the voice count so the spread widens 2->4 voices; each stacked
    // voice has its own phase accumulator, so they beat against each other.
    let inc = (inc as s32 + ((inc >> u32:9) as s32) * (v.uni as s32)) as u32;
    let phase_n = v.phase + inc;
    // sub-oscillator one octave down: toggle a 1-bit square each time the main phase
    // wraps (a 32b/voice 2nd accumulator overflowed VPR's packer -> use 1 bit instead).
    let wrapped = phase_n < v.phase;                            // u32 overflow = one osc period
    let subhi_n = if wrapped { v.subhi ^ u1:1 } else { v.subhi };
    // 2nd-osc accumulator: DETUNE saw (xmode 0) OR cross-osc MODULATOR (xmode>0) -- mutually
    // exclusive uses of the one spare accumulator, so no new per-voice ring state. The
    // modulator runs at ratio*inc via shifts/adds -- 8 ratios incl. inharmonic FM ratios.
    let doff = match detsel { u2:0 => u32:0, u2:1 => inc >> u32:9,
                              u2:2 => inc >> u32:8, _ => inc >> u32:7 };
    let mstep = match xratio {                                 // mod:carrier  (all shift/add)
        u3:0 => inc,                                           // 1
        u3:1 => inc + (inc >> u32:1),                          // 1.5
        u3:2 => inc << u32:1,                                  // 2
        u3:3 => (inc << u32:1) + inc,                          // 3
        u3:4 => inc << u32:2,                                  // 4
        u3:5 => (inc << u32:2) + inc,                          // 5
        u3:6 => (inc << u32:3) - inc,                          // 7
        _    => inc >> u32:1 };                                // 0.5
    let ph2_n = v.ph2 + (if xmode == u2:0 { inc + doff } else { mstep });
    let modsig = SINE[ph2_n[24:32]];                           // s16 +-2047: FM/ring modulator
    // STRONG FM (xmode>=2): index = modsig * depth (one soft-multiply) scaled into the phase.
    // FM reaches beta~1.5 rad at full depth, FM+ ~pi -- M19's shift index (beta~0.1) was far
    // too weak to voice bells. Product <= 2047*127 < 2^18, so << 13 stays inside s32.
    let fmoff = if xmode >= u2:2 {
                    let midx = (modsig as s32) * (xdepth as s32);
                    (midx << (if xmode == u2:2 { u32:12 } else { u32:13 })) as u32
                } else { u32:0 };
    let main = voice_wave(wave, phase_n + fmoff, noise, pw);   // s16, |main| <= 2048
    // RING (xmode==1): main * modsig -- the ONE new soft-multiply -- blended dry->ring by
    // depth in 4 shift-based steps (no blend multiply).
    let ring = (((main as s32) * (modsig as s32)) >> u32:11) as s16;
    // DETUNE 2nd osc uses the SAME waveform as the main (was hardcoded to a saw, which turned
    // e.g. sine+detune into sine+saw). ph2_n runs slightly faster, so the two copies beat.
    let det2 = voice_wave(wave, ph2_n, noise, pw);
    let o12 = if xmode == u2:0 { if detsel == u2:0 { main } else { (main + det2) >> u16:1 } }
              else if xmode == u2:1 {
                  match xdepth[5:7] { u2:0 => main,
                                      u2:1 => main - (main >> u16:2) + (ring >> u16:2),
                                      u2:2 => (main >> u16:1) + (ring >> u16:1),
                                      _    => ring } }
              else { main };   // FM: modulation already carried in `main` via the offset phase
    // sub-osc square mixed in by a shift-based level (CC73) -- a 4-way select, NOT a
    // multiply (an extra soft-mult overflows VPR's SLICE packer).
    let sub = if subhi_n == u1:1 { s16:1800 } else { s16:0 - s16:1800 };
    let subm = match subsel { u2:0 => s16:0, u2:1 => sub >> u16:2, u2:2 => sub >> u16:1, _ => sub };
    let w = o12 + subm;                                         // |w| <= ~3850, fits s16
    // Collapse envelope (7-bit) and velocity (7-bit) into one 7-bit gain via a tiny
    // 8x8 multiply, then a single ~16x8 multiply for the sample. Both stay small.
    let e7 = (env_n >> u16:9) as u8;                            // 0..127
    let g7 = (((e7 as u16) * (v.vel as u16)) >> u16:7) as u8;   // 0..127
    let g7t = (((g7 as u16) * (tg as u16)) >> u16:6) as u8;     // TREMOLO: scale gain by LFO (tg/64)
    let amp = (w as s32) * (g7t as s32);                        // narrows to ~16x8
    // Per-voice cutoff = base (CC74) + KEY-TRACKING (brighter high notes) + FILTER
    // ENVELOPE (per-voice pluck) + global LFO. All per-voice except the LFO, so every
    // voice filters differently -- impossible with one master filter.
    let ktrack = (v.note as s32) * s32:16;                     // note 0..127 -> +0..2032
    let fmod = (((fenv_n >> u16:6) as s32) * (fdepth as s32)) >> u32:7;  // env pluck, 0..~1015
    let fsum = (cutoff as s32) + ktrack + fmod + lfo_mod;
    // Cap f at 4095 (12-bit) not 5000: shrinks the f*band multiply to 12x18, shaving the SVF
    // critical path so 4-part multitimbral fits the 40 ns budget with margin. Only the very
    // brightest patches at high notes lose a little top-end (coefficient ~0.5 vs 0.61).
    let f = if fsum < s32:60 { s32:60 } else if fsum > s32:4095 { s32:4095 } else { fsum };
    // Attenuate the input 4x so the resonant state stays off the clamp rails (which
    // would latch the filter); scale the selected output back up 4x afterwards.
    // &0x1FFF forces XLS to see f/q as 13-bit (they're already <=4095/<=4000 from the clamps
    // above), so the SVF f*band / q*band / f*high products map to a single DSP48 (25x18) instead
    // of the 22-bit operands that made yosys emit cascaded DSPs nextpnr can't route. No behavior change.
    let (lo, bd, lp, hp, bp) = svf(v.flo as s32, v.fbnd as s32, amp >> u32:2, f & s32:8191, (reso as s32) & s32:8191);
    let filt = match fmode { u2:0 => lp, u2:1 => hp, u2:2 => bp, _ => lp + hp };  // LP/HP/BP/notch
    // clamp bounds the returned amp to ~22 bits (|filt<<2| <= ~1.24M anyway) so the downstream
    // mix multiply amp*comp fits one DSP48 (else amp stays 30-bit -> cascaded DSP). No behavior change.
    (Voice { phase: phase_n, subhi: subhi_n, ph2: ph2_n, cinc: cinc_n, env: env_n, env_st: est_n,
             flo: lo as s19, fbnd: bd as s19, fenv: fenv_n, fenv_st: fest_n, ..v }, clampx(filt << u32:2, s32:2097151))
}
fn scale_mix(acc: s32) -> u16 {
    let s = acc >> u32:5;   // amp now ~/128 vs before (gain folded to 7 bits); 5 keeps loudness
    let c = if s > s32:32767 { s32:32767 } else if s < s32:0 - s32:32767 { s32:0 - s32:32767 } else { s };
    (c + s32:32768) as u16
}

// Rotate the ring left by one: [v[1], v[2], ..., v[31], tail].
// All indices are loop-constant (unrolled) -> wires, not a dynamic mux.
fn rotate_in(v: Voice[32], tail: Voice) -> Voice[32] {
    let shifted = for (i, acc): (u32, Voice[32]) in u32:0..u32:31 {
        update(acc, i, v[i + u32:1])
    }(v);
    update(shifted, u32:31, tail)
}

// Time-multiplexed voice engine. Voices live in a rotating ring so the "current"
// voice is ALWAYS at slot 0 -> constant-index read/write, no 32:1 mux (the 21.4ns
// wall was that dynamic `voices[vidx]` access). One voice/clock; 32 clocks/sample.
proc engine {
    midi_in: chan<u8> in;
    audio_out: chan<u16> out;
    viz_out: chan<u32> out;   // per-cycle {env[15:0], is_new@16, last@17} for the LED comet
    config(midi_in: chan<u8> in, audio_out: chan<u16> out, viz_out: chan<u32> out) {
        (midi_in, audio_out, viz_out)
    }
    init { Eng { parts: Part[4]:[DEFAULT_PART, DEFAULT_PART, DEFAULT_PART, DEFAULT_PART],
                 lfsr: u16:0xACE1, ..zero!<Eng>() } }
    next(st: Eng) {
        let (tok, mb, valid) = recv_non_blocking(join(), midi_in, u8:0);
        let (ps, pd, pc, evk, evn, evv) = if valid { parse(st.p_status, st.p_data1, st.p_cnt, mb) }
                                          else { (st.p_status, st.p_data1, st.p_cnt, u3:0, u8:0, u8:0) };
        let ch = ps[0:2];                                          // MIDI channel (low 2 bits) -> part 0-3
        let ep = st.parts[ch];                                     // the event's part patch (4:1 read)
        // --- note on/off route to the event's part; each voice is tagged with its part ---
        let porta = if ep.portsel == u2:0 { u1:0 } else { u1:1 };  // glide from prev pitch on note-on
        let uni_n = (ep.unison as u3) + u3:1;                       // 1/2/3/4 voices per note
        let voices1 = if evk == u3:1 { apply_on(st.voices, evn, evv, porta, uni_n, st.lfsr, ch) }
                      else if evk == u3:2 { apply_off(st.voices, evn, ch) } else { st.voices };
        // --- CC / pitch-bend route to the event's part (CC76 LFO rate handled per-part in apply_cc) ---
        let is_cc = evk == u3:3;
        // pitch bend (0xE0): 14-bit (msb=evv, lsb=evn), center 8192 -> Q12 offset ~+-2 semitones
        let bend_v = (((((evv as s32) << u32:7) | (evn as s32)) - s32:8192) >> u32:4) as s16;
        let ep1 = if is_cc { apply_cc(ep, evn, evv) }
                  else if evk == u3:4 { Part { bend: bend_v, ..ep } } else { ep };
        let parts1 = update(st.parts, ch, ep1);
        // white-noise LFSR (16-bit Galois); the LFO sine is read per-part below
        let lfsr1 = (st.lfsr >> u16:1) ^ (if (st.lfsr & u16:1) == u16:1 { u16:0xB400 } else { u16:0 });
        let noise = (st.lfsr[0:12] as s16) - s16:2048;            // ~+-2048 white noise
        // --- process the current ring-slot-0 voice using ITS part's patch (4:1 part mux) ---
        let cur = voices1[u32:0];
        let p = parts1[cur.part];
        let lfo_raw = SINE[p.lfo_ph[24:32]];                      // s16, +-2047 (this part's LFO phase)
        let lfo_mod = ((lfo_raw as s32) * (p.lfo_depth as s32)) >> u32:8;   // per-part cutoff LFO depth
        let vib = match p.vibsel { u2:0 => s32:0, u2:1 => (lfo_raw as s32) >> u32:6,
                                   u2:2 => (lfo_raw as s32) >> u32:5, _ => (lfo_raw as s32) >> u32:4 };
        // clamp keeps the pitch-mod multiply (inc0>>12)*pmod within a single DSP48 (else pmod
        // stays 32-bit and yosys splits it across cascaded DSPs that nextpnr can't route). |pmod|
        // is only ~640 in practice, so the clamp is a no-op on behavior.
        let pmod = clampx(vib + (p.bend as s32), s32:2047);   // vibrato + pitch bend (both per-part)
        let lfoU = ((lfo_raw as s32) >> u32:6) + s32:32;   // unipolar LFO ~0..64
        // TREMOLO: continuous depth (CC92). trdep=0 -> tg=64 (no trem); trdep=127 -> tg~=lfoU
        // (full gain swing). tg/64 scales the amp gain downstream. Small 7x7 multiply (off the SVF path).
        let tg = (s32:64 - (((s32:64 - lfoU) * (p.trdep as s32)) >> u32:7)) as u8;
        let pwr = ((p.pw << u16:1) as s32) + (lfo_mod >> u32:4);
        let pwthr = (if pwr < s32:12 { s32:12 } else if pwr > s32:244 { s32:244 } else { pwr }) as u8;
        let (v2, amp) = process_voice(cur, p.wave, p.cutoff, p.reso, lfo_mod, p.fdepth,
                                      noise, p.fmode, p.subsel, pwthr, p.detsel, pmod, p.portsel,
                                      p.xmode, p.xdepth, p.xratio, tg,
                                      p.a_att, p.a_dec, p.a_sus, p.a_rel, p.f_att, p.f_dec, p.f_sus, p.f_rel);
        let voices2 = rotate_in(voices1, v2);
        // unison gain compensation ~256/sqrt(N) -- uses the PROCESSED voice's part
        let comp = match p.unison { u2:0 => s32:256, u2:1 => s32:181, u2:2 => s32:148, _ => s32:128 };
        // Per-part VOLUME (CC7): fold into comp (both small, off the SVF path) so the mix stays a
        // single amp*compv DSP48. vol=127 -> ~unity. (amp as s24) forces a <=24-bit operand so
        // amp*compv is one DSP48 (amp is clamped to +-2^21 in process_voice, truncation lossless).
        let compv = (comp * (p.vol as s32)) >> u32:7;
        let mix1 = st.mixacc + (((amp as s24 as s32) * compv) >> u32:8);
        let last = st.vidx == u5:31;
        send_if(tok, audio_out, last, scale_mix(mix1));
        // advance every part's LFO phase once per sample (on the ring's last slot)
        let parts2 = if last {
            Part[4]:[Part { lfo_ph: parts1[u32:0].lfo_ph + parts1[u32:0].lfo_rate, ..parts1[u32:0] },
                     Part { lfo_ph: parts1[u32:1].lfo_ph + parts1[u32:1].lfo_rate, ..parts1[u32:1] },
                     Part { lfo_ph: parts1[u32:2].lfo_ph + parts1[u32:2].lfo_rate, ..parts1[u32:2] },
                     Part { lfo_ph: parts1[u32:3].lfo_ph + parts1[u32:3].lfo_rate, ..parts1[u32:3] }]
        } else { parts1 };
        // LED-comet tap: stream the slot-0 voice's live envelope every cycle.
        let is_new = (cur.env == u16:0) && (cur.env_st == Env::ATTACK);
        let viz = (v2.env as u32) | ((is_new as u32) << u32:16) | ((last as u32) << u32:17);
        send(tok, viz_out, viz);
        Eng { voices: voices2, vidx: if last { u5:0 } else { st.vidx + u5:1 },
              mixacc: if last { s32:0 } else { mix1 }, parts: parts2, lfsr: lfsr1,
              p_status: ps, p_data1: pd, p_cnt: pc }
    }
}
