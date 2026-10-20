"""Shared helpers: project.json loading, ffmpeg discovery, source probing, segment cache keys, timeline maths."""
import copy
import functools
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from fractions import Fraction

from text_layout import normalize

DEFAULTS = {
    "name": "film",
    "library_dir": ".",
    "output_dir": "./output",
    "build_dir": ".",
    "scratch_dir": "./.cache",
    "fonts": {"cjk": "", "cjk_index_regular": 0, "cjk_index_bold": 0, "latin": "", "latin_bold": ""},
    "colors": {"navy": [24, 28, 36], "blue": [52, 96, 104], "accent": [220, 176, 64]},
    "logos": [],
    "cards": {"title_logo_h": 140, "end_logo_h": 200},
    "music": {"dir": "", "tracks": {}, "credit_template": ""},
    "credit_file_name": "music_credit_Rev{rev}.txt",
    "send_limit_mb": 500,
    "master": {"width": 3840, "height": 2160, "crf": 17, "fps": "30000/1001", "preset": "medium",
               "level": "5.1", "maxrate": "60M", "bufsize": "90M"},
    "send": {"height": 1080, "bitrates": ["16M", "13M", "10M"], "maxrate": "20M", "bufsize": "30M", "level": "4.1"},
    "loudness": {"I": -15, "TP": -1.5, "LRA": 11, "aac_headroom_db": 0.5},
    "source_audio": {"peak_dbfs": -9, "duck": 0.4, "highpass_hz": 100},
    "labels": {"master": "master{height}p", "send": "send{height}p", "draft": "draft"},
    "default_grade": "eq=contrast=1.04:saturation=1.07",
    "timing": {"title_in": 1.0, "title_out": 7.5, "caption_s": 4.2, "endcard_s": 6.0,
               "endcard_xfade": 1.2, "fade_in": 1.2, "fade_out": 1.5},
    "segment_codec": None,
}
PATH_KEYS = ("library_dir", "output_dir", "build_dir", "scratch_dir")
REV_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".mts")
HDR_TRANSFER = ("smpte2084", "arib-std-b67")
SCALE_MATRIX = {"bt709": "bt709", "fcc": "fcc", "bt470bg": "bt470", "smpte170m": "smpte170m",
                "smpte240m": "smpte240m", "bt2020nc": "bt2020", "bt2020c": "bt2020"}
TAG709 = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709"
LUT_FILE = re.compile(r"lut3d=(?:file=)?'?([^:,']+)")


class ProbeError(Exception):
    pass


def tool(name):
    env = os.environ.get(name.upper())
    if env:
        return env
    found = shutil.which(name)
    if found:
        return found
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        if os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    sys.exit(f"{name} not found: install ffmpeg or set {name.upper()}=/path/to/{name}")


FFMPEG, FFPROBE = tool("ffmpeg"), tool("ffprobe")


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _abs(path, root):
    return os.path.normpath(os.path.join(root, os.path.expanduser(path))) if path else path


def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        sys.exit(f"{what} not found: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"{what} {path} is not valid JSON: {e}")


def load_project(path, out_dir=None, scratch_dir=None):
    root = os.path.dirname(os.path.abspath(path))
    P = merge(DEFAULTS, read_json(path, "project file"))
    for k in PATH_KEYS:
        P[k] = _abs(P[k], root)
    P["fonts"] = {k: (_abs(v, root) if isinstance(v, str) else v) for k, v in P["fonts"].items()}
    P["logos"] = [dict(l, path=_abs(l["path"], root)) if isinstance(l, dict) else {"path": _abs(l, root)}
                  for l in P["logos"]]
    P["music"]["dir"] = _abs(P["music"]["dir"], root)
    if out_dir:
        P["output_dir"] = os.path.abspath(out_dir)
    if scratch_dir:
        P["scratch_dir"] = os.path.abspath(scratch_dir)
    return P


def add_project_args(ap):
    ap.add_argument("--project", default="project.json", help="per-film project.json (never commit it)")
    ap.add_argument("--edl", help="EDL path (default: <build_dir>/edl.json)")
    ap.add_argument("--out-dir", help="override project output_dir")
    ap.add_argument("--scratch-dir", help="override project scratch_dir (segment/card cache)")


def load(args):
    """(project, EDL); the EDL's text blocks all carry "lines" (older per-language keys are converted)."""
    P = load_project(args.project, args.out_dir, args.scratch_dir)
    edl = read_json(args.edl or os.path.join(P["build_dir"], "edl.json"), "EDL")
    if not isinstance(edl, dict) or not isinstance(edl.get("shots"), list) or not edl["shots"]:
        sys.exit('EDL problems:\n- the EDL needs a non-empty "shots" list')
    if not all(isinstance(s, dict) for s in edl["shots"]):
        sys.exit('EDL problems:\n- every entry of "shots" must be an object')
    return P, normalize(edl)


def rev_label(rev):
    """Revision labels end up in file names: letters, digits, '.', '_' and '-' only."""
    if rev is not None and not REV_LABEL.fullmatch(str(rev)):
        sys.exit(f"--rev {rev!r}: use 1-40 letters, digits, '.', '_' or '-', starting with a letter or digit")
    return rev


def run(cmd, quiet=False):
    if not quiet:
        print("$", " ".join(str(c) for c in cmd[:12]), "...", flush=True)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-4000:], file=sys.stderr)
        sys.exit(f"command failed (exit {r.returncode}): {os.path.basename(str(cmd[0]))}")
    return r


