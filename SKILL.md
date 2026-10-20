---
name: footage-to-film
description: Use when asked to cut a 1-5 minute landscape (16:9) film from a folder of clips and photos - a promo, project or company film, event recap, opening / launch / ceremony video, montage, aftermovie or highlight reel ("make a video from these clips / photos") - or to fix shaky shots, grading, loudness, music, transitions or on-screen text in such a film. A team of agents logs every clip from timestamped contact sheets, storyboards with a judge panel and checks every shot on real frames; ffmpeg / Python tools stabilise, grade, mix music and render a 4K master plus a size-capped send version, which is then reviewed through adversarial lenses. Not for trimming or converting a single clip, and not for vertical social reels (TikTok, Reels, Shorts).
---

# footage-to-film

A proven pipeline for short films cut from an existing footage library. Everything specific to one film
lives in two local files that are never committed: `project.json` (paths, fonts, logos, music, delivery
specs) and `edl.json` (the cut). The scripts are generic; the agents do the judgement.
`project.json` fields, workflow arguments and environment variables are listed in `REFERENCE.md`;
the EDL fields are described below under "edl.json".

## Requirements

- **Claude Code with the Workflow tool** for `workflows/*.js`. It is available when `Workflow` is among
  your tools (a deferred one is found with ToolSearch `select:Workflow`); otherwise follow "Without the
  Workflow tool" below and say so.
- **ffmpeg / ffprobe 5.1 or newer** with libx264, xfade, acrossfade, ebur128 and alimiter (drawtext is NOT
  needed: text is rendered with Pillow). `scripts/selftest.sh` prints the version and whether it has
  **zscale**, which HDR clips need (iPhone and recent Android videos are usually HDR / HLG): without it
  the build refuses them. To get it: macOS `brew install ffmpeg-full` (keg-only, so point the scripts at
  it: `export FFMPEG="$(brew --prefix ffmpeg-full)/bin/ffmpeg" FFPROBE="$(brew --prefix ffmpeg-full)/bin/ffprobe"`);
  Debian / Ubuntu `apt install ffmpeg` (built with libzimg); Windows a "full" build from gyan.dev or a
  BtbN build. Every script and the selftest use `FFMPEG` / `FFPROBE` when set. Without zscale, export the
  HDR clips as SDR Rec.709 in the phone or editor, or set `"tonemap": false` on the shot: it then looks
  flat and washed out (HLG) or dark and flat (PQ).
- **Python 3.9+** with `requirements.txt` (numpy, Pillow >= 10.1, opencv-python), best in a venv. The
  packages must be installed for the exact `python3` that runs the scripts; a script started with another
  interpreter stops with a message naming it. Below, `S` is the skill folder and `PY` that venv's
  interpreter, e.g. `PY=~/.venvs/footage-to-film/bin/python`; always call the scripts as `$PY $S/scripts/<name>.py`.
- **At least one licensed music track** (a file the client owns the rights to, or a CC BY track with
  its credit line): the build does not render without music.
- **Output is landscape** at the master size (16:9 by default). Vertical clips are cropped to a centre strip
  unless the shot sets `"fit": "blur"` or `"pillarbox"`; HDR clips are tone-mapped to Rec.709 (see
  ffmpeg above); log profiles (D-Log, S-Log ...) need a LUT first: start that shot's `grade` with
  `lut3d=file=<absolute path of the .cube>` (a relative path is resolved against the folder the build
  is started from). Photos (JPEG / PNG; convert HEIC first) go in as stills.
- Run `PYTHON=$PY $S/scripts/selftest.sh` once on a new machine.

## Running the workflows

`workflows/storyboard.js`, `grade.js` and `review.js` are Claude Code Workflow scripts (they use the
`agent` / `parallel` / `pipeline` / `phase` / `log` globals; they are not Node programs). Claude runs one
with the Workflow tool and the script's absolute path, passing args as a JSON object (not a string):

