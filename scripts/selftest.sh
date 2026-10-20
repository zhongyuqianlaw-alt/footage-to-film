#!/usr/bin/env bash
# End-to-end self-test on synthetic media only (ffmpeg test sources, generated tones, drawn pictures).
# Renders a 5-shot film (stabilised handheld, a 4:3 and a portrait still, a tag running across shots, a
# two-part music switch, a ship horn) and exercises every script. Exits non-zero on the first failure.
# env: PYTHON=/path/to/python3 (needs numpy, Pillow, opencv-python), FFMPEG / FFPROBE, KEEP=1 keeps the temp dir
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FFMPEG="${FFMPEG:-$(command -v ffmpeg || echo /opt/homebrew/bin/ffmpeg)}"
FFPROBE="${FFPROBE:-$(command -v ffprobe || echo /opt/homebrew/bin/ffprobe)}"
export FFMPEG FFPROBE PYTHONDONTWRITEBYTECODE=1

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok    $*"; }
pick_python() {
  for c in python3 /usr/bin/python3; do
    if "$c" -c "import numpy, PIL, cv2" 2>/dev/null; then echo "$c"; return 0; fi
  done
  return 1
}
if [ -n "${PYTHON:-}" ]; then
  "$PYTHON" -c "import numpy, PIL, cv2" 2>/dev/null || fail "PYTHON=$PYTHON cannot import numpy, Pillow and opencv-python"
  PY="$PYTHON"
else
  PY="$(pick_python)" || fail "need a python3 with numpy, Pillow and opencv-python (set PYTHON=...)"
fi
[ -x "$FFMPEG" ] || fail "ffmpeg not found (set FFMPEG=...)"
echo "== $("$FFMPEG" -version | head -1); python: $PY"
has() { "$FFMPEG" -hide_banner "$1" 2>/dev/null | grep -q " $2 " && echo yes || echo no; }
ZS=$(has -filters zscale)
echo "== capabilities: zscale (HDR tone mapping) $ZS, h264_videotoolbox $(has -encoders h264_videotoolbox)"
[ "$ZS" = yes ] || echo "WARNING: this ffmpeg cannot tone-map HDR clips (iPhone HLG, PQ): the build refuses them;" \
  "see SKILL.md Requirements for an ffmpeg with libzimg (then FFMPEG=... FFPROBE=...)" >&2

T="$(mktemp -d "${TMPDIR:-/tmp}/footage-to-film-selftest.XXXXXX")"
if [ "${KEEP:-0}" = 1 ]; then echo "work dir: $T"; else trap 'rm -rf "$T"' EXIT; fi
py() { "$PY" "$HERE/$1" "${@:2}"; }
check() {  # check LABEL NAME ARGS...: one function of selftest_checks.py; its traceback is the failure message
  local out
  out=$("$PY" "$HERE/selftest_checks.py" "${@:2}" 2>&1) || fail "$1: $out"
  ok "$1: $out"
}
loud() {  # loud FILE: integrated loudness within 1 LU of -15 and true peak <= -1.5 dBTP
  "$PY" - "$1" <<'EOF'
import os, subprocess, sys
err = subprocess.run([os.environ["FFMPEG"], "-hide_banner", "-nostats", "-i", sys.argv[1], "-vn", "-af", "ebur128=peak=true",
                      "-f", "null", "-"], capture_output=True, text=True).stderr
tail = err[err.rindex("Summary:"):]
lufs, peak = float(tail.split("I:")[1].split("LUFS")[0]), float(tail.split("Peak:")[1].split("dBFS")[0])
assert abs(lufs + 15) <= 1.0 and peak <= -1.5, (lufs, peak)
print(f"{lufs} LUFS, true peak {peak} dBTP")
EOF
}

echo "== workflow scripts parse (export stripped, wrapped as the Workflow tool's async function)"
if command -v node >/dev/null 2>&1; then
  node -e '
const fs = require("fs")
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
for (const f of process.argv.slice(1)) {
  try { new AsyncFunction("args", "agent", "parallel", "pipeline", "phase", "log", fs.readFileSync(f, "utf8").replace(/^export\s+/gm, "")) }
  catch (e) { console.error(f + ": " + e.message); process.exitCode = 1 }
}' "$HERE"/../workflows/*.js || fail "a workflow script does not parse"
  ok "workflows/*.js parse"