def duration(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def fps(P):
    return float(Fraction(P["master"]["fps"]))


def cut_s(P):
    return math.ceil(10000 / fps(P)) / 10000


@functools.lru_cache(maxsize=None)
def has_encoder(name):
    r = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True)
    return f" {name} " in r.stdout


@functools.lru_cache(maxsize=None)
def has_filter(name):
    r = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True)
    return f" {name} " in r.stdout


def _rotation(v):
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            return int(float(sd["rotation"]))
    return int(float(v.get("tags", {}).get("rotate", 0)))


@functools.lru_cache(maxsize=None)
def probe_source(path):
    """Display size (rotation applied), frame rate, audio presence and HDR transfer of a clip."""
    if not os.path.isfile(path):
        raise ProbeError(f"not found: {path}")
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate,color_transfer,color_space:"
                        "stream_side_data=rotation:"
                        "stream_tags=rotate:format=duration", "-of", "json", path], capture_output=True, text=True)
    st = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    v = next((x for x in st.get("streams", []) if x.get("codec_type") == "video"), None)
    if v is None:
        raise ProbeError(f"no readable video stream in {path}: {r.stderr.strip()[-200:]}")
    rot, (w, h) = _rotation(v), (v["width"], v["height"])
    if abs(rot) % 180 == 90:
        w, h = h, w
    num, _, den = v.get("r_frame_rate", "0/1").partition("/")
    trc = v.get("color_transfer", "")
    return {"width": w, "height": h, "rotation": rot, "r_frame_rate": v.get("r_frame_rate"),
            "fps": float(num) / float(den or 1) if float(den or 1) else 0.0,
            "duration": float(st.get("format", {}).get("duration") or 0),
            "has_audio": any(x.get("codec_type") == "audio" for x in st["streams"]),
            "transfer": trc, "hdr": trc in HDR_TRANSFER, "matrix": v.get("color_space", ""), "vertical": h > w}


def has_audio(path):
    if not os.path.isfile(path):
        raise ProbeError(f"not found: {path}")
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise ProbeError(f"ffprobe could not read {path}: {r.stderr.strip()[-200:]}")
    return bool(r.stdout.strip())


def expand(inputs, exts=VIDEO_EXT):
    """Files as given; directories expand to the matching files inside them (sorted)."""
    for p in inputs:
        if os.path.isdir(p):
            yield from (os.path.join(p, f) for f in sorted(os.listdir(p))
                        if f.lower().endswith(exts) and not f.startswith("."))
        else:
            yield p