```
Workflow({scriptPath: "<S>/workflows/storyboard.js", args: {
  libraryDir: "/data/Footage/Harbor", sheetsDir: "/data/Films/harbor/_cache/sheets",
  workDir: "/data/Films/harbor/_cache/storyboard",
  indexPath: "/data/Films/harbor/_cache/sheets/index_compact.json",
  brief: "2-minute film for partners: one working day at the harbor",
  facts: "Harbor Example, Example City. Opened 2024. Berths 1-4.", audience: "partners and staff",
  constraints: ["vertical clips only in the crew chapter, with fit blur", "never show the gate guard's face"],
  languages: ["en", "es"], targetSeconds: [110, 130], masterFps: "30000/1001",
  endcardSeconds: 6, endcardXfade: 1.2,
  musicOptions: [{track: "Example Theme", seconds: 150, mood: "calm, builds"}],
  stills: [{file: "photos/crew_briefing.jpg", desc: "morning briefing on the quay"}]}})
```

Some args repeat `project.json` and must be kept equal by hand: `libraryDir` = `library_dir` and
`sheetsDir` / `indexPath` = the `make_sheets.py --out` folder (all absolute, `~` expanded: agents Read
files under them), `masterFps` = `master.fps`, `endcardSeconds` / `endcardXfade` = `timing.endcard_s` /
`endcard_xfade`, every `musicOptions[].track` = a key of `music.tracks`, grade.js `defaultGrade` =
`default_grade`. `targetSeconds` left out means 170-190 s (a 3-minute film); `musicOptions` is required.
To call the workflows by name, link them into `~/.claude/workflows/` (e.g.
`ln -s $S/workflows/storyboard.js ~/.claude/workflows/footage-to-film-storyboard.js`).

- **storyboard.js** returns `{edl, verdicts, judges, logs, missing, stillRejected, ...}`. Read
  `edl.notes` and `stillRejected`, then Write `result.edl` to `<build_dir>/edl.json` (extra keys such as
  `est_total_s`, `notes`, `why` are ignored by the build) and `result.logs` to
  `<build_dir>/shot_logs.json` (review.js `logsPath`). It stops after logging when fewer than half the
  clips came back logged.
- **grade.js** returns `{graded, audits}`: a grade is accepted when no audit lists a `high` finding for
  that shot; copy each accepted `filter` into that shot's `grade` (fix `medium` findings by hand).
- **review.js** returns one entry per lens with the findings that survived the refute pass.
- A required arg that is missing stops the workflow with a message; an external auditor that fails is
  reported as `WARNING ... NOT reviewed` (see "External auditor").

**Without the Workflow tool** the same steps run inline; they lose the independent perspectives, so be
stricter about verifying:

1. **Log.** Read every sheet listed in `index.json` and write `<build_dir>/shot_logs.json`: a list of
   `{"id": "c001", "summary": "...", "overall_quality": 1-5, "issues": [...], "shots": [{"in_s", "out_s",
   "desc", "subject", "shot_type", "motion", "quality", "hero", "problems"}]}` (the shape storyboard.js
   returns as `logs`; review.js reads it through `logsPath`).
2. **Storyboard.** Draft `edl.json` by hand under the rules below, from the logs.
3. **Verify.** For every shot extract frames at `in_s + 0.3`, the midpoint and `out_s - 0.3` and Read them:
   `ffmpeg -v error -y -ss <t> -i "<library_dir>/<file>" -frames:v 1 -vf scale=960:-2 <scratch_dir>/verify/<n>_<in|mid|out>.jpg`.
   Fix in / out or replace shots that fail.
4. **Grade.** For each flagged shot try a filter on its mid frame
   (`ffmpeg -v error -y -ss <t> -i "<library_dir>/<file>" -frames:v 1 -vf "scale=960:-2,<filter>" try.jpg`)
   next to the reference frames; when it is close, write it as the shot's `grade` (old chain as
   `grade_prev`), run `grade_compare.py` and audit the sheets, re-cut with `--cut-only` and run
   `color_stats.py` again on the paths in `segments.txt`.
5. **Review.** Read the `review_prep.py` images (`film_NN.jpg`, `transitions_NN.jpg`, `textsheet_N.jpg`)
   and `tech.json` through the four lenses: on-screen text against the facts, transitions and story
   order, a first-time viewer, technical (loudness, size, black or frozen frames).

## Playbook

