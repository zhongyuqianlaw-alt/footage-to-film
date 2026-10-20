#!/usr/bin/env python3
"""Synthetic media, project.json and edl.json for selftest.sh (test sources, tones and drawn pictures only).

usage: selftest_media.py <work dir>      (FFMPEG from the environment)
"""
import argparse
import json
import os
import subprocess
import sys
import wave

import deps  # noqa: F401
import numpy as np
from PIL import Image, ImageDraw

SR = 48000
FF = os.environ.get("FFMPEG", "ffmpeg")


def wav(path, x):
    st = (np.repeat(np.clip(x, -1, 1)[:, None], 2, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(st.tobytes())


def chord(freqs, seconds):
    t = np.arange(seconds * SR) / SR
    return sum(a * np.sin(2 * np.pi * f * t) for a, f in zip((0.10, 0.07, 0.06), freqs)) * (0.6 + 0.4 * np.sin(2 * np.pi * t / 8))


def sounds(T):
    wav(f"{T}/music/theme.wav", chord((220.0, 277.18, 329.63), 60))
    wav(f"{T}/music/theme2.wav", chord((392.0, 493.88, 587.33), 60))
    t = np.arange(10 * SR) / SR
    noise = np.convolve(np.random.default_rng(7).normal(0, 0.02, len(t)), np.ones(8) / 8, "same")
    on = lambda a, b: (t >= a) & (t < b)
    wav(f"{T}/horn_audio.wav", noise + np.where(on(2.0, 5.0), 0.25 * np.sin(2 * np.pi * 300 * t)
                                                 + 0.12 * np.sin(2 * np.pi * 600 * t), 0))
    low = sum((0.15 / k) * np.sin(2 * np.pi * 70 * k * t) for k in range(1, 9))
    wav(f"{T}/horn_70hz.wav", noise + np.where(on(3.0, 6.0), low, 0))
    upper = sum(a * np.sin(2 * np.pi * 233 * k * t) for k, a in ((1, 0.05), (2, 0.25), (3, 0.08)))
    wav(f"{T}/horn_upper.wav", noise + np.where(on(3.0, 5.0), upper, 0))


def pictures(T):
    rng = np.random.default_rng(7)
    for name, col in (("logo_a", (40, 140, 90)), ("logo_b", (120, 70, 160))):
        im = Image.new("RGBA", (400, 160), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([10, 10, 390, 150], 30, fill=col + (255,))
        d.ellipse([40, 40, 120, 120], fill=(255, 255, 255, 255))
        im.save(f"{T}/{name}.png")
    tex = Image.new("RGB", (1600, 900), (90, 110, 120))
    d = ImageDraw.Draw(tex)
    for k in range(400):
        x, y, r = rng.integers(0, 1600), rng.integers(0, 900), int(rng.integers(6, 40))
        c = tuple(int(v) for v in rng.integers(0, 256, 3))
        (d.ellipse if k % 2 else d.rectangle)([x - r, y - r, x + r, y + r], fill=c)
    tex.save(f"{T}/texture.png")
    wide = Image.new("RGB", (1200, 900), (120, 120, 120))
    ImageDraw.Draw(wide).ellipse([400, 250, 800, 650], fill=(220, 30, 30))
    wide.save(f"{T}/lib/day1/photo_43.jpg", quality=92)
    tall = Image.new("RGB", (600, 900), (40, 90, 200))
    d = ImageDraw.Draw(tall)
    for y in range(0, 900, 60):
        d.rectangle([0, y, 600, y + 20], fill=(250, 250, 250))
    tall.save(f"{T}/lib/day1/photo_tall.jpg", quality=92)
    with open(f"{T}/lib/day1/not_a_picture.jpg", "w") as fh:
        fh.write("this is text, not a JPEG\n")


def clips(T):
    tex, lib = f"{T}/texture.png", f"{T}/lib/day1"
    enc = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    still = ["-loop", "1", "-framerate", "30000/1001"]
    jobs = [
        (["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=60000/1001:duration=6"], enc, f"{lib}/aerial_60p.mp4"),
        (still + ["-t", "6", "-i", tex], ["-vf", "crop=1280:720:x='160+14*sin(t*17)+6*sin(t*41)':"
                                                  "y='90+9*sin(t*13+1)+4*sin(t*37)'"] + enc, f"{lib}/handheld.mp4"),
        (still + ["-t", "6", "-i", tex], ["-vf", "rotate='0.012*sin(2*PI*3*t)+0.006*sin(2*PI*7*t)':ow=iw:oh=ih,"
                                                  "crop=1280:720"] + enc, f"{T}/roll.mp4"),
        (still + ["-t", "5", "-i", tex], ["-vf", "crop=1280:720:x='20+2*n':y=90"] + enc, f"{T}/pan.mp4"),
        (["-f", "lavfi", "-i", "color=c=0x787878:s=1280x720:rate=30000/1001:duration=4"] + still
         + ["-t", "4", "-i", tex],
         ["-filter_complex", "[1:v]crop=110:70:300:300[p];[0:v][p]overlay=x='300+12*sin(t*17)':y='200+8*sin(t*13+1)',"
                             "noise=alls=8:allf=t"] + enc, f"{T}/flat_twin.mp4"),
        (["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30000/1001:duration=10", "-i", f"{T}/horn_audio.wav",
          "-shortest"], enc + ["-c:a", "aac", "-b:a", "192k"], f"{lib}/horn.mp4"),
        (["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=24"], enc + ["-g", "90", "-keyint_min", "90",
                                                                                  "-sc_threshold", "0"],
         f"{lib}/sparse_gop.mp4"),
        (still + ["-t", "3", "-i", tex], ["-vf", "crop=1280:720:160:90"] + enc, f"{T}/plain.mp4"),
        (still + ["-t", "3", "-i", tex], ["-vf", "crop=1280:720:160:90,scale=404:720,pad=1280:720:438:0:black"] + enc,
         f"{T}/pillar.mp4"),
    ]
    for inputs, outputs, dst in jobs:
        r = subprocess.run([FF, "-v", "error", "-y", *inputs, *outputs, dst], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"could not make {dst}: {r.stderr[-300:]}")


PROJECT = {
    "name": "selftest_film", "library_dir": "lib", "output_dir": "out", "build_dir": "build", "scratch_dir": "cache",
    "fonts": {}, "logos": [{"path": "logo_a.png"}, {"path": "logo_b.png", "height": 0.8}],
    "music": {"dir": "music", "tracks": {"Example Theme": "theme.wav", "Second Theme": "theme2.wav"},
              "credit_template": "Music: \"{track}\" (example credit line)\nLicensed under an example licence"},
    "send_limit_mb": 20, "master": {"width": 960, "height": 540, "crf": 23},
    "send": {"height": 360, "bitrates": ["1500k", "800k"]},
}
EDL = {
    "concept": "Synthetic self-test film: aerial, shaky handheld, two stills, a ship horn, two music parts.",
    "title": {"lines": ["Harbor Example", "A synthetic self-test"], "date_line": "Example date line"},
    "music": {"parts": [
        {"track": "Example Theme", "end_music_t": 30.0, "to_shot": 3, "offset": 0.5, "xfade": 2.0},
        {"track": "Second Theme", "end_music_t": 50.0, "gain_db": 1.0}]},
    "sections": [{"key": "s1", "lines": ["Chapter one", "Second language line", "Third language line"]}],
    "shots": [
        {"n": 1, "file": "day1/aerial_60p.mp4", "in_s": 0.5, "out_s": 4.5, "speed": 0.5,
         "transition_in": "fadein_from_black", "transition_s": 0, "desc": "59.94p test pattern at 0.5x"},
        {"n": 2, "file": "day1/handheld.mp4", "in_s": 0.5, "out_s": 4.5, "speed": 1.0, "transition_in": "fadeblack",
         "transition_s": 1.0, "caption_section": "s1", "stab": {"smooth_s": 1.0, "max_zoom": 1.10},
         "grade_prev": "eq=contrast=1.04:saturation=1.07", "grade": "eq=contrast=1.08:saturation=1.12",
         "desc": "shaky handheld, stabilised"},
        {"n": 3, "file": "day1/photo_43.jpg", "still": True, "dur_s": 3.5,
         "kb": {"from": [0.5, 0.5, 1.0], "to": [0.5, 0.5, 1.2]}, "transition_in": "dissolve", "transition_s": 0.7,
         "tag": {"lines": ["Quay 2", "Muelle 2"], "span_s": 4.5}, "grade": "eq=saturation=1.05",
         "desc": "4:3 still, push-in; its tag runs on over the next still"},
        {"n": 4, "file": "day1/photo_tall.jpg", "still": True, "dur_s": 3.5, "transition_in": "dissolve",
         "transition_s": 0.6, "desc": "portrait still on a blurred copy of itself"},
        {"n": 5, "file": "day1/horn.mp4", "in_s": 1.0, "out_s": 9.0, "speed": 1.0, "transition_in": "dissolve",
         "transition_s": 0.6, "source_audio": True, "tag": {"lines": ["MV EXAMPLE ONE", "Berth A", "Muelle A"]},
         "desc": "horn sounds 1.0-4.0 s into the shot"},
    ],
    "closing": {"on_shot": 5, "offset": 5.0, "lines": ["MV EXAMPLE ONE alongside", "Another day at Harbor Example"]},
    "endcard": {"line1": "Harbor Example", "line2": "Example Port Operations"},
}
LEGACY = {"title": {"en": "Harbor Example", "es": "Puerto ejemplo", "date_line": "Example date line"},
          "sections": [{"key": "s1", "en": "Chapter one", "es": "Primera parte"}],
          "closing": {"on_shot": 5, "offset": 5.0, "en1": "MV EXAMPLE ONE alongside", "en2": "Second headline line",
                      "es": "Buen trabajo"}}


def configs(T):
    with open(f"{T}/project.json", "w") as fh:
        json.dump(PROJECT, fh, indent=1)
    with open(f"{T}/build/edl.json", "w") as fh:
        json.dump(EDL, fh, indent=1)
    legacy = {**EDL, **LEGACY, "shots": [dict(s) for s in EDL["shots"]]}
    legacy["shots"][2]["tag"] = {"en": "Quay 2", "es": "Muelle 2", "span_s": 4.5}
    with open(f"{T}/build/edl_legacy.json", "w") as fh:
        json.dump(legacy, fh, indent=1)
    with open(f"{T}/candidates.txt", "w") as fh:
        fh.write("# candidates\nday1/aerial_60p.mp4\nday1/handheld.mp4\nday1/horn.mp4\nday1/sparse_gop.mp4\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work_dir", help="folder to fill (created if missing)")
    T = ap.parse_args().work_dir
    for d in ("lib/day1", "music", "out", "build", "cache"):
        os.makedirs(f"{T}/{d}", exist_ok=True)
    sounds(T)
    pictures(T)
    clips(T)
    configs(T)


if __name__ == "__main__":
    main()
