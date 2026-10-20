#!/usr/bin/env python3
"""Render a film from edl.json + project.json.

Pipeline: validate the EDL (edl_check.py) -> cut cached segments (tone-map, fit, stabilise; stills get a
pan-and-zoom move) -> PIL text cards -> xfade assembly with the music bed (one track or crossfaded parts) and
in-sync source sounds -> two-pass loudness -> master (libx264 CRF) + size-capped send version -> loudness
check of the encoded master.

usage:
  build_film.py --project project.json --rev 3                 full film (refuses to overwrite Rev3)
  build_film.py --project project.json --rev P1 --shots 4-9    preview of shots 4..9 (new label each time)
  build_film.py --project project.json --rev D1 --draft        fast draft (up to 1080p, named after its size)
  build_film.py --project project.json --cut-only              only cut segments (then measure shake / colour)
  build_film.py --project project.json --check                 only validate (EDL, sources, fonts, logos, music)
  --draft and --shots combine (the quickest preview); add --force to re-cut the selected segments,
  --out-dir / --scratch-dir to render somewhere else.
  A full render writes the master, the send version, the music credit of that revision and EDL_Rev<N>.md
  (next to the outputs with --out-dir); it refuses when the master, send version or credit already exist.
"""
import argparse
import os
import subprocess
import sys

import deps  # noqa: F401

import common as C
import music
from audio import make_audio, measure_loudness
from edl_check import text_notes, validate
from text_layout import text_windows

STAB_HANDLE_S = 1.0


def stab_handles(P, s, src):
    """Source seconds cut before / after a stabilised shot, and (first frame, frame count) of the shot itself."""
    f, speed = C.fps(P), s.get("speed") or 1.0
    pre_frames = int(min(STAB_HANDLE_S, s["in_s"]) / speed * f)
    post = max(0.0, min(STAB_HANDLE_S, C.probe_source(src)["duration"] - s["out_s"]))
    return pre_frames * speed / f, post, (pre_frames, round((s["out_s"] - s["in_s"]) / speed * f))


def cut_still(P, s, out):
    from panzoom import FROM, TO, render
    m, kb, raw = P["master"], s.get("kb") or {}, out[:-4] + "_raw.mp4"
    render(os.path.join(P["library_dir"], s["file"]), raw, s["dur_s"], tuple(kb.get("from", FROM)),
           tuple(kb.get("to", TO)), s.get("grade"), m["width"], m["height"], m["fps"],
           C.segment_codec(P, m["width"], m["height"]))
    os.replace(raw, out)


def cut_shot(P, s):
    """Cached segment of one shot; a stabilised shot is cut with handles so a real camera move keeps its speed."""
    out = C.segment_path(P, s)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if s.get("still"):
        cut_still(P, s, out)
        return out
    m, src = P["master"], os.path.join(P["library_dir"], s["file"])
    w, h = m["width"], m["height"]
    pre, post, keep = stab_handles(P, s, src) if s.get("stab") else (0.0, 0.0, None)
    vf = (f"setpts=(PTS-STARTPTS)/{s.get('speed') or 1.0},{C.picture_chain(P, s, w, h)},"
          f"fps={m['fps']},{s.get('grade') or P['default_grade']},{C.to_bt709(C.shot_matrix(P, s))}")
    raw = out[:-4] + "_raw.mp4"
    C.run([C.FFMPEG, "-y", "-hide_banner", "-ss", round(s["in_s"] - pre, 4),
           "-t", round(s["out_s"] - s["in_s"] + pre + post, 4), "-i", src, "-vf", vf, "-an",
           *C.segment_codec(P, w, h), raw])
    if keep:
        from stabilize import stabilize
        st = s["stab"]
        stabilize(raw, out, st.get("smooth_s", 1.0), st.get("max_zoom", 1.10), C.segment_codec(P, w, h), keep,
                  f"shot {s['n']}")
        os.remove(raw)
    else:
        os.replace(raw, out)
    return out


