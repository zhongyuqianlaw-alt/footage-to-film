#!/usr/bin/env python3
"""Before/after grading sheets: for every shot with a grade, the source frame at the
shot's midpoint rendered with grade_prev (or default_grade when a shot had no grade before; nothing for a
still) and grade side by side (4 shots per sheet). Frames go through the same tone mapping (HDR) and fit
(crop / blur / pillarbox; a still's pan-and-zoom framing at mid-move) as the build, so the grade is judged on
the picture the film shows.

Audit these for over-processing before accepting a grade: skin tones, red decks, greens, clipped skies.

usage: grade_compare.py --project project.json --out /path/prefix [--labels before,after]
"""
import argparse
import os
import subprocess

import deps  # noqa: F401
from PIL import Image, ImageDraw

import common as C
from cards import load_font

TW, TH = 960, 540


def source_args(P, s):
    """(ffmpeg input args, picture chain) of the shot's mid frame; a still is framed by panzoom.py first."""
    src = os.path.join(P["library_dir"], s["file"])
    if s.get("still"):
        from panzoom import FROM, TO, frame_at
        kb = s.get("kb") or {}
        im = frame_at(src, TW, TH, tuple(kb.get("from", FROM)), tuple(kb.get("to", TO)))
        return ["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{TW}x{TH}", "-i", "-"], "null", im.tobytes()
    try:
        chain = C.picture_chain(P, s, TW, TH)
    except C.ProbeError as e:
        raise SystemExit(f"shot {s['n']}: {e}")
    return ["-ss", f"{(s['in_s'] + s['out_s']) / 2:.2f}", "-i", src], chain, None


def frame(P, s, grade):
    inp, chain, raw = source_args(P, s)
    r = subprocess.run([C.FFMPEG, "-v", "error", *inp, "-frames:v", "1", "-vf", f"{chain},{grade or 'null'}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], input=raw, capture_output=True)
    if r.returncode or len(r.stdout) != TW * TH * 3:
        err = (r.stderr.decode(errors="replace").strip().splitlines() or ["no frame decoded"])[0]
        raise SystemExit(f"shot {s['n']}: grade \"{grade}\" could not be rendered: {err}")
    return Image.frombytes("RGB", (TW, TH), r.stdout)


def label(img, text, font):
    d = ImageDraw.Draw(img)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.rectangle([0, 0, r - l + 24, b - t + 18], fill=(0, 0, 0))
    d.text((12 - l, 9 - t), text, font=font, fill=(255, 220, 0))


def sheets(P, shots, prefix, labels):
    font = load_font(P["fonts"].get("latin_bold") or P["fonts"].get("latin", ""), 26)
    out = []
    for k in range(0, len(shots), 4):
        rows = shots[k:k + 4]
        sheet = Image.new("RGB", (2 * TW + 8, len(rows) * (TH + 8)), "white")
        for i, s in enumerate(rows):
            before = s.get("grade_prev") or (None if s.get("still") else P["default_grade"])
            a, b = frame(P, s, before), frame(P, s, s["grade"])
            label(a, f"shot {s['n']} - {labels[0]}", font)
            label(b, f"shot {s['n']} - {labels[1]}", font)
            sheet.paste(a, (0, i * (TH + 8)))
            sheet.paste(b, (TW + 8, i * (TH + 8)))
        path = f"{prefix}_{k // 4 + 1}.jpg"
        sheet.save(path, quality=88)
        out.append(path)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_project_args(ap)
    ap.add_argument("--out", required=True, help="output prefix; writes <prefix>_1.jpg, <prefix>_2.jpg ...")
    ap.add_argument("--labels", default="before,after")
    a = ap.parse_args()
    P, edl = C.load(a)
    shots = [s for s in edl["shots"] if s.get("grade")]
    if not shots:
        raise SystemExit("no shot has a grade")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    print("\n".join(sheets(P, shots, a.out, a.labels.split(","))))


if __name__ == "__main__":
    main()
