#!/usr/bin/env python3
"""Generate the animated pipeline GIFs for the slide deck (slides 33/34 bridge).

Slide 33 draws the 48 stages (spatial, static). Slide 34 draws the voice schedule
(temporal, static). Neither shows the join: there is exactly ONE physical datapath,
32 voices take turns on it, and a new voice may only enter every ~24 cycles because
`apply_on`'s free-voice search occupies stages 2-20 and the `env_st` array it reads
at stage 1 is not written back until stage 24 (see ARCHITECTURE.md, A1).

This renders both views on one shared clock so the causality is visible: the moment a
voice reaches stage 24, the next voice appears at stage 0.

    uv run python docs/gen_pipeline_gif.py

Outputs (under docs/slides/assets/):
    pipeline_anim.gif      English labels
    pipeline_anim_ja.gif   Japanese labels
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "slides", "assets")

# --- animation constants -----------------------------------------------------
STAGES = 48        # pipeline depth reported by codegen (STAGES=48)
II = 24            # measured initiation interval, in engine cycles
NVOICE = 6         # voice rows drawn in the lower panel
MAX_T = II * NVOICE          # 144 frames: v5 is mid-flight when the loop wraps
AXIS_T = II * NVOICE + STAGES - II   # 168: enough axis for every bar to finish
FRAME_MS = 70
HOLD_MS = 1600
FLASH = 5          # frames the stage-24 release callout stays up
CE_NS = 30         # one engine cycle = 3 master clocks at 100 MHz

# --- canvas ------------------------------------------------------------------
W, H = 1152, 486   # 1152 = the deck's content width (1280 slide - 2x64 padding)
SCALE = 2          # render at 2x so the GIF stays crisp on a scaled-up deck

# --- palette (the deck's Google colours) -------------------------------------
BG = "#ffffff"
INK = "#202124"
INK2 = "#3c4043"
INK3 = "#5f6368"
GREY = "#80868b"
HAIR = "#dadce0"
FAINT = "#e8eaed"

AMBER = "#f9ab00"
AMBER_LT = "#feefc3"
AMBER_DK = "#b06000"
GREEN = "#188038"

VOICE = ["#1a73e8", "#188038", "#a142f4", "#12b5cb", "#d93025", "#e8710a"]

# stage bands: (first, last, fill, text colour, label key)
BANDS = [
    (0, 1, "#f1f3f4", INK3, "b_midi"),
    (2, 20, "#d2e3fc", "#174ea6", "b_scan"),
    (21, 23, "#f8f9fa", INK3, "b_adsr"),
    (24, 24, AMBER_LT, AMBER_DK, None),
    (25, 33, "#f3e8fd", "#7627bb", "b_dsp"),
    (34, 47, "#fafafa", GREY, "b_tail"),
]

# --- geometry (logical units; multiplied by SCALE at draw time) --------------
PA_X0, PA_W = 20.0, 1112.0
PITCH = PA_W / STAGES
CELL_W = PITCH - 2.2

Y_CAP = 0
Y_ATITLE = 22
Y_BAND, H_BAND = 46, 16
Y_TOK = 71
Y_CARET = 88
Y_CELL, H_CELL = 94, 32
Y_ARROW = 133
Y_NUM = 147
Y_NOTE = 164
Y_SEP = 190
Y_BTITLE = 200
Y_AXIS = 224
Y_ROW0, ROW_PITCH, H_BAR = 240, 30, 20
Y_TAKE, H_TAKE = 428, 44

PB_X0, PB_W = 106.0, 1026.0


def sx(cycle):
    """x of a cycle on the lower panel's time axis."""
    return PB_X0 + PB_W * cycle / AXIS_T


def cx(stage):
    """x of a stage cell on the upper panel."""
    return PA_X0 + PITCH * stage