else
  ok "workflow syntax check skipped: node not found"
fi

echo "== friendly message when a python3 lacks the packages"
if ! "$PY" -S -c "import numpy" 2>/dev/null; then
  if "$PY" -S "$HERE/shake_meter.py" none.mp4 > "$T/nodeps.log" 2>&1; then fail "missing numpy must stop the script"; fi
  grep -q "requirements.txt" "$T/nodeps.log" && ! grep -q Traceback "$T/nodeps.log" || fail "no friendly message: $(cat "$T/nodeps.log")"
  "$PY" -S "$HERE/build_film.py" --help > "$T/nodeps_help.log" 2>&1 && grep -q "usage:" "$T/nodeps_help.log" \
    || fail "--help without the packages: $(head -3 "$T/nodeps_help.log")"
  ok "missing numpy / opencv gives an install hint, not a traceback; --help still works"
else
  ok "dependency message check skipped: numpy loads even without site-packages"
fi

echo "== synthetic media in $T"
py selftest_media.py "$T"
PROJ=(--project "$T/project.json")
check "units" units
check "text at fades through black / chapter breaks" text_breaks
check "fade through black" fade_black

echo "== helper and library scripts on the command line"
(cd "$T" && "$PY" "$HERE/selftest_media.py" --help > "$T/media_help.log" 2>&1) \
  || fail "selftest_media.py --help must exit 0: $(tail -2 "$T/media_help.log")"
grep -q "usage:" "$T/media_help.log" && [ ! -e "$T/--help" ] \
  || fail "selftest_media.py --help must print usage and create nothing: $(head -2 "$T/media_help.log")"
for a in --help no_such_check; do
  if py selftest_checks.py "$a" > "$T/checks_help.log" 2>&1; then fail "selftest_checks.py $a must exit non-zero"; fi
  grep -q "checks: units" "$T/checks_help.log" && ! grep -q Traceback "$T/checks_help.log" \
    || fail "selftest_checks.py $a: $(tail -2 "$T/checks_help.log")"
done
for m in audio music common text_layout deps; do
  for args in "" --help; do
    rc=0; py "$m.py" $args > "$T/lib_$m.log" 2>&1 || rc=$?
    [ "$rc" = 2 ] && grep -q "is a library module used by build_film.py" "$T/lib_$m.log" \
      || fail "$m.py $args run directly: exit $rc, $(head -1 "$T/lib_$m.log")"
  done
done
ok "selftest_media.py --help prints usage; selftest_checks.py lists its checks; library modules say so (exit 2)"

echo "== contact sheets, tonal events"
py make_sheets.py "$T/candidates.txt" --library "$T/lib" --out "$T/sheets" --jobs 4 >/dev/null
check "make_sheets" sheets "$T/sheets/index.json"
py find_tonal_events.py --json "$T/lib/day1/horn.mp4" "$T/horn_70hz.wav" "$T/horn_upper.wav" > "$T/tonal.jsonl"
check "find_tonal_events" tonal "$T/tonal.jsonl"

echo "== shake, subject tracking, stabilisation"
py shake_meter.py "$T/lib/day1/handheld.mp4" "$T/lib/day1/aerial_60p.mp4" > "$T/shake.jsonl"
check "shake_meter" shake "$T/shake.jsonl"
py stabilize.py "$T/lib/day1/handheld.mp4" "$T/stab.mp4" --smooth 1.0 --max-zoom 1.10 > "$T/stab.log"
check "stabilize" stab "$T/stab.log"
[ "$("$FFPROBE" -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$T/stab.mp4")" = "30000/1001" ] \
  || fail "stabilize must keep the exact rational frame rate"
py stabilize.py "$T/roll.mp4" "$T/stab_roll.mp4" > "$T/stab_roll.log"
for cx in 0.5 0.2; do
  py subject_track.py "$T/roll.mp4" --cx $cx --cy 0.5 >> "$T/roll_subject.jsonl"
  py subject_track.py "$T/stab_roll.mp4" --cx $cx --cy 0.5 >> "$T/roll_subject.jsonl"
