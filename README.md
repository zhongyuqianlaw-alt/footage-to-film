# footage-to-film

A Claude Code skill that turns a footage library into a short landscape promo film: an agent team logs
every clip from timestamped contact sheets, storyboards with a judge panel, verifies every shot on real
frames, measures shake and colour, mixes in photos with gentle pan-and-zoom moves, lets the music change mood
in parts, renders a 4K master plus a size-capped send version, and reviews the result through
adversarial lenses.

It was distilled from production work on short industrial films and keeps the lessons that cost time:
why 0.75x slow motion judders, why optical-flow shake readings lie over water, why a dissolve between
unlike shots looks muddy (and why clients still prefer dissolves to hard cuts), how to keep a ship's horn
in sync under the music, how many photos in a row start to look like an album, and why the send version
must copy the master's audio.

## What you get

- `SKILL.md` - the playbook, the hard-won rules the agents follow and the EDL fields; `REFERENCE.md` -
  `project.json` fields, workflow arguments and environment variables
- `scripts/` - ffmpeg / OpenCV / Pillow tools: contact sheets, tonal-event (horn) finder, shake meter,
  subject tracker, stabiliser, pan-and-zoom stills, colour stats, grade comparison, text cards, the film
  builder (EDL validation, multi-part music, two-pass loudness), review prep
- `workflows/` - Claude Code Workflow scripts: `storyboard.js`, `review.js`, `grade.js`
- `config/` - example `project.json` and `edl.json`

Everything specific to one film lives in a local `project.json` + `edl.json` that you never commit.

## Requirements

- Claude Code with the Workflow tool (for the three workflows; SKILL.md describes a manual fallback)
- ffmpeg / ffprobe 5.1 or newer with libx264 (drawtext is not required: text is rendered with Pillow);
  zscale (libzimg) if you cut HDR footage, which most recent phone clips are. Homebrew's `ffmpeg` has no
  zscale: `brew install ffmpeg-full` and point the scripts at it with `FFMPEG` / `FFPROBE` (SKILL.md,
  Requirements). On macOS the segment cache uses VideoToolbox when available.
- At least one licensed music track: the build does not render without music.
- Python 3.9+ with numpy, Pillow >= 10.1 and opencv-python, installed in a virtual environment.
  The packages must be in the same `python3` that runs the scripts: macOS ships `/usr/bin/python3`,
  Homebrew adds another one, and `pip install` into one does not make them visible to the other. Run
  every script with the venv's interpreter; a script started with an interpreter that lacks them stops
  with a message naming that interpreter and the install command, instead of a traceback.

## Install

```bash
git clone https://github.com/zhongyuqianlaw-alt/footage-to-film.git ~/.claude/skills/footage-to-film
python3 -m venv ~/.venvs/footage-to-film
~/.venvs/footage-to-film/bin/pip install -r ~/.claude/skills/footage-to-film/requirements.txt
PYTHON=~/.venvs/footage-to-film/bin/python ~/.claude/skills/footage-to-film/scripts/selftest.sh   # ~1 minute
```

A venv avoids the `externally-managed-environment` error of Homebrew and recent Debian / Ubuntu Pythons.
Always run the scripts with that interpreter (`$PY scripts/x.py`, not `./scripts/x.py`), and pass it to
`workflows/grade.js` as `python`. `selftest.sh` also syntax-checks the workflow scripts when `node` is installed.

## Quickstart

```bash
S=~/.claude/skills/footage-to-film
PY=~/.venvs/footage-to-film/bin/python
L=~/Footage/HarborExample                  # the footage library: set library_dir in project.json to the same folder
F=~/Films/harbor                           # one folder per film
mkdir -p "$F/_build"
cp "$S/config/project.example.json" "$F/_build/project.json"   # edit library_dir, fonts, logos, music (SKILL.md step 1)
P="$F/_build/project.json"                 # the example puts scratch_dir at $F/_cache, outputs in $F

(cd "$L" && find . -type f \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' -o -iname '*.mts' -o -iname '*.mkv' \) \
  ! -name '._*' | sed 's|^\./||' | sort) > "$F/_build/candidates.txt"
$PY "$S/scripts/make_sheets.py" "$F/_build/candidates.txt" --library "$L" --out "$F/_cache/sheets"
# in Claude Code: run workflows/storyboard.js (SKILL.md "Running the workflows"); save result.edl as $F/_build/edl.json

$PY "$S/scripts/build_film.py" --project "$P" --check           # validates the EDL, sources, stills, fonts, logos, music
$PY "$S/scripts/build_film.py" --project "$P" --cut-only        # caches segments and lists them
tr '\n' '\0' < "$F/_cache/segments.txt" | xargs -0 $PY "$S/scripts/shake_meter.py"    # decide what to stabilise
$PY "$S/scripts/build_film.py" --project "$P" --rev D1 --draft --shots 1-6   # quick draft of shots 1-6 (new label each time)
$PY "$S/scripts/build_film.py" --project "$P" --rev 1                # master + send version + credit + EDL_Rev1.md
$PY "$S/scripts/review_prep.py" --project "$P" --rev 1               # then run workflows/review.js on its manifest.json
```

