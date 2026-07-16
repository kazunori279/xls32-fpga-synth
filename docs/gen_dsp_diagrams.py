#!/usr/bin/env python3
"""Generate the DSP signal-flow SVGs in docs/ for ARCHITECTURE.md (Part D — effects).

These are proper signal-flow block diagrams (z^-D delay blocks, gain triangles, summing
junctions) — the notation Mermaid can't express. They mirror the WaveDrom -> committed-SVG
pattern: this script is the text source; the committed docs/dsp_*.svg display everywhere.

    uv sync --extra docs && uv run python docs/gen_dsp_diagrams.py

Outputs (all under docs/):
    dsp_delayline.svg  the circular-delay-line primitive shared by all three effects
    dsp_chorus.svg     chorus: LFO-swept read tap, no feedback
    dsp_echo.svg       echo: feedback delay, ping-pong L<->R
    dsp_comb.svg       reverb: one lowpass-feedback comb filter (x8)
    dsp_allpass.svg    reverb: one Schroeder all-pass diffuser (x4)
    dsp_freeverb.svg   reverb: full Freeverb topology (8 combs -> Sigma -> 4 all-pass)
    dsp_svf.svg        per-voice datapath: Chamberlin state-variable filter (B7)
    dsp_crossmod.svg   per-voice datapath: cross-osc FM / ring-mod (B6)
    dsp_datapath.svg   per-voice datapath overview (osc -> filter -> VCA, B intro)
    dsp_reverb_m14.svg dev-history: the M14 reverb (4 combs + 2 all-pass)
"""
import os

import schemdraw
import schemdraw.elements as elm
from schemdraw import dsp

schemdraw.use("svg")  # native SVG backend — no matplotlib dependency

DOCS = os.path.dirname(os.path.abspath(__file__))


def out(name):
    return os.path.join(DOCS, name)


# ---------------------------------------------------------------------------
# 1. The delay-line primitive: one circular buffer, write head + trailing read tap.
# ---------------------------------------------------------------------------
def delayline():
    with schemdraw.Drawing(file=out("dsp_delayline.svg"), show=False) as d:
        d.config(fontsize=13)
        d += dsp.Line().right().length(1.1).label("x[n]", "left")
        b = d.add(
            dsp.Box(w=3.6, h=1.5)
            .anchor("W")
            .label("z⁻ᴰ", loc="top", ofst=0.15)
            .label("BRAM circular buffer\nwrite @ waddr\nread @ waddr − D")
        )
        d += dsp.Arrow().right().length(1.6).at(b.E).label("y[n] = x[n − D]", "right")
        d += (
            dsp.Line()
            .down()
            .length(0.9)
            .at(b.S)
            .label("waddr += 1 each sample; D = write−read distance", "bottom", ofst=0.1)
        )


# ---------------------------------------------------------------------------
# 2. Chorus: a short read tap swept by a triangle LFO, then depth. No feedback.
# ---------------------------------------------------------------------------
def chorus():
    with schemdraw.Drawing(file=out("dsp_chorus.svg"), show=False) as d:
        d.config(fontsize=13)
        d += dsp.Arrow().right().length(1.2).at([-1.2, 0]).label("x[n]", "left")
        b = d.add(
            dsp.Box(w=3.0, h=1.4)
            .anchor("W")
            .label("z⁻ᴰ⁽ᵗ⁾  short tap\n(BRAM, ~9–17 ms)")
        )
        d += dsp.Arrow().right().length(1.4).at(b.E)
        amp = d.add(dsp.Amp().right().label("× CC94 depth", loc="top", ofst=0.15))
        d += dsp.Arrow().right().length(1.4).at(amp.out).label("chorus wet", "right")
        # triangle LFO directly above the delay, sweeping the tap
        lfo = d.add(dsp.Box(w=2.2, h=0.9).anchor("S").at([b.N[0], b.N[1] + 1.9]).label("triangle LFO"))
        d += dsp.Arrow().down().at(lfo.S).to(b.N)
        d += elm.Label().at([lfo.S[0] + 1.1, (lfo.S[1] + b.N[1]) / 2]).label("sweeps D")
        d += elm.Label().at([b.S[0], b.S[1] - 0.7]).label(
            "L / R taps anti-phase  •  read-only (no feedback)"
        )