def segment_codec(P=None, w=3840, h=2160):
    if P and P.get("segment_codec"):
        return list(P["segment_codec"])
    if sys.platform == "darwin" and has_encoder("h264_videotoolbox"):
        return ["-c:v", "h264_videotoolbox", "-b:v", f"{max(8, round(90 * w * h / (3840 * 2160)))}M"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12"]


def to_bt709(matrix=None):
    """Filters leaving 8-bit BT.709 limited-range YUV, tagged so (untagged files make players guess the colours).
    matrix: the input's ffprobe color_space; unknown counts as BT.709, as players assume for HD (RGB input
    ignores it)."""
    m = SCALE_MATRIX.get(matrix or "", "bt709")
    return f"scale=in_color_matrix={m}:out_color_matrix=bt709:out_range=tv,format=yuv420p,{TAG709}"


def shot_matrix(P, s):
    """The YUV matrix of a shot's picture after picture_chain: BT.709 once tone-mapped, else the source's."""
    info = probe_source(os.path.join(P["library_dir"], s["file"]))
    return "bt709" if info["hdr"] and s.get("tonemap", True) else info["matrix"]


def tonemap_chain(info):
    return (f"zscale=tin={info['transfer']}:pin=bt2020:min=bt2020nc:t=linear:npl=100,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,")


def fit_chain(s, w, h):
    fit = s.get("fit", "crop")
    if fit == "blur":
        return (f"split[bg][fg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"boxblur=40:2,eq=brightness=-0.08[b];[fg]scale={w}:{h}:force_original_aspect_ratio=decrease:"
                "flags=lanczos[f];[b][f]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if fit == "pillarbox":
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
    return f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}"


def picture_chain(P, s, w, h):
    """Tone mapping (HDR sources) and fit to w x h: the picture a shot shows before speed, fps and grade."""
    info = probe_source(os.path.join(P["library_dir"], s["file"]))
    tm = tonemap_chain(info) if info["hdr"] and s.get("tonemap", True) else ""
    return tm + fit_chain(s, w, h)


def draft_profile(P):
    m = P["master"]
    h = min(1080, m["height"])
    w = round(m["width"] * h / m["height"] / 2) * 2
    return {**P, "draft": True, "master": {**m, "width": w, "height": h, "crf": 23, "preset": "veryfast"}}


def _key(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def _stamp(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_size, int(st.st_mtime)]


def file_stamps(P, s):
    """Size and date of the shot's source and of every LUT file its grade names (a replaced file re-cuts)."""
    grade = s.get("grade") or ("" if s.get("still") else P["default_grade"]) or ""
    return [_stamp(os.path.join(P["library_dir"], s["file"]))] + [_stamp(os.path.expanduser(f))
                                                                   for f in LUT_FILE.findall(grade)]


def segment_path(P, s):
    m = P["master"]
    still = {"still": 1, **{k: s.get(k) for k in ("dur_s", "kb")}} if s.get("still") else {}
    key = _key({k: s.get(k) for k in ("file", "in_s", "out_s", "speed", "grade", "stab")}
               | {k: s[k] for k in ("fit", "tonemap") if k in s} | still | {"files": file_stamps(P, s)}
               | {"w": m["width"], "h": m["height"], "fps": m["fps"], "dg": P["default_grade"]})
    return os.path.join(P["scratch_dir"], "segments", f"shot_{s['n']:02d}_{key}.mp4")


def source_audio_path(P, s):
    key = _key({k: s.get(k) for k in ("file", "in_s", "out_s")} | P["source_audio"]
               | {"src": _stamp(os.path.join(P["library_dir"], s["file"]))})
    return os.path.join(P["scratch_dir"], "audio", f"source_{s['n']:02d}_{key}.wav")


def est_duration(P, s):
    f = fps(P)
    if s.get("still"):
        return round(s["dur_s"] * f) / f
    return round((s["out_s"] - s["in_s"]) / (s.get("speed") or 1.0) * f) / f


def shot_duration(P, s):
    p = segment_path(P, s)
    return duration(p) if os.path.exists(p) and os.path.getsize(p) > 0 else est_duration(P, s)


def timeline(P, shots, durs, with_endcard):
    """Start time of every piece, (kind, seconds) of every transition, and total length."""
    t = P["timing"]
    durs = list(durs) + ([t["endcard_s"]] if with_endcard else [])
    trans = [(s["transition_in"], cut_s(P) if s["transition_in"] == "cut" else float(s["transition_s"]))
             for s in shots[1:]] + ([("fadeblack", t["endcard_xfade"])] if with_endcard else [])
    starts = [0.0]
    for k in range(1, len(durs)):
        starts.append(round(starts[-1] + durs[k - 1] - trans[k - 1][1], 4))
    return durs, trans, starts, round(starts[-1] + durs[-1], 4)


def shot_range(edl, spec):
    """'A-B' (shot numbers, inclusive) → (lo, hi) list indices; None → whole film."""
    shots = edl["shots"]
    if not spec:
        return 0, len(shots) - 1
    a, _, b = str(spec).partition("-")
    nums = [s.get("n") for s in shots]
    try:
        lo, hi = nums.index(int(a)), nums.index(int(b or a))
    except ValueError:
        sys.exit(f"--shots {spec}: shot numbers must exist in the EDL ({nums[0]}..{nums[-1]})")
    if lo > hi:
        sys.exit(f"--shots {spec}: write the range in film order (A-B with A before B)")
    return lo, hi


def output_paths(P, rev, shots=None):
    """(master, send). "{height}" in a label becomes that file's real height (default master2160p, send1080p);
    a draft is one file that always carries its real size, e.g. <name>_draft1080p_Rev<N>.mp4."""
    suffix, lab, out = f"_shots{shots[0]}-{shots[1]}" if shots else "", P["labels"], P["output_dir"]
    if P.get("draft"):
        d, h = lab.get("draft", "draft"), P["master"]["height"]
        d = d.replace("{height}", str(h)) if "{height}" in d else f"{d}{h}p"
        p = os.path.join(out, f"{P['name']}_{d}_Rev{rev}{suffix}.mp4")
        return p, p
    sizes = {"master": P["master"]["height"], "send": P["send"]["height"]}
    return tuple(os.path.join(out, f"{P['name']}_{lab[k].replace('{height}', str(sizes[k]))}_Rev{rev}{suffix}.mp4")
                 for k in ("master", "send"))


def edl_md_path(P, rev, beside_outputs=False):
    """EDL_Rev<N>.md lives in build_dir, or next to the outputs when --out-dir redirects a render."""
    return os.path.join(P["output_dir"] if beside_outputs else P["build_dir"], f"EDL_Rev{rev}.md")


def credit_path(P, rev):
    """One music credit file per revision: {rev} in credit_file_name, else _Rev<N> before the extension."""
    name = P["credit_file_name"]
    if "{rev}" in name:
        name = name.replace("{rev}", str(rev))
    else:
        stem, ext = os.path.splitext(name)
        name = f"{stem}_Rev{rev}{ext}"
    return os.path.join(P["output_dir"], name)


if __name__ == "__main__":
    print("common.py is a library module used by build_film.py, not a command: run build_film.py --help")
    sys.exit(2)
