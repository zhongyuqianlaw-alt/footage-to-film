#!/usr/bin/env python3
"""Subject-tracked jitter: what a viewer sees while watching the subject, not the whole frame.

A template around (cx, cy) is matched frame to frame (and refreshed every frame), with a sub-pixel peak fit;
the residual of its position against a 0.3 s moving average is the jitter, in px at 640 px width (comparable
with shake_meter's hf reading). Use it when water, sky or foliage dominate the frame and the global
optical-flow reading of shake_meter.py is inflated. --cx/--cy give the subject centre as fractions of
width/height (0-1): look at a frame first (e.g. a hull at the lower third: --cx 0.5 --cy 0.66).

Confidence: "match" is the mean normalised match score (1.0 = identical), "texture" the ratio of the weaker
to the stronger gradient direction inside the first template (near 0 on a lone straight edge or a flat
patch, where the match can slide along the edge). A second template half a template away (towards the
frame centre) is tracked too: on one rigid detail the distance between the two stays the same however the
camera shakes or rolls, so "twin_gap_px", the jitter of that distance, is near 0; a template sliding along
an edge makes it large. A twin that lands on a flat patch (sky, calm water, a plain wall: "twin_detail", the
variance of its lightly blurred Laplacian, below 0.15) only follows noise, so it is ignored (twin_gap_px null).
"low_confidence" is true when match < 0.6, texture < 0.25 or the twin gap is above 0.3 px and half the
reading; the reading may then be wrong: pick a corner or a textured detail of the subject.
Fewer frames than the 0.3 s window (e.g. --ss past the end) is an error (exit 1), as in shake_meter.py.

usage: subject_track.py clip.mp4 --cx 0.5 --cy 0.5 [--tw 140 --th 90 --search 40] [--ss 12.5 --t 6]
"""
import argparse
import json
import sys

import deps  # noqa: F401
import cv2
import numpy as np

import common as C
from shake_meter import AH, AW, HF_S, frames, moving_average, window

MIN_MATCH, MIN_TEXTURE, MAX_GAP, MIN_DETAIL = 0.6, 0.25, 0.3, 0.15


def peak_offset(a, b, c):
    """Sub-pixel offset of a peak from three samples around it (parabola fit), within +-0.5."""
    d = a - 2 * b + c
    return float(np.clip(0.5 * (a - c) / d, -0.5, 0.5)) if d < 0 else 0.0


def match(win, tpl):
    """Best template position in win, refined to sub-pixel: (int x, int y, float x, float y, score)."""
    r = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, (mx, my) = cv2.minMaxLoc(r)
    fx = mx + (peak_offset(*r[my, mx - 1:mx + 2]) if 0 < mx < r.shape[1] - 1 else 0.0)
    fy = my + (peak_offset(*r[my - 1:my + 2, mx]) if 0 < my < r.shape[0] - 1 else 0.0)
    return mx, my, fx, fy, float(score)


def texture(tpl):
    """Weaker / stronger eigenvalue of the template's gradient structure tensor (0 = one edge direction only)."""
    g = tpl.astype(np.float32)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    a, b, c = float((gx * gx).sum()), float((gx * gy).sum()), float((gy * gy).sum())
    half, disc = (a + c) / 2, (((a - c) / 2) ** 2 + b * b) ** 0.5
    return (half - disc) / (half + disc) if half + disc > 0 else 0.0


