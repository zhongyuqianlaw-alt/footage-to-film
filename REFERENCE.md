# footage-to-film reference

Field tables for `project.json`, the environment variables and the arguments of the three workflows. The
playbook and the `edl.json` fields are in `SKILL.md`.

## project.json

Lives in the film's build folder (e.g. `<film>/_build/project.json`); relative paths resolve against it,
`~` is expanded. Anything left out takes the default shown.

| key | meaning | default |
|---|---|---|
| `name` | file stem of every output (`<name>_<label>_Rev<N>.mp4`) | `film` |
| `library_dir` | footage root; EDL `file` paths are relative to it | `.` |
| `output_dir` | master, send version, music credit | `./output` |
| `build_dir` | holds `edl.json` and `EDL_Rev<N>.md` | `.` |
| `scratch_dir` | cache: `segments/`, `audio/`, `cards/`, `check/`, `review_Rev<N>[_draft][_shotsA-B]/`, mix files | `./.cache` |
| `fonts.cjk`, `fonts.cjk_index_regular`, `fonts.cjk_index_bold` | CJK font file and the face indices inside a `.ttc` | none |
| `fonts.latin`, `fonts.latin_bold` | Latin regular / bold font files | none |
| `colors.navy` | caption / tag bar and the top of the end-card gradient (hex `#RRGGBB` or `[r, g, b]`) | `[24, 28, 36]` |
| `colors.blue` | bottom of the end-card gradient | `[52, 96, 104]` |
| `colors.accent` | accent rules and the stripe on caption bars | `[220, 176, 64]` |
| `logos[]` | `{path, height, trim}`: drawn left to right on one white rounded plate with thin dividers; `height` = relative scale (1.0), `trim` = crop transparent margins (true); a bare path string also works; a missing or unreadable file stops `--check` | `[]` |
| `cards.title_logo_h`, `cards.end_logo_h` | logo height on the title / end card, px on a 1080-line canvas | `140`, `200` |
| `music.dir`, `music.tracks` | folder and `{title: file}` of licensed tracks (a file may be an absolute path); EDL `music.track` / `parts[].track` names a key | none |
| `music.credit_template` | credit text written next to the outputs; `{track}` is the only placeholder and is replaced by each music part's `credit` (default: its track name), so put title and composer there when they differ per track; with several parts the template is repeated per track and the lines all tracks share (licence, link) are written once at the end | none |
| `credit_file_name` | name of the credit file of a revision: `{rev}` is replaced by the revision label, otherwise `_Rev<N>` is added before the extension; an existing one is never overwritten | `music_credit_Rev{rev}.txt` |
| `send_limit_mb` | size cap of the send version, MiB | `500` |
| `master.width`, `height`, `fps`, `crf`, `preset`, `level`, `maxrate`, `bufsize` | master video: pixels, exact rational frame rate (set from the footage: `30000/1001`, `25`, ...), libx264 CRF and preset (a `--draft` uses CRF 23, `veryfast`), H.264 level, VBV | `3840`, `2160`, `30000/1001`, `17`, `medium`, `5.1`, `60M`, `90M` |
| `send.height`, `bitrates`, `maxrate`, `bufsize`, `level` | send version: height (width follows), bitrates tried in order until the file is under `send_limit_mb` and not bigger than the master (each is capped at 95 % of the master's video bitrate; else the build stops: append a lower one) | `1080`, `[16M, 13M, 10M]`, `20M`, `30M`, `4.1` |
| `loudness.I`, `TP`, `LRA`, `aac_headroom_db` | integrated target (LUFS), true-peak ceiling (dBTP), loudness range (LU) the encoded master's range is reported against (a note when wider; the gain does not depend on it; also in review `tech.json`), true-peak margin kept for the AAC encode (dB) | `-15`, `-1.5`, `11`, `0.5` |
| `source_audio.peak_dbfs`, `duck`, `highpass_hz` | peak of a kept source sound in the finished film (set against the gain the music bed gets; the build prints where it lands), music level under it (0-1), high-pass against wind | `-9`, `0.4`, `100` |
| `labels.master`, `labels.send`, `labels.draft` | name parts of the outputs (`<name>_<label>_Rev<N>.mp4`); `{height}` in a label is replaced by that file's real height; a `--draft` render always carries its real size: `<name>_<draft><height>p_Rev<N>.mp4` | `master{height}p`, `send{height}p`, `draft` |
| `default_grade` | ffmpeg filter chain for video shots without `grade` (part of the segment cache key); stills get only their own `grade` | `eq=contrast=1.04:saturation=1.07` |
| `timing.title_in`, `title_out` | title card window, seconds of film | `1.0`, `7.5` |
| `timing.caption_s` | chapter caption duration, s | `4.2` |
| `timing.endcard_s`, `endcard_xfade` | end card length and its fade from the last shot, s (pass the same values to storyboard.js) | `6.0`, `1.2` |
| `timing.fade_in`, `fade_out` | fade from / to black at the very start and end, s | `1.2`, `1.5` |
| `segment_codec` | codec for cached segments: `null` = VideoToolbox on macOS when available, else libx264 CRF 12; or an ffmpeg argument list such as `["-c:v", "libx264", "-crf", "14"]` | `null` |

Font paths per system (any TTF / OTF / TTC works; a configured path that does not exist stops `--check`;
`""` uses Pillow's default font, which has no bold, no CJK and few accented glyphs; set `cjk` only when
the on-screen text needs it; any on-screen character the chosen fonts cannot draw stops the build before
cutting):

| system | `cjk` (index regular / bold) | `latin` / `latin_bold` |
|---|---|---|
| macOS | `/System/Library/Fonts/Hiragino Sans GB.ttc` (0 / 2) | `/System/Library/Fonts/Supplemental/Arial.ttf` / `Arial Bold.ttf` |
| Debian / Ubuntu (`fonts-noto-cjk`, `fonts-dejavu`) | `/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc` (2 / 2 = Simplified Chinese; bold CJK is a separate file, so one weight serves both) | `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` / `DejaVuSans-Bold.ttf` |
| Windows | `C:/Windows/Fonts/msyh.ttc` (0 / 0) | `C:/Windows/Fonts/arial.ttf` / `arialbd.ttf` |

Segments are cached in `<scratch_dir>/segments/` under a hash of the shot's file, in / out, speed, grade,
stabilisation, fit, tone mapping and still move, the size and date of its source file and of any
`lut3d` file its grade names, the master size and fps and `default_grade`; `--force` re-cuts anyway.

## Environment

| variable | meaning |
|---|---|
| `FFMPEG`, `FFPROBE` | ffmpeg / ffprobe binaries for every script and the selftest (default: the first on `PATH`, then `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`); e.g. a build with zscale for HDR clips |
| `PYTHON` | selftest.sh only: the interpreter with the packages |
| `KEEP=1` | selftest.sh only: keep its work folder |

## storyboard.js

| arg | meaning | default |
|---|---|---|
| `libraryDir` | project `library_dir` as an absolute path (`~` expanded) | required |
| `sheetsDir` | absolute `make_sheets.py --out` folder | required |
| `workDir` | absolute scratch folder for verification frames and auditor files | required |
| `indexPath` or `clips` | absolute `<sheetsDir>/index_compact.json`, or its rows inline | required |
| `brief`, `audience` | what the film is for and who watches it | `''`, `general` |
| `facts` | the only statements allowed on screen | none (text limited to title and places) |
| `constraints` | extra hard requirements: rights, must-use / never-use shots, how to treat vertical and HDR clips, allowed off-rate clips | `[]` |
| `languages` | caption languages in order, one line each, e.g. `["en", "es"]` | `["en"]` |
| `targetSeconds` | `[min, max]` film length; a 2-minute film is `[110, 130]` | `[170, 190]` (3 minutes) |
| `endcardSeconds`, `endcardXfade` | must equal project `timing.endcard_s` / `endcard_xfade` | `6`, `1.2` |
| `masterFps` | project `master.fps`, used by the slow-motion and frame-rate rules | `30000/1001` |
| `musicOptions` | `[{track, seconds, mood}]`; `track` must be a key of project `music.tracks`, `seconds` its length (`ffprobe -v error -show_entries format=duration -of csv=p=0 <file>`) | required |
| `subjects` | optional enum for shot logging, e.g. `["aerial", "ship", "people"]` | free text |
| `stills` | photos the film may use, `[{file, desc}]` with `file` relative to `libraryDir`; chosen ones become `still` shots with `dur_s` and `kb` | none |
| `angles`, `judges` | override the three storyboard angles / two judge lenses | built in |
| `auditorCmd`, `auditorWaitCmd`, `auditorImageFlag`, `forwarderModel` | external reviewer for the facts judge (SKILL.md) | none |

Result: `{edl, verdicts, judges, tally, winnerKey, proposals, logs, missing, stillRejected}`. Save `edl`
as `<build_dir>/edl.json` and `logs` as `<build_dir>/shot_logs.json`. The workflow stops after the Log
phase when fewer than half the clips with sheets came back logged.

## grade.js

| arg | meaning | default |
|---|---|---|
| `dir` | absolute work folder for frames and comparison sheets | required |
| `libraryDir` | absolute `library_dir` | required |
| `shots` | `[{n, file, in_s, out_s, desc, grade, stats, why}]` copied from the EDL; `stats` = the `color_stats.py` entry whose `shot` equals `n` (`L_mean`, `contrast`, `sat`, `chroma`, `a`, `b` ...); `grade` may be omitted | required |
| `reference` | `{frames: [jpg ...], stats, description}`; `stats` from `color_stats.py <frames>` (median of the frames or the one closest to the look) | empty |
| `context` | one paragraph about the film and its look | `''` |
| `python` | interpreter with numpy + opencv (the venv's) | `python3` |
| `defaultGrade` | project `default_grade` | `eq=contrast=1.04:saturation=1.07` |
| `auditorCmd` ... | external auditor for the audit stage | none |

Example:

```
{dir: "/data/Films/harbor/_cache/grade", libraryDir: "/data/Footage/Harbor",
 python: "/data/venvs/footage-to-film/bin/python", context: "calm harbor day, natural colour",
 reference: {frames: ["/data/Films/harbor/_cache/ref/ref1.jpg"], stats: {L_mean: 52, contrast: 21, sat: 31, chroma: 14, a: -1, b: 4},
             description: "clear morning light, deep blue water, neutral steel"},
 shots: [{n: 4, file: "shift/gate_bell.mp4", in_s: 10, out_s: 18, desc: "crews at the gate", why: "flat, dull",
          stats: {L_mean: 47, contrast: 13, sat: 19, chroma: 7, a: 0, b: 2}}]}
```

Apply each accepted `filter` (no `high` audit finding for that shot) as the shot's `grade` and set
`grade_prev` to its previous `grade` (or the project `default_grade`), then audit with `grade_compare.py`.

## review.js

Pass the whole `<scratch_dir>/review_Rev<N>[_draft][_shotsA-B]/manifest.json` (`dir`, `send`, `master`, `timeline`, `tech`,
`filmImages`, `transitionImages`, `textImages`, `textSheets`, `sendLimitMb`, `loudness`) plus:

| arg | meaning | default |
|---|---|---|
| `brief`, `facts`, `audience` | as for storyboard.js | `''`, none, `general` |
| `creditOnScreen` | true if the music credit is meant to be on the end card | `false` |
| `logsPath` | `<build_dir>/shot_logs.json`, so reviewers can propose replacement shots | none |
| `auditorCmd` ... | external reviewer for the text and transition lenses | none |

The workflow stops if a path or an image list is missing (usually: the printed summary was passed instead
of the manifest file).
