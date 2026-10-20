"""Music bed on the whole film's timeline: one track, or several parts joined by equal-power crossfades.

EDL forms (both resolve to a list of parts):
  {"track": T, "start_s" | "end_at" | "align": {"shot": n, "music_t": s}}            one track
  {"parts": [{"track": T | "file": F, "start_s" | "end_music_t" | "align", "to_shot": n, "offset": s,
              "xfade": 3.0, "gain_db": 0, "credit": "..."}, ...]}                   several tracks
A part plays until the film time "to_shot start + offset", where the next part takes over through a
crossfade of xfade seconds centred on that point; the last part runs to the end of the film. end_music_t
(end_at for one track) is the track second where the part ends, so a part can end on a chosen musical
moment; align puts music_t on a shot's start.
"""
import os
import sys

import common as C

XFADE, FADE_IN, FADE_OUT = 3.0, 2.0, 3.0
STARTS = ("start_s", "end_music_t", "end_at", "align")


def parts_of(m):
    if m.get("parts"):
        return [dict(p) for p in m["parts"]]
    return [{k: m[k] for k in ("track", "file", "gain_db", "credit", *STARTS) if k in m}]


def part_name(m, k):
    return f"music.parts[{k}]" if m.get("parts") else "music"


def path_of(P, p):
    """file (relative to music.dir, or absolute) or the file project music.tracks names for track."""
    rel = p.get("file") or P["music"]["tracks"].get(p.get("track"), p.get("track") or "")
    return os.path.join(P["music"]["dir"], os.path.expanduser(rel))


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def field_errors(m, nums):
    """Shape errors of the music block (types, switch shots, start choice), before any timing is known."""
    if not isinstance(m, dict) or not (m.get("track") or m.get("file") or m.get("parts")):
        return ['music needs "track" (a key of project music.tracks) or "parts": [...]']
    parts, errs = parts_of(m), []
    for k, p in enumerate(parts):
        name, last = part_name(m, k), k == len(parts) - 1
        if not (p.get("track") or p.get("file")):
            errs.append(f"{name}: needs track or file")
        errs += [f"{name}.{f} must be a number" for f in ("start_s", "end_music_t", "end_at", "offset", "gain_db")
                 if p.get(f) is not None and not _num(p[f])]
        if p.get("xfade") is not None and not (_num(p["xfade"]) and p["xfade"] >= 0):
            errs.append(f"{name}.xfade must be a number of seconds >= 0")
        if sum(p.get(f) is not None for f in STARTS) > 1:
            errs.append(f"{name}: give only one of {', '.join(STARTS)}")
        al = p.get("align")
        if al is not None and not (isinstance(al, dict) and al.get("shot") in nums and _num(al.get("music_t"))):
            errs.append(f'{name}.align must be {{"shot": <a shot number of the EDL>, "music_t": <seconds>}}, got {al}')
        if not last and p.get("to_shot") not in nums:
            errs.append(f"{name}.to_shot must be the shot number where the next part takes over, got {p.get('to_shot')}")
    return errs


def _start(p, L, t0, x_in, starts, nums, total):
    """Track second of the part's first sample (the part plays track seconds start .. start + L)."""
    if p.get("start_s") is not None:
        return float(p["start_s"])
    end = p.get("end_music_t") if p.get("end_music_t") is not None else p.get("end_at")
    if end is not None:
        return end - L
    if p.get("align"):
        return p["align"]["music_t"] - starts[nums.index(p["align"]["shot"])] + t0 - x_in
    return 0.0


