#!/usr/bin/env python3
"""Timestamped contact sheets for every candidate clip, so agents log real content instead of guessing.

One keyframe-only decode pass per clip; showinfo gives the true pts of each picked frame, which is
printed (mm:ss.s) in the corner of every tile. Clips shorter than 10 s are decoded frame by frame; when
the keyframes of a longer clip give fewer than 80 % of the target tiles (long keyframe intervals), one
frame is taken per step with an accurate seek instead. A same-stem low-res proxy (.LRF) is decoded
instead of the original when present (same timeline, much faster).

input : a text file with one clip path per line, relative to --library ('#' starts a comment line)
output: <out>/<id>/sheet_NN.jpg, <out>/index.json and <out>/index_compact.json
        index rows carry display size (rotation applied), fps, flags (vertical, hdr), absolute sheet paths
        and any error; compact rows = [id, file, duration, res, fps, step_s, n_sheets, flags] for
        workflows/storyboard.js
prints: clips without sheets, vertical and HDR clips (and whether this ffmpeg can tone-map HDR), and the
        frame rates of the library (set project master.fps to the main one)
exit  : 0, or 1 when any candidate got no sheets (the index is written either way)

usage: make_sheets.py candidates.txt --library /footage --out /scratch/sheets [--frames 47 --jobs 6]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import deps  # noqa: F401
from PIL import Image, ImageDraw

import common as C
from cards import load_font

SHORT_S, MIN_KEYFRAME_SHARE = 10.0, 0.8


def proxy_for(path):
    stem = os.path.splitext(path)[0]
    for ext in (".LRF", ".lrf"):
        if os.path.exists(stem + ext):
            return stem + ext
    return path


def decode(src, d, step, tw, keyframes):
    for f in os.listdir(d):
        if f.startswith("f_"):
            os.remove(os.path.join(d, f))
    sel = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{step - 0.05:.2f})',scale={tw}:-2,showinfo"
    base = [C.FFMPEG, "-y", "-hide_banner", *(["-skip_frame", "nokey"] if keyframes else []), "-i", proxy_for(src),
            "-vf", sel]
    out = ["-q:v", "4", os.path.join(d, "f_%03d.jpg")]
    r = subprocess.run(base + ["-fps_mode", "vfr"] + out, capture_output=True, text=True)
    if r.returncode and "fps_mode" in r.stderr:
        r = subprocess.run(base + ["-vsync", "vfr"] + out, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("ffmpeg failed: " + (r.stderr.strip().splitlines() or ["?"])[-1])
    frames = sorted(f for f in os.listdir(d) if f.startswith("f_"))
    times = [float(m) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)][:len(frames)]
    return list(zip(frames, times))


def seek(src, d, step, tw, dur):
    """One frame per step, each found with an accurate seek (decodes from the keyframe before it)."""
    for f in os.listdir(d):
        if f.startswith("f_"):
            os.remove(os.path.join(d, f))
    picked = []
    for j in range(max(1, int(dur / step))):
        name, t = f"f_{j + 1:03d}.jpg", j * step
        r = subprocess.run([C.FFMPEG, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", proxy_for(src), "-frames:v", "1",
                            "-vf", f"scale={tw}:-2", "-q:v", "4", os.path.join(d, name)], capture_output=True)
        if r.returncode == 0 and os.path.exists(os.path.join(d, name)):
            picked.append((name, t))
    return picked


def extract(src, d, step, tw, dur):
    """Keyframes for long clips, frame-by-frame decoding for short ones, accurate seeks when keyframes are sparse."""
    if dur < SHORT_S:
        return decode(src, d, step, tw, False)
    picked = decode(src, d, step, tw, True)
    return picked if len(picked) >= MIN_KEYFRAME_SHARE * int(dur / step) else seek(src, d, step, tw, dur)


def tile_sheets(d, picked, cols, rows, tw, font):
    sheets, per = [], cols * rows
    for k in range(0, len(picked), per):
        chunk = picked[k:k + per]
        th = Image.open(os.path.join(d, chunk[0][0])).height
        sheet = Image.new("RGB", (cols * tw, ((len(chunk) + cols - 1) // cols) * th), "black")
        dr = ImageDraw.Draw(sheet)
        for i, (f, t) in enumerate(chunk):
            x, y = (i % cols) * tw, (i // cols) * th
            sheet.paste(Image.open(os.path.join(d, f)), (x, y))
            dr.rectangle([x, y, x + 96, y + 28], fill=(0, 0, 0))
            dr.text((x + 4, y + 2), f"{int(t // 60):02d}:{t % 60:04.1f}", font=font, fill=(255, 220, 0))
        p = os.path.join(d, f"sheet_{k // per + 1:02d}.jpg")
        sheet.save(p, quality=82)
        sheets.append(p)
    return sheets


def grab(cid, rel, a, font):
    src, row = os.path.join(a.library, rel), {"id": cid, "file": rel, "sheets": [], "flags": []}
    try:
        info = C.probe_source(src)
        dur = info["duration"]
        step = max(2.0, dur / a.frames)
        d = os.path.join(a.out, cid)
        os.makedirs(d, exist_ok=True)
        picked = extract(src, d, step, a.width, dur)
        sheets = tile_sheets(d, picked, a.cols, a.rows, a.width, font) if picked else []
        for f, _ in picked:
            os.remove(os.path.join(d, f))
    except (C.ProbeError, RuntimeError, OSError) as e:
        return {**row, "error": str(e), "flags": ["error"]}
    flags = [k for k in ("vertical", "hdr") if info[k]]
    return {**row, "duration": round(dur, 2), "res": f"{info['width']}x{info['height']}", "fps": info["r_frame_rate"],
            "rotation": info["rotation"], "transfer": info["transfer"], "step_s": round(step, 2), "tiles": len(picked),
            "sheets": sheets, "flags": flags + ([] if sheets else ["no-sheets"])}


def read_candidates(path, library):
    with open(path, encoding="utf-8") as fh:
        rels = [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    missing = [r for r in rels if not os.path.isfile(os.path.join(library, r))]
    if missing:
        print(f"warning: {len(missing)} candidate(s) not found under {library}:\n  " + "\n  ".join(missing[:20]),
              file=sys.stderr)
    return rels


def report(res):
    bad = [r for r in res if not r["sheets"]]
    for r in bad:
        print(f"warning: {r['id']} {r['file']}: no sheets ({r.get('error', 'no frames decoded')})", file=sys.stderr)
    for k in ("vertical", "hdr"):
        hits = [f"{r['id']} {r['file']}" for r in res if k in r["flags"]]
        if hits:
            print(f"{k} clips ({len(hits)}), decide how to treat them before storyboarding:\n  " + "\n  ".join(hits))
    if any("hdr" in r["flags"] for r in res):
        print("this ffmpeg " + ("CAN tone-map HDR clips (zscale found)" if C.has_filter("zscale") else
                                "CANNOT tone-map HDR clips (no zscale): see SKILL.md Requirements, or set "
                                "FFMPEG / FFPROBE to an ffmpeg built with libzimg"))
    rates = Counter(r["fps"] for r in res if r.get("fps"))
    if rates:
        print("frame rates: " + ", ".join(f"{k} x{n}" for k, n in rates.most_common())
              + " (set master.fps to the main rate; 2x rates are for 0.5 slow motion)")
    print(len(res), "clips,", sum(len(r["sheets"]) for r in res), "sheets,", len(bad), "without sheets")
    return len(bad)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates")
    ap.add_argument("--library", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=47, help="target frames per clip (min spacing 2 s)")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--rows", type=int, default=4)
    ap.add_argument("--width", type=int, default=480, help="tile width in px")
    ap.add_argument("--font", default="", help="TTF for the timestamps (default: PIL's built-in)")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    small = [f"--{k}" for k in ("frames", "cols", "rows", "width", "jobs") if getattr(a, k) < 1]
    if small:
        ap.error(f"{', '.join(small)} must be 1 or more")
    a.out = os.path.abspath(a.out)
    rels = read_candidates(a.candidates, a.library)
    os.makedirs(a.out, exist_ok=True)
    font = load_font(a.font, 22)
    with ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(lambda j: grab(f"c{j[0]:03d}", j[1], a, font), enumerate(rels, 1)))
    with open(os.path.join(a.out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    compact = [[r["id"], r["file"], r.get("duration"), r.get("res"), r.get("fps"), r.get("step_s"), len(r["sheets"]),
                ",".join(r["flags"])] for r in res]
    with open(os.path.join(a.out, "index_compact.json"), "w", encoding="utf-8") as fh:
        json.dump(compact, fh, ensure_ascii=False)
    failed = report(res)
    print("index:", os.path.join(a.out, "index_compact.json"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
