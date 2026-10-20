"""On-screen text: text blocks (current "lines" form and the older per-language keys) and where each text
shows in film time. Shared by build_film.py, edl_to_md.py and review_prep.py so they agree."""
import copy
import re
import sys

LANG_KEY = re.compile(r"[a-z]{2,3}[0-9]?")
NOT_TEXT = ("key", "why")
FADES = ("fadeblack", "fadein_from_black")
LABEL = {"caption": "chapter caption", "tag": "place tag", "closing": "closing statement"}
FIX = {"caption": "lengthen the shot or move the caption", "tag": "shorten tag.span_s or move the tag",
       "closing": "move closing.on_shot or use a dissolve there"}


def lines_of(obj):
    """Text blocks are {"lines": [...]}; the end card also takes line1 / line2."""
    if not obj:
        return []
    if isinstance(obj, list):
        return [x for x in obj if x]
    return [x for x in obj.get("lines") or [obj.get("line1"), obj.get("line2")] if x]


def _from_legacy(block, closing=False):
    """A block with one key per language (older EDLs: a 2-3 letter language code such as "en", "es") -> the
    same block with "lines" in key order. Numbered keys ("en1", "en2") were a closing statement's headline
    lines: they come first and go above the accent rule."""
    if not isinstance(block, dict) or block.get("lines"):
        return block
    keys = [k for k, v in block.items() if LANG_KEY.fullmatch(k) and k not in NOT_TEXT and isinstance(v, str) and v]
    if not keys:
        return block
    heads = [k for k in keys if k[-1].isdigit()]
    out = {**block, "lines": [block[k] for k in heads + [k for k in keys if k not in heads]]}
    if closing and "rule_after" not in block:
        out["rule_after"] = max(1, len(heads))
    return out


def normalize(edl):
    """A copy of the EDL whose text blocks all carry "lines" (older EDLs used one key per language)."""
    e = copy.deepcopy(edl)
    if e.get("title"):
        e["title"] = _from_legacy(e["title"])
    if e.get("closing"):
        e["closing"] = _from_legacy(e["closing"], closing=True)
    e["sections"] = [_from_legacy(x) for x in e.get("sections") or []]
    for s in e.get("shots") or []:
        if isinstance(s, dict) and s.get("tag"):
            s["tag"] = _from_legacy(s["tag"])
    return e


def _window(kind, name, key, shot, a, b, fade):
    return {"kind": kind, "name": name, "key": key, "shot": shot, "a": round(a, 3), "b": round(b, 3), "fade": fade}


def _next_break(shots, starts, i, end_card=True):
    """(film time, what) of the first break after shot i that text must not run into: a fade through black, a new
    chapter or (end_card) the end card; (None, None) when there is none."""
    for k in range(i + 1, len(shots)):
        fade, chapter = shots[k].get("transition_in") in FADES, bool(shots[k].get("caption_section"))
        if fade or chapter:
            what = "the fade through black" if fade else "the next chapter"
            return starts[k], what + (" into the next chapter" if fade and chapter else "") + f" (shot {shots[k]['n']})"
    if end_card and len(starts) > len(shots):
        return starts[len(shots)], "the end card"
    return None, None


def _caption(shots, durs, starts, i, a, late, caption_s, notes):
    """A chapter caption from a, shortened so it never runs into the next break and, when it had to wait for
    earlier text (late), not past the end of its own shot; None (with a note) when 1.2 s or less would be left."""
    s, (stop, what) = shots[i], _next_break(shots, starts, i)
    if late and (stop is None or starts[i] + durs[i] < stop):
        stop, what = starts[i] + durs[i], f"the end of shot {s['n']}"
    b = a + caption_s if stop is None else min(a + caption_s, stop)
    if b < a + caption_s and b - a <= 1.2:
        notes.append(f"shot {s['n']}: no room for the chapter caption before {what} at {stop:.1f} s once the text "
                     "before it has cleared, left out; lengthen the shot or shorten the text before it")
        return None
    if b < a + caption_s:
        notes.append(f"shot {s['n']}: chapter caption shortened to {b - a:.1f} s to end before {what} at {b:.1f} s; "
                     "lengthen the shot or shorten the text before it")
    return _window("caption", f"cap_{s['caption_section']}", s["caption_section"], s["n"], a, b, 0.5)


