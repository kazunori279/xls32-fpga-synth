# A pure-Python bit-exact model of StereoFx, for checking the gateware in seconds.
#
# This is not a "reference implementation" in the loose sense -- it is a second transcription of
# boards/basys3/rtl/top.v:159-400, written from the same constants in fx.py, whose only job is to
# disagree with the gateware when one of the two is wrong. Every truncation, every saturation and
# every shift is reproduced, so a mismatch is a real mismatch and not a modelling artefact.
#
# The one thing it does NOT model is timing: it computes a whole sample at a time and assumes the
# echo tap reads exactly `edly` samples back and the chorus taps exactly `cti`/`cti+1` back. That
# assumption is precisely what the FSM has to get right, so a shared assumption here would be a
# hole in the check -- but it is the only shared one, and test_fx.py drives the gateware through
# its real handshakes to close it.

from fx import (CH_BASE, CH_SWEEP, CH_WORDS, DELAYS, ECHO_MAX_DELAY, ECHO_MIN, ECHO_STEP,
                LFO_PERIOD, NCOMB, NREG, REGION, RVG, SPREAD, TANK_WORDS)


def sat16(x):
    return -32768 if x < -32768 else (32767 if x > 32767 else x)


def wrap16(x):
    """Assignment into a 16-bit signed target: truncate, do not clamp."""
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


class FxModel:

    def __init__(self, rsize=3, revwet=0, chdep=64, echodep=64, dtime=63,
                 max_echo=ECHO_MAX_DELAY):
        self.rsize, self.revwet = rsize, revwet
        self.chdep, self.echodep, self.dtime = chdep, echodep, dtime
        self.max_echo = max_echo

        self.echo = [[0] * max_echo for _ in range(2)]
        self.tank = [[0] * TANK_WORDS for _ in range(2)]
        self.chor = [[0] * CH_WORDS for _ in range(2)]
        self.wp = 0                                        # echo write pointer
        self.cwaddr = 0                                    # chorus ring write pointer
        self.cp = [[0] * NREG for _ in range(2)]           # per-region rotating pointers
        self.dlp = [[0] * NCOMB for _ in range(2)]         # comb damping state
        self.lfo = 0

    def region_len(self, chan, i):
        return DELAYS[i] + (SPREAD if chan else 0)

    def step(self, raws):
        echo_on = self.echodep != 0
        chorus_on = self.chdep != 0
        rvg = RVG[self.rsize]
        edly = (self.dtime * ECHO_STEP + ECHO_MIN) % self.max_echo
        wetgn = self.revwet << 8
        chdep_q15 = self.chdep << 8
        echdep_q15 = self.echodep << 8

        # --- chorus LFO (advanced on sample intake, as the FSM does in IDLE) ---
        self.lfo = 0 if self.lfo == LFO_PERIOD - 1 else self.lfo + 1
        fold = self.lfo if self.lfo < LFO_PERIOD // 2 else LFO_PERIOD - 1 - self.lfo
        ctri = [fold >> 3, CH_SWEEP - 1 - (fold >> 3)]
        ctap = [CH_BASE + t for t in ctri]

        # --- echo tap ---
        echod = [self.echo[c][(self.wp - edly) % self.max_echo] for c in range(2)]

        # --- chorus tap, interpolated at quarter-sample resolution ---
        chint = [0, 0]
        for c in range(2):
            cti, cfr = ctap[c] >> 3, (ctap[c] & 7) >> 1
            s0 = self.chor[c][(self.cwaddr - cti) % CH_WORDS]
            s1 = self.chor[c][(self.cwaddr - cti - 1) % CH_WORDS]
            d = s1 - s0
            cble = (0, d >> 2, d >> 1, (d >> 1) + (d >> 2))[cfr]
            chint[c] = wrap16(s0 + cble)

        echow = [wrap16((echdep_q15 * echod[c]) >> 15) for c in range(2)]
        chw = [wrap16((chdep_q15 * chint[c]) >> 15) for c in range(2)]

        # --- ping-pong history write: L stores dry + half of what R just read ---
        wr = [sat16(raws + (echod[1 - c] >> 1 if echo_on else 0)) for c in range(2)]
        for c in range(2):
            self.echo[c][self.wp] = wr[c]
            self.chor[c][self.cwaddr] = wr[c]
        self.wp = (self.wp + 1) % self.max_echo
        self.cwaddr = (self.cwaddr + 1) % CH_WORDS

        ecw = [sat16(raws + (echow[c] if echo_on else 0) + (chw[c] if chorus_on else 0))
               for c in range(2)]

        # --- Freeverb tank: 8 combs then 4 all-pass, L then R ---
        # Runs unconditionally, as the gateware does: with revwet == 0 the wet multiply zeroes
        # it out anyway, and running it keeps the tank primed for when the knob comes up.
        rin = wrap16((ecw[0] + ecw[1]) >> 6)
        revw = [0, 0]
        for c in range(2):
            acc = 0
            for i in range(NCOMB):
                a = REGION[i] + self.cp[c][i]
                drd = self.tank[c][a]
                nlp = wrap16(self.dlp[c][i] + ((drd - self.dlp[c][i] + 1) >> 1))
                self.dlp[c][i] = nlp
                fbm = wrap16((rvg * nlp + 16384) >> 15)
                cbn = sat16(rin + fbm)
                self.tank[c][a] = cbn
                acc += cbn
            csr = sat16(acc >> 2)
            apy = 0
            for j in range(NCOMB, NREG):
                a = REGION[j] + self.cp[c][j]
                drd = self.tank[c][a]
                apin = csr if j == NCOMB else apy
                self.tank[c][a] = sat16(apin + (drd >> 1))
                apy = sat16(drd - (apin >> 1))
            revw[c] = apy

        for c in range(2):
            for i in range(NREG):
                ln = self.region_len(c, i)
                self.cp[c][i] = 0 if self.cp[c][i] == ln - 1 else self.cp[c][i] + 1

        rwet = [wrap16((wetgn * revw[c]) >> 15) for c in range(2)]
        return [sat16(ecw[c] + rwet[c]) for c in range(2)]