1. **Set up.** `mkdir -p <film>/_build` and copy `config/project.example.json` to
   `<film>/_build/project.json`; relative paths in it resolve against that file. Edit at least: `name`,
   `library_dir`, `output_dir` / `scratch_dir`, `logos` (the client's logo files, or `[]`; they are
   checked before cutting), `fonts` (the example has macOS paths; Linux and Windows paths are in
   REFERENCE.md; `""` uses Pillow's default font; `cjk` only when the text needs it), `master.fps` (step 2),
   `music.dir` / `music.tracks` / `music.credit_template`. Ask the client for: purpose and audience,
   target length, deadline, delivery limits (e.g. a messenger's 500 MB cap), languages on screen, facts
   that may be stated, footage that must or must not be used, the music and its licence, and whether
   the film is public (then faces, names and safety matter more, see rules 11 and 16).
   **Music:** copy the licensed tracks into `music.dir`, add each as `"Title": "file.mp3"` under
   `music.tracks`, and read its length with `ffprobe -v error -show_entries format=duration -of csv=p=0 <file>`:
   these become storyboard.js `musicOptions` (`[{track: "<music.tracks key>", seconds, mood}]`).
   `credit_template` replaces only `{track}`; when tracks have different composers, write the full credit
   (`"\"Title\" by Composer"`) as each EDL music part's `credit`.
2. **Inventory.** List candidate clips relative to `library_dir`, one per line (`#` starts a comment);
   `<build_dir>` must be absolute here because the command runs inside the library:
   `cd <library_dir> && find . -type f \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' -o -iname '*.mts' -o -iname '*.mkv' \) ! -name '._*' | sed 's|^\./||' | sort > <build_dir>/candidates.txt`.
   Drone sidecars (`.LRF`, `.THM`, `.SRT`) stay out; an `.LRF` next to a clip is used as a fast proxy
   automatically. Take third-party material out of the film by deleting its line from `candidates.txt`
   (or prefixing it with `#`); never delete, move or rename anything in `library_dir`. Photos: the same
   `find` with `-iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.heic'` into
   `<build_dir>/photos.txt`; Read them, and pass the usable ones as the storyboard `stills` arg
   (`[{file, desc}]`, `file` relative to `library_dir`). Convert HEIC first (macOS
   `sips -s format jpeg in.heic --out in.jpg`; elsewhere ImageMagick `magick in.heic in.jpg`). Then run
   `$PY $S/scripts/make_sheets.py <build_dir>/candidates.txt --library <library_dir> --out <scratch_dir>/sheets`.
   It lists clips without sheets (exit status 1; fix or drop them: `n_sheets` 0 in `index_compact.json`),
   the vertical and HDR clips (and whether this ffmpeg can tone-map HDR) and the library's frame rates.
   **Frame rate:** set `master.fps` (and `masterFps`) to the main rate; clips at twice that rate can run
   at 0.5 slow motion. Clips at other rates (25/50p in a 29.97 master, 23.976/24p anywhere but a 23.976/24
   master) are refused at 1.0: leave them out, or keep a few knowingly with `"allow_speed": true` (frames
   repeat or drop unevenly: barely visible on static or slow shots, obvious on pans). Agree with the client
   how to treat vertical and HDR clips, and write that decision and the must-use / never-use list into
   storyboard.js `constraints` (example above).
3. **Log + storyboard.** Run `storyboard.js` (above). It logs every clip from the sheets, drafts three
   storyboards, judges them with two different lenses, synthesizes the EDL, frame-checks every shot at its
   real timestamps and repairs rejects. Save its `edl` as `<build_dir>/edl.json`.
4. **Source sound.** If a real sound matters (a ship's horn, a bell), locate it with
   `$PY $S/scripts/find_tonal_events.py clip.mp4` on the whole clip. The threshold is relative to the
   clip's own background (the median level of its non-tonal frames), so the clip needs a few seconds
   without the sound: when the sound runs through almost all of it the script warns, and the result needs
   checking by ear. Steady pitches only; a fast siren sweep is found in parts at best. Mark the shot
   `"source_audio": true` at speed 1.0; several shots may carry source sound, the music ducks under each.
   **Speech, applause, a ribbon cut** are not tonal: pick in / out by ear (or from a waveform) on phrase
   boundaries, keep soundbites short, and check intelligibility in the preview - speech may need the
   music lower (`source_audio.duck` 0.2-0.3) and a higher `peak_dbfs` (-6) than a horn. Caption quoted
   speech only with words the client has confirmed.
5. **Check, cut and measure.** `$PY $S/scripts/build_film.py --project <build_dir>/project.json --check`
   validates the whole EDL (fields, sources, stills, fonts, logos, music, on-screen text timing) without
   cutting anything; `--cut-only` then cuts the segments (`<scratch_dir>/segments/shot_NN_<hash>.mp4`)
   and lists this EDL's ones in `<scratch_dir>/segments.txt`; measure exactly those (a glob over the
   folder also catches stale segments from earlier edits):
   `tr '\n' '\0' < <scratch_dir>/segments.txt | xargs -0 $PY $S/scripts/shake_meter.py`.
   An hf `jit_px` below 0.5 is steady, 0.5-1.5 deserves a look, above 1.5 stabilise with
   `"stab": {"smooth_s": 1-2, "max_zoom": 1.08-1.10}`; the build warns when the shot is still above 1.0
   after stabilising, when it did not help, or when `max_zoom` held the correction back (with the zoom that
   would be needed): raise `max_zoom` a little if the crop allows it, else replace the shot. Over water or
   sky, or with moving content in a fixed frame, confirm on the subject: look at a frame first
   (`ffmpeg -v error -y -ss 2 -i <segment> -frames:v 1 <scratch_dir>/look.jpg`, then Read it), then
   `$PY $S/scripts/subject_track.py <segment> --cx 0.5 --cy 0.66` (subject centre as fractions of width
   and height); when it reports `low_confidence`, pick a corner or textured detail of the subject instead.
   `color_stats.py` on the same paths (`--out stats.json`) flags shots as clipped / crushed / dark /
   bright / flat / dull against the film's own median (black pillarbox bars are ignored).
6. **Grade.** Pick 2-3 reference frames with the look you want and extract them
   (`ffmpeg -v error -y -ss <t> -i <segment or source> -frames:v 1 <scratch_dir>/ref/ref1.jpg`), run
   `color_stats.py` on them (it reads images), and run `grade.js` on the flagged shots (args and the
   `shot` mapping are in REFERENCE.md). Apply each accepted filter (no `high` audit finding for that shot)
   as `grade`, keep the previous chain as `grade_prev` (the project's `default_grade` if the shot had
   none) and audit with `$PY $S/scripts/grade_compare.py --project ... --out <prefix>`.
7. **Preview, then render.** Iterate with `--draft --shots A-B --rev D1` (fast, up to 1080p, named after
   its real size, e.g. `<name>_draft1080p_RevD1_shotsA-B.mp4`) - it has the music the range will have in
   the full film; drop `--shots` for a draft of the whole film. Without `--draft`, `--shots A-B --rev P1`
   renders the full-quality master and send version of the range: keep that for final checks. Every
   render needs a new label (D2, P2 ... or another `--out-dir`). The full render `--rev N` writes the
   master, the send version, that revision's music credit text and `EDL_RevN.md` (next to the outputs
   when `--out-dir` is used) and refuses when the master, send version or credit of that revision exist.
8. **Review.** `$PY $S/scripts/review_prep.py --project ... --rev N` (with the same `--shots`, `--draft`
   and `--out-dir` as the render) writes the review material and prints the path of `manifest.json`;
   run `review.js` with that file's whole content as args plus `brief`, `facts`, `audience` (and
   `logsPath`). Only findings that survive the refute pass get fixed. Bump the revision for every change.
