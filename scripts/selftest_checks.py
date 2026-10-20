#!/usr/bin/env python3
"""Assertions for selftest.sh: `selftest_checks.py <check> <args...>`; prints a one-line summary, raises on failure."""
import glob
import json
import os
import re
import subprocess
import sys

import deps  # noqa: F401
import numpy as np

import common as C

FF, FP = C.FFMPEG, C.FFPROBE


def probe(p):
    r = subprocess.run([FP, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,"
                        "r_frame_rate,profile,level,duration", "-of", "json", p], capture_output=True, text=True)
    return json.loads(r.stdout)


def stream(j, kind):
    return next(s for s in j["streams"] if s["codec_type"] == kind)


def gray(p, t, w, h, rgb=False):
    raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", p, "-frames:v", "1", "-vf", f"scale={w}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24" if rgb else "gray", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape((h, w, 3) if rgb else (h, w)).astype(float)


def jsonl(p):
    return [json.loads(ln) for ln in open(p) if ln.strip()]


def units():
    import music
    from build_film import send_rates
    from text_layout import normalize, text_windows
    P = C.merge(C.DEFAULTS, {"output_dir": "/o", "name": "f", "music": {"credit_template": 'Music: "{track}"\nLicence'}})
    edl = {"sections": [{"key": "b", "lines": ["Two"]}], "closing": {"on_shot": 2, "offset": 2.0, "lines": ["x"]}}
    shots = [{"n": 1, "transition_in": "fadein_from_black"}, {"n": 2, "caption_section": "b", "transition_in": "cut"}]
    wins, notes = text_windows(P, edl, shots, [8.0, 9.0], [0.0, 7.97], False)
    cap, close = [[w for w in wins if w["kind"] == k][0] for k in ("caption", "closing")]
    assert close["a"] >= cap["b"] and notes, ("closing must wait for the caption", cap, close, notes)
    shots = [{"n": 1, "transition_in": "cut", "tag": {"lines": ["Place"], "span_s": 4.2}}, {"n": 2, "transition_in": "cut"}]
    wins, _ = text_windows(P, {}, shots, [3.0, 4.0], [0.0, 2.97], False)
    assert len(wins) == 1 and abs(wins[0]["b"] - wins[0]["a"] - 4.2) < 1e-6 and wins[0]["b"] > 3.0, wins
    shots = [{"n": 1, "transition_in": "cut"}, {"n": 2, "caption_section": "b", "transition_in": "cut"}]
    wins, notes = text_windows(P, {**edl, "title": {"lines": ["T"]}, "closing": None}, shots, [6.0, 9.0], [0.0, 5.97],
                               True)
    title, cap = [[w for w in wins if w["kind"] == k][0] for k in ("title", "caption")]
    assert cap["a"] >= title["b"] + 0.3 and notes, ("a chapter caption must wait for the title", title, cap, notes)
    e = normalize({"title": {"de": "A", "en": "B", "es": "C", "date_line": "D"},
                   "sections": [{"key": "k", "en": "x", "es": "y"}],
                   "closing": {"on_shot": 1, "en": "c", "es": "d", "en1": "a", "en2": "b"},
                   "shots": [{"n": 1, "tag": {"en": "u", "span_s": 4.0}}]})
    assert e["title"]["lines"] == ["A", "B", "C"] and e["sections"][0]["lines"] == ["x", "y"], e
    assert e["closing"]["lines"] == ["a", "b", "c", "d"] and e["closing"]["rule_after"] == 2, e["closing"]
    assert e["shots"][0]["tag"]["lines"] == ["u"], e["shots"]
    assert send_rates(["16M", "13M", "10M"], 9000) == ["8550k"], send_rates(["16M", "13M", "10M"], 9000)
    assert send_rates(["16M", "10M"], 40000) == ["16M", "10M"] and send_rates(["16M"], None) == ["16M"]
    assert C.credit_path(P, "4") == "/o/music_credit_Rev4.txt", C.credit_path(P, "4")
    assert C.credit_path({**P, "credit_file_name": "c.txt"}, 4) == "/o/c_Rev4.txt"
    assert os.path.basename(C.output_paths(C.draft_profile(P), "D1")[0]) == "f_draft1080p_RevD1.mp4"
    names = [os.path.basename(p) for p in C.output_paths(C.merge(P, {"master": {"height": 720}}), "2")]
    assert names == ["f_master720p_Rev2.mp4", "f_send1080p_Rev2.mp4"], names
    parts = [{"credit": "A"}, {"credit": "B"}]
    assert music.credit_text(P, parts) == 'Music: "A"\nMusic: "B"\nLicence\n', music.credit_text(P, parts)
    assert music.credit_text(P, parts[:1]) == 'Music: "A"\nLicence\n'
    return ("text windows (caption vs closing and title, tag span_s), legacy text keys, send bitrate cap, credit "
            "names, output names")