def detail(tpl):
    """Variance of the Laplacian of the lightly blurred template: near 0 on a flat patch, noisy or not, and on a
    smooth gradient (a sky), where the match only follows noise."""
    g = cv2.GaussianBlur(tpl.astype(np.float32), (0, 0), 1.5)
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def corners(cx, cy, tw, th):
    """Top-left corners of the subject template and of its twin half a template away, towards the frame centre."""
    q = (min(max(0, int(cx * AW - tw / 2)), AW - tw), min(max(0, int(cy * AH - th / 2)), AH - th))
    tx = q[0] + (tw // 2 if q[0] + tw / 2 < AW / 2 else -(tw // 2))
    ty = q[1] + (th // 2 if q[1] + th / 2 < AH / 2 else -(th // 2))
    return [q, (min(max(0, tx), AW - tw), min(max(0, ty), AH - th))]


def follow(g, q, tpl, size, sr):
    """One frame step of a template: (new top-left, sub-pixel move, refreshed template, score), or None."""
    tw, th = size
    xa, ya = max(0, q[0] - sr), max(0, q[1] - sr)
    win = g[ya:min(AH, q[1] + th + sr), xa:min(AW, q[0] + tw + sr)]
    if win.shape[0] < th or win.shape[1] < tw:
        return None
    mx, my, fx, fy, score = match(win, tpl)
    nq = (xa + mx, ya + my)
    return nq, (xa + fx - q[0], ya + fy - q[1]), g[nq[1]:nq[1] + th, nq[0]:nq[0] + tw], score


def track(path, starts, tw, th, sr, ss=None, t=None):
    """Positions (frames x templates x 2) of templates from the given top-left corners, the first one's
    match scores, the texture of its first template and the detail of every template on the first frame."""
    pos, scores, qs, tpls, tex, det = [], [], list(starts), None, 0.0, [0.0] * len(starts)
    for g in frames(path, ss, t):
        if tpls is None:
            tpls = [g[q[1]:q[1] + th, q[0]:q[0] + tw] for q in qs]
            pos.append(np.array(qs, float))
            tex, det = texture(tpls[0]), [detail(x) for x in tpls]
            continue
        row = pos[-1].copy()
        for j, q in enumerate(qs):
            step = follow(g, q, tpls[j], (tw, th), sr)
            if step:
                qs[j], d, tpls[j], score = step
                row[j] += d
                if j == 0:
                    scores.append(score)
        pos.append(row)
    return np.array(pos, float).reshape(-1, len(qs), 2), scores, tex, det


def residual(pos, win=9):
    """Distance of each sample (a position, or a scalar) from its moving average."""
    if len(pos) < win + 2:
        return None
    pos = pos.reshape(len(pos), -1)
    sm = np.stack([moving_average(pos[:, i], win) for i in range(pos.shape[1])], 1)
    return np.sqrt(((pos - sm) ** 2).sum(1))


def measure(path, cx, cy, tw=140, th=90, sr=40, ss=None, t=None):
    """The reading as a dict; subject_hf_px is None when fewer frames than the 0.3 s window were decoded."""
    win = window(C.probe_source(path)["fps"], HF_S)
    pos, scores, tex, det = track(path, corners(cx, cy, tw, th), tw, th, sr, ss, t)
    dist = np.linalg.norm(pos[:, 1] - pos[:, 0], axis=1)
    res, gap = residual(pos[:, 0], win), residual(dist, win)
    score = round(float(np.mean(scores)), 3) if scores else None
    out = {"file": path, "cx": cx, "cy": cy, "frames": len(pos), "subject_hf_px": None, "p95": None,
           "match": score, "texture": round(tex, 3), "twin_detail": round(det[1], 2), "twin_gap_px": None,
           "low_confidence": True}
    if res is None:
        return out
    hf = round(float(res.mean()), 2)
    gap = round(float(gap.mean()), 2) if det[1] >= MIN_DETAIL else None
    low = (score is None or score < MIN_MATCH or tex < MIN_TEXTURE
           or (gap is not None and gap > max(MAX_GAP, 0.5 * hf)))
    out.update(subject_hf_px=hf, p95=round(float(np.percentile(res, 95)), 2), twin_gap_px=gap, low_confidence=low)
    return out


def fraction(text):
    v = float(text)
    if not 0.0 <= v <= 1.0:
        raise argparse.ArgumentTypeError(f"{v} is not a fraction between 0 and 1")
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--cx", type=fraction, default=0.5, help="subject centre, fraction of width (0-1)")
    ap.add_argument("--cy", type=fraction, default=0.5, help="subject centre, fraction of height (0-1)")
    ap.add_argument("--tw", type=int, default=140, help="template width in proxy px")
    ap.add_argument("--th", type=int, default=90, help="template height in proxy px")
    ap.add_argument("--search", type=int, default=40, help="search radius in proxy px per frame")
    ap.add_argument("--ss", type=float)
    ap.add_argument("--t", type=float)
    a = ap.parse_args()
    if not (8 <= a.tw <= AW and 8 <= a.th <= AH and a.search >= 1):
        ap.error(f"--tw 8-{AW}, --th 8-{AH} and --search >= 1 (proxy px)")
    try:
        out = measure(a.file, a.cx, a.cy, a.tw, a.th, a.search, a.ss, a.t)
    except C.ProbeError as e:
        sys.exit(f"error: {e}")
    if out["subject_hf_px"] is None:
        need = window(C.probe_source(a.file)["fps"], HF_S) + 2
        sys.exit(f"error: {a.file}: {out['frames']} frames decoded, {need} needed for the {HF_S} s window; nothing "
                 "to measure (--ss past the end, or --t too short?)")
    if out["low_confidence"]:
        twin = "twin on a flat patch" if out["twin_gap_px"] is None else f"twin gap {out['twin_gap_px']} px"
        print(f"note: {a.file}: low tracking confidence at ({a.cx}, {a.cy}) (match {out['match']}, texture "
              f"{out['texture']}, {twin}): the template may slide along an edge or lose the subject; pick a corner "
              "or a textured detail", file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