Ask Claude something like *"cut a 3-minute film for our partners from the clips in ~/Footage/Harbor"*
and the skill walks through the same steps, asking for the brief, facts and delivery limits first.

## Design notes

- Every EDL mistake that can be found up front (wrong types, unknown section, overlong transition, uneven
  slow motion, missing audio, unreadable stills, missing logo or font files, music that does not fit its
  track, invalid grade, text the configured fonts cannot draw) stops the build with one list of problems
  before anything is cut.
- Stills are rendered with sub-pixel pan-and-zoom moves on a smoothstep ease; 4:3 and wider pictures are
  cover-cropped at the output aspect, portrait ones sit on a blurred, darkened copy of themselves.
- Music can be one track or several parts joined by equal-power crossfades at chosen shots; each part's
  start is solved so it ends on a chosen musical moment, and loudness is normalised after the mix.
- Stabilised shots are cut with handles and smoothed with trend-preserving padding, so a steady pan or
  drone move keeps its speed right up to the cut.
- Segments are cached under a hash of file, in/out, speed, grade, stabilisation and still move plus the
  size and date of the source and of any LUT file, so editing the EDL or replacing a source never reuses
  a stale cut.
- `--shots A-B` previews a sub-range with the music it will have in the full film; `--draft` renders a
  fast master of up to 1080p for iteration, and the two combine.
- A kept source sound (a horn, a bell) is levelled against the gain the music gets, so a quiet music bed
  does not lift it above the cap.
- A revision is never overwritten: master, send version and that revision's music credit are refused when
  they exist; every full render writes `EDL_RevN.md`.
- Workflows take every project specific from `args`; an optional external reviewer CLI can be plugged
  in through `auditorCmd` (see SKILL.md).

## 中文说明

这是一个 Claude Code skill：把一个素材库剪成几分钟的横屏宣传短片。流程是「带时间码的缩略图拼板逐段记镜头日志 →
三版分镜 + 两位评审 → 综合成剪辑表 → 每个镜头按真实时间点抽帧核对 → 量抖动、防抖、量色彩、调色 → 穿插照片（推拉）、配乐可分段换曲 →
出 4K 高清版和限大小的发送版 → 四个视角审片并逐条证伪」。项目相关的一切（路径、字体、logo、配乐、交付规格、剪辑表）都放在本地的
`project.json` 与 `edl.json`，不进仓库。

几条踩坑换来的规矩：慢放只用 0.5 倍，且素材帧率要正好是成片的两倍；水面、天空或画面里的移动物体会把全局光流的抖动读数抬高，要用主体跟踪复核；
转场默认用渐变（客户常觉得直切不自然），构图相近的镜头排在一起、人物或构图差异大的镜头溶解缩到 0.5–0.7 秒，避免叠影发脏；
照片每张 3–4.5 秒、连续不超过三张，裁切别切到人脸，合影之间穿插工作画面；配乐可按故事分段，换曲点放在画面转折处，曲名避开宗教、战争等含义；
字幕只写核实过的事实，按事实清单审字幕时清单外的真实信息要先查原始文件再删；公开片子不出现安全不合规画面、现金和可辨认的个人姓名；
保留原声（如汽笛）时配乐要压低、峰值要限住；发送版直接拷贝高清版的音频，不要二次压缩；任何版本都不覆盖，改一次升一个版本号。

安装：克隆到 `~/.claude/skills/footage-to-film`，用 `python3 -m venv` 建虚拟环境装 `requirements.txt`，
再用该环境的 python 跑 `scripts/selftest.sh` 自检（`PYTHON=<venv>/bin/python`）。numpy / opencv 必须装在实际运行脚本的那个
python3 里，否则脚本会提示是哪个解释器缺包。三个 workflow 需要 Claude Code 的 Workflow 工具。至少要有一首有授权的配乐，
否则不出片；手机拍的 HDR 素材需要带 zscale 的 ffmpeg（macOS 用 `brew install ffmpeg-full`，再用 `FFMPEG` / `FFPROBE` 指过去）。

## License

MIT © 2026 BigG