9. **Hand over.** Master + send version, the credit text for the post, `EDL_RevN.md`, and the list of
   open questions (rights, consent, facts the client must confirm).

## Hard-won rules

1. **Log before you storyboard.** Every candidate clip gets a shot log written from its timestamped
   contact sheets. Agents must Read the sheets; they never guess content from file names.
2. **Judge panel, then frame-verify.** Three storyboard angles, two judges with different lenses
   (viewer/editor vs facts/compliance), synthesis from the winner plus grafts, then every chosen shot is
   checked on frames extracted at its real in/mid/out times.
3. **Tell it in the order it happened** unless the client asks otherwise. Do not jump back and forth
   between places or states (e.g. "berthed" followed by "still berthing").
4. **On-screen text = verified facts only** (time, place, names). Prefer plain statements to slogans;
   a client who asks for a mood or a message does not want that word written on screen - show it with
   pictures. Never invent legal or company names: copy them from a source document.
5. **Transitions:** default to gradual cross-dissolves - clients often read hard cuts as "not natural".
   Avoid muddy double exposures by ordering shots so that similar compositions sit next to each other,
   and shorten dissolves between dissimilar shots or between shots of people to 0.5-0.7 s. Fade through
   black only for real time jumps. Ask the client before switching to hard cuts.