# ---------------------------------------------------------------------------
# 3. Echo: feedback delay line, cross-coupled L<->R (ping-pong).
# ---------------------------------------------------------------------------
def echo():
    with schemdraw.Drawing(file=out("dsp_echo.svg"), show=False) as d:
        d.config(fontsize=13)
        s = d.add(dsp.Sum().at([0, 0]).anchor("center"))
        d += dsp.Arrow().at([-1.4, 0]).to(s.W).label("x", "left")
        d += dsp.Arrow().at(s.E).to([1.3, 0])
        b = d.add(dsp.Box(w=3.0, h=1.1).anchor("W").at([1.3, 0]).label("z⁻ᴰ  (BRAM)\nlong tap, CC82"))
        tap = d.add(dsp.Dot().at([b.E[0] + 0.4, 0]))
        d += dsp.Line().at(b.E).to(tap.center)
        d += dsp.Arrow().at(tap.center).to([b.E[0] + 1.9, 0]).label("echo out\n(× CC95)", "right")
        # feedback loop: down, ×½, up into the sum
        yb = -2.0
        d += dsp.Line().at(tap.center).to([tap.center[0], yb])
        g = d.add(dsp.Amp().anchor("input").at([tap.center[0], yb]).left().label("× ½", "bottom", ofst=0.12))
        d += dsp.Line().at(g.out).to([0, yb])
        d += dsp.Arrow().at([0, yb]).to(s.S)
        d += elm.Label().at([b.center[0], yb - 1.35]).label(
            "single-channel view — feedback ½.  In stereo, L writes R's delayed\n"
            "sample and vice-versa → the echo ping-pongs across the field."
        )


# ---------------------------------------------------------------------------
# 4. Reverb comb: lowpass-damped feedback comb (Freeverb), one of 8 per channel.
# ---------------------------------------------------------------------------
def comb():
    with schemdraw.Drawing(file=out("dsp_comb.svg"), show=False) as d:
        d.config(fontsize=13)
        s = d.add(dsp.Sum().at([0, 0]).anchor("center"))
        d += dsp.Arrow().at([-1.4, 0]).to(s.W).label("in", "left")
        d += dsp.Arrow().at(s.E).to([1.3, 0])
        b = d.add(dsp.Box(w=3.0, h=1.1).anchor("W").at([1.3, 0]).label("z⁻ᴰ  (BRAM tank)\ncomb 810…1230"))
        tap = d.add(dsp.Dot().at([b.E[0] + 0.4, 0]))
        d += dsp.Line().at(b.E).to(tap.center)
        d += dsp.Arrow().at(tap.center).to([b.E[0] + 1.7, 0]).label("y  (→ Σ)", "right")
        # feedback loop: down, LP damp, ×g, up into the sum
        yb = -2.2
        d += dsp.Line().at(tap.center).to([tap.center[0], yb])
        lp = d.add(dsp.Box(w=2.4, h=0.9).anchor("E").at([tap.center[0], yb]).label("LP damp\n½old+½new"))
        g = d.add(dsp.Amp().anchor("input").at(lp.W).left().label("× g  (CC91, DSP48)", "bottom", ofst=0.12))
        d += dsp.Line().at(g.out).to([0, yb])
        d += dsp.Arrow().at([0, yb]).to(s.S)


# ---------------------------------------------------------------------------
# 5. Reverb all-pass: Schroeder all-pass diffuser (g = ½), one of 4 per channel.
# ---------------------------------------------------------------------------
def allpass():
    with schemdraw.Drawing(file=out("dsp_allpass.svg"), show=False) as d:
        d.config(fontsize=12)
        d += dsp.Line().right().length(0.9).label("x", "left")
        node = d.add(dsp.Dot())
        s1 = d.add(dsp.Sum().anchor("W").at([node.center[0] + 0.6, 0]))
        d += dsp.Line().at(node.center).to(s1.W)
        d += dsp.Arrow().right().length(0.9).at(s1.E)
        b = d.add(dsp.Box(w=2.4, h=1.0).anchor("W").label("z⁻ᴰ (BRAM)\n163…403"))
        dtap = d.add(dsp.Dot().at([b.E[0] + 0.4, 0]))
        d += dsp.Line().at(b.E).to(dtap.center)
        d += dsp.Arrow().right().length(0.9).at(dtap.center)
        s2 = d.add(dsp.Sum().anchor("W"))
        d += dsp.Arrow().right().length(1.3).at(s2.E).label("y", "right")
        # feedforward: x -> ×(−g) -> s2 (top input)
        d += dsp.Line().up().at(node.center).length(1.7)
        ff = d.add(dsp.Amp().right().anchor("input").label("×(−½)", "top", ofst=0.05))
        d += dsp.Line().right().at(ff.out).tox(s2.N)
        d += dsp.Arrow().down().toy(s2.N)
        # feedback: delayed d -> ×g -> s1 (bottom input)
        d += dsp.Line().down().at(dtap.center).length(1.7)
        fb = d.add(dsp.Amp().left().anchor("input").label("×½", "bottom", ofst=0.05))
        d += dsp.Line().left().at(fb.out).tox(s1.S)
        d += dsp.Arrow().up().toy(s1.S)


