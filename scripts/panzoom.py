#!/usr/bin/env python3
"""Pan-and-zoom move on a still picture -> a video segment at the master size and frame rate.

kb from / to = [cx, cy, zoom]: centre of the crop window as fractions of the picture, and zoom (1.0 shows
the whole canvas, 1.08 is a gentle push-in). The window moves on a smoothstep ease with sub-pixel sampling
(warpAffine), so a slow move glides instead of crawling pixel by pixel. Pictures with an aspect of 1.25 or
wider (4:3, 3:2, 16:9, panoramas) are cover-cropped; the crop window always has the output aspect, so
nothing is squashed. Portrait and near-square pictures sit centred on a blurred, darkened copy of
themselves. EXIF orientation is applied. An optional ffmpeg filter chain grades the result.

usage: panzoom.py photo.jpg out.mp4 4.0 [--from 0.5,0.5,1.0] [--to 0.5,0.5,1.08] [--grade FILTERS]
                   [--size 3840x2160] [--fps 30000/1001]
"""
import argparse
import subprocess
from fractions import Fraction

import deps  # noqa: F401
import cv2
import numpy as np
from PIL import Image, ImageOps

import common as C

WIDE = 1.25
FROM, TO = (0.5, 0.5, 1.0), (0.5, 0.5, 1.08)
ROTATED = (5, 6, 7, 8)


def picture_size(path):
    """Display size of a picture (EXIF rotation applied); C.ProbeError when Pillow cannot read it."""
    try:
        with Image.open(path) as im:
            w, h = im.size
            return (h, w) if im.getexif().get(0x0112) in ROTATED else (w, h)
    except (OSError, ValueError) as e:
        raise C.ProbeError(f"not a picture Pillow can read ({e}); convert it to JPEG or PNG (HEIC from a phone: "
                           f"'sips -s format jpeg in.heic --out in.jpg' on macOS, 'magick in.heic in.jpg' with "
                           f"ImageMagick elsewhere): {path}")


def load(path):
    with Image.open(path) as im:
        rgb = ImageOps.exif_transpose(im).convert("RGB")
    return cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)


def _resize(img, w, h):
    shrink = w < img.shape[1]
    return cv2.resize(img, (max(1, w), max(1, h)), interpolation=cv2.INTER_AREA if shrink else cv2.INTER_CUBIC)


def canvas(img, cw, ch, out_w):
    """At least cw x ch: wide pictures cover it, others sit centred on a blurred, darkened copy of themselves."""
    ih, iw = img.shape[:2]
    s = max(cw / iw, ch / ih)
    cover = _resize(img, max(cw, round(iw * s)), max(ch, round(ih * s)))
    if iw / ih >= WIDE:
        return cover
    y0, x0 = (cover.shape[0] - ch) // 2, (cover.shape[1] - cw) // 2
    bg = cv2.GaussianBlur(cover[y0:y0 + ch, x0:x0 + cw], (0, 0), max(1.0, 40 * out_w / 3840))
    bg = (bg * 0.45).astype(np.uint8)
    s = min(cw / iw, ch / ih)
    fg = _resize(img, min(cw, round(iw * s)), min(ch, round(ih * s)))
    oy, ox = (ch - fg.shape[0]) // 2, (cw - fg.shape[1]) // 2
    bg[oy:oy + fg.shape[0], ox:ox + fg.shape[1]] = fg
    return bg


def upscale(size, w, h, zmax):
    """Output pixels per picture pixel at the tightest framing (above ~1.5 the still looks soft)."""
    iw, ih = size
    cw, ch = w * zmax, h * zmax
    return max(cw / iw, ch / ih) if iw / ih >= WIDE else min(cw / iw, ch / ih)


def ease(t):
    return t * t * (3 - 2 * t)


def matrix(shape, a, b, t, w, h, zmax):
    """Affine map from the canvas to the output frame at time t (0..1) of the move."""
    ch, cw = shape[:2]
    cx, cy, z = (a[i] + (b[i] - a[i]) * ease(t) for i in range(3))
    ww, hh = w * zmax / z, h * zmax / z
    x0 = min(max(cx * cw - ww / 2, 0), cw - ww)
    y0 = min(max(cy * ch - hh / 2, 0), ch - hh)
    s = w / ww
    return np.float32([[s, 0, -x0 * s], [0, s, -y0 * s]])


def frame_at(src, w, h, a=FROM, b=TO, t=0.5):
    """One BGR frame of the move (for before/after sheets)."""
    zmax = max(a[2], b[2])
    cv = canvas(load(src), round(w * zmax), round(h * zmax), w)
    return cv2.warpAffine(cv, matrix(cv.shape, a, b, t, w, h, zmax), (w, h), flags=cv2.INTER_LINEAR)


def render(src, dst, secs, a=FROM, b=TO, grade=None, w=3840, h=2160, rate="30000/1001", codec=None):
    zmax = max(a[2], b[2])
    cv = canvas(load(src), round(w * zmax), round(h * zmax), w)
    n = max(1, round(float(secs) * float(Fraction(rate))))
    vf = C.to_bt709() + (f",{grade},format=yuv420p" if grade else "")
    enc = subprocess.Popen([C.FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                            "-r", rate, "-i", "-", "-vf", vf, *(codec or C.segment_codec(None, w, h)), dst],
                           stdin=subprocess.PIPE)
    try:
        for k in range(n):
            m = matrix(cv.shape, a, b, k / max(1, n - 1), w, h, zmax)
            enc.stdin.write(cv2.warpAffine(cv, m, (w, h), flags=cv2.INTER_LINEAR).tobytes())
    except BrokenPipeError:
        pass
    enc.stdin.close()
    if enc.wait():
        raise SystemExit(f"panzoom: ffmpeg could not encode {dst}")
    return n


def triple(text):
    v = tuple(float(x) for x in text.split(","))
    if len(v) != 3:
        raise argparse.ArgumentTypeError("expected cx,cy,zoom")
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("seconds", type=float)
    ap.add_argument("--from", dest="a", type=triple, default=FROM, help="cx,cy,zoom at the start")
    ap.add_argument("--to", dest="b", type=triple, default=TO, help="cx,cy,zoom at the end")
    ap.add_argument("--grade", help="ffmpeg filter chain applied to every frame")
    ap.add_argument("--size", default="3840x2160")
    ap.add_argument("--fps", default="30000/1001")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.lower().split("x"))
    print(render(a.src, a.dst, a.seconds, a.a, a.b, a.grade, w, h, a.fps), "frames ->", a.dst)


if __name__ == "__main__":
    main()
