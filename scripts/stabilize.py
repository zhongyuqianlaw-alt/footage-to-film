#!/usr/bin/env python3
"""Stabilise a clip: estimate the camera trajectory, Gaussian-smooth it, warp each frame by the
difference (rotation about the frame centre) and apply one fixed zoom so the borders never show.

The zoom is the exact one at which no output corner samples outside the source. When that is above
--max-zoom (stab.max_zoom in the EDL), the corrections it cannot hide (translation and rotation together)
are shortened on those frames just enough to fit, and a warning gives the zoom that would be needed and
the jitter left. A clip with corrections under 1 px is written without zoom ("nothing to stabilise").
The trajectory is padded by odd reflection, so a steady pan or drone move keeps its speed up to the
first and last frame instead of easing in and out. build_film.py cuts stabilised shots with about 1 s
of handles on each side and keeps only the shot's own frames (keep=(first frame, count)).
The output keeps the source's exact rational frame rate (e.g. 30000/1001, not 29.970030),
otherwise xfade later refuses to join it with other segments. Rotated phone clips are handled at
their display size. Run on its own, the output keeps the source's audio (copied, or AAC when the
container cannot take the original codec); build_film.py segments carry no audio. A warning is printed
when the visible (hf) jitter does not drop by at least 20 % (the movement is then mostly scene content:
water, people, traffic; keep the original or replace the shot) and when it is still above 1 px.
An existing out.mp4 is refused unless --force is given.

usage: stabilize.py in.mp4 out.mp4 [--smooth 1.0] [--max-zoom 1.10] [--force]
"""
import argparse
import json
import math
import os
import subprocess
import sys
from fractions import Fraction

import deps  # noqa: F401
import cv2
import numpy as np

import common as C
from common import FFMPEG, segment_codec
from shake_meter import AH, AW, report, trajectory

STILL_HF = 1.0