def card(P, name):
    d = os.path.join(P["scratch_dir"], "cards")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def overlays(P, sty, edl, shots, durs, starts, first):
    """[(png, start, end, fade_s)] for title, chapter captions, place tags and the closing statement."""
    import cards
    wins, notes = text_windows(P, edl, shots, durs, starts, first)
    for note in notes:
        print("warning:", note, file=sys.stderr)
    secs, by_n, ov = {x["key"]: x for x in edl.get("sections", [])}, {s["n"]: s for s in shots}, []
    for w in wins:
        if w["kind"] == "title":
            cards.make_dim(sty, card(P, "dim35.png"), 0.35)
            cards.make_title(sty, edl["title"], card(P, "title.png"))
            layers = ["dim35.png", "title.png"]
        elif w["kind"] == "closing":
            cards.make_dim(sty, card(P, "dim20.png"), 0.20)
            cards.make_closing(sty, edl["closing"], card(P, "closing.png"))
            layers = ["dim20.png", "closing.png"]
        elif w["kind"] == "caption":
            layers = [w["name"] + ".png"]
            cards.make_caption(sty, secs[w["key"]], card(P, layers[0]))
        else:
            layers = [w["name"] + ".png"]
            cards.make_tag(sty, by_n[w["shot"]]["tag"], card(P, layers[0]))
        ov += [(card(P, x), w["a"], w["b"], w["fade"]) for x in layers]
    return ov


def fade_through_black(P, prev, k, d, offset):
    """[prev] to [v<k>] through black, the same overlap as one xfade of d: the outgoing picture fades to black
    over the first half and the incoming one fades up over the second (ffmpeg's own fadeblack is lopsided, black
    after ~0.2 of d). Two dissolves via a black clip; the incoming shot loses the frames hidden under the black."""
    m, n = P["master"], round(d / 2 * C.fps(P))
    if n < 1:
        return [f"[{prev}][v{k}]xfade=transition=fade:duration={d}:offset={offset}[x{k}]"]
    h = round(n / C.fps(P), 6)
    return [f"color=c=black:s={m['width']}x{m['height']}:r={m['fps']}:d={round(d + 0.1, 3)},settb=AVTB,"
            f"{C.to_bt709()},setsar=1[k{k}]",
            f"[{prev}][k{k}]xfade=transition=fade:duration={h}:offset={offset}[h{k}]",
            f"[v{k}]trim=start_frame={n},setpts=PTS-STARTPTS[t{k}]",
            f"[h{k}][t{k}]xfade=transition=fade:duration={round(d - h, 6)}:offset={round(offset + h, 6)}[x{k}]"]


def build_filter(P, nv, trans, starts, total, ov):
    """xfade chain, text overlays, fades; the video keeps the frame count nearest to total so it ends with the audio."""
    r, t = P["master"]["fps"], P["timing"]
    f = [f"[{i}:v]fps={r},settb=AVTB,setpts=PTS-STARTPTS,{C.to_bt709()},setsar=1[v{i}]" for i in range(nv)]
    prev = "v0"
    for k in range(1, nv):
        kind, d = trans[k - 1]
        if kind in ("fadeblack", "fadein_from_black"):
            f += fade_through_black(P, prev, k, d, starts[k])
        else:
            f.append(f"[{prev}][v{k}]xfade=transition=fade:duration={d}:offset={starts[k]}[x{k}]")
        prev = f"x{k}"
    for j, (_, a, b, fd) in enumerate(ov):
        f.append(f"[{nv + j}:v]format=rgba,settb=AVTB,setpts=PTS-STARTPTS+{a}/TB,"
                 f"fade=t=in:st={a}:d={fd}:alpha=1,fade=t=out:st={round(b - fd, 3)}:d={fd}:alpha=1[ov{j}]")
        f.append(f"[{prev}][ov{j}]overlay=enable='between(t,{a},{b})':eof_action=pass[o{j}]")
        prev = f"o{j}"
    f.append(f"[{prev}]fade=t=in:st=0:d={t['fade_in']},fade=t=out:st={round(total - t['fade_out'], 3)}:"
             f"d={t['fade_out']},trim=end_frame={round(total * C.fps(P))},format=yuv420p,{C.TAG709}[vout]")
    return ";".join(f)


