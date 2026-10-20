#!/usr/bin/env python3
"""Text cards rendered with PIL as transparent PNGs (many ffmpeg builds lack drawtext).

Sizes are designed on a 1080-line canvas and scaled by k = master height / 1080.
Fonts, colours and logos come from project.json; a missing font file falls back to Pillow's default (Pillow >= 10.1
for a scalable default) with a warning, and build_film.py stops before cutting when a line has characters the
chosen font cannot draw (they would show as boxes). build_film.py renders the cards itself; run this on its own
to look at every card of an EDL before cutting anything: <name>.png at the master size plus <name>.jpg on grey.

usage: cards.py --project project.json [--edl edl.json] --out DIR
"""
import argparse
import functools
import os
import re
import sys
import unicodedata
from collections import namedtuple

import deps  # noqa: F401
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from text_layout import lines_of

WHITE, SOFT, END_SOFT, DIVIDER = (255, 255, 255, 255), (225, 233, 244, 255), (215, 225, 240, 255), (184, 190, 200, 255)
CJK = re.compile(r"[⺀-鿿぀-ヿ가-힯＀-￯]")
Style = namedtuple("Style", "w h k fonts colors logos opts")
NOTDEF = "\U0010FFFD"


def _rgb(v):
    if isinstance(v, str):
        v = v.lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(v[:3])


def style_from(P):
    m = P["master"]
    return Style(m["width"], m["height"], m["height"] / 1080, P["fonts"],
                 {k: _rgb(v) for k, v in P["colors"].items()}, P.get("logos") or [], P["cards"])


def S(st, v):
    return round(v * st.k)


@functools.lru_cache(maxsize=None)
def _warn_missing(path):
    print(f"warning: font not found: {path} - falling back to Pillow's default font (no bold, no CJK glyphs)",
          file=sys.stderr)


@functools.lru_cache(maxsize=None)
def load_font(path, size, index=0):
    if path and os.path.exists(path):
        return ImageFont.truetype(path, size, index=index)
    if path:
        _warn_missing(path)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def font(st, text, size, bold):
    f, px = st.fonts, max(1, S(st, size))
    if CJK.search(text) and f.get("cjk"):
        return load_font(f["cjk"], px, int(f.get("cjk_index_bold" if bold else "cjk_index_regular", 0)))
    latin = f.get("latin_bold") if bold else f.get("latin")
    if latin or f.get("latin"):
        return load_font(latin or f.get("latin"), px, 0)
    return load_font(f.get("cjk", ""), px, int(f.get("cjk_index_bold" if bold else "cjk_index_regular", 0)))


@functools.lru_cache(maxsize=None)
def _ink(f, ch):
    l, t, r, b = f.getbbox(ch)
    im = Image.new("L", (max(1, r - l), max(1, b - t)))
    ImageDraw.Draw(im).text((-l, -t), ch, font=f, fill=255)
    return im.size, im.tobytes()


def _missing(f, ch):
    return not ch.isspace() and unicodedata.category(ch)[0] not in "CZ" and _ink(f, ch) == _ink(f, NOTDEF)


def missing_glyphs(P, texts):
    """[(text, characters)] for every line whose regular or bold font draws a character as the missing-glyph box."""
    st, out = style_from(P), []
    for text in texts:
        fonts = [font(st, text, 40, bold) for bold in (False, True)]
        miss = [ch for ch in dict.fromkeys(text) if any(_missing(f, ch) for f in fonts)]
        if miss:
            out.append((text, "".join(miss)))
    return out


def blank(st):
    return Image.new("RGBA", (st.w, st.h), (0, 0, 0, 0))


def centered_lines(st, img, y, items, shadow=True):
    """items: [(text, font, fill, gap_before)] centred horizontally; returns the y below the last line."""
    placed = []
    for text, f, fill, gap in items:
        y += S(st, gap)
        l, t, r, b = f.getbbox(text)
        placed.append(((st.w - (r - l)) // 2 - l, y - t, text, f, fill))
        y += b - t
    if shadow:
        sh = blank(st)
        sd = ImageDraw.Draw(sh)
        for x, yy, text, f, _ in placed:
            sd.text((x + S(st, 2), yy + S(st, 3)), text, font=f, fill=(0, 0, 0, 170))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(S(st, 4))))
    d = ImageDraw.Draw(img)
    for x, yy, text, f, fill in placed:
        d.text((x, yy), text, font=f, fill=fill)
    return y