# --- text --------------------------------------------------------------------
LABELS = {
    "en": {
        "cap": "one box = one pipeline stage · everything advances exactly one box per engine cycle",
        "cyc": "engine cycle",
        "atitle": "THE ONE PHYSICAL PIPELINE — 48 stages, and every voice takes its turn on the same silicon",
        "b_midi": "MIDI",
        "b_scan": "apply_on — free-voice search, unrolled 32×",
        "b_adsr": "adsr()",
        "b_dsp": "per-voice DSP",
        "b_tail": "tail — SVF write-back, then just the valid/token chain",
        "voice": "voice",
        "hint": "Stages 2–20 are one thing: apply_on hunting for a free voice, 32 slots deep. That search is the whole 24-cycle gap.",
        "flash": "stage 24 — env_st is written back. Only now may the next voice enter stage 0.",
        "btitle": "THE SAME RUN, SEEN OVER TIME",
        "lat": "48 cycles — one voice, end to end",
        "iilab": "24",
        "take1": "Two voices are always in flight. The pipeline never drains.",
        "take2": "Latency 48 cycles · a new voice every 24 · so 32 voices ≈ 768 engine cycles ≈ 23 µs — one audio sample.",
    },
    "ja": {
        "cap": "1マス = パイプライン1段 · エンジン1サイクルごとに、すべてがきっちり1マスずつ進む",
        "cyc": "engine cycle",
        "atitle": "物理的なパイプラインは1本だけ — 48段を、すべてのボイスが順番に使い回す",
        "b_midi": "MIDI",
        "b_scan": "apply_on — 空きボイス探索を32回展開",
        "b_adsr": "adsr()",
        "b_dsp": "ボイスごとのDSP",
        "b_tail": "末尾 — SVFの書き戻しの後は valid/token の連鎖だけ",
        "voice": "ボイス",
        "hint": "ステージ2〜20の正体はひとつ — apply_on が32スロット分の空きボイスを探している。この探索が24サイクルの間隔そのもの。",
        "flash": "ステージ24 — env_st を書き戻す。ここで初めて次のボイスがステージ0に入れる。",
        "btitle": "同じ動きを、時間軸で見る",
        "lat": "48サイクル — ボイス1つ分の所要時間",
        "iilab": "24",
        "take1": "つねに2ボイスが同時に流れている。パイプラインが空になることはない。",
        "take2": "レイテンシ48サイクル · 24サイクルごとに次のボイス · 32ボイスで約768エンジンサイクル ≈ 23 µs — これが1サンプル。",
    },
}

FONTS = {
    "en": {
        "sans": ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
        "bold": ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
        "med": ("/System/Library/Fonts/HelveticaNeue.ttc", 10),
    },
    "ja": {
        "sans": ("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", 0),
        "bold": ("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", 0),
        "med": ("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", 0),
    },
}
MONO = ("/System/Library/Fonts/Menlo.ttc", 0)
MONO_B = ("/System/Library/Fonts/Menlo.ttc", 1)

_fcache = {}


def font(kind, pt, lang="en"):
    """Resolve a face at `pt` logical points. Fails loudly if the face is missing."""
    spec = MONO if kind == "mono" else MONO_B if kind == "monob" else FONTS[lang][kind]
    key = (spec, round(pt * SCALE))
    if key not in _fcache:
        try:
            _fcache[key] = ImageFont.truetype(spec[0], round(pt * SCALE), index=spec[1])
        except OSError as exc:
            sys.exit(f"font not available: {spec[0]} (index {spec[1]}): {exc}")
    return _fcache[key]


class Canvas:
    """Draws in logical units and scales up, so the layout math stays readable."""

    def __init__(self, lang):
        self.lang = lang
        self.img = Image.new("RGB", (W * SCALE, H * SCALE), BG)
        self.d = ImageDraw.Draw(self.img)

    def _s(self, *v):
        return [x * SCALE for x in v]

    def rect(self, x, y, w, h, fill=None, outline=None, width=1, r=0):
        x0, y0, x1, y1 = self._s(x, y, x + w, y + h)
        args = dict(fill=fill, outline=outline, width=max(1, round(width * SCALE)))
        if r:
            self.d.rounded_rectangle([x0, y0, x1, y1], radius=r * SCALE, **args)
        else:
            self.d.rectangle([x0, y0, x1, y1], **args)

    def line(self, pts, fill, width=1):
        self.d.line([(x * SCALE, y * SCALE) for x, y in pts],
                    fill=fill, width=max(1, round(width * SCALE)))

    def poly(self, pts, fill):
        self.d.polygon([(x * SCALE, y * SCALE) for x, y in pts], fill=fill)

    def dot(self, x, y, r, fill, outline=None):
        x0, y0, x1, y1 = self._s(x - r, y - r, x + r, y + r)
        self.d.ellipse([x0, y0, x1, y1], fill=fill, outline=outline,
                       width=max(1, round(1.4 * SCALE)))

    def text(self, x, y, s, kind, pt, fill, anchor="la", mono=False):
        f = font("mono" if mono else kind, pt, self.lang)
        self.d.text((x * SCALE, y * SCALE), s, font=f, fill=fill, anchor=anchor)

    def width(self, s, kind, pt, mono=False):
        f = font("mono" if mono else kind, pt, self.lang)
        return self.d.textlength(s, font=f) / SCALE

    def fit(self, x, y, s, kind, pt, fill, maxw, anchor="la", mono=False):
        """Shrink until it fits — the JA strings are wider than the EN ones."""
        while pt > 6 and self.width(s, kind, pt, mono) > maxw:
            pt -= 0.5
        self.text(x, y, s, kind, pt, fill, anchor, mono)


