#!/usr/bin/env python3
"""Review material for a rendered revision, consumed by workflows/review.js.

Writes into --out (default <scratch_dir>/review_Rev<N>, plus _draft and _shotsA-B for those renders, so the
reviews of a full render, a draft and a preview never share a folder; sheets of an earlier run there are
removed first):
  timeline.json          every shot's start/end/transition/text, plus title/sections/closing/endcard
  film_NN.jpg            one frame per second of the send version (at k + 0.5 s), 16 per sheet, time-stamped
  transitions_NN.jpg     pre / mid / post frames of every transition
  text_*.jpg, textsheet_N.jpg   full-width frames wherever on-screen text is shown
  tech.json              size, blackdetect, freezedetect, EBU R128 loudness / true peak / range, RMS per second
  manifest.json          the paths above plus sendLimitMb / loudness: pass its whole content as review.js args
The single frames are extracted into <out>/f/ while the sheets are built and deleted afterwards.

usage: review_prep.py --project project.json --rev 3 [--shots A-B] [--draft] [--out DIR]
  use the same --shots / --draft / --out-dir as the render; a --draft render has no send version, so its
  master stands in for both
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess

import deps  # noqa: F401
import numpy as np
from PIL import Image, ImageDraw

import common as C
from audio import ebur128
from cards import load_font
from text_layout import text_windows


def frame(src, t, path, w):
    subprocess.run([C.FFMPEG, "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", src, "-frames:v", "1",
                    "-vf", f"scale={w}:-2", "-q:v", "3", path], check=True)


def grid(items, cols, path, font, tw=480):
    ims = [(Image.open(p), lab) for p, lab in items]
    th = ims[0][0].height * tw // ims[0][0].width
    g = Image.new("RGB", (cols * tw, ((len(ims) + cols - 1) // cols) * th), "black")
    d = ImageDraw.Draw(g)
    for i, (im, lab) in enumerate(ims):
        x, y = (i % cols) * tw, (i // cols) * th
        g.paste(im.resize((tw, th)), (x, y))
        l, t, r, b = d.textbbox((0, 0), lab, font=font)
        d.rectangle([x, y, x + r - l + 12, y + b - t + 10], fill=(0, 0, 0))
        d.text((x + 6 - l, y + 5 - t), lab, font=font, fill=(255, 220, 0))
    g.save(path, quality=85)
    return path


def film_sheets(send, total, out, font):
    d = os.path.join(out, "f")
    C.run([C.FFMPEG, "-y", "-hide_banner", "-i", send, "-vf", "fps=1,scale=480:-2", "-q:v", "3",
           os.path.join(d, "s_%04d.jpg")], quiet=True)
    items = [(os.path.join(d, f"s_{k + 1:04d}.jpg"), f"{int((k + .5) // 60)}:{(k + .5) % 60:04.1f}")
             for k in range(int(total))]
    items = [x for x in items if os.path.exists(x[0])]
    return [grid(items[k:k + 16], 4, os.path.join(out, f"film_{k // 16 + 1:02d}.jpg"), font)
            for k in range(0, len(items), 16)]


def transition_sheets(send, starts, trans, out, font):
    items = []
    for k in range(1, len(starts)):
        d = trans[k - 1][1]
        for tag, t in (("pre", starts[k] - 0.6), ("mid", starts[k] + d / 2), ("post", starts[k] + d + 0.4)):
            p = os.path.join(out, "f", f"x{k:02d}_{tag}.jpg")
            frame(send, t, p, 480)
            items.append((p, f"T{k} {tag} {t:.1f}s"))
    return [grid(items[k:k + 18], 3, os.path.join(out, f"transitions_{k // 18 + 1:02d}.jpg"), font)
            for k in range(0, len(items), 18)]


def text_times(P, edl, shots, starts, durs, total, first, with_end):
    """The middle of every text window the build actually shows (same rules as build_film.py)."""
    wins, _ = text_windows(P, edl, shots, durs, starts, first)
    out = [(w["name"], (w["a"] + w["b"]) / 2) for w in wins]
    return out + ([("endcard", total - 3)] if with_end else [])


def text_sheets(send, times, out, font):
    shots = []
    for name, t in times:
        p = os.path.join(out, f"text_{name}.jpg")
        frame(send, t, p, 1920)
        shots.append((p, f"{name} {t:.1f}s"))
    sheets = [grid(shots[k:k + 4], 2, os.path.join(out, f"textsheet_{k // 4 + 1}.jpg"), font, 960)
              for k in range(0, len(shots), 4)]
    return sheets, [p for p, _ in shots]


def tech(send, master, total, lra_target):
    def ff(args):
        return subprocess.run([C.FFMPEG, "-hide_banner", "-nostats", *args, "-f", "null", "-"],
                              capture_output=True, text=True).stderr
    r = {"send_bytes": os.path.getsize(send), "master_bytes": os.path.getsize(master), "total_s": total,
         "lra_target_lu": lra_target}
    e = ff(["-i", send, "-vf", "blackdetect=d=0.15:pix_th=0.08", "-an"])
    r["black"] = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", e)
    e = ff(["-i", send, "-vf", "freezedetect=n=0.002:d=1.5", "-an"])
    r["freeze"] = list(zip(re.findall(r"freeze_start: ([\d.]+)", e), re.findall(r"freeze_end: ([\d.]+)", e)))
    r["lufs"], r["true_peak_dbfs"], r["lra_lu"] = ebur128(send)
    raw = subprocess.run([C.FFMPEG, "-v", "error", "-i", send, "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    r["rms_db_per_s"] = [round(float(20 * np.log10(np.sqrt(np.mean(x[i * 8000:(i + 1) * 8000] ** 2)) + 1e-9)), 1)
                         for i in range(len(x) // 8000)]
    return r


def timeline_json(edl, shots, durs, starts, total, with_end):
    rows = [{"n": s["n"], "start": round(starts[i], 2), "end": round(starts[i] + durs[i], 2),
             "transition_in": s["transition_in"] if i else "fade in", "file": os.path.basename(s["file"]),
             "desc": s.get("desc", ""), "caption_section": s.get("caption_section"), "tag": s.get("tag"),
             "still": bool(s.get("still")), "source_audio": bool(s.get("source_audio"))}
            for i, s in enumerate(shots)]
    if with_end:
        rows.append({"n": "endcard", "start": round(starts[-1], 2), "end": total})
    return {"total": total, "timeline": rows, **{k: edl.get(k) for k in ("title", "sections", "closing", "endcard")}}


def clear_sheets(out):
    """Sheets and frames of an earlier run in the same folder would otherwise sit next to the new ones."""
    for pattern in ("film_*.jpg", "transitions_*.jpg", "text_*.jpg", "textsheet_*.jpg"):
        for p in glob.glob(os.path.join(glob.escape(out), pattern)):
            os.remove(p)
    shutil.rmtree(os.path.join(out, "f"), ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_project_args(ap)
    ap.add_argument("--rev", required=True)
    ap.add_argument("--shots")
    ap.add_argument("--draft", action="store_true", help="review a --draft render (master only, up to 1080p)")
    ap.add_argument("--out")
    a = ap.parse_args()
    C.rev_label(a.rev)
    P, edl = C.load(a)
    P = C.draft_profile(P) if a.draft else P
    lo, hi = C.shot_range(edl, a.shots)
    shots = edl["shots"][lo:hi + 1]
    master, send = C.output_paths(P, a.rev, (shots[0]["n"], shots[-1]["n"]) if a.shots else None)
    send = master if a.draft else send
    for p in (master, send):
        if not os.path.exists(p):
            raise SystemExit(f"{p} not found: render Rev{a.rev} with build_film.py first "
                             "(same --shots / --draft / --out-dir)")
    tail = ("_draft" if a.draft else "") + (f"_shots{shots[0]['n']}-{shots[-1]['n']}" if a.shots else "")
    out = a.out or os.path.join(P["scratch_dir"], f"review_Rev{a.rev}{tail}")
    clear_sheets(out)
    os.makedirs(os.path.join(out, "f"), exist_ok=True)
    first, with_end = lo == 0, hi == len(edl["shots"]) - 1 and bool(edl.get("endcard"))
    durs, trans, starts, total = C.timeline(P, shots, [C.shot_duration(P, s) for s in shots], with_end)
    font = load_font(P["fonts"].get("latin_bold") or P["fonts"].get("latin", ""), 22)
    files = {"timeline": os.path.join(out, "timeline.json"), "tech": os.path.join(out, "tech.json")}
    with open(files["timeline"], "w", encoding="utf-8") as fh:
        json.dump(timeline_json(edl, shots, durs, starts, total, with_end), fh, ensure_ascii=False, indent=1)
    with open(files["tech"], "w", encoding="utf-8") as fh:
        json.dump(tech(send, master, total, P["loudness"]["LRA"]), fh, indent=1)
    sheets, texts = text_sheets(send, text_times(P, edl, shots, starts, durs, total, first, with_end), out, font)
    manifest = {"dir": out, "send": send, "master": master, **files,
                "filmImages": film_sheets(send, total, out, font),
                "transitionImages": transition_sheets(send, starts, trans, out, font),
                "textImages": texts, "textSheets": sheets,
                "sendLimitMb": P["send_limit_mb"], "loudness": {k: P["loudness"][k] for k in ("I", "TP")}}
    shutil.rmtree(os.path.join(out, "f"), ignore_errors=True)
    path = os.path.join(out, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    print(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"manifest: {path}  (pass its whole content as workflows/review.js args, plus brief / facts)")


if __name__ == "__main__":
    main()