def smooth(x, sigma):
    r = max(1, int(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    return np.convolve(np.pad(x, r, mode="reflect", reflect_type="odd"), k / k.sum(), "valid")


def corrections(traj, fps, smooth_s, w, h):
    """Per-frame (dx, dy, angle) that moves each frame onto the smoothed path, in source pixels."""
    traj = np.vstack([np.zeros(3), traj])
    sm = np.stack([smooth(traj[:, i], max(0.5, smooth_s * fps / 2)) for i in range(3)], 1)
    c = sm - traj
    c[:, 0] *= w / AW
    c[:, 1] *= h / AH
    return c


def needed_zoom(c, w, h):
    """Per frame, the smallest zoom at which warp_stream samples no output corner outside the source (exact).

    A source point about the centre is R(-a) (q / z - d) for an output corner q, so it moves on a straight line
    from -R(-a) d (infinite zoom) as 1 / z grows; the largest 1 / z that keeps all four corners inside gives z."""
    q = np.array([[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]], float) - [w / 2, h / 2]
    cos, sin = np.cos(c[:, 2]), np.sin(c[:, 2])
    inv = np.stack([np.stack([cos, sin], -1), np.stack([-sin, cos], -1)], 1)
    a = -np.einsum("nij,nj->ni", inv, c[:, :2])[:, None]
    b = np.einsum("nij,kj->nki", inv, q)
    lo, hi = np.array([-w / 2, -h / 2]), np.array([w / 2 - 1, h / 2 - 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(b > 0, (hi - a) / b, np.where(b < 0, (lo - a) / b, np.inf)).min((1, 2))
    inside = ((a[:, 0] >= lo) & (a[:, 0] <= hi)).all(1)
    return np.where(inside & (s > 0), 1 / np.maximum(s, 1e-12), np.inf)


def fit(c, max_zoom, w, h):
    """Shorten each correction max_zoom cannot hide (translation and rotation together) just enough to fit it.

    The factor is a continuous function of the correction, so the output path stays continuous; returns
    (corrections, factor per frame)."""
    over = needed_zoom(c, w, h) > max_zoom
    if not over.any():
        return c, np.ones(len(c))
    lo, hi = np.zeros(len(c)), np.ones(len(c))
    for _ in range(30):
        mid = (lo + hi) / 2
        ok = needed_zoom(c * mid[:, None], w, h) <= max_zoom
        lo, hi = np.where(ok, mid, lo), np.where(ok, hi, mid)
    k = np.where(over, lo, 1.0)
    return c * k[:, None], k


def warp_stream(src, dst, c, z, size, rate, codec, keep, matrix=None):
    """Decode with the source's YUV matrix (unknown = BT.709), warp in BGR, encode as tagged BT.709."""
    (w, h), (first, count) = size, keep
    m = C.SCALE_MATRIX.get(matrix or "", "bt709")
    dec = subprocess.Popen([FFMPEG, "-v", "error", "-i", src, "-frames:v", str(first + count), "-vf",
                            f"scale=in_color_matrix={m}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                            "-r", rate, "-i", "-", "-vf", C.to_bt709(), *codec, "-pix_fmt", "yuv420p", dst],
                           stdin=subprocess.PIPE)
    nbytes, k, written = w * h * 3, 0, 0
    while written < count:
        b = dec.stdout.read(nbytes)
        if len(b) < nbytes:
            break
        if k >= first:
            dx, dy, da = c[min(k, len(c) - 1)]
            m = cv2.getRotationMatrix2D((w / 2, h / 2), -np.degrees(da), z)
            m[:, 2] += [dx * z, dy * z]
            frame = cv2.warpAffine(np.frombuffer(b, np.uint8).reshape(h, w, 3), m, (w, h),
                                   flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
            enc.stdin.write(frame.tobytes())
            written += 1
        k += 1
    dec.stdout.close()
    enc.stdin.close()
    if enc.wait() or dec.wait() or not written:
        raise SystemExit(f"stabilize: ffmpeg failed on {src} ({written} frames written)")
    return written


def check_dst(src, dst):
    """Fail before the slow trajectory pass, not after it."""
    d = os.path.dirname(os.path.abspath(dst))
    if not os.path.isdir(d):
        raise SystemExit(f"stabilize: output folder {d} does not exist; create it first")
    if os.path.abspath(src) == os.path.abspath(dst):
        raise SystemExit("stabilize: the output must be a different file from the input")


def mux_audio(video, src, dst):
    """The stabilised picture with the source's audio: copied when the container takes it, else AAC."""
    for acodec in (["-c:a", "copy"], ["-c:a", "aac", "-b:a", "256k"]):
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", video, "-i", src, "-map", "0:v:0", "-map", "1:a",
                            "-c:v", "copy", *acodec, dst], capture_output=True, text=True)
        if r.returncode == 0:
            return
    raise SystemExit(f"stabilize: could not add the audio of {src}: {r.stderr.strip()[-300:]}")


def no_gain(out):
    """A warning when stabilising did not lower the visible (hf) jitter by at least 20 %, else None."""
    b, a = out["before"]["hf"], out["after"]["hf"]
    if not (b and a):
        return None
    pairs = [(b[k], a[k]) for k, floor in (("jit_px", 0.3), ("jit_rot_deg", 0.05)) if b[k] >= floor]
    if not pairs or any(after <= 0.8 * before for before, after in pairs):
        return None
    return (f"stabilisation did not help (hf {b['jit_px']} -> {a['jit_px']} px, {b['jit_rot_deg']} -> "
            f"{a['jit_rot_deg']} deg; moving content, not the camera?); keep the original or replace the shot")


def plan(c, shown, max_zoom, w, h):
    """(corrections, zoom, facts): one fixed zoom for the shown frames; what max_zoom cannot hide is scaled down."""
    need = min(10.0, max(1.0, float(needed_zoom(c[shown], w, h).max())))
    if need <= 1 + 2 / w:
        return np.zeros_like(c), 1.0, {"zoom_needed": 1.0, "reduced_frames": 0, "steady": True}
    c, k = fit(c, max_zoom, w, h)
    z = min(max_zoom, max(1.0, float(needed_zoom(c[shown], w, h).max())) + 0.0005)
    return c, z, {"zoom_needed": round(need, 3), "reduced_frames": int((k[shown] < 0.999).sum())}


def warnings(out, max_zoom):
    """Why the result may still shake: the zoom limit held the correction back, no gain, or jitter above 1 px."""
    after, msgs = (out["after"]["hf"] or {}).get("jit_px"), []
    if out["reduced_frames"]:
        msgs.append(f"hiding the borders needs zoom {out['zoom_needed']} but max_zoom is {max_zoom}, so the correction "
                    f"was reduced on {out['reduced_frames']} of {out['frames']} frames (hf jitter after: {after} px); "
                    f"stab.max_zoom / --max-zoom {math.ceil(out['zoom_needed'] * 100) / 100} would correct it fully")
    gain = no_gain(out)
    if gain:
        msgs.append(gain)
    elif after is not None and after > STILL_HF:
        msgs.append(f"still shaky after stabilising (hf {after} px > {STILL_HF} px): replace the shot")
    return msgs


def stabilize(src, dst, smooth_s=1.0, max_zoom=1.10, codec=None, keep=None, label=None):
    """keep=(first frame, count) is build_film's handle cut (video only); without it the audio is kept.
    label names the clip in the notes and warnings (default: src)."""
    check_dst(src, dst)
    try:
        info = C.probe_source(src)
    except C.ProbeError as e:
        raise SystemExit(f"stabilize: {e}")
    w, h, rate = info["width"], info["height"], info["r_frame_rate"]
    fps = float(Fraction(rate))
    traj = trajectory(src)
    first, count = keep or (0, 1 << 30)
    c, z, facts = plan(corrections(traj, fps, smooth_s, w, h), slice(first, first + count), max_zoom, w, h)
    if facts.pop("steady", False):
        print(f"note: {label or src}: nothing to stabilise (corrections under 1 px); written without zoom",
              file=sys.stderr, flush=True)
    audio = keep is None and info["has_audio"]
    root, ext = os.path.splitext(dst)
    video = f"{root}.video{ext}" if audio else dst
    n = warp_stream(src, video, c, z, (w, h), rate, codec or segment_codec(None, w, h), (first, count),
                    info["matrix"])
    if audio:
        mux_audio(video, src, dst)
        os.remove(video)
    shown = traj[max(0, first - 1):first + count - 1]
    out = {"frames": n, "zoom": round(z, 3), **facts, "before": report(shown, fps),
           "after": report(trajectory(dst), fps)}
    print("stabilized", json.dumps(out), flush=True)
    for msg in warnings(out, max_zoom):
        print(f"warning: {label or src}: {msg}", file=sys.stderr, flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--smooth", type=float, default=1.0, help="smoothing window in seconds (>= 0.1)")
    ap.add_argument("--max-zoom", type=float, default=1.10, help="largest crop zoom that hides the borders (>= 1.0)")
    ap.add_argument("--force", action="store_true", help="overwrite dst when it exists")
    a = ap.parse_args()
    if a.smooth < 0.1:
        ap.error("--smooth must be at least 0.1 s")
    if a.max_zoom < 1.0:
        ap.error("--max-zoom must be 1.0 or more (below 1.0 the frame shrinks and its mirrored borders show)")
    if os.path.exists(a.dst) and not a.force:
        sys.exit(f"stabilize: {a.dst} exists; pass --force to overwrite it")
    stabilize(a.src, a.dst, a.smooth, a.max_zoom)


if __name__ == "__main__":
    main()