# ---------------------------------------------------------------------------
# 6. Full Freeverb: 8 parallel combs -> Sigma/4 -> 4 series all-pass -> wet.
# ---------------------------------------------------------------------------
def freeverb():
    with schemdraw.Drawing(file=out("dsp_freeverb.svg"), show=False) as d:
        d.config(fontsize=12)
        d += dsp.Line().right().length(1.0).label("send\n(÷64)", "left")
        fan = d.add(dsp.Dot())
        combs = ["comb 0", "comb 1", "⋮  (8 combs)", "comb 7"]
        ys = [2.4, 0.8, -0.8, -2.4]
        boxes = []
        for lbl, y in zip(combs, ys):
            bx = d.add(dsp.Box(w=2.2, h=0.8).anchor("W").at([fan.center[0] + 1.4, fan.center[1] + y]).label(lbl))
            boxes.append(bx)
            d += dsp.Line().at(fan.center).to([fan.center[0], bx.W[1]])
            d += dsp.Arrow().at([fan.center[0], bx.W[1]]).to(bx.W)
        sm = d.add(dsp.SumSigma().anchor("center").at([boxes[0].E[0] + 2.2, fan.center[1]]))
        for bx in boxes:
            d += dsp.Line().at(bx.E).to([sm.W[0], bx.E[1]])
            d += dsp.Arrow().at([sm.W[0], bx.E[1]]).to(sm.W)
        d += dsp.Arrow().right().length(1.0).at(sm.E).label("÷4", "top")
        ap = None
        for i in range(4):
            ap = d.add(dsp.Box(w=1.9, h=0.9).anchor("W").label(f"all-pass {i}"))
            if i < 3:
                d += dsp.Arrow().right().length(0.6).at(ap.E)
        d += dsp.Arrow().right().length(1.2).at(ap.E).label("wet\n(×CC93)", "right")