done
check "stabilize (roll) + subject_track" roll "$T/stab_roll.log" "$T/roll_subject.jsonl"
py subject_track.py "$T/flat_twin.mp4" --cx 0.336 --cy 0.396 > "$T/flat_twin.jsonl"
check "subject_track (twin on a flat patch)" flat_twin "$T/flat_twin.jsonl" "$T/roll_subject.jsonl"
py stabilize.py "$T/pan.mp4" "$T/stab_pan.mp4" > /dev/null
check "stabilize (pan)" pan "$T/pan.mp4" "$T/stab_pan.mp4"
if "$FFMPEG" -v error -y -display_rotation 90 -i "$T/pan.mp4" -c copy "$T/pan_rot.mp4" 2>/dev/null; then
  py stabilize.py "$T/pan_rot.mp4" "$T/stab_rot.mp4" > /dev/null
  check "stabilize (rotation metadata)" rotated "$T/pan_rot.mp4" "$T/stab_rot.mp4"
fi
py stabilize.py "$T/lib/day1/horn.mp4" "$T/stab_horn.mp4" > /dev/null 2> "$T/stab_horn.err"
check "stabilize (audio)" has_audio "$T/stab_horn.mp4"
grep -q "did not help" "$T/stab_horn.err" || fail "stabilize should warn on a clip whose motion is its content"
ok "stabilize warns when the jitter does not drop (moving test pattern)"
if py stabilize.py "$T/lib/day1/horn.mp4" "$T/missing_dir/out.mp4" > "$T/stab_dir.log" 2>&1; then fail "missing output dir"; fi
grep -q "does not exist" "$T/stab_dir.log" || fail "stabilize: no clear message for a missing output folder"
ok "stabilize checks the output folder before the trajectory pass"
if py stabilize.py "$T/pan.mp4" "$T/stab_pan.mp4" > "$T/stab_exists.log" 2>&1; then fail "stabilize overwrote an existing output"; fi
grep -q "pass --force" "$T/stab_exists.log" || fail "stabilize: no clear message for an existing output"
py stabilize.py "$T/lib/day1/handheld.mp4" "$T/stab_held.mp4" --max-zoom 1.01 > "$T/stab_held.log" 2> "$T/stab_held.err"
check "stabilize (max_zoom too small)" held "$T/stab_held.log" "$T/stab_held.err"
if py subject_track.py "$T/pan.mp4" --ss 60 > "$T/subj_late.log" 2>&1; then fail "subject_track past the end must exit 1"; fi
grep -q "^error:" "$T/subj_late.log" || fail "subject_track past the end: $(tail -1 "$T/subj_late.log")"
ok "stabilize refuses an existing output without --force; subject_track --ss past the end is an error"
py panzoom.py "$T/lib/day1/photo_tall.jpg" "$T/kb.mp4" 1.0 --size 320x180 --to 0.5,0.4,1.1 > /dev/null
ok "panzoom.py on its own"

echo "== command-line arguments out of range are refused"
refuse() {  # refuse SCRIPT ARGS...: argparse exit 2 with a message, no traceback
  local rc=0
  py "$@" > "$T/arg.log" 2>&1 || rc=$?
  [ "$rc" = 2 ] && grep -q "error:" "$T/arg.log" && ! grep -q Traceback "$T/arg.log" || fail "$*: $(tail -2 "$T/arg.log")"
}
refuse find_tonal_events.py --band 1200 200 "$T/lib/day1/horn.mp4"
refuse find_tonal_events.py --band 9000 12000 "$T/lib/day1/horn.mp4"
refuse make_sheets.py "$T/candidates.txt" --library "$T/lib" --out "$T/sheets0" --frames 0
refuse stabilize.py --max-zoom 0.9 "$T/pan.mp4" "$T/z.mp4"
refuse stabilize.py --smooth 0 "$T/pan.mp4" "$T/z.mp4"
refuse subject_track.py "$T/pan.mp4" --cx 1.5
ok "6 out-of-range arguments refused"

