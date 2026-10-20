"""Every EDL mistake that can be found before anything is cut: field types and ranges, text blocks, fonts,
logos, sources, stills, slow-motion rule, grades, transitions and music. build_film.py calls validate() first."""
import os
import subprocess
import sys

import common as C
import music
from text_layout import lines_of, text_windows

KINDS = ("fadein_from_black", "cut", "dissolve", "fadeblack")
FITS = ("crop", "blur", "pillarbox")
STILL_S, STILL_RUN, MAX_UPSCALE = (3.0, 4.5), 3, 1.5


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def number_errors(s):
    """Types and ranges of a shot's numeric fields (a bad value must not crash a later check)."""
    n, still, errs = s.get("n"), s.get("still"), []
    if not isinstance(n, int) or isinstance(n, bool):
        errs.append(f"shot {n}: n must be an integer")
    for k in ("dur_s",) if still else ("in_s", "out_s"):
        if k not in s:
            errs.append(f"shot {n}: missing {k}")
        elif not num(s[k]) or s[k] < 0 or (k == "dur_s" and s[k] <= 0):
            errs.append(f"shot {n}: {k} must be a number of seconds {'> 0' if k == 'dur_s' else '>= 0'}, got {s[k]!r}")
    if not still and num(s.get("in_s")) and num(s.get("out_s")) and s["out_s"] <= s["in_s"]:
        errs.append(f"shot {n}: out_s must be greater than in_s")
    if s.get("speed") is not None and not (num(s["speed"]) and s["speed"] > 0):
        errs.append(f"shot {n}: speed must be a number > 0 (0.5 or 1.0), got {s['speed']!r}")
    if s.get("transition_s") is not None and not (num(s["transition_s"]) and s["transition_s"] >= 0):
        errs.append(f"shot {n}: transition_s must be a number of seconds >= 0, got {s['transition_s']!r}")
    st = s.get("stab")
    if st and not (isinstance(st, dict) and num(st.get("smooth_s", 1.0)) and st.get("smooth_s", 1.0) >= 0.1
                   and num(st.get("max_zoom", 1.1)) and st.get("max_zoom", 1.1) >= 1.0):
        errs.append(f'shot {n}: stab must be {{"smooth_s": >= 0.1, "max_zoom": >= 1.0}}, got {st!r}')
    span = (s.get("tag") or {}).get("span_s") if isinstance(s.get("tag"), dict) else None
    if span is not None and not (num(span) and span > 0):
        errs.append(f"shot {n}: tag.span_s must be a number of seconds > 0, got {span!r}")
    return errs


def shot_errors(s, first, secs):
    n, errs = s.get("n"), number_errors(s)
    if not s.get("file"):
        errs.append(f"shot {n}: missing file")
    kind = s.get("transition_in")
    if kind not in KINDS:
        errs.append(f"shot {n}: transition_in must be one of {', '.join(KINDS)}")
    elif not first and kind != "cut" and not num(s.get("transition_s")):
        errs.append(f"shot {n}: transition_s (seconds) is required for a {kind}")
    cs = s.get("caption_section")
    if cs and (not isinstance(cs, str) or cs not in secs):
        errs.append(f"shot {n}: caption_section {cs!r} is not a key in sections")
    if s.get("source_audio") and (s.get("speed") or 1.0) != 1.0:
        errs.append(f"shot {n}: source_audio needs speed 1.0 to stay in sync")
    if not s.get("still") and s.get("fit", "crop") not in FITS:
        errs.append(f"shot {n}: fit must be one of {', '.join(FITS)}")
    return errs


def check_text(edl):
    """Every text block has at least one line ("lines", or the one-key-per-language form of older EDLs)."""
    blocks = [("title", edl.get("title")), ("closing", edl.get("closing")), ("endcard", edl.get("endcard"))]
    blocks += [(f"section {x.get('key')}", x) for x in edl.get("sections", [])]
    blocks += [(f"shot {s.get('n')} tag", s["tag"]) for s in edl["shots"] if s.get("tag")]
    return [f'{name}: no text found; write it as "lines": ["first line", ...]' for name, b in blocks
            if b and not (isinstance(b, (dict, list)) and lines_of(b))]


def check_fields(edl):
    errs, shots, nums = [], edl["shots"], [s.get("n") for s in edl["shots"]]
    if len(set(map(str, nums))) != len(nums):
        errs.append(f"shot numbers n must be unique: {nums}")
    secs = {x.get("key") for x in edl.get("sections", []) if isinstance(x, dict)}
    for i, s in enumerate(shots):
        errs += shot_errors(s, i == 0, secs)
    c = edl.get("closing")
    if c and c.get("on_shot") not in nums:
        errs.append(f"closing.on_shot {c.get('on_shot')} is not a shot number")
    if c and c.get("offset") is not None and not num(c["offset"]):
        errs.append(f"closing.offset must be a number of seconds, got {c['offset']!r}")
    return errs + music.field_errors(edl.get("music"), nums) + check_text(edl)