def _crossings(wins, shots, starts):
    """Notes on every caption, tag or closing statement still on screen when a later fade through black or chapter
    begins (the end card is left out: the closing statement and tags are laid out to fade with it)."""
    ns, notes = [s["n"] for s in shots], []
    for w in wins:
        if w["kind"] == "title":
            continue
        t, what = _next_break(shots, starts, ns.index(w["shot"]), end_card=False)
        if t is not None and w["b"] > t + 0.01:
            notes.append(f"shot {w['shot']}: its {LABEL[w['kind']]} is still on screen at {t:.1f} s, where {what} "
                         f"begins, until {w['b']:.1f} s; {FIX[w['kind']]}")
    return notes


def _after_caption(close, cap_end, n, notes):
    """The closing statement waits for a chapter caption it would overlap, if 3 s or more of it remain."""
    a = round(cap_end + 0.3, 3)
    if close["b"] - a < 3.0:
        notes.append(f"shot {n}: the chapter caption overlaps the closing statement and the shot is too short to "
                     "show them one after the other; move closing.on_shot or the caption to another shot")
        return close
    notes.append(f"shot {n}: closing statement starts at {a:.1f} s, after the chapter caption")
    return {**close, "a": a}


def _tag_window(s, base, busy, shot_end, last_end):
    """A place tag needs about 3.3 s of its shot; with span_s it runs that long, across the next shots."""
    a, span = max(base + 0.3, busy + 0.2), s["tag"].get("span_s")
    b = min(a + span, last_end - 0.5) if span else min(base + 3.9, shot_end - 1.2)
    return a, b


def text_windows(P, edl, shots, durs, starts, first):
    """Every on-screen text in film time ({kind, name, key, shot, a, b, fade}) plus notes on text left out or moved.

    One chapter caption or place tag per shot (a caption wins over a tag). Both share the lower-left bar,
    so one waits for the previous one to clear (and for the title); a tag is left out where it would overlap
    the closing statement, while the closing statement waits for a chapter caption. A caption is shortened (or
    left out) rather than run into a fade through black, a new chapter or the end card, or past its shot when it
    had to wait; other text still on screen when a fade through black or a chapter begins gets a note.
    """
    t, out, notes, titled = P["timing"], [], [], bool(first and edl.get("title"))
    if titled:
        out.append(_window("title", "title", None, None, t["title_in"], t["title_out"], 0.8))
    c, ns, close = edl.get("closing"), [s["n"] for s in shots], None
    busy = after_title = t["title_out"] + 0.3 if titled else -1.0
    last_end = starts[len(shots) - 1] + durs[len(shots) - 1]
    if c and c.get("on_shot") in ns:
        i = ns.index(c["on_shot"])
        close = _window("closing", "closing", None, c["on_shot"], starts[i] + c.get("offset", 1.5),
                        starts[i] + durs[i] - 0.8, 1.0)
    for i, s in enumerate(shots):
        base = starts[i] + (t["title_out"] + 0.5 if i == 0 and first else 0.6)
        key, tag = s.get("caption_section"), s.get("tag")
        if key:
            if tag:
                notes.append(f"shot {s['n']}: tag not shown, the chapter caption takes its place")
            a = max(base, busy + 0.2)
            if busy == after_title and a > base + 1.0:
                notes.append(f"shot {s['n']}: the chapter caption waits for the title and starts {a - starts[i]:.1f} s "
                             f"into the shot ({a:.1f} s); lengthen shot {shots[0]['n']} or move the caption")
            cap = _caption(shots, durs, starts, i, a, a > base, t["caption_s"], notes)
            if cap:
                out.append(cap)
                busy = cap["b"]
                if close and a < close["b"] and close["a"] < busy:
                    close = _after_caption(close, busy, s["n"], notes)
            continue
        if not tag:
            continue
        a, b = _tag_window(s, base, busy, starts[i] + durs[i], last_end)
        if b - a <= 1.2:
            notes.append(f"shot {s['n']}: no room for its place tag (needs about 3.3 s clear of other text, or "
                         "span_s to run across the next shots), left out")
        elif close and a < close["b"] and close["a"] < b:
            notes.append(f"shot {s['n']}: tag would overlap the closing statement, tag left out")
        else:
            out.append(_window("tag", f"tag_{s['n']:02d}", None, s["n"], a, b, 0.5))
            busy = b
    out += [close] if close else []
    return out, notes + _crossings(out, shots, starts)


if __name__ == "__main__":
    print("text_layout.py is a library module used by build_film.py, not a command: run build_film.py --help")
    sys.exit(2)