# ---------------------------------------------------------------------------
# 7. Chamberlin state-variable filter (B7): two integrators + resonance feedback.
# ---------------------------------------------------------------------------
def svf():
    with schemdraw.Drawing(file=out("dsp_svf.svg"), show=False) as d:
        d.config(fontsize=13)
        # main path: Σ(=HP) → ×f → ∫₁(=BP) → ×f → ∫₂(=LP)
        s = d.add(dsp.Sum().at([0, 0]).anchor("center"))
        d += dsp.Arrow().at([-1.6, 0]).to(s.W).label("x (in ÷4)", "left")
        hp = d.add(dsp.Dot().at([s.E[0] + 0.7, 0]))
        d += dsp.Line().at(s.E).to(hp.center)
        d += dsp.Line().up().at(hp.center).length(0.7).label("HP", "right", ofst=0.05)
        f1 = d.add(dsp.Amp().at([hp.center[0] + 0.5, 0]).right().label("× f", "top", ofst=0.08))
        d += dsp.Line().at(hp.center).to(f1.input)
        i1 = d.add(dsp.Box(w=2.3, h=1.0).anchor("W").at([f1.out[0] + 0.5, 0]).label("∫₁  (z⁻¹)\nband += f·high"))
        bp = d.add(dsp.Dot().at([i1.E[0] + 0.6, 0]))
        d += dsp.Line().at(i1.E).to(bp.center)
        d += dsp.Arrow().at(f1.out).to(i1.W)
        d += dsp.Line().up().at(bp.center).length(0.7).label("BP", "right", ofst=0.05)
        f2 = d.add(dsp.Amp().at([bp.center[0] + 0.5, 0]).right().label("× f", "top", ofst=0.08))
        d += dsp.Line().at(bp.center).to(f2.input)
        i2 = d.add(dsp.Box(w=2.3, h=1.0).anchor("W").at([f2.out[0] + 0.5, 0]).label("∫₂  (z⁻¹)\nlow += f·band"))
        d += dsp.Arrow().at(f2.out).to(i2.W)
        lp = d.add(dsp.Dot().at([i2.E[0] + 0.6, 0]))
        d += dsp.Line().at(i2.E).to(lp.center)
        d += dsp.Arrow().at(lp.center).to([lp.center[0] + 1.3, 0]).label("LP", "right")
        # feedback merge: fb = low + q·band, subtracted at the input sum
        yb = -2.4
        fb = d.add(dsp.Sum().at([s.center[0], yb]).anchor("center"))
        d += dsp.Arrow().at(fb.N).to(s.S).label("−", "right", ofst=0.05)
        # resonance: q·band  (BP → ×q → fb.E)
        d += dsp.Line().at(bp.center).to([bp.center[0], yb])
        q = d.add(dsp.Amp().at([bp.center[0], yb]).left().label("× q", "bottom", ofst=0.1))
        d += dsp.Arrow().at(q.out).to(fb.E)
        # damping: low  (LP → down → left → fb.W)
        yl = yb - 1.5
        d += dsp.Line().at(lp.center).to([lp.center[0], yl])
        d += dsp.Line().at([lp.center[0], yl]).to([fb.W[0] - 1.2, yl])
        d += dsp.Line().at([fb.W[0] - 1.2, yl]).to([fb.W[0] - 1.2, yb])
        d += dsp.Arrow().at([fb.W[0] - 1.2, yb]).to(fb.W).label("low", "left", ofst=0.05)
        d += elm.Label().at([i1.E[0], yl - 0.9]).label(
            "high = x − low − q·band   •   outputs: LP / HP / BP / notch (= LP+HP)"
        )


# ---------------------------------------------------------------------------
# 8. Cross-osc FM / ring-mod (B6): one modulator, FM into carrier phase + ring product.
# ---------------------------------------------------------------------------
def crossmod():
    with schemdraw.Drawing(file=out("dsp_crossmod.svg"), show=False) as d:
        d.config(fontsize=13)
        mbox = d.add(dsp.Box(w=2.4, h=0.9).anchor("W").at([0, 0]).label("SINE[ph2]\nmodulator"))
        m = d.add(dsp.Dot().at([mbox.E[0] + 0.6, 0]))
        d += dsp.Line().at(mbox.E).to(m.center)
        d += dsp.Arrow().at([-1.3, 0]).to(mbox.W).label("ph2", "left")
        # FM branch: modsig × depth → + carrier phase → voice_wave → main
        # (start the gain triangle a little past the branch dot so they don't overlap)
        gd = d.add(dsp.Amp().at([m.center[0] + 0.5, 0]).right().label("× depth\n(FM index)", "top", ofst=0.1))
        d += dsp.Line().at(m.center).to(gd.input)
        ps = d.add(dsp.Sum().at([gd.out[0] + 1.0, 0]).anchor("W"))
        d += dsp.Arrow().at(gd.out).to(ps.W)
        d += dsp.Arrow().at([ps.N[0], ps.N[1] + 1.2]).to(ps.N)
        d += elm.Label().at([ps.N[0] + 1.0, ps.N[1] + 0.9]).label("carrier phase\n(main osc)")
        vw = d.add(dsp.Box(w=2.3, h=0.9).anchor("W").at([ps.E[0] + 0.6, 0]).label("voice_wave\n(carrier)"))
        d += dsp.Arrow().at(ps.E).to(vw.W)
        main = d.add(dsp.Dot().at([vw.E[0] + 0.7, 0]))
        d += dsp.Line().at(vw.E).to(main.center)
        # ring branch: modsig × main
        yb = -2.4
        d += dsp.Line().at(m.center).to([m.center[0], yb])
        rm = d.add(dsp.Mixer().at([m.center[0], yb]).right().label("ring\n× main", "bottom", ofst=0.15))
        d += dsp.Line().at(main.center).to([main.center[0], yb + 1.1])
        d += dsp.Line().at([main.center[0], yb + 1.1]).to([rm.N[0], yb + 1.1])
        d += dsp.Arrow().at([rm.N[0], yb + 1.1]).to(rm.N)
        # blend mux by xmode (tall enough to catch both the main and ring inputs)
        blend = d.add(dsp.Box(w=1.9, h=3.0).anchor("W").at([main.center[0] + 1.0, yb / 2]).label("blend\n(xmode)"))
        d += dsp.Arrow().at(main.center).to([blend.W[0], main.center[1]])
        d += dsp.Arrow().at(rm.E).to([blend.W[0], rm.center[1]])
        d += dsp.Arrow().at([blend.E[0], blend.center[1]]).to([blend.E[0] + 1.2, blend.center[1]]).label("o12", "right")