def check_fonts(P, edl, shots):
    """Text the configured fonts cannot draw would come out as boxes: stop before cutting."""
    import cards
    blocks = [edl.get("title"), edl.get("closing"), edl.get("endcard"), *edl.get("sections", []),
              *[s.get("tag") for s in shots]]
    texts = [x for b in blocks for x in lines_of(b)] + [(edl.get("title") or {}).get("date_line") or ""]
    return [f'"{t}": the configured fonts have no glyph for {m} (drawn as boxes); set fonts.cjk, fonts.latin and '
            "fonts.latin_bold in project.json to fonts that cover it" for t, m in cards.missing_glyphs(P, texts)]


def check_assets(P):
    """Logo and font files named in project.json: a missing one would otherwise stop the render after the cut."""
    from PIL import Image
    errs = []
    for logo in P["logos"]:
        try:
            with Image.open(logo["path"]) as im:
                im.verify()
        except Exception as e:
            errs.append(f"logos: cannot read {logo['path']} ({type(e).__name__}); fix the path or remove the entry "
                        "from project.json logos")
    errs += [f'fonts.{k}: font file not found: {v}; fix the path, or set it to "" for Pillow\'s default font'
             for k, v in P["fonts"].items() if isinstance(v, str) and v and not os.path.isfile(v)]
    return errs


def text_notes(P, edl, lo, hi):
    """Notes on on-screen text that is left out or moved, from estimated shot lengths (the build repeats them)."""
    shots = edl["shots"][lo:hi + 1]
    with_end = hi == len(edl["shots"]) - 1 and bool(edl.get("endcard"))
    durs, _, starts, _ = C.timeline(P, shots, [C.est_duration(P, s) for s in shots], with_end)
    return text_windows(P, edl, shots, durs, starts, lo == 0)[1]


def speed_problem(info, speed, P):
    """Frames judder unless source fps x speed is a whole multiple of the master fps."""
    master, src = P["master"]["fps"], info["r_frame_rate"]
    ratio = info["fps"] * speed / C.fps(P)
    if speed not in (0.5, 1.0):
        return f"speed {speed}: only 0.5 or 1.0 play evenly (0.5 on a source at twice the master rate)"
    if round(ratio) >= 1 and abs(ratio - round(ratio)) <= 0.01:
        return None
    if speed == 0.5:
        return (f"speed 0.5 on a {src} source gives {info['fps'] * speed:.3g} fps in a {master} master and judders; "
                "slow down only sources at twice the master rate, otherwise use 1.0")
    return (f"a {src} source in a {master} master repeats or drops frames unevenly (judder); set master.fps to "
            "match the footage (e.g. 25 for a 25/50p library) or replace the shot")


def check_source(P, s):
    """Problems a video shot's source causes: (errors, warnings)."""
    n, speed = s["n"], s.get("speed") or 1.0
    try:
        info = C.probe_source(os.path.join(P["library_dir"], s["file"]))
    except C.ProbeError as e:
        return [f"shot {n}: {e}"], []
    errs, warns = [], []
    if s.get("source_audio") and not info["has_audio"]:
        errs.append(f"shot {n}: source_audio is set but {s['file']} has no audio stream")
    if info["duration"] and s["out_s"] > info["duration"] + 0.05:
        errs.append(f"shot {n}: out_s {s['out_s']} is past the end of the clip ({info['duration']:.2f} s)")
    if info["hdr"] and s.get("tonemap", True) and not C.has_filter("zscale"):
        errs.append(f"shot {n}: HDR source ({info['transfer']}) needs tone mapping but this ffmpeg has no zscale "
                    "(libzimg): export an SDR Rec.709 version, use an ffmpeg built with zimg, or set "
                    "\"tonemap\": false to use it as is")
    problem = speed_problem(info, speed, P)
    if problem and s.get("allow_speed"):
        warns.append(f"shot {n}: {problem} (accepted: allow_speed)")
    elif problem:
        errs.append(f"shot {n}: {problem}; to keep it anyway set \"allow_speed\": true on the shot")
    if info["vertical"] and s.get("fit", "crop") == "crop":
        warns.append(f"shot {n}: vertical source is cropped to a centre strip; set \"fit\": \"blur\" or "
                     "\"pillarbox\", or replace the shot")
    return errs, warns


def kb_errors(s):
    kb, errs = s.get("kb") or {}, []
    if not isinstance(kb, dict):
        return [f"shot {s['n']}: kb must be {{\"from\": [cx, cy, zoom], \"to\": [cx, cy, zoom]}}"]
    for k in ("from", "to"):
        v = kb.get(k)
        ok = v is None or (isinstance(v, list) and len(v) == 3 and all(map(num, v))
                           and 0 <= v[0] <= 1 and 0 <= v[1] <= 1 and 1.0 <= v[2] <= 4.0)
        if not ok:
            errs.append(f"shot {s['n']}: kb.{k} must be [cx, cy, zoom] with cx, cy in 0..1 and zoom 1.0..4.0, got {v!r}")
    return errs