def band_of(stage):
    for lo, hi, fill, ink, key in BANDS:
        if lo <= stage <= hi:
            return fill, ink, key
    raise AssertionError(stage)


def draw_frame(t, lang):
    L = LABELS[lang]
    c = Canvas(lang)
    flashing = t >= II and t % II < FLASH
    fresh = t >= II and t % II == 0

    # ---- caption + cycle counter ----
    c.fit(PA_X0, Y_CAP, L["cap"], "sans", 12.5, GREY, 820)
    us = t * CE_NS / 1000.0
    c.text(PA_X0 + PA_W, Y_CAP - 1, f'{L["cyc"]} {t:3d}   {us:6.2f} µs',
           "monob", 13, INK2, anchor="ra", mono=True)

    # ================= panel A: the one physical pipeline =================
    c.fit(PA_X0, Y_ATITLE, L["atitle"], "med", 14.5, INK, PA_W)

    for lo, hi, fill, ink, key in BANDS:
        x0, x1 = cx(lo), cx(hi) + CELL_W
        c.rect(x0, Y_BAND, x1 - x0, H_BAND, fill=fill, r=3)
        if key:
            c.fit((x0 + x1) / 2, Y_BAND + H_BAND / 2, L[key], "med", 11.5, ink,
                  (x1 - x0) - 8, anchor="mm")

    # the 48 stage cells; the occupied ones carry a voice
    occupied = {}
    for n in range(NVOICE + 1):
        s = t - II * n
        if 0 <= s < STAGES:
            occupied[s] = n
    for s in range(STAGES):
        fill, _, _ = band_of(s)
        edge = HAIR
        if s == 24 and flashing:
            fill, edge = (AMBER if fresh else AMBER_LT), AMBER
        if s in occupied:
            fill = VOICE[occupied[s] % len(VOICE)]
            edge = AMBER if (s == 24 and flashing) else fill
        c.rect(cx(s), Y_CELL, CELL_W, H_CELL, fill=fill, outline=edge,
               width=2 if edge == AMBER else 1)
        if s in occupied:
            c.text(cx(s) + CELL_W / 2, Y_CELL + H_CELL / 2, str(occupied[s]),
                   "monob", 13, "#ffffff", anchor="mm", mono=True)

    # voice tags + carets above their current cell
    for s, n in sorted(occupied.items()):
        col = VOICE[n % len(VOICE)]
        mid = cx(s) + CELL_W / 2
        lab = f'{L["voice"]} {n}'
        half = c.width(lab, "med", 11.5) / 2
        c.text(min(max(mid, PA_X0 + half), PA_X0 + PA_W - half), Y_TOK, lab,
               "med", 11.5, col, anchor="ma")
        c.poly([(mid - 4, Y_CARET), (mid + 4, Y_CARET), (mid, Y_CARET + 5)], col)

    # stage numbers
    for s in (0, 8, 16, 21, 24, 28, 34, 41, 47):
        col = AMBER_DK if s == 24 else GREY
        c.text(cx(s) + CELL_W / 2, Y_NUM, str(s), "mono", 10, col, anchor="ma", mono=True)

    # the release arrow: stage 24 writes env_st, so stage 0 opens up
    if flashing:
        xa, xb = cx(24) + CELL_W / 2, cx(0) + CELL_W / 2
        y = Y_ARROW + 8
        c.line([(xa, Y_CELL + H_CELL + 1), (xa, y), (xb, y), (xb, Y_CELL + H_CELL + 4)],
               AMBER, 2)
        c.poly([(xb - 4.5, Y_CELL + H_CELL + 6), (xb + 4.5, Y_CELL + H_CELL + 6),
                (xb, Y_CELL + H_CELL + 0.5)], AMBER)

    if flashing:
        c.fit(PA_X0, Y_NOTE, L["flash"], "bold", 13, AMBER_DK, PA_W)
    else:
        c.fit(PA_X0, Y_NOTE, L["hint"], "sans", 13, INK3, PA_W)

    c.line([(PA_X0, Y_SEP), (PA_X0 + PA_W, Y_SEP)], FAINT, 1)

    # ================= panel B: the same run over time =================
    c.fit(PA_X0, Y_BTITLE, L["btitle"], "med", 14.5, INK, PA_W)

    y_bot = Y_ROW0 + ROW_PITCH * (NVOICE - 1) + H_BAR + 6
    for g in range(0, AXIS_T + 1, II):
        x = sx(g)
        c.line([(x, Y_AXIS + 14), (x, y_bot)], FAINT, 1)
        c.text(x, Y_AXIS, str(g), "mono", 10, GREY, anchor="ma", mono=True)

    # the playhead ties the two panels to one clock; it goes under the bars and
    # labels so it never strikes through them — the bar edges sit on it anyway
    xp = sx(t)
    c.line([(xp, Y_AXIS + 14), (xp, y_bot)], INK2, 1.4)

    for n in range(NVOICE):
        col = VOICE[n % len(VOICE)]
        y = Y_ROW0 + ROW_PITCH * n
        start = II * n
        c.text(PB_X0 - 10, y + H_BAR / 2, f'{L["voice"]} {n}', "med", 11.5, col,
               anchor="rm")
        if t < start:
            continue
        x0, x1 = sx(start), sx(start + STAGES)
        c.rect(x0, y, x1 - x0, H_BAR, outline=HAIR, width=1, r=4)  # where it is headed
        done = min(t - start, STAGES)
        if done > 0:
            c.rect(x0, y, sx(start + done) - x0, H_BAR, fill=col, r=4)
        # the stage-24 mark inside the bar, and the hand-off to the next voice
        if done >= II:
            xm = sx(start + II)
            c.line([(xm, y + 2), (xm, y + H_BAR - 2)], AMBER, 2)
            if n + 1 < NVOICE:
                c.line([(xm, y + H_BAR), (xm, y + ROW_PITCH)], AMBER, 2)
                if n == 0:
                    c.text(xm + 5, y + H_BAR + 1, L["iilab"], "monob", 10, AMBER_DK,
                           mono=True)
        if t >= start + STAGES:
            c.dot(x1 + 8, y + H_BAR / 2, 4.5, GREEN)
            if n == 0:
                c.fit(x1 + 18, y + H_BAR / 2, L["lat"], "sans", 11.5, INK3,
                      PA_X0 + PA_W - x1 - 20, anchor="lm")

    c.poly([(xp - 4, Y_AXIS + 8), (xp + 4, Y_AXIS + 8), (xp, Y_AXIS + 15)], INK2)

    # ---- takeaway ----
    c.rect(PA_X0, Y_TAKE, PA_W, H_TAKE, fill="#f8f9fa", r=8)
    c.rect(PA_X0, Y_TAKE, 4, H_TAKE, fill=VOICE[0], r=2)
    c.fit(PA_X0 + 18, Y_TAKE + 9, L["take1"], "bold", 14, INK, PA_W - 36)
    c.fit(PA_X0 + 18, Y_TAKE + 27, L["take2"], "sans", 12.5, INK3, PA_W - 36)
    return c.img


