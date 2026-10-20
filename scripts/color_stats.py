#!/usr/bin/env python3
"""Per-shot colour statistics from N evenly spaced frames (or one still image), to decide which shots
need grading.

Reports Lab lightness mean / P2 / P98, contrast (L std-dev), HSV saturation, Lab chroma (mean distance
from grey in a*/b*, so a darkened and desaturated shot reads low even where HSV saturation, normalised by
brightness, does not) and colour cast (a*, b*), and flags weak shots: "clipped" (P98 > 96), "crushed"
(P2 < 2) and, compared with the median of all inputs of the run, "dark" / "bright" (L_mean 10 below /
above), "flat" (contrast < 75 %) and "dull" (chroma < 75 %).
Results are keyed by path as given; cached segments (shot_NN_<hash>.mp4) also get "shot": NN. Still
images (.jpg/.png) are accepted, e.g. the reference frames for workflows/grade.js. Black pillarbox and
letterbox bars (uniformly near-black edge columns / rows, e.g. a vertical clip with "fit": "pillarbox")
are left out, so they do not read as a dark, crushed shot. Unreadable inputs are reported on stderr
("error: ...") and make the exit status 1.

usage: color_stats.py <segment dir | clip.mp4 | frame.jpg> [...] [--n 5] [--out stats.json]
"""
import argparse
import json
import os
import re
import subprocess
import sys

import deps  # noqa: F401
import cv2
import numpy as np

import common as C
from common import FFMPEG

SW, SH = 640, 360
IMAGE_EXT = (".jpg", ".jpeg", ".png")
BAR_MAX = 24


def _span(dark):
    """First and last index of content along one axis, skipping dark runs at both edges."""
    idx = np.flatnonzero(~dark)
    return (int(idx[0]), int(idx[-1]) + 1) if len(idx) else (0, len(dark))


def without_bars(frame):
    """The frame without black bars: edge columns / rows whose brightest pixel is near black."""
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    (x0, x1), (y0, y1) = _span(g.max(0) <= BAR_MAX), _span(g.max(1) <= BAR_MAX)
    return frame[y0:y1, x0:x1]


def sample(path, n=5):
    if path.lower().endswith(IMAGE_EXT):
        im = cv2.imread(path) if os.path.isfile(path) else None
        if im is None:
            raise C.ProbeError(f"{'not an image' if os.path.isfile(path) else 'not found'}: {path}")
        yield cv2.resize(im, (SW, SH), interpolation=cv2.INTER_AREA)
        return
    d = C.probe_source(path)["duration"]
    for k in range(n):
        raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{d * (k + 0.5) / n:.2f}", "-i", path, "-frames:v", "1",
                              "-vf", f"scale={SW}:{SH}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                             capture_output=True).stdout
        if len(raw) == SW * SH * 3:
            yield np.frombuffer(raw, np.uint8).reshape(SH, SW, 3)


def stats(path, n=5):
    fr = [without_bars(f) for f in sample(path, n)]
    if not fr:
        raise C.ProbeError(f"no frame could be decoded from {path}")
    lab = np.concatenate([cv2.cvtColor(f, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(float) for f in fr])
    hsv = np.concatenate([cv2.cvtColor(f, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(float) for f in fr])
    L = lab[:, 0] * 100 / 255
    out = {"L_mean": round(L.mean(), 1), "L_p2": round(np.percentile(L, 2), 1), "L_p98": round(np.percentile(L, 98), 1),
           "contrast": round(L.std(), 1), "sat": round(hsv[:, 1].mean() / 255 * 100, 1),
           "chroma": round(np.hypot(lab[:, 1] - 128, lab[:, 2] - 128).mean(), 1),
           "a": round(lab[:, 1].mean() - 128, 1), "b": round(lab[:, 2].mean() - 128, 1)}
    m = re.match(r"shot_(\d+)_", os.path.basename(path))
    return {**out, "shot": int(m.group(1))} if m else out


def flag(results):
    ok = [v for v in results.values() if v]
    med = {k: float(np.median([v[k] for v in ok])) for k in ("L_mean", "contrast", "chroma")} if len(ok) >= 3 else None
    for v in ok:
        f = (["clipped"] if v["L_p98"] > 96 else []) + (["crushed"] if v["L_p2"] < 2 else [])
        if med:
            f += ["dark"] if v["L_mean"] < med["L_mean"] - 10 else []
            f += ["bright"] if v["L_mean"] > med["L_mean"] + 10 else []
            f += ["flat"] if v["contrast"] < 0.75 * med["contrast"] else []
            f += ["dull"] if v["chroma"] < 0.75 * med["chroma"] else []
        v["flags"] = f
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--n", type=int, default=5, help="frames sampled per clip")
    ap.add_argument("--out", help="write all results to this JSON file")
    a = ap.parse_args()
    files = [p for p in C.expand(a.inputs, C.VIDEO_EXT + IMAGE_EXT) if not p.endswith("_raw.mp4")]
    if not files:
        sys.exit("no clips or images found in " + " ".join(a.inputs))
    out = {}
    for p in files:
        try:
            out[p] = stats(p, a.n)
        except C.ProbeError as e:
            print(f"error: {e}", file=sys.stderr)
            out[p] = None
    for p, s in flag(out).items():
        if s:
            print(p, json.dumps(s), flush=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
    sys.exit(1 if any(s is None for s in out.values()) else 0)


if __name__ == "__main__":
    main()