def rule(st, img, y, offset=18):
    top = y + S(st, offset)
    ImageDraw.Draw(img).rectangle([st.w // 2 - S(st, 60), top, st.w // 2 + S(st, 60), top + S(st, 3)],
                                  fill=st.colors["accent"] + (255,))
    return top + S(st, 3)


def _logo(entry, height):
    im = Image.open(entry["path"]).convert("RGBA")
    if entry.get("trim", True) and im.getbbox():
        im = im.crop(im.getbbox())
    h = max(1, round(height * entry.get("height", 1.0)))
    return im.resize((max(1, round(im.width * h / im.height)), h), Image.LANCZOS)


def logo_panel(st, height, pad_x, pad_y, radius, max_w):
    """All logos left to right on one white rounded plate, separated by thin dividers."""
    if not st.logos:
        return None
    q = 2 * st.k
    ims = [_logo(l, height * q) for l in st.logos]
    gap, div, px, py, ch = round(44 * q), max(1, round(3 * q)), round(pad_x * q), round(pad_y * q), round(height * q)
    pw = 2 * px + sum(i.width for i in ims) + (len(ims) - 1) * (2 * gap + div)
    ph = ch + 2 * py
    panel = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle([0, 0, pw - 1, ph - 1], round(radius * q), fill=WHITE)
    x = px
    for j, im in enumerate(ims):
        if j:
            d.rectangle([x + gap, py + ch // 6, x + gap + div - 1, py + ch - ch // 6], fill=DIVIDER)
            x += 2 * gap + div
        panel.alpha_composite(im, (x, (ph - im.height) // 2))
        x += im.width
    tw = min(pw // 2, S(st, max_w))
    return panel.resize((tw, round(ph * tw / pw)), Image.LANCZOS)


def make_dim(st, path, alpha):
    Image.new("RGBA", (st.w, st.h), (0, 0, 0, round(255 * alpha))).save(path)


def make_title(st, title, path):
    lines = lines_of(title)
    img = blank(st)
    panel = logo_panel(st, st.opts["title_logo_h"], 30, 24, 22, 1500)
    y = (st.h - ((panel.height if panel else 0) + S(st, 330))) // 2
    if panel:
        img.alpha_composite(panel, ((st.w - panel.width) // 2, y))
        y += panel.height + S(st, 54)
    y = centered_lines(st, img, y, [(lines[0], font(st, lines[0], 84, True), WHITE, 0)])
    rest = [(s, font(st, s, 44 if j == 0 else 42, j == 0), WHITE if j == 0 else SOFT, 30 if j == 0 else 14)
            for j, s in enumerate(lines[1:])]
    if title.get("date_line"):
        rest.append((title["date_line"], font(st, title["date_line"], 30, False), SOFT, 34))
    if rest:
        centered_lines(st, img, rule(st, img, y), rest)
    img.save(path)


def _bar(st, items, pad=(40, 44, 24), gaps=(14, 8), radius=14, alpha=0.70):
    """Lower-left translucent bar with an accent stripe; items = [(text, font, fill)]."""
    img = blank(st)
    pad_l, pad_r, pad_y = (S(st, v) for v in pad)
    bbs = [f.getbbox(s) for s, f, _ in items]
    hs = [b[3] - b[1] for b in bbs]
    gap = [0] + [S(st, gaps[min(i, len(gaps) - 1)]) for i in range(len(items) - 1)]
    x0, y1 = S(st, 96), st.h - S(st, 104)
    y0 = y1 - (2 * pad_y + sum(hs) + sum(gap))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0, y0, x0 + max(b[2] - b[0] for b in bbs) + pad_l + pad_r, y1], S(st, radius),
                        fill=st.colors["navy"] + (round(255 * alpha),))
    d.rounded_rectangle([x0 + S(st, 12), y0 + S(st, 16), x0 + S(st, 17), y1 - S(st, 16)], S(st, 2),
                        fill=st.colors["accent"] + (255,))
    y = y0 + pad_y
    for (s, f, fill), bb, h, g in zip(items, bbs, hs, gap):
        y += g
        d.text((x0 + pad_l - bb[0], y - bb[1]), s, font=f, fill=fill)
        y += h
    return img


def make_caption(st, section, path):
    lines = lines_of(section)
    items = [(lines[0], font(st, lines[0], 52, True), WHITE)]
    items += [(s, font(st, s, 30 if j == 0 else 28, False), SOFT) for j, s in enumerate(lines[1:])]
    _bar(st, items).save(path)


def make_tag(st, tag, path):
    lines = lines_of(tag)
    items = [(lines[0], font(st, lines[0], 38, True), WHITE)]
    if len(lines) > 1:
        rest = "  |  ".join(lines[1:])
        items.append((rest, font(st, rest, 24, False), SOFT))
    _bar(st, items, pad=(34, 36, 18), gaps=(10,), radius=12, alpha=0.62).save(path)


def make_closing(st, closing, path):
    """Closing statement in the upper third (sky), lines before the accent rule are the headline."""
    lines, split = lines_of(closing), closing.get("rule_after", 1)
    img = blank(st)
    top = [(s, font(st, s, 64 if j == 0 else 40, j == 0), WHITE, 0 if j == 0 else 22)
           for j, s in enumerate(lines[:split])]
    bottom = [(s, font(st, s, 34 if j == 0 else 32, False), SOFT, 28 if j == 0 else 12)
              for j, s in enumerate(lines[split:])]
    y = centered_lines(st, img, S(st, 150), top)
    if bottom:
        centered_lines(st, img, rule(st, img, y), bottom)
    img.save(path)


def make_endcard(st, endcard, path):
    lines = lines_of(endcard)
    img = Image.new("RGBA", (st.w, st.h))
    d = ImageDraw.Draw(img)
    a, b = st.colors["navy"], st.colors["blue"]
    for y in range(st.h):
        k = y / (st.h - 1)
        d.line([(0, y), (st.w, y)], fill=tuple(round(x + (z - x) * k) for x, z in zip(a, b)) + (255,))
    panel = logo_panel(st, st.opts["end_logo_h"], 44, 34, 28, 1640)
    ph = panel.height if panel else 0
    y = (st.h - (ph + S(st, 230))) // 2 - S(st, 30)
    if panel:
        img.alpha_composite(panel, ((st.w - panel.width) // 2, y))
    y += ph + S(st, 80)
    if lines:
        y = centered_lines(st, img, y, [(lines[0], font(st, lines[0], 50, True), WHITE, 0)], shadow=False)
    if len(lines) > 1:
        items = [(s, font(st, s, 36, False), END_SOFT, 30 if j == 0 else 12) for j, s in enumerate(lines[1:])]
        centered_lines(st, img, rule(st, img, y, 26), items, shadow=False)
    img.convert("RGB").save(path)


def preview(st, layers, path):
    """Layers over a neutral grey frame, saved as a ≤1920-wide JPEG for eyeballing."""
    base = Image.new("RGBA", (st.w, st.h), (110, 120, 130, 255))
    for p in layers:
        base.alpha_composite(Image.open(p).convert("RGBA").resize((st.w, st.h)))
    w = min(1920, st.w)
    base.convert("RGB").resize((w, round(st.h * w / st.w)), Image.LANCZOS).save(path, quality=90)


def all_cards(edl):
    """[(name, maker, block)] for every card of an EDL, named as build_film.py names them."""
    out = [("title", make_title, edl["title"])] if edl.get("title") else []
    out += [(f"cap_{x.get('key')}", make_caption, x) for x in edl.get("sections") or []]
    for s in edl["shots"]:
        if s.get("tag"):
            n = s.get("n")
            out.append((f"tag_{n:02d}" if isinstance(n, int) else f"tag_{n}", make_tag, s["tag"]))
    for key, make in (("closing", make_closing), ("endcard", make_endcard)):
        if edl.get(key):
            out.append((key, make, edl[key]))
    return out


def main():
    import common as C
    from edl_check import check_assets, check_fonts, check_text, stop
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_project_args(ap)
    ap.add_argument("--out", required=True, help="folder for the card PNGs and their previews on grey")
    a = ap.parse_args()
    P, edl = C.load(a)
    stop(check_text(edl) + check_assets(P) + check_fonts(P, edl, edl["shots"]))
    st = style_from(P)
    os.makedirs(a.out, exist_ok=True)
    for name, make, block in all_cards(edl):
        png = os.path.join(a.out, f"{name}.png")
        make(st, block, png)
        preview(st, [png], os.path.join(a.out, f"{name}.jpg"))
        print(png)


if __name__ == "__main__":
    main()
