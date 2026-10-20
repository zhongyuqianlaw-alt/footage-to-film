#!/usr/bin/env python3
"""edl.json -> human-readable EDL_Rev<N>.md, one per revision.

Durations come from cached segments when present, otherwise from in/out/speed (dur_s for stills).
Before a revision is rendered its EDL_Rev<N>.md is only a plan and is rewritten freely (the render of that
revision rewrites it too); once the revision's master exists the file is kept unless --force.

usage: edl_to_md.py --project project.json --rev 3 [--force] [--out-dir DIR: write it next to those outputs]
"""
import argparse
import os
import sys

import common as C
import music
from text_layout import lines_of, text_windows


def texts_by_shot(edl, wins, first_n):
    """Every text the build shows, listed on the shot it starts on (same rules as build_film.py)."""
    secs, by_n = {x["key"]: x for x in edl.get("sections", [])}, {s["n"]: s for s in edl["shots"]}
    out = {}
    for w in wins:
        n = w["shot"] if w["shot"] is not None else first_n
        if w["kind"] == "caption":
            label = "caption: " + (lines_of(secs.get(w["key"])) or [""])[0]
        elif w["kind"] == "tag":
            label = "tag: " + (lines_of(by_n[n].get("tag")) or [""])[0]
        else:
            label = {"title": "title", "closing": "closing statement"}[w["kind"]]
        out.setdefault(n, []).append(label)
    return {n: "; ".join(v) for n, v in out.items()}


def effects(s):
    fx = [name for key, name in (("still", "pan-and-zoom still"), ("grade", "grade"), ("stab", "stabilised"),
                                 ("source_audio", "source sound")) if s.get(key)]
    return ", ".join(fx)


def source_range(s):
    if s.get("still"):
        return f"still, {s['dur_s']} s", "-"
    return f"{s['in_s']}-{s['out_s']}", s.get("speed") or 1.0


def rows(P, edl):
    shots = edl["shots"]
    durs, _, starts, total = C.timeline(P, shots, [C.shot_duration(P, s) for s in shots], bool(edl.get("endcard")))
    wins, notes = text_windows(P, edl, shots, durs, starts, True)
    texts, out = texts_by_shot(edl, wins, shots[0]["n"]), []
    for i, s in enumerate(shots):
        tr = s["transition_in"] if i else "fade in from black"
        rng, speed = source_range(s)
        out.append(f"| {s['n']} | {starts[i]:.1f} | {durs[i]:.1f} | {tr} | {os.path.basename(s['file'])} | "
                   f"{rng} | {speed} | {texts.get(s['n'], '')} | {effects(s)} | {s.get('desc', '')} |")
    return out, total, notes


def music_line(edl):
    parts = music.parts_of(edl.get("music") or {})
    names = [p.get("credit") or p.get("track") or os.path.basename(p.get("file", "")) for p in parts]
    switches = [f"to part {k + 2} at shot {p.get('to_shot')}" for k, p in enumerate(parts[:-1])]
    return " + ".join(names) + (f" ({', '.join(switches)})" if switches else "")


def render(P, edl, rev):
    body, total, notes = rows(P, edl)
    title = " / ".join(lines_of(edl.get("title")))
    lines = [f"# EDL Rev{rev} (generated from edl.json; edit edl.json, not this file)", "",
             f"Title: {title}  |  total {total:.1f} s  |  music: {music_line(edl)}", ""]
    if edl.get("concept"):
        lines += [edl["concept"], ""]
    lines += ["| # | starts at | dur | transition in | source | in-out (s) | speed | on-screen text | fx | picture |",
              "|---|---|---|---|---|---|---|---|---|---|", *body, ""]
    if notes:
        lines += ["On-screen text notes:", *[f"- {n}" for n in notes], ""]
    if edl.get("closing"):
        lines += ["Closing: " + " / ".join(lines_of(edl["closing"])), ""]
    if edl.get("endcard"):
        lines += ["End card: " + " / ".join(lines_of(edl["endcard"])), ""]
    lines.append("Re-render: `build_film.py --project project.json --rev <next>` "
                 "(segments are cached by in/out/speed/grade/stab/still and the size and date of the source and LUT "
                 "files, so edited shots re-cut automatically).")
    return "\n".join(lines) + "\n"


def write_md(P, edl, rev, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(P, edl, rev))
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_project_args(ap)
    ap.add_argument("--rev", required=True)
    ap.add_argument("--force", action="store_true", help="rewrite the file of an already rendered revision")
    a = ap.parse_args()
    C.rev_label(a.rev)
    P, edl = C.load(a)
    path = C.edl_md_path(P, a.rev, bool(a.out_dir))
    if os.path.exists(path) and os.path.exists(C.output_paths(P, a.rev)[0]) and not a.force:
        print(f"{path} belongs to the rendered Rev{a.rev} and is kept (use --force to rewrite it)", file=sys.stderr)
        print(path)
        return
    print(write_md(P, edl, a.rev, path))


if __name__ == "__main__":
    main()