def text_breaks():
    """Text near a fade through black / chapter break: a late caption is shortened (or left out), never spills, and
    a tag running across the break is reported."""
    from text_layout import text_windows
    P = C.DEFAULTS
    edl = {"sections": [{"key": "b", "lines": ["Two"]}]}
    shots = [{"n": 1, "transition_in": "cut", "tag": {"lines": ["Place"], "span_s": 6.0}},
             {"n": 2, "transition_in": "fadeblack", "caption_section": "b"}]
    wins, notes = text_windows(P, edl, shots, [6.0, 5.0, 6.0], [0.0, 5.0, 8.8], False)
    cap = [w for w in wins if w["kind"] == "caption"][0]
    assert cap["a"] > 6.9 and cap["b"] <= 8.8 and any("shortened" in n for n in notes), (wins, notes)
    assert any(n.startswith("shot 1:") and "fade through black into the next chapter (shot 2)" in n
               for n in notes), notes
    shots[0]["tag"]["span_s"] = 8.0
    wins, notes = text_windows(P, edl, shots, [6.0, 5.0, 6.0], [0.0, 5.0, 8.8], False)
    assert not [w for w in wins if w["kind"] == "caption"] and any("left out" in n for n in notes), (wins, notes)
    shots = [{"n": 1, "transition_in": "cut", "tag": {"lines": ["Place"], "span_s": 5.0}},
             {"n": 2, "transition_in": "dissolve"}, {"n": 3, "transition_in": "fadeblack"}]
    wins, notes = text_windows(P, {}, shots, [4.0, 3.0, 4.0], [0.0, 3.5, 5.5], False)
    assert len(wins) == 1 and any("the fade through black (shot 3)" in n for n in notes), (wins, notes)
    return "late caption shortened or left out before the break; text across a fade / chapter reported"