6. **Slow motion only at exactly 0.5, and only on sources at twice the master rate** (59.94/60p into a
   29.97/30 master). 0.75 or 0.8 drops frames unevenly and judders. Set `master.fps` from the footage:
   25 for a 25/50p library. The build refuses any shot whose frame rate does not map evenly unless the
   shot says `"allow_speed": true`.
7. **Measure shake.** Global optical-flow jitter is inflated when water or sky dominate (waves are
   features too) and by anything moving inside a fixed frame; confirm with subject tracking before
   blaming the camera. Stabilise handheld shots, replace the ones stabilisation cannot save.
8. **Colour:** measure per-shot stats, grade only the weak shots toward a reference look - close to,
   not equal to (an overcast day stays overcast) - and audit before/after for over-processing: skin,
   red-painted steel, greens, clipped skies.
9. **Audio:** two-pass loudness to -15 LUFS / -1.5 dBTP: measure, then one linear gain, with a
   true-peak limiter only when peaks need it, and 0.5 dB spare for the AAC encode; the build then measures
   the encoded master. Keep a meaningful source sound in sync, duck the music under it (to ~40 %), and cap
   its peak (~-9 dBFS in the finished film: the build sets it against the gain the music gets, so a quiet
   music bed does not lift it, and prints where it lands) so it is not jarring.
10. **Two deliverables:** a 4K master (libx264 CRF ~17) and a send version under the size cap
    (1080p, H.264 High@4.1, ref=4) that copies the master's AAC - never re-encode audio twice. The send
    version is never bigger than the master: its bitrate is capped at the master's.
11. **Rights and content checks:** no third-party footage without permission; CC BY music may be
    credited in the post text instead of on screen; scan frames for safety issues an industrial client
    would flag (hot work next to flammables, missing PPE, people under loads) and for readable personal
    data (badges, screens, documents, plates).
12. **Never overwrite a revision.** Bump `--rev`, keep a human-readable `EDL_RevN.md` and a music credit
    file per revision (different revisions may use different music).
13. **Pitfalls:** many ffmpeg builds have no drawtext, so text is rendered with PIL; xfade needs
    identical frame rates (force `fps` on every input and write exact rationals like 30000/1001);
    loudnorm's JSON sits mid-stderr with stats after it, and its second pass silently switches to dynamic
    mode when the loudness range exceeds the target (this repo measures with ebur128 and applies a plain
    gain instead); cached segments go stale when in/out, speed, grade, stabilisation, a still's move, the
    source file or a LUT file change - this repo keys the cache on those fields and on the size and date of
    the source and LUT files, so edited shots re-cut automatically (`--force` re-cuts anyway).
14. **Mixing stills with video.** Keep stills 3-4.5 s and do not run more than about three in a row (the
    build warns). Frame people with the crop centre (`kb` cx / cy) so no face is cut at any point of the
    move; crop out readable screens, whiteboards and clutter. Group photos back to back look like an album:
    break them up with shots of people at work. A still enlarged beyond ~1.5x looks soft (the build warns).
15. **Music can follow the story in parts** - e.g. rousing up to the milestone, calm after it. Put the
    switch on a visual turning point (a ship made fast, a door opening) and let the crossfade sit there.
    The credit travels with the film, so avoid track titles with religious, war or other negative meaning.
16. **Public films:** never show safety-non-compliant scenes (no hard hat on a working deck, hot work
    near flammables), cash gifts or readable personal names. Check every still for this as well - photos
    are taken with less care than footage.
17. **Caption reviews against a fact list flag real facts the list does not contain.** Before deleting
    a flagged caption, look for it in the source documents (drawings, delivery records, schedules); only
    delete what no document supports.

## edl.json

