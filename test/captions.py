"""Render caption title-card PNGs with Pillow (this ffmpeg build has no drawtext).
Each card precedes a test's spectrogram in the report video: category, title,
description, expected outcome, and the post-run verdict + score."""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 400
BG = (18, 17, 15)
FG = (233, 220, 194)
DIM = (150, 140, 122)
CAT = {"basic": (70, 120, 200), "integration": (70, 165, 100), "stress": (210, 120, 40),
       "intro": (150, 130, 90), "summary": (150, 130, 90)}
VERD = {"PASS": (80, 190, 100), "WARN": (220, 175, 60), "FAIL": (215, 70, 55)}

_FONTS = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial Unicode.ttf",
          "/System/Library/Fonts/Helvetica.ttc"]
_BOLD = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Helvetica.ttc"]

def _font(size, bold=False):
    for p in (_BOLD if bold else _FONTS):
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return ImageFont.load_default()

def _wrap(draw, text, font, maxw):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if draw.textlength(t, font=font) <= maxw: cur = t
        else: lines.append(cur); cur = wd
    if cur: lines.append(cur)
    return lines

def render_card(path, category, title, desc, expected, index=None, total=None,
                verdict=None, score=None, metric=None):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    col = CAT.get(category, DIM)
    d.rectangle([0, 0, W, 6], fill=col)                       # top accent bar
    # category badge
    badge = category.upper()
    bw = d.textlength(badge, font=_font(20, True)) + 24
    d.rectangle([40, 34, 40 + bw, 70], fill=col)
    d.text((52, 40), badge, font=_font(20, True), fill=(15, 14, 12))
    if index is not None:
        d.text((W - 150, 40), f"{index}/{total}", font=_font(20, True), fill=DIM)
    # title
    d.text((40, 92), title, font=_font(38, True), fill=FG)
    # description
    y = 158
    for ln in _wrap(d, desc, _font(24), W - 80):
        d.text((40, y), ln, font=_font(24), fill=FG); y += 32
    # expected
    if expected:
        d.text((40, y + 8), "Expected:", font=_font(18, True), fill=DIM)
        y2 = y + 34
        for ln in _wrap(d, expected, _font(20), W - 80):
            d.text((40, y2), ln, font=_font(20), fill=DIM); y2 += 26
    # verdict + score (post-run)
    if verdict:
        vc = VERD.get(verdict, DIM)
        label = f"{verdict}"
        sc = f"{score:.0f}/100" if score is not None else ""
        d.rectangle([40, H - 66, 250, H - 26], fill=vc)
        d.text((56, H - 60), label, font=_font(26, True), fill=(15, 14, 12))
        d.text((270, H - 60), sc, font=_font(26, True), fill=vc)
        if metric:
            mt = metric if len(metric) < 62 else metric[:60] + "…"
            d.text((40, H - 96), mt, font=_font(16), fill=DIM)
    img.save(path)
    return path

def render_intro(path, title, subtitle):
    img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=CAT["intro"])
    d.text((40, 150), title, font=_font(48, True), fill=FG)
    for i, ln in enumerate(_wrap(d, subtitle, _font(24), W - 80)):
        d.text((40, 220 + i * 32), ln, font=_font(24), fill=DIM)
    img.save(path); return path

def render_summary(path, lines, grade, score):
    img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=CAT["summary"])
    d.text((40, 40), "Test Report — Summary", font=_font(38, True), fill=FG)
    gc = VERD["PASS"] if score >= 85 else VERD["WARN"] if score >= 60 else VERD["FAIL"]
    d.text((40, 100), f"{score:.0f}/100   {grade}", font=_font(44, True), fill=gc)
    for i, ln in enumerate(lines[:6]):
        d.text((40, 180 + i * 32), ln, font=_font(22), fill=FG)
    img.save(path); return path