# ---------------------------------------------------------------------------
# 9. Per-voice datapath overview (B intro): the osc → filter → VCA signal chain.
# ---------------------------------------------------------------------------
def datapath():
    with schemdraw.Drawing(file=out("dsp_datapath.svg"), show=False) as d:
        d.config(fontsize=12)
        labels = ["DDS osc(s)\n+ waveform", "cross-mod\nFM / ring", "+ sub-osc",
                  "resonant\nSVF", "VCA\nenv·vel·trem", "serialized\nmix"]
        boxes = []
        x = 0.0
        prev = None
        for lbl in labels:
            b = d.add(dsp.Box(w=2.1, h=1.0).anchor("W").at([x, 0]).label(lbl))
            boxes.append(b)
            if prev is not None:
                d += dsp.Arrow().at(prev.E).to(b.W)
            prev = b
            x = b.E[0] + 0.9
        d += dsp.Arrow().at([-1.3, 0]).to(boxes[0].W).label("note", "left")
        d += dsp.Arrow().at(boxes[-1].E).to([boxes[-1].E[0] + 1.3, 0]).label("sample", "right")
        # modulation side-inputs from below
        def mod(src_label, target_box, xoff=0.0):
            mb = d.add(dsp.Box(w=1.9, h=0.8).anchor("N").at([target_box.S[0] + xoff, -2.0]).label(src_label))
            d.add(dsp.Arrow().at(mb.N).to([target_box.S[0] + xoff, target_box.S[1]]))
            return mb
        mod("amp ADSR", boxes[4])
        mod("filter ADSR", boxes[3])
        mod("part LFO\n(→ OSC + SVF)", boxes[0])


# ---------------------------------------------------------------------------
# 10. M14 reverb (dev history): 4 parallel combs -> Sigma -> 2 series all-pass.
# ---------------------------------------------------------------------------
def reverb_m14():
    with schemdraw.Drawing(file=out("dsp_reverb_m14.svg"), show=False) as d:
        d.config(fontsize=12)
        d += dsp.Line().right().length(1.0).label("in ÷8", "left")
        fan = d.add(dsp.Dot())
        combs = ["comb 810", "comb 878", "comb 940", "comb 1012"]
        ys = [2.0, 0.7, -0.7, -2.0]
        boxes = []
        for lbl, y in zip(combs, ys):
            bx = d.add(dsp.Box(w=2.2, h=0.8).anchor("W").at([fan.center[0] + 1.4, fan.center[1] + y]).label(lbl))
            boxes.append(bx)
            d += dsp.Line().at(fan.center).to([fan.center[0], bx.W[1]])
            d += dsp.Arrow().at([fan.center[0], bx.W[1]]).to(bx.W)
        sm = d.add(dsp.SumSigma().anchor("center").at([boxes[0].E[0] + 2.2, fan.center[1]]))
        for bx in boxes:
            d += dsp.Line().at(bx.E).to([sm.W[0], bx.E[1]])
            d += dsp.Arrow().at([sm.W[0], bx.E[1]]).to(sm.W)
        d += dsp.Arrow().right().length(1.1).at(sm.E)
        ap1 = d.add(dsp.Box(w=2.0, h=0.9).anchor("W").label("all-pass 348"))
        d += dsp.Arrow().right().length(0.7).at(ap1.E)
        ap2 = d.add(dsp.Box(w=2.0, h=0.9).anchor("W").label("all-pass 116"))
        d += dsp.Arrow().right().length(1.2).at(ap2.E).label("wet", "right")


if __name__ == "__main__":
    delayline()
    chorus()
    echo()
    comb()
    allpass()
    freeverb()
    svf()
    crossmod()
    datapath()
    reverb_m14()
    print("wrote dsp_*.svg to", DOCS)