```json
{
  "title": {"lines": ["Main title", "Second language"], "date_line": "Spring 2026"},
  "music": {"parts": [
    {"track": "Rousing Theme", "end_music_t": 96.0, "to_shot": 12, "offset": 1.0, "xfade": 2.0},
    {"track": "Calm Theme", "end_music_t": 210.0, "gain_db": 2.0}]},
  "sections": [{"key": "dusk", "lines": ["Evening · Sunset", "Tarde · Atardecer"]}],
  "shots": [{"n": 1, "file": "dusk/berth_wide.mp4", "in_s": 2.0, "out_s": 7.0, "speed": 0.5,
             "transition_in": "fadein_from_black", "transition_s": 0, "caption_section": "dusk",
             "grade": "<ffmpeg filters>", "grade_prev": "<previous>",
             "stab": {"smooth_s": 1.0, "max_zoom": 1.10}, "source_audio": false, "desc": "..."},
            {"n": 2, "file": "photos/crew.jpg", "still": true, "dur_s": 3.6,
             "kb": {"from": [0.5, 0.45, 1.0], "to": [0.5, 0.42, 1.08]}, "transition_in": "dissolve",
             "transition_s": 0.7, "tag": {"lines": ["Crew briefing"], "span_s": 3.0}, "desc": "..."},
            {"n": 3, "file": "dusk/quay_lights.mp4", "in_s": 3.0, "out_s": 12.0,
             "transition_in": "dissolve", "transition_s": 1.0, "desc": "..."}],
  "closing": {"on_shot": 3, "offset": 1.5, "lines": ["Headline", "Second line"], "rule_after": 1},
  "endcard": {"line1": "Organisation", "line2": "Place · date"}
}
```

- **Shots:** `n` unique integers in film order; `file` relative to `library_dir`; `in_s` / `out_s` in source
  seconds (numbers, `in_s` >= 0); `speed` 1.0 (default) or 0.5 (`allow_speed: true` accepts anything else,
  and sources whose rate does not map evenly, with a warning); `transition_in` on every shot:
  `fadein_from_black` (first shot), `cut` (1-frame xfade), `dissolve`, `fadeblack` (out to black over the
  first half, up from black over the second); `transition_s` (seconds) on every non-cut shot after the
  first, shorter than both pieces it joins. Optional:
  `caption_section` (a `sections` key: a chapter caption opens on this shot; captions and tags wait for
  the title to clear, so one on shot 2 after a short shot 1 starts late, with a warning), `tag` (a place tag, `lines`;
  needs about 3.3 s of shot, or `span_s` seconds to run on across the next shots; it is skipped when the
  shot has a caption or the tag would overlap the closing statement; a closing statement that would
  overlap a chapter caption starts after it when 3 s of shot remain - `--check` and the build warn in
  every case, so put the closing on a shot without a caption; a caption never runs into a fade through
  black, the next chapter or the end card, and one that waited for earlier text ends with its shot - it is
  shortened or left out instead, with a warning; a tag or closing statement still on screen when a fade
  through black or a new chapter begins gets a warning too), `grade` / `grade_prev`, `stab`
  (`smooth_s` >= 0.1, `max_zoom` >= 1.0), `source_audio`, `fit` (`crop` default, `blur`, `pillarbox`
  for vertical sources), `tonemap: false` (use an HDR source as is).
- **Stills:** `still: true`, `file` a JPEG / PNG, `dur_s` its length, `kb.from` / `kb.to` =
  `[cx, cy, zoom]` (crop centre as fractions of the picture, zoom 1.0 = whole canvas; default a gentle
  centred push-in 1.0 -> 1.08) moved with smoothstep easing and sub-pixel sampling. Pictures 4:3 or wider
  are cover-cropped (the crop always has the output aspect, nothing is squashed); portrait and
  near-square ones sit on a blurred, darkened copy of themselves. `grade` is optional (stills get no
  `default_grade`); `speed`, `stab` and `source_audio` do not apply.