def encode_master(P, vids, ov, audio, fc, master):
    loop = ["-loop", "1", "-framerate", P["master"]["fps"]]
    cmd = [C.FFMPEG, "-y", "-hide_banner"]
    for p, dur in vids:
        cmd += (loop + ["-t", dur] if p.endswith(".png") else []) + ["-i", p]
    for p, a, b, _ in ov:
        cmd += loop + ["-t", round(b - a, 3), "-i", p]
    m, tmp = P["master"], master[:-4] + ".partial.mp4"
    cmd += ["-i", audio, "-filter_complex", fc, "-map", "[vout]", "-map", f"{len(vids) + len(ov)}:a",
            "-c:v", "libx264", "-preset", m.get("preset", "medium"), "-crf", m["crf"], "-maxrate", m["maxrate"],
            "-bufsize", m["bufsize"], "-profile:v", "high", "-level", m["level"], "-pix_fmt", "yuv420p",
            "-r", m["fps"], "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", tmp]
    C.run(cmd)
    os.replace(tmp, master)


def assemble(P, edl, lo, hi, paths, master, rev):
    """Master of the selected shots; returns (total seconds, music parts used)."""
    import cards
    shots = edl["shots"][lo:hi + 1]
    first, with_end = lo == 0, hi == len(edl["shots"]) - 1 and bool(edl.get("endcard"))
    sel = [C.duration(p) for p in paths]
    durs, trans, starts, total = C.timeline(P, shots, sel, with_end)
    sty = cards.style_from(P)
    ov = overlays(P, sty, edl, shots, durs, starts, first)
    vids = list(zip(paths, sel))
    if with_end:
        cards.make_endcard(sty, edl["endcard"], card(P, "endcard.png"))
        vids.append((card(P, "endcard.png"), P["timing"]["endcard_s"]))
    print(f"total {total:.2f}s; overlays {[(os.path.basename(p), a, b) for p, a, b, _ in ov]}", flush=True)
    audio, parts = make_audio(P, edl, lo, hi, (sel, durs, starts, total))
    fc = build_filter(P, len(vids), trans, starts, total, ov)
    with open(os.path.join(P["scratch_dir"], "filter_complex.txt"), "w") as fh:
        fh.write(fc.replace(";", ";\n"))
    encode_master(P, vids, ov, audio, fc, master)
    check = os.path.join(P["scratch_dir"], "check", f"Rev{rev}")
    os.makedirs(check, exist_ok=True)
    for p in [o[0] for o in ov] + ([vids[-1][0]] if with_end else []):
        cards.preview(sty, [p], os.path.join(check, f"card_{os.path.basename(p)[:-4]}.jpg"))
    return total, parts


def kbps(rate):
    r = str(rate).strip().lower()
    scale = {"k": 1.0, "m": 1000.0}.get(r[-1:])
    return float(r[:-1]) * scale if scale else float(r) / 1000