def fade_black():
    """A fade through black between two white clips: as long as one xfade, darkest in the middle, both halves equal."""
    from build_film import build_filter
    P = C.merge(C.DEFAULTS, {"master": {"width": 160, "height": 90}})
    src = ["-f", "lavfi", "-i", f"color=c=white:s=160x90:r={P['master']['fps']}:d=4"]
    fc = build_filter(P, 2, [("fadeblack", 1.0)], [0.0, 3.0], 7.0, [])
    raw = subprocess.run([FF, "-v", "error", *src, *src, "-filter_complex", fc, "-map", "[vout]", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    y, f = np.frombuffer(raw, np.uint8).reshape(-1, 90 * 160).mean(1), C.fps(P)
    at = lambda t: y[round(t * f)]
    darkest = (90 + int(np.argmin(y[90:120]))) / f
    assert len(y) == round(7.0 * f), (len(y), round(7.0 * f))
    assert abs(darkest - 3.5) <= 1 / f and abs(at(3.25) - at(3.75)) < 25 and 90 < at(3.25) < 165, \
        (darkest, at(3.25), at(3.75))
    return f"darkest at {darkest:.2f} s, quarter points {at(3.25):.0f} / {at(3.75):.0f} of 255, {len(y)} frames"


def sheets(index):
    rows = {os.path.basename(r["file"]): r for r in json.load(open(index))}
    assert len(rows) == 4 and all(r["sheets"] and all(map(os.path.exists, r["sheets"])) for r in rows.values()), rows
    sparse = rows["sparse_gop.mp4"]["tiles"]
    assert sparse >= 11, f"sparse-keyframe clip got {sparse} tiles; accurate seek should give 12"
    return f"4 clips logged; sparse-keyframe clip {sparse} tiles"


def tonal(path):
    a, b, c = [r["events"] for r in jsonl(path)]
    assert len(a) == 1 and 1.6 <= a[0]["start_s"] <= 2.4 and 4.6 <= a[0]["end_s"] <= 5.4, a
    assert abs(a[0]["f0_hz"] - 300) < 4 and abs(a[0]["peak_hz"] - 300) < 8, a
    assert len(b) == 1 and 2.6 <= b[0]["start_s"] <= 3.4 and 5.6 <= b[0]["end_s"] <= 6.4 and abs(b[0]["f0_hz"] - 70) < 3, b
    assert len(c) == 1 and abs(c[0]["peak_hz"] - 466) < 8 and abs(c[0]["f0_hz"] - 233) < 4, c
    return f"f0 {a[0]['f0_hz']} / {b[0]['f0_hz']} / {c[0]['f0_hz']} Hz (loudest partial of the last at {c[0]['peak_hz']} Hz)"


def shake(path):
    a, b = jsonl(path)
    assert a["hf"]["jit_px"] > 3 * max(b["hf"]["jit_px"], 0.05), (a, b)
    return f"handheld {a['hf']['jit_px']} px vs tripod-like {b['hf']['jit_px']} px"


def stab(log):
    r = json.loads(open(log).read().split("stabilized", 1)[1])
    b, a = r["before"]["hf"]["jit_px"], r["after"]["hf"]["jit_px"]
    assert a < 0.25 * b, (b, a)
    return f"hf jitter {b} -> {a} px"


def held(log, err):
    """max_zoom too small: corrections shortened (not clipped per axis) and a warning naming the zoom needed."""
    r = json.loads(open(log).read().split("stabilized", 1)[1])
    text = open(err).read()
    assert r["zoom"] <= 1.01 and r["reduced_frames"] > 0 and r["zoom_needed"] > 1.01, r
    assert "would correct it fully" in text, text
    return f"zoom {r['zoom']} of {r['zoom_needed']} needed, {r['reduced_frames']} frames held back, warned"


def roll(log, subj):
    r = json.loads(open(log).read().split("stabilized", 1)[1])
    b, a = r["before"]["hf"]["jit_rot_deg"], r["after"]["hf"]["jit_rot_deg"]
    assert a < 0.3 * b, ("rotation jitter", b, a)
    s = [x["subject_hf_px"] for x in jsonl(subj)]
    assert s[1] <= s[0] + 0.3 and s[3] <= s[2] + 0.3, ("subject jitter rose after stabilising a pure roll", s)
    return f"roll {b} -> {a} deg"


def flat_twin(flat, textured):
    """A twin template on a flat, noisy patch is ignored instead of flagging a well-tracked subject; on texture
    it is kept."""
    r, rows = jsonl(flat)[0], jsonl(textured)
    assert r["twin_gap_px"] is None and not r["low_confidence"] and r["subject_hf_px"] > 1.0, r
    assert all(x["twin_gap_px"] is not None for x in rows), rows
    return f"twin on a flat patch (detail {r['twin_detail']}) ignored, subject {r['subject_hf_px']} px not flagged"


def pan(*paths):
    from shake_meter import trajectory
    for p in paths:
        dx = np.diff(trajectory(p)[:, 0])
        ends, mid = np.r_[dx[:8], dx[-8:]], np.median(dx)
        assert np.abs(ends - mid).max() < 0.1 * abs(mid), (p, np.round(dx[:8], 2), np.round(dx[-8:], 2), mid)
    return f"steady pan keeps {mid:.2f} px/frame to both ends"


def rotated(src, out):
    a, b = gray(src, 2, 180, 320), gray(out, 2, 180, 320)
    assert np.abs(a - b).mean() < 12, np.abs(a - b).mean()
    return "rotated phone clip stabilised upright"


def has_audio(path):
    assert any(s["codec_type"] == "audio" for s in probe(path)["streams"]), "stabilize dropped the audio"
    return "standalone stabilize keeps the audio"


def color(seg_json, pillar_json):
    r = json.load(open(seg_json))
    assert len(r) == 5 and all(v and 0 < v["L_mean"] < 100 and v["shot"] in range(1, 6) for v in r.values()), r
    plain, pillar = [v for _, v in sorted(json.load(open(pillar_json)).items())]
    assert abs(plain["L_mean"] - pillar["L_mean"]) < 4 and "crushed" not in pillar["flags"], (plain, pillar)
    return f"5 segments; pillarboxed copy reads L {pillar['L_mean']} vs {plain['L_mean']} without bars"


def render(M, S, T):
    m, s = probe(M), probe(S)
    dur = float(m["format"]["duration"])
    assert abs(dur - 28.93) < 0.25, f"master duration {dur}, expected ~28.93"
    vd, ad = (float(stream(m, k)["duration"]) for k in ("video", "audio"))
    assert abs(vd - ad) < 1001 / 30000, f"video {vd} s vs audio {ad} s: more than one frame apart"
    assert (stream(m, "video")["width"], stream(m, "video")["height"]) == (960, 540)
    assert stream(s, "video")["height"] == 360 and stream(s, "video")["level"] == 41, stream(s, "video")
    assert stream(m, "video")["r_frame_rate"] == "30000/1001" and stream(s, "audio")["codec_name"] == "aac"
    md5 = lambda p: subprocess.run([FF, "-v", "error", "-i", p, "-map", "0:a", "-c", "copy", "-f", "md5", "-"],
                                   capture_output=True, text=True).stdout
    assert md5(M) == md5(S), "send version must copy the master's AAC packets"
    assert os.path.getsize(S) <= min(20 * 2**20, os.path.getsize(M)), "send version over the cap or the master"
    for p in (M, S):
        tags = subprocess.run([FP, "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=color_space,color_transfer,color_primaries,color_range", "-of", "csv=p=0", p],
                              capture_output=True, text=True).stdout.split()
        assert sorted(tags[0].split(",")) == ["bt709", "bt709", "bt709", "tv"], (p, tags)
    credit = open(f"{T}/out/music_credit_Rev1.txt").read().splitlines()
    assert credit == ['Music: "Example Theme" (example credit line)', 'Music: "Second Theme" (example credit line)',
                      "Licensed under an example licence"], credit
    md = open(f"{T}/build/EDL_Rev1.md").read()
    assert "| 5 |" in md and "pan-and-zoom still" in md and "Example Theme + Second Theme" in md, md[:400]
    cards = os.listdir(f"{T}/cache/check/Rev1")
    for c in ("card_title.jpg", "card_cap_s1.jpg", "card_tag_03.jpg", "card_tag_05.jpg", "card_closing.jpg",
              "card_endcard.jpg"):
        assert c in cards, (c, cards)
    return f"duration {dur:.2f}s, send {os.path.getsize(S) / 2**20:.1f} MB, BT.709 tags, credit lists both parts"


def _red_box(frame):
    r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
    ys, xs = np.nonzero((r > 150) & (g < 90) & (b < 90))
    return xs.max() - xs.min() + 1, ys.max() - ys.min() + 1


def stills(segdir):
    wide, tall = (glob.glob(f"{segdir}/shot_0{n}_*.mp4") for n in (3, 4))
    wide, tall = [p for p in wide if "_raw" not in p][0], [p for p in tall if "_raw" not in p][0]
    d = C.duration(wide)
    (w0, h0), (w1, h1) = _red_box(gray(wide, 0, 960, 540, True)), _red_box(gray(wide, d - 0.05, 960, 540, True))
    assert abs(w0 / h0 - 1) < 0.04 and abs(w1 / h1 - 1) < 0.04, ("circle squashed", w0, h0, w1, h1)
    assert abs(w0 - 320) < 8 and abs(w1 / w0 - 1.2) < 0.04, ("cover crop or push-in wrong", w0, w1)
    f = gray(tall, 1, 960, 540)
    side, centre = f[:, 20:200], f[:, 400:560]
    assert 5 < side.mean() < 0.7 * centre.mean() and side.std() < 0.5 * centre.std(), (side.mean(), side.std(),
                                                                                     centre.mean(), centre.std())
    return f"4:3 still cover-cropped, circle {w0}x{h0} -> {w1}x{h1} px; portrait on a blurred, darker copy"


def music(M):
    def level(t, hz):
        x = subprocess.run([FF, "-v", "error", "-ss", str(t), "-t", "2", "-i", M, "-ac", "1", "-ar", "8000",
                            "-f", "f32le", "-"], capture_output=True).stdout
        spec = np.abs(np.fft.rfft(np.frombuffer(x, np.float32) * np.hanning(len(x) // 4)))
        f = np.fft.rfftfreq(len(x) // 4, 1 / 8000)
        return spec[np.abs(f - hz) < 4].max()
    before, after = (level(t, 220) / level(t, 392) for t in (3.5, 13.2))
    assert before > 4 and after < 0.25, (before, after)
    return f"220/392 Hz ratio {before:.1f} before the switch, {after:.2f} after"


def source_peak(M, log):
    """The horn is not lifted by the loudness gain: planned at ~-9 dBFS; with the ducked music on top the
    window peaks a few dB higher, while lifting it by the gain (+7 dB here) would put it near the ceiling."""
    text = open(log).read()
    a = float(re.search(r"source sounds \[\(([\d.]+), [\d.]+\)\]", text).group(1))
    planned = float(re.search(r"source sounds peak at about ([-\d.]+) dBFS", text).group(1))
    err = subprocess.run([FF, "-hide_banner", "-nostats", "-ss", str(a + 1.3), "-t", "2.4", "-i", M, "-vn",
                          "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    horn = float(re.search(r"max_volume: ([-\d.]+) dB", err).group(1))
    assert abs(planned + 9) <= 1.0 and -12.0 <= horn <= -5.0, ("source sound level after the gain", planned, horn)
    return f"horn planned at {planned} dBFS, horn + ducked music peak {horn} dBFS"


def preview(p):
    dur = float(probe(p)["format"]["duration"])
    assert abs(dur - 6.81) < 0.2, dur
    return f"legacy-key EDL preview, {dur:.2f} s"


def review(manifest):
    m = json.load(open(manifest))
    for k in ("filmImages", "transitionImages", "textImages", "textSheets"):
        assert m[k] and all(os.path.exists(p) for p in m[k]), k
    assert m["sendLimitMb"] == 20 and m["loudness"]["I"] == -15, m
    tech = json.load(open(m["tech"]))
    assert tech["lufs"] is not None and tech["true_peak_dbfs"] <= -1.5, tech
    names = [os.path.basename(p) for p in m["textImages"]]
    for n in ("text_title.jpg", "text_cap_s1.jpg", "text_tag_03.jpg", "text_closing.jpg", "text_endcard.jpg"):
        assert n in names, (n, names)
    assert not os.path.exists(os.path.join(m["dir"], "f")), "the single frames folder f/ should be removed"
    assert tech["lra_lu"] is not None and tech["lra_target_lu"] == 11, tech
    return "manifest, sheets and tech metrics (incl. loudness range); intermediate frames removed"


CHECKS = {f.__name__: f for f in (units, text_breaks, fade_black, sheets, tonal, shake, stab, held, roll, flat_twin,
                                  pan, rotated, has_audio, color, render, stills, music, source_peak, preview, review)}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CHECKS:
        print(f"usage: selftest_checks.py <check> <args...>   (called by selftest.sh)\nchecks: {', '.join(CHECKS)}",
              file=sys.stderr)
        sys.exit(2)
    print(CHECKS[sys.argv[1]](*sys.argv[2:]))