- **music** is required. One track: `track` (a key of project `music.tracks`) plus one of `start_s`
  (offset into the track), `end_at` (the track second where the film's last frame lands, so the film ends
  where the music ends) or `align: {"shot": n, "music_t": s}` (a musical moment lands on a shot's start).
  Several tracks: `parts`, each with `track` (or `file`, relative to `music.dir` or absolute), one of
  `start_s` / `end_music_t` (track second where this part ends) / `align`, `to_shot` + `offset` (the film
  moment where the next part takes over; not on the last part), `xfade` (equal-power crossfade centred on
  that moment, default 3 s), `gain_db`, `credit` (the text that replaces `{track}` in the credit file).
  The build checks every part fits inside its track before cutting.
- **title** `lines` (first = main title) and `date_line` (small line under the accent rule); **closing**
  `on_shot`, `offset` (seconds after that shot starts), `lines`, `rule_after` (how many lines go above the
  accent rule, default 1); **endcard** `line1` / `line2` (or `lines`). Write text blocks as `lines`, one
  per language, in any languages; older EDLs with one key per language are still read. The build stops
  when a line has a character the configured fonts cannot draw (it would show as a box): set
  `fonts.cjk` / `fonts.latin` for those scripts.
- Unknown keys (`concept`, `clip_id`, `section`, `why`, `est_total_s`, `notes`) are kept for humans and
  ignored by the build. Every mistake that can be found up front (wrong types or ranges, unknown section
  key, text without lines, missing glyphs, missing logo or font files, missing audio for `source_audio`,
  uneven speeds, unreadable stills, overlong transitions, music that does not fit its track, missing
  files, invalid grade filters) stops the build with an "EDL problems" list before anything is cut.

## project.json

See `config/project.example.json` and the field table in `REFERENCE.md` (meaning, unit, default, per-OS
font paths). Relative paths resolve against the project file.

## External auditor (optional)

The workflows default to Claude agents everywhere. To have a different model family review the text,
transitions, facts or grades, pass `auditorCmd`: a command template with `{prompt_file}`,
`{schema_file}` and `{images}` (set `auditorImageFlag`, e.g. `--image`, if the CLI wants one flag per
image). A forwarding agent writes the task and a strict JSON schema to files, runs the command and
returns its stdout JSON unchanged. The rest is an example contract, not a prerequisite - adapt your
wrapper to it: workflow agents are considered stalled after about three minutes without progress, so a
reviewer that can run longer must detach, print a job id on its last line, and come with `auditorWaitCmd`
(with `{job}`) that blocks at most ~150 s and exits 5 while the job is still running. Strict-schema APIs
need `additionalProperties: false` and every property listed in `required` at every object level; the
audit schemas here already comply. A failed auditor returns `AUDITOR-FAILED: ...`, which the workflows
log as not reviewed.

## Script reference

| script | does |
|---|---|
| `make_sheets.py` | candidates -> timestamped 4x4 contact sheets + `index.json` / `index_compact.json` (vertical / HDR flags, frame rates); accurate seeks when keyframes are sparse |
| `find_tonal_events.py` | horn / whistle finder: band 200-1200 Hz >= +9 dB over the clip's non-tonal median, >= 0.85 harmonic or chord, >= 0.8 s, f0 from harmonic spacing down to 50 Hz |
| `shake_meter.py` | global LK-flow jitter, `hf` (0.3 s) and `lf` (1.0 s) windows in px at 640 wide; files or folders |
| `subject_track.py` | template-tracked subject jitter for water/sky-dominated shots, with a match / texture / twin-template confidence |
| `stabilize.py` | smoothed trajectory (rotation about the centre, steady moves keep their speed) + the exact fixed zoom that hides the borders, exact rational fps, audio kept; warns when it does not help, leaves > 1 px or is held back by `--max-zoom` |
| `panzoom.py` | still -> segment: smoothstep pan-and-zoom move, cover crop or blurred backdrop (used by build_film) |
| `color_stats.py` | per-shot Lab / HSV statistics (incl. chroma) and weak-shot flags, pillarbox bars ignored; clips, folders or still images |
| `grade_compare.py` | before/after sheets from `grade_prev` (or `default_grade`) vs `grade` |
| `cards.py` | PIL title, chapter caption, place tag, closing statement, end card; on its own (`--project ... --out DIR`) renders every card of the EDL to look at before cutting |
| `build_film.py` | edl + project -> master + send version (`--rev`, `--shots A-B`, `--draft`, `--check`, `--cut-only`, `--force`) |
| `edl_check.py`, `text_layout.py`, `music.py`, `audio.py`, `common.py`, `deps.py` | modules used by build_film, not commands: validation (run it as `build_film.py --check`), text placement, music parts, soundtrack, shared helpers, dependency message |
| `review_prep.py` | per-second sheets, transition triplets, text frames, tech metrics, `manifest.json` (`--draft` for a draft render) |
| `edl_to_md.py` | `EDL_RevN.md` |
| `selftest.sh` | synthetic end-to-end test (with `selftest_media.py`, `selftest_checks.py`); prints the ffmpeg capabilities |