def check_still(P, s):
    """A still: pan-and-zoom fields, a readable picture, enough pixels for its tightest framing."""
    from panzoom import FROM, TO, picture_size, upscale
    n, errs, warns = s["n"], kb_errors(s), []
    errs += [f"shot {n}: {k} is not available for a still" for k in ("stab", "source_audio") if s.get(k)]
    if (s.get("speed") or 1.0) != 1.0:
        errs.append(f"shot {n}: a still has no speed; set its length with dur_s")
    try:
        size = picture_size(os.path.join(P["library_dir"], s["file"]))
    except C.ProbeError as e:
        return errs + [f"shot {n}: {e}"], warns
    if not STILL_S[0] <= s["dur_s"] <= STILL_S[1]:
        warns.append(f"shot {n}: still of {s['dur_s']} s; {STILL_S[0]:g}-{STILL_S[1]:g} s reads best")
    if not errs:
        kb, m = s.get("kb") or {}, P["master"]
        up = upscale(size, m["width"], m["height"], max(kb.get("from", FROM)[2], kb.get("to", TO)[2]))
        if up > MAX_UPSCALE:
            warns.append(f"shot {n}: the {size[0]}x{size[1]} picture is enlarged {up:.1f}x at its tightest framing "
                         "and will look soft; zoom less or use a larger original")
    return errs, warns


def still_runs(shots):
    out, run = [], []
    for s in shots + [{}]:
        if s.get("still"):
            run.append(s["n"])
            continue
        if len(run) > STILL_RUN:
            out.append(f"shots {run[0]}-{run[-1]}: {len(run)} stills in a row look like an album; break them up "
                       "with moving shots")
        run = []
    return out


def check_grades(P, shots):
    errs = []
    for g in sorted({s.get("grade") or P["default_grade"] for s in shots}):
        r = subprocess.run([C.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x36:d=0.1", "-vf", g,
                            "-f", "null", "-"], capture_output=True, text=True)
        if r.returncode:
            errs.append(f"grade \"{g}\" is not a valid ffmpeg filter chain: "
                        + (r.stderr.strip().splitlines() or ["?"])[0])
    return errs


def check_transitions(P, shots, with_end):
    durs = [C.est_duration(P, s) for s in shots]
    _, trans, _, _ = C.timeline(P, shots, durs, with_end)
    pieces = durs + ([P["timing"]["endcard_s"]] if with_end else [])
    names = [f"shot {s['n']}" for s in shots] + ["end card"]
    errs = []
    for k, (kind, d) in enumerate(trans, 1):
        if d < 0 or d >= min(pieces[k - 1], pieces[k]) - 0.1:
            errs.append(f"{names[k]}: a {d} s {kind} must be shorter than both pieces it joins "
                        f"({pieces[k - 1]:.2f} s and {pieces[k]:.2f} s)")
    for k in range(1, len(pieces) - 1):
        if trans[k - 1][1] + trans[k][1] >= pieces[k] - 0.1:
            errs.append(f"{names[k]}: its incoming and outgoing transitions overlap ({pieces[k]:.2f} s long)")
    return errs


def stop(errs):
    if errs:
        sys.exit("EDL problems:\n- " + "\n- ".join(errs))


def validate(P, edl, lo, hi, cut_only):
    """Fields of the whole EDL (the music timing depends on every shot), sources of the selected shots."""
    shots = edl["shots"][lo:hi + 1]
    errs = check_fields(edl)
    missing = [s["file"] for s in shots if s.get("file") and not os.path.exists(os.path.join(P["library_dir"], s["file"]))]
    if missing:
        errs.append("missing source files under library_dir:\n    " + "\n    ".join(missing))
    stop(errs)
    warns = still_runs(shots)
    for s in shots:
        e, w = (check_still if s.get("still") else check_source)(P, s)
        errs, warns = errs + e, warns + w
    for w in warns:
        print("warning:", w, file=sys.stderr)
    with_end = hi == len(edl["shots"]) - 1 and bool(edl.get("endcard"))
    errs += check_grades(P, shots) + check_transitions(P, shots, with_end)
    if not cut_only:
        errs += check_assets(P) + check_fonts(P, edl, shots)
        errs += music.check(P, edl, [C.est_duration(P, s) for s in edl["shots"]])
    stop(errs)


if __name__ == "__main__":
    print("edl_check.py is a module used by build_film.py, not a command. To validate an EDL before cutting:\n"
          "  build_film.py --project project.json [--edl edl.json] --check")
    sys.exit(0 if set(sys.argv[1:]) <= {"-h", "--help"} else 2)