echo "== EDL validation runs before any cutting"
edge() {  # edge NAME 'python expression editing e' EXPECTED_MESSAGE [extra build args]
  "$PY" -c "import json,sys; e=json.load(open(sys.argv[1])); $2; json.dump(e, open(sys.argv[2], 'w'))" "$T/build/edl.json" "$T/build/edge_$1.json"
  if py build_film.py "${PROJ[@]}" --edl "$T/build/edge_$1.json" --rev E1 --out-dir "$T/edge_out" "${@:4}" > "$T/edge_$1.log" 2>&1; then
    fail "edge case $1 should have been refused"
  fi
  grep -qF -- "$3" "$T/edge_$1.log" || fail "edge case $1: expected '$3', got: $(tail -3 "$T/edge_$1.log")"
  ! grep -q Traceback "$T/edge_$1.log" || fail "edge case $1 crashed instead of a clean message: $(tail -4 "$T/edge_$1.log")"
}
edge long_dissolve "e['shots'][4]['transition_s']=3.95" "must be shorter than both pieces"
edge bad_section "e['shots'][1]['caption_section']='nope'" "is not a key in sections"
edge no_audio "e['shots'][0]['source_audio']=True; e['shots'][0]['speed']=1.0" "has no audio stream"
edge music_late "e['music']['parts'][1]['end_music_t']=70" "past the end of the track"
edge music_switch "e['music']['parts'][0]['to_shot']=99" "to_shot must be"
edge bad_align "e['music']={'track': 'Example Theme', 'align': {'shot': 99, 'music_t': 10}}" "music.align must be"
edge reversed "pass" "film order" --shots 3-2
edge no_text "e['title']={'date_line': 'only a date'}" "no text found"
edge no_glyph "e['sections'][0]['lines'][1]='港口'" "have no glyph"
edge judder "e['shots'][1]['speed']=0.8" "allow_speed"
edge negative_in "e['shots'][0]['in_s']=-1" "in_s must be a number"
edge text_out "e['shots'][4]['out_s']='end'" "out_s must be a number"
edge bad_kb "e['shots'][2]['kb']={'from': [0.5, 0.5, 0.9]}" "kb.from must be"
edge not_picture "e['shots'][3]['file']='day1/not_a_picture.jpg'" "not a picture"
edge still_stab "e['shots'][3]['stab']={'smooth_s': 1.0}" "not available for a still"
edge bad_rev "pass" "--rev '../x'" --rev "../x"
[ ! -d "$T/edge_out" ] || [ -z "$(ls -A "$T/edge_out")" ] || fail "refused EDLs must not write outputs"
ok "16 broken EDLs / arguments refused up front with a clean message"
"$PY" -c "import json,sys; p=json.load(open(sys.argv[1])); p['logos'].append({'path': 'no_such_logo.png'}); p['fonts']={'latin': 'no_such_font.ttf'}; json.dump(p, open(sys.argv[2], 'w'))" \
  "$T/project.json" "$T/project_assets.json"
if py build_film.py --project "$T/project_assets.json" --check > "$T/assets.log" 2>&1; then fail "a missing logo / font must stop --check"; fi
grep -q "logos: cannot read" "$T/assets.log" && grep -q "fonts.latin: font file not found" "$T/assets.log" \
  && ! grep -q Traceback "$T/assets.log" || fail "missing logo / font message: $(tail -3 "$T/assets.log")"
ok "a missing logo or font file stops --check before anything is cut"
"$PY" -c "import json,sys; e=json.load(open(sys.argv[1])); s=e['shots'][1]; s.update(speed=0.8, allow_speed=True); s.pop('stab'); json.dump(e, open(sys.argv[2], 'w'))" \
  "$T/build/edl.json" "$T/build/allow.json"
py build_film.py "${PROJ[@]}" --edl "$T/build/allow.json" --cut-only --shots 2-2 --scratch-dir "$T/cache_allow" > "$T/allow.log" 2>&1 \
  || fail "allow_speed should accept the shot: $(tail -3 "$T/allow.log")"
grep -q "accepted: allow_speed" "$T/allow.log" || fail "allow_speed should still warn"
ok "allow_speed accepts an uneven speed with a warning"

