#!/usr/bin/env python3
"""Global camera-shake meter.

LK optical flow on a 640x360 grey proxy gives a per-frame similarity transform (translation measured
about the frame centre, so a pure roll does not read as sideways shake); the cumulative trajectory minus
its moving average is the jitter. Two windows are reported, defined in seconds so clips of different
frame rates compare:
  hf = 0.3 s window (9 frames at 29.97p; what reads as "shaky" to a viewer)
  lf = 1.0 s window (31 frames at 29.97p; includes slower wobble / sway)
Readings are in pixels at 640 px width. The reading is about everything that moves in the frame, not
only the camera: water, sky and waves, and also moving content in a static frame (people walking past a
tripod, a crane swinging, traffic, flags, smoke) inflate it. Confirm with subject_track.py or by eye.
Rules of thumb for hf jit_px: < 0.5 steady; 0.5-1.5 check on a big screen (and with subject_track.py when
water, sky or moving content dominate); > 1.5 stabilise; still > 1.0 after stabilize.py, replace the shot.

usage: shake_meter.py <clip.mp4 | segment dir> [...] [--ss 12.5 --t 6]
"""
import argparse
import json
import subprocess
import sys

import deps  # noqa: F401
import cv2
import numpy as np

import common as C
from common import FFMPEG

AW, AH = 640, 360
HF_S, LF_S = 0.3, 1.0


def frames(path, ss=None, t=None):
    cmd = [FFMPEG, "-v", "error"]
    if ss is not None:
        cmd += ["-ss", str(ss)]
    if t is not None:
        cmd += ["-t", str(t)]
    cmd += ["-i", path, "-vf", f"scale={AW}:{AH},format=gray", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    while True:
        b = p.stdout.read(AW * AH)
        if len(b) < AW * AH:
            break
        yield np.frombuffer(b, np.uint8).reshape(AH, AW)
    err = p.stderr.read().decode(errors="replace").strip()
    if p.wait() != 0:
        raise C.ProbeError(f"ffmpeg could not decode {path}: {err[-300:]}")


def step(prev, cur):
    pts = cv2.goodFeaturesToTrack(prev, 300, 0.01, 12)
    if pts is None or len(pts) < 10:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None)
    ok = st.ravel() == 1
    if ok.sum() < 10:
        return None
    m, _ = cv2.estimateAffinePartial2D(pts[ok], nxt[ok])
    if m is None:
        return None
    c = np.array([AW / 2, AH / 2])
    t = m[:, 2] - (np.eye(2) - m[:, :2]) @ c
    return np.array([t[0], t[1], np.arctan2(m[1, 0], m[0, 0])])


def trajectory(path, ss=None, t=None):
    """Cumulative (dx, dy, angle) of frame k relative to frame 0 about the frame centre, for k >= 1."""
    prev, acc, out = None, np.zeros(3), []
    for g in frames(path, ss, t):
        if prev is not None:
            d = step(prev, g)
            acc = acc + d if d is not None else acc
            out.append(acc.copy())
        prev = g
    return np.array(out).reshape(-1, 3)


def window(fps, seconds):
    n = max(3, int(round(fps * seconds)))
    return n if n % 2 else n + 1


def moving_average(x, win):
    """Centred moving average; odd reflection at the ends keeps a steady pan from reading as jitter."""
    return np.convolve(np.pad(x, win // 2, mode="reflect", reflect_type="odd"), np.ones(win) / win, "valid")


def jitter(traj, win=31):
    if len(traj) < win + 2:
        return None
    sm = np.stack([moving_average(traj[:, i], win) for i in range(3)], 1)
    r = traj - sm
    mag = np.sqrt((r[:, :2] ** 2).sum(1))
    return {"jit_px": round(float(mag.mean()), 2), "jit_p95_px": round(float(np.percentile(mag, 95)), 2),
            "jit_rot_deg": round(float(np.degrees(np.sqrt((r[:, 2] ** 2).mean()))), 3)}


def report(traj, fps):
    return {"hf": jitter(traj, window(fps, HF_S)), "lf": jitter(traj, window(fps, LF_S))}


def measure(path, ss=None, t=None):
    fps = C.probe_source(path)["fps"]
    tr = trajectory(path, ss, t)
    return {"file": path, "fps": round(fps, 3), "frames": len(tr) + 1, **report(tr, fps)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="clips, or folders of clips (e.g. <scratch_dir>/segments)")
    ap.add_argument("--ss", type=float)
    ap.add_argument("--t", type=float)
    a = ap.parse_args()
    files = [p for p in C.expand(a.inputs) if not p.endswith("_raw.mp4")]
    if not files:
        sys.exit("no clips found in " + " ".join(a.inputs))
    failed = 0
    for p in files:
        try:
            r = measure(p, a.ss, a.t)
        except C.ProbeError as e:
            print(f"error: {e}", file=sys.stderr)
            failed += 1
            continue
        print(json.dumps(r, ensure_ascii=False), flush=True)
        if r["frames"] < 2:
            print(f"error: {p}: {r['frames']} frame decoded, nothing to measure (--ss past the end, or not a clip?)",
                  file=sys.stderr)
            failed += 1
        elif r["lf"] is None:
            need = window(r["fps"], LF_S if r["hf"] else HF_S) + 3
            print(f"note: {p}: {r['frames']} frames is too short for the {LF_S if r['hf'] else HF_S} s window "
                  f"({need} needed); its reading is null", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