def build(lang):
    frames = [draw_frame(t, lang) for t in range(MAX_T)]

    # One shared palette for every frame: no inter-frame flicker, and Pillow can
    # emit small delta frames because most of the canvas never changes.
    sample_ts = [0, II - 1, II, II + 1, STAGES, MAX_T - 1]
    strip = Image.new("RGB", (W * SCALE, H * SCALE * len(sample_ts)))
    for i, t in enumerate(sample_ts):
        strip.paste(frames[t], (0, H * SCALE * i))
    pal = strip.quantize(colors=128, method=Image.Quantize.MEDIANCUT)

    out = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    durations = [FRAME_MS] * (len(out) - 1) + [HOLD_MS]
    path = os.path.join(ASSETS, f"pipeline_anim{'_' + lang if lang != 'en' else ''}.gif")
    out[0].save(path, save_all=True, append_images=out[1:], duration=durations,
                loop=0, optimize=True, disposal=1)
    return path


def main():
    if not os.path.isdir(ASSETS):
        sys.exit(f"missing output directory: {ASSETS}")
    for lang in ("en", "ja"):
        path = build(lang)
        kb = os.path.getsize(path) / 1024
        print(f"{os.path.relpath(path, os.path.dirname(HERE))}  "
              f"{MAX_T} frames  {W * SCALE}x{H * SCALE}  {kb:,.0f} KB")


if __name__ == "__main__":
    main()