echo "== cut segments, colour stats, stills"
py build_film.py "${PROJ[@]}" --cut-only > "$T/cut.log"
[ "$(grep -c . "$T/cache/segments.txt")" = 5 ] || fail "build_film --cut-only: expected 5 segments"
py color_stats.py "$T/cache/segments" --out "$T/color.json" >/dev/null
py color_stats.py "$T/plain.mp4" "$T/pillar.mp4" --out "$T/pillar.json" >/dev/null
check "color_stats" color "$T/color.json" "$T/pillar.json"
check "pan-and-zoom stills" stills "$T/cache/segments"

echo "== full render Rev1"
py edl_to_md.py "${PROJ[@]}" --rev 1 > /dev/null
py cards.py "${PROJ[@]}" --out "$T/cards_only" > /dev/null
[ "$(ls "$T/cards_only" | grep -c '\.png$')" = 6 ] || fail "cards.py on its own: $(ls "$T/cards_only")"
ok "cards.py on its own renders the 6 cards of the EDL"
py build_film.py "${PROJ[@]}" --rev 1 > "$T/build1.log" 2>&1 || { tail -30 "$T/build1.log"; fail "build_film --rev 1"; }
M="$T/out/selftest_film_master540p_Rev1.mp4"; S="$T/out/selftest_film_send360p_Rev1.mp4"
check "full render (an EDL plan written before it did not block it)" render "$M" "$S" "$T"
r=$(loud "$S" 2>&1) || fail "loudness of the send version: $r"
ok "send loudness: $r"
grep -q "Second Theme: track" "$T/build1.log" || fail "the music plan is not printed"
check "two-part music" music "$M"
check "source sound level" source_peak "$M" "$T/build1.log"

if py build_film.py "${PROJ[@]}" --rev 1 > "$T/build1b.log" 2>&1; then fail "re-rendering an existing revision must refuse"; fi
grep -q "bump --rev" "$T/build1b.log" || fail "refusal message missing"
: > "$T/out/music_credit_Rev2.txt"
if py build_film.py "${PROJ[@]}" --rev 2 > "$T/build2.log" 2>&1; then fail "an existing credit of Rev2 must refuse"; fi
grep -q "music_credit_Rev2.txt exists" "$T/build2.log" || fail "credit refusal message missing: $(tail -2 "$T/build2.log")"
py edl_to_md.py "${PROJ[@]}" --rev 1 > "$T/md.log" 2>&1
grep -q "is kept" "$T/md.log" || fail "edl_to_md must keep the EDL of a rendered revision"
ok "revisions are never overwritten (master, per-revision credit, rendered EDL_RevN.md)"

echo "== previews: older EDL text keys, draft naming"
py build_film.py "${PROJ[@]}" --edl "$T/build/edl_legacy.json" --rev P1 --shots 2-3 > "$T/preview.log" 2>&1 \
  || { tail -20 "$T/preview.log"; fail "preview with per-language text keys"; }
check "preview --shots 2-3" preview "$T/out/selftest_film_master540p_RevP1_shots2-3.mp4"
r=$(loud "$T/out/selftest_film_master540p_RevP1_shots2-3.mp4" 2>&1) || fail "loudness of the preview: $r"
ok "preview loudness: $r"
py build_film.py "${PROJ[@]}" --rev D1 --draft --shots 4-5 > "$T/draft.log" 2>&1 || { tail -20 "$T/draft.log"; fail "draft"; }
[ -s "$T/out/selftest_film_draft540p_RevD1_shots4-5.mp4" ] || fail "draft should be named after its size: $(ls "$T/out")"
ok "draft named selftest_film_draft540p_RevD1_shots4-5.mp4"

echo "== grade compare, EDL markdown, review prep"
py grade_compare.py "${PROJ[@]}" --out "$T/grade/compare" > /dev/null
[ -s "$T/grade/compare_1.jpg" ] || fail "grade_compare"
py edl_to_md.py "${PROJ[@]}" --rev 1 --force > /dev/null
grep -q "| 5 |" "$T/build/EDL_Rev1.md" || fail "edl_to_md"
py review_prep.py "${PROJ[@]}" --rev 1 > /dev/null
check "review_prep" review "$T/cache/review_Rev1/manifest.json"

echo "PASS: all footage-to-film self-tests"