def video_kbps(path):
    r = subprocess.run([C.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=bit_rate",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip()) / 1000
    except ValueError:
        return None


def send_rates(bitrates, cap_kbps):
    """Bitrates to try in order, none above 95 % of the master's video bitrate (a send version is never bigger)."""
    out = []
    for b in bitrates:
        r = b if not cap_kbps or kbps(b) <= 0.95 * cap_kbps else f"{round(0.95 * cap_kbps)}k"
        if r not in out:
            out.append(r)
    return out


def make_send(P, master, send):
    """1080p-class H.264 High@4.1, ref=4, master's AAC copied (never re-encode audio twice)."""
    s, limit, tmp = P["send"], P["send_limit_mb"] * 1024 * 1024, send[:-4] + ".partial.mp4"
    cap = min(limit, os.path.getsize(master))
    for br in send_rates(s["bitrates"], video_kbps(master)):
        C.run([C.FFMPEG, "-y", "-hide_banner", "-i", master, "-vf", f"scale=-2:{s['height']}:flags=lanczos,{C.TAG709}",
               "-c:v", "libx264", "-preset", "slow", "-b:v", br, "-maxrate", s["maxrate"], "-bufsize", s["bufsize"],
               "-profile:v", "high", "-level", s["level"], "-x264-params", "ref=4",
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", tmp])
        size = os.path.getsize(tmp)
        if size <= cap:
            os.replace(tmp, send)
            return br
        print(f"send version {size / 2**20:.1f} MB at {br} is over {cap / 2**20:.1f} MB (send_limit_mb or the "
              "master's size), retrying lower", flush=True)
    os.remove(tmp)
    sys.exit("send version still over send_limit_mb or the master's size: append a lower bitrate to send.bitrates")


def write_credit(P, parts, path):
    """CC BY music may be credited in the post text instead of on screen: one copy-ready file per revision."""
    text = music.credit_text(P, parts)
    if text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path
    return None


def verify(paths):
    for p in paths:
        r = subprocess.run([C.FFPROBE, "-v", "error", "-show_entries",
                            "format=duration,size:stream=codec_name,width,height,r_frame_rate",
                            "-of", "compact", p], capture_output=True, text=True)
        print(os.path.basename(p), "|", r.stdout.strip().replace("\n", " | "), flush=True)


def parse():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_project_args(ap)
    ap.add_argument("--rev", help="revision label (letters, digits, . _ -); outputs are never overwritten")
    ap.add_argument("--shots", help="A-B: render only shots A..B (by shot number) as a preview")
    ap.add_argument("--draft", action="store_true",
                    help="fast draft at up to 1080p, master only, named after its real size (for iteration)")
    ap.add_argument("--force", action="store_true", help="re-cut the selected segments even if cached")
    ap.add_argument("--cut-only", action="store_true", help="only cut and cache segments")
    ap.add_argument("--check", action="store_true", help="only validate the EDL, sources, fonts and music, then stop")
    a = ap.parse_args()
    if not a.rev and not (a.cut_only or a.check):
        ap.error("--rev is required unless --cut-only or --check")
    C.rev_label(a.rev)
    return a


def cut_all(P, shots, force):
    if force:
        for p in [C.segment_path(P, s) for s in shots] + [C.source_audio_path(P, s) for s in shots]:
            if os.path.exists(p):
                os.remove(p)
    return [cut_shot(P, s) for s in shots]


def main():
    a = parse()
    P, edl = C.load(a)
    P = C.draft_profile(P) if a.draft else P
    lo, hi = C.shot_range(edl, a.shots)
    shots = edl["shots"][lo:hi + 1]
    validate(P, edl, lo, hi, a.cut_only)
    if a.check:
        for note in text_notes(P, edl, lo, hi):
            print("warning:", note, file=sys.stderr)
        print(f"EDL ok: shots {shots[0]['n']}-{shots[-1]['n']} checked")
        return
    preview = (shots[0]["n"], shots[-1]["n"]) if a.shots else None
    master, send = C.output_paths(P, a.rev or "0", preview)
    full = not (preview or a.draft or a.cut_only)
    credit = C.credit_path(P, a.rev) if full and P["music"].get("credit_template") else None
    for p in ([] if a.cut_only else [x for x in (master, send, credit) if x]):
        if os.path.exists(p):
            sys.exit(f"{p} exists: bump --rev instead of overwriting a revision")
    paths = cut_all(P, shots, a.force)
    if a.cut_only:
        listing = os.path.join(P["scratch_dir"], "segments.txt")
        with open(listing, "w", encoding="utf-8") as fh:
            fh.write("\n".join(paths) + "\n")
        print("\n".join(paths) + f"\n(segment list for this EDL: {listing})")
        return
    os.makedirs(P["output_dir"], exist_ok=True)
    _, parts = assemble(P, edl, lo, hi, paths, master, a.rev)
    measure_loudness(P, master)
    if not a.draft:
        print("send bitrate", make_send(P, master, send), flush=True)
    if full:
        from edl_to_md import write_md
        print("credit", write_credit(P, parts, credit) if credit else "(no music.credit_template)")
        print("EDL", write_md(P, edl, a.rev, C.edl_md_path(P, a.rev, bool(a.out_dir))))
    verify((master,) if a.draft else (master, send))


if __name__ == "__main__":
    main()