def plan(P, edl, durs):
    """Parts resolved on the whole film: film window t0..t1, track seconds a..a+L, crossfade x into the next."""
    shots, m = edl["shots"], edl["music"]
    _, _, starts, total = C.timeline(P, shots, durs, bool(edl.get("endcard")))
    nums, parts, out, t0 = [s["n"] for s in shots], parts_of(m), [], 0.0
    for k, p in enumerate(parts):
        last = k == len(parts) - 1
        t1 = total if last else round(starts[nums.index(p["to_shot"])] + p.get("offset", 0), 3)
        x_in = out[-1]["x"] / 2 if k else 0.0
        x = 0.0 if last else float(p.get("xfade", XFADE))
        L = round(t1 - t0 + x_in + x / 2, 3)
        a = round(_start(p, L, t0, x_in, starts, nums, total), 3)
        out.append({"name": part_name(m, k), "path": path_of(P, p), "t0": t0, "t1": t1, "a": a, "L": L, "x": x,
                    "gain_db": p.get("gain_db", 0), "credit": p.get("credit") or p.get("track")
                    or os.path.splitext(os.path.basename(p.get("file", "")))[0]})
        t0 = t1
    return out, total


def check(P, edl, durs):
    """Timing errors of the music on the whole film: switch order, crossfade lengths, track lengths."""
    parts, total = plan(P, edl, durs)
    errs = []
    for k, p in enumerate(parts):
        if p["t1"] <= p["t0"] or p["t1"] > total:
            errs.append(f"{p['name']}: switches at {p['t1']:.2f} s, which is not after the previous switch "
                        f"({p['t0']:.2f} s) and inside the film ({total:.2f} s)")
            continue
        if k and min(p["L"], parts[k - 1]["L"]) <= parts[k - 1]["x"]:
            errs.append(f"{p['name']}: the {parts[k - 1]['x']} s crossfade is longer than the parts it joins")
        if not os.path.isfile(p["path"]):
            errs.append(f"{p['name']}: music file not found: {p['path']} (name it in project.json music.tracks)")
            continue
        over = p["a"] + p["L"] - C.duration(p["path"])
        if p["a"] < 0:
            errs.append(f"{p['name']}: would start {-p['a']:.2f} s before the beginning of the track: raise "
                        "end_music_t / end_at / align.music_t or lower start_s")
        elif over > 0.01:
            errs.append(f"{p['name']}: runs {over:.2f} s past the end of the track: lower end_music_t / end_at / "
                        "start_s or pick a longer track")
    return errs


def describe(parts):
    return "; ".join(f"{p['credit']}: track {p['a']:.1f}-{p['a'] + p['L']:.1f} s -> film {p['t0']:.1f}-{p['t1']:.1f} s"
                     + (f", {p['x']:g} s crossfade" if p["x"] else "") for p in parts)


def bed(parts, w0, w1):
    """(ffmpeg inputs, filter chain ending in [bed]): the music of film seconds w0..w1 with fades at both ends."""
    inputs, f = [], []
    for k, p in enumerate(parts):
        inputs += ["-i", p["path"]]
        f.append(f"[{k}:a]atrim={p['a']}:{round(p['a'] + p['L'], 3)},asetpts=PTS-STARTPTS,aresample=48000,"
                 f"aformat=channel_layouts=stereo,volume={p['gain_db']}dB[m{k}]")
    prev = "m0"
    for k in range(1, len(parts)):
        f.append(f"[{prev}][m{k}]acrossfade=d={parts[k - 1]['x']}:c1=qsin:c2=qsin[mx{k}]")
        prev = f"mx{k}"
    d = round(w1 - w0, 3)
    f.append(f"[{prev}]apad,atrim={w0}:{w1},asetpts=PTS-STARTPTS,afade=t=in:st=0:d={FADE_IN},"
             f"afade=t=out:st={round(d - FADE_OUT, 3)}:d={FADE_OUT}[bed]")
    return inputs, f


def credit_text(P, parts):
    """The credit template once per track; lines every track shares (licence, link) are written once, at the end."""
    tpl = P["music"].get("credit_template")
    names = list(dict.fromkeys(p["credit"] for p in parts if p["credit"]))
    if not tpl or not names:
        return None
    rendered = [tpl.replace("{track}", n).rstrip("\n").splitlines() for n in names]
    common = [ln for ln in rendered[0] if all(ln in r for r in rendered)] if len(rendered) > 1 else []
    own = [ln for r in rendered for ln in r if ln not in common]
    return "\n".join(own + common) + "\n"


if __name__ == "__main__":
    print("music.py is a library module used by build_film.py, not a command: run build_film.py --help")
    sys.exit(2)
