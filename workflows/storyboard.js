export const meta = {
  name: 'footage-to-film-storyboard',
  description: 'Log every candidate clip from timestamped contact sheets, draft 3 storyboards, judge with 2 lenses, synthesize an EDL, frame-verify every shot, repair rejects',
  whenToUse: 'After make_sheets.py has produced contact sheets for the candidate clips of a short film',
  phases: [
    { title: 'Log', detail: 'shot logs from timestamped contact sheets' },
    { title: 'Storyboard', detail: '3 independent storyboard angles' },
    { title: 'Judge', detail: '2 judges with different lenses' },
    { title: 'Synthesize', detail: 'final EDL from the winner plus grafts' },
    { title: 'Verify', detail: 'frame-check every EDL shot at its real timestamps' },
    { title: 'Repair', detail: 'replace rejected shots and re-verify' },
  ],
}

// args (all project specifics live here, nothing is hard-coded; full table in REFERENCE.md):
//   libraryDir, sheetsDir, workDir   absolute paths ('~' expanded): project library_dir, make_sheets --out, a scratch folder
//   indexPath | clips                <sheetsDir>/index_compact.json (absolute), or its rows passed inline
//                                    rows = [id, file, duration, res, fps, step_s, n_sheets, flags]
//   brief, facts, audience           strings; facts = the ONLY statements allowed on screen
//   constraints                      array of extra hard requirements (rights, must-use / never-use shots,
//                                    how to treat vertical and HDR clips)
//   languages                        e.g. ['en', 'es']: one caption line per language, in this order (default ['en'])
//   targetSeconds [min, max]         default [170, 190] (a 3-minute film); e.g. [110, 130] for 2 minutes
//   endcardSeconds, endcardXfade     must equal project.json timing.endcard_s / endcard_xfade (default 6 / 1.2)
//   masterFps                        project.json master.fps (default '30000/1001'), for the frame-rate rules
//   musicOptions                     required: [{track, seconds, mood}]; track must be a key of project.json music.tracks
//   subjects                         optional enum for shot logging, e.g. ['aerial', 'ship', 'people']
//   stills                           optional photos the film may use: [{file, desc}], file relative to libraryDir
//   angles, judges                   optional overrides [{key, name, text}] / [{key, lens}]
//   auditorCmd, auditorWaitCmd, auditorImageFlag, forwarderModel   optional external reviewer (see SKILL.md)
const A = args || {}
const missingArgs = ['libraryDir', 'sheetsDir', 'workDir'].filter(k => !A[k])
if (!A.clips && !A.indexPath) missingArgs.push('indexPath (or clips)')
if (!(A.musicOptions || []).length) missingArgs.push('musicOptions (the licensed tracks in project.json music.tracks)')
if (missingArgs.length) throw new Error(`storyboard.js: missing args: ${missingArgs.join(', ')} (see REFERENCE.md)`)
const notAbsolute = ['libraryDir', 'sheetsDir', 'workDir', 'indexPath'].filter(k => A[k] && !/^(\/|[A-Za-z]:[\\/])/.test(A[k]))
if (notAbsolute.length) throw new Error(`storyboard.js: not an absolute path ('~' is not expanded here; agents Read files under it): ${notAbsolute.map(k => `${k}=${A[k]}`).join(', ')}`)
const LIB = A.libraryDir
const WORK = A.workDir
const LANGS = (A.languages || ['en']).join(' / ')
const [TMIN, TMAX] = A.targetSeconds || [170, 190]
const END_S = A.endcardSeconds ?? 6
const END_X = A.endcardXfade ?? 1.2
const FPS = A.masterFps || '30000/1001'
const MUSIC = A.musicOptions || []
const STILLS = A.stills || []

const HOUSE = `
House rules (hard):
1. Story order: tell events in the order they happened unless the brief says otherwise. Never jump back and forth between places or states (e.g. "berthed" followed by "still berthing").
2. Transitions: default to 'dissolve' (clients read hard cuts as unnatural). Order shots so similar compositions sit next to each other; between dissimilar shots or shots of people keep the dissolve at 0.5-0.7 s, between similar ones up to 1.0 s; 'fadeblack' (~1.2 s) only for a real time jump; 'cut' only if the brief asks for it; the first shot is 'fadein_from_black'.
3. Speed: the master runs at ${FPS} fps. 0.5 only on sources at exactly twice that rate (59.94/60p into a 29.97/30 master, 50p into a 25 master), otherwise 1.0. Never 0.75 or 0.8 (uneven frame drops, judder). At 1.0 a source must run at the master rate or a whole multiple of it (29.97 or 59.94 into 29.97; 25 or 50 into 25): skip clips at other rates (25/50p or 23.976/24p in a 29.97 master, 29.97/30p in a 25 master) unless a constraint allows them, and list any you keep in notes - the build refuses them without "allow_speed". Film length of a shot = (out_s - in_s) / speed.
4. On-screen text: only statements backed by the facts list below; plain statements beat slogans; never invent names, numbers, dates or legal/company names.
5. Shots are usually 3-9 s (opening / closing wides up to 12 s); no near-identical framings back to back.
6. Reject: shaky or tilted handheld, blown highlights, screen / remote-control close-ups, heavy fisheye, readable personal data, anything an industrial client would flag as unsafe (e.g. hot work next to flammables, missing PPE), third-party footage without permission. The film is landscape 16:9: a clip flagged "vertical" is only usable if the brief allows it, and then set "fit": "blur" or "pillarbox" on that shot; a clip flagged "hdr" needs tone mapping (the build handles it or stops with a message).
7. Captions: chapter captions and place tags are one line per language in this order: ${LANGS}.
8. Estimated total = sum(film lengths) - sum(transition seconds of shots 2..n, cut = 0.03) + ${END_S} s end card - ${END_X} s; target ${TMIN}-${TMAX} s.
9. Stills (only from the stills list): a shot with still=true, dur_s 3-4.5 (its film length), kb {from, to} = [cx, cy, zoom] crop centre as fractions of the picture and zoom 1.0-1.2, framed so no face is cut at any point and readable screens, whiteboards and clutter stay out; no more than three stills in a row and never group photo after group photo: break them with shots of people at work. In public films no cash gifts, readable personal names or people without required protective equipment, in stills as in footage.`

const BRIEF = `${A.brief || ''}
Audience: ${A.audience || 'general'}
${(A.constraints || []).map((c, i) => `Constraint ${i + 1}: ${c}`).join('\n')}
Music options (pick one, give start_s and the reason): ${JSON.stringify(MUSIC)}
${STILLS.length ? `Stills you may use (file as written here, relative to ${LIB}; Read the picture before choosing it): ${STILLS.map(s => `${s.file} - ${s.desc || ''}`).join(' ; ')}` : 'No stills: every shot is a clip.'}
${HOUSE}

Facts (the only facts allowed on screen):
${A.facts || '(none given: keep on-screen text to the title and places visible in the file names)'}`

const S_TEXT = { type: 'object', properties: { lines: { type: 'array', items: { type: 'string' } } }, required: ['lines'] }
const S_TEXT_NULL = { ...S_TEXT, type: ['object', 'null'] }
const LOG_SCHEMA = {
  type: 'object', required: ['clips'],
  properties: { clips: { type: 'array', items: {
    type: 'object', required: ['id', 'summary', 'overall_quality', 'issues', 'shots'],
    properties: {
      id: { type: 'string' }, summary: { type: 'string' },
      overall_quality: { type: 'integer', minimum: 1, maximum: 5 },
      issues: { type: 'array', items: { type: 'string' } },
      shots: { type: 'array', items: {
        type: 'object', required: ['in_s', 'out_s', 'desc', 'subject', 'shot_type', 'motion', 'quality', 'hero', 'problems'],
        properties: {
          in_s: { type: 'number' }, out_s: { type: 'number' }, desc: { type: 'string' },
          subject: A.subjects ? { type: 'string', enum: A.subjects } : { type: 'string' },
          shot_type: { type: 'string', enum: ['extreme_wide', 'wide', 'medium', 'close', 'pov'] },
          motion: { type: 'string' }, quality: { type: 'integer', minimum: 1, maximum: 5 },
          hero: { type: 'boolean' }, problems: { type: 'string' },
        } } },
    } } } },
}
const SHOT = {
  type: 'object',
  required: ['n', 'clip_id', 'file', 'in_s', 'out_s', 'section', 'desc', 'transition_in', 'transition_s', 'speed', 'caption_section', 'tag', 'source_audio', 'why'],
  properties: {
    n: { type: 'integer' }, clip_id: { type: 'string' }, file: { type: 'string' },
    in_s: { type: 'number' }, out_s: { type: 'number' }, section: { type: 'string' }, desc: { type: 'string' },
    transition_in: { type: 'string', enum: ['fadein_from_black', 'cut', 'dissolve', 'fadeblack'] },
    transition_s: { type: 'number' }, speed: { type: 'number', enum: [0.5, 1] },
    caption_section: { type: ['string', 'null'], description: 'section key whose chapter caption starts on this shot' },
    tag: S_TEXT_NULL, source_audio: { type: 'boolean' }, why: { type: 'string' },
    fit: { type: 'string', enum: ['crop', 'blur', 'pillarbox'], description: 'only for vertical sources the brief allows' },
    still: { type: 'boolean', description: 'true for a picture from the stills list (then in_s 0, out_s = dur_s, speed 1)' },
    dur_s: { type: 'number' },
    kb: { type: 'object', properties: { from: { type: 'array', items: { type: 'number' } }, to: { type: 'array', items: { type: 'number' } } } },
  },
}
const EDL_SCHEMA = {
  type: 'object',
  required: ['concept', 'title', 'music', 'sections', 'shots', 'closing', 'endcard', 'est_total_s', 'notes'],
  properties: {
    concept: { type: 'string' },
    title: { type: 'object', required: ['lines', 'date_line'], properties: { lines: { type: 'array', items: { type: 'string' } }, date_line: { type: 'string' } } },
    music: { type: 'object', required: ['track', 'start_s', 'reason'], properties: { track: { type: 'string', enum: MUSIC.map(m => m.track) }, start_s: { type: 'number' }, reason: { type: 'string' } } },
    sections: { type: 'array', items: { type: 'object', required: ['key', 'lines'], properties: { key: { type: 'string' }, lines: { type: 'array', items: { type: 'string' } } } } },
    shots: { type: 'array', items: SHOT },
    closing: { type: ['object', 'null'], required: ['on_shot', 'offset', 'lines'], properties: { on_shot: { type: 'integer' }, offset: { type: 'number' }, lines: { type: 'array', items: { type: 'string' } } } },
    endcard: { type: 'object', required: ['line1', 'line2'], properties: { line1: { type: 'string' }, line2: { type: 'string' } } },
    est_total_s: { type: 'number' }, notes: { type: 'string' },
  },
}
// proposal / winner may only name proposals that exist (built once the storyboards are in)
const judgeSchema = keys => {
  const key = { type: 'string', enum: keys }
  const score = {
    type: 'object', additionalProperties: false,
    required: ['proposal', 'brief_fit', 'flow_and_transitions', 'shot_quality', 'caption_accuracy', 'pacing_music', 'strengths', 'weaknesses'],
    properties: {
      proposal: key, brief_fit: { type: 'integer' }, flow_and_transitions: { type: 'integer' },
      shot_quality: { type: 'integer' }, caption_accuracy: { type: 'integer' }, pacing_music: { type: 'integer' },
      strengths: { type: 'string' }, weaknesses: { type: 'string' },
    },
  }
  return {
    type: 'object', additionalProperties: false, required: ['scores', 'winner', 'grafts', 'factual_problems'],
    properties: { scores: { type: 'array', items: score }, winner: key, grafts: { type: 'array', items: { type: 'string' } }, factual_problems: { type: 'array', items: { type: 'string' } } },
  }
}
const VERIFY_SCHEMA = {
  type: 'object', required: ['results'],
  properties: { results: { type: 'array', items: {
    type: 'object', required: ['n', 'verdict', 'new_in_s', 'new_out_s', 'observed', 'problems'],
    properties: { n: { type: 'integer' }, verdict: { type: 'string', enum: ['ok', 'adjust', 'replace'] }, new_in_s: { type: ['number', 'null'] }, new_out_s: { type: ['number', 'null'] }, observed: { type: 'string' }, problems: { type: 'string' } },
  } } },
}

// ---------- optional external auditor (forwarder pattern) ----------
const fill = (tpl, vars) => tpl.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m))
const imageArgs = imgs => imgs.map(i => `${A.auditorImageFlag ? A.auditorImageFlag + ' ' : ''}'${i}'`).join(' ')
const forwardPrompt = (task, schema, images, tag) => {
  const pf = `${WORK}/audit/${tag}.prompt.md`, sf = `${WORK}/audit/${tag}.schema.json`
  const start = fill(A.auditorCmd, { prompt_file: pf, schema_file: sf, images: imageArgs(images) })
  const poll = A.auditorWaitCmd
    ? `The command prints a job id on its last stdout line. Poll with one Bash call at a time (each must return within 150 s): ${fill(A.auditorWaitCmd, { job: '<job id>' })}; echo "EXIT=$?"  - EXIT=5 means still running: repeat, at most 30 times.`
    : 'Run it with a Bash timeout of 170000 ms (a call that blocks longer stalls the workflow; long reviewers need auditorWaitCmd).'
  return `You are a forwarding shell. Do not review anything yourself and do not read the files.
1) mkdir -p ${WORK}/audit, then write the task text between the markers to ${pf} with the Write tool, unchanged:
<<<TASK
${task}
TASK>>>
2) Write this JSON schema to ${sf} unchanged: ${JSON.stringify(schema)}
3) Run: ${start}
   ${poll}
4) On success, return the reviewer's stdout JSON unchanged via StructuredOutput. On failure, return a schema-valid object: every free-text string field and every array of strings holds "AUDITOR-FAILED: <last stderr line>" (as the single item of such an array), enum fields take their first allowed value, other arrays are empty.`
}
const auditor = (task, schema, images, tag, opts) => A.auditorCmd
  ? agent(forwardPrompt(task, schema, images, tag), { ...opts, agentType: 'general-purpose', effort: 'low', schema, ...(A.forwarderModel ? { model: A.forwarderModel } : {}) })
  : agent(`${task}${images.length ? `\nImages to Read: ${images.join(' , ')}` : ''}`, { ...opts, schema })

// ---------- Log ----------
phase('Log')
const rows = A.clips || (await agent(`Read ${A.indexPath} with the Read tool and return its rows unchanged as "rows" (each row = [id, file, duration, res, fps, step_s, n_sheets, flags]).`,
  { label: 'load-index', phase: 'Log', effort: 'low', schema: { type: 'object', required: ['rows'], properties: { rows: { type: 'array', items: { type: 'array' } } } } }))?.rows
if (!Array.isArray(rows)) throw new Error(`storyboard.js: could not read the clip index ${A.indexPath}`)
const all = rows.map(([id, file, duration, res, fps, step_s, n, flags]) => ({ id, file, duration, res, fps, step_s, flags: flags || '',
  sheets: Array.from({ length: n || 0 }, (_, k) => `${A.sheetsDir}/${id}/sheet_${String(k + 1).padStart(2, '0')}.jpg`) }))
const index = all.filter(c => c.sheets.length)
if (index.length < all.length) log(`WARNING: ${all.length - index.length} clips have no contact sheets and are skipped: ${all.filter(c => !c.sheets.length).map(c => `${c.id} ${c.file}`).join(', ')}`)
if (!index.length) throw new Error('storyboard.js: no clip has contact sheets; run make_sheets.py first')
const batches = []
let cur = [], nSheets = 0
for (const c of index) {
  if (cur.length && (nSheets + c.sheets.length > 9 || cur.length >= 8)) { batches.push(cur); cur = []; nSheets = 0 }
  cur.push(c); nSheets += c.sheets.length
}
if (cur.length) batches.push(cur)
log(`${index.length} clips in ${batches.length} logging batches`)

const logPrompt = b => `You are writing a shot log for a short film. Footage library: ${LIB}
${A.brief || ''}
Each clip has contact sheets; the yellow mm:ss in each tile is that frame's real time in the clip (keyframes, about step_s apart).
Read EVERY sheet of every clip with the Read tool before writing anything; never guess content.
Split each clip into shots (continuous ranges with one subject / camera position / move). For each give in_s/out_s (from the sheet timestamps), a concrete description (what is visible, framing, light, weather), subject, shot_type, motion (slow push-in / orbit / pan left / static / handheld walk ...), quality 1-5 (sharpness, shake, tilted horizon, exposure, occlusion, fisheye, screen close-ups), hero (could carry the film) and problems.
Events and places in file names were named by the owner and can be trusted; do not invent anything the frames do not show.
Clips:
${b.map(c => `- id=${c.id} file=${c.file} duration=${c.duration}s res=${c.res} fps=${c.fps} step_s=${c.step_s}${c.flags ? ` flags=${c.flags}` : ''}\n  sheets: ${c.sheets.join(' , ')}`).join('\n')}`

const logs = (await parallel(batches.map((b, i) => () =>
  agent(logPrompt(b), { label: `log:${i + 1}/${batches.length}`, phase: 'Log', schema: LOG_SCHEMA })))).filter(Boolean).flatMap(r => r.clips)
const byId = Object.fromEntries(index.map(c => [c.id, c]))
const missing = index.filter(c => !logs.find(l => l.id === c.id)).map(c => c.id)
if (missing.length) log(`WARNING: ${missing.length} clips not logged: ${missing.join(',')}`)
if (missing.length * 2 > index.length) throw new Error(`storyboard.js: only ${index.length - missing.length} of ${index.length} clips were logged (not logged: ${missing.join(',')}); storyboarding on so few logs would waste the next stages - check the sheets paths and rerun`)
const LOGS_JSON = JSON.stringify(logs.map(l => ({
  id: l.id, file: byId[l.id]?.file, dur: byId[l.id]?.duration, res: byId[l.id]?.res, fps: byId[l.id]?.fps, flags: byId[l.id]?.flags,
  sheets: byId[l.id]?.sheets, q: l.overall_quality, summary: l.summary, issues: l.issues,
  shots: l.shots.map(s => [s.in_s, s.out_s, s.subject, s.shot_type, s.quality, s.hero ? 'HERO' : '', s.motion, s.desc, s.problems]),
})))

// ---------- Storyboard ----------
phase('Storyboard')
const ANGLES = A.angles || [
  { key: 'A', name: 'geography', text: 'Explain the place as one working system: follow the flow through its areas so a newcomer understands how it works; subjects and people appear where they belong.' },
  { key: 'B', name: 'chronology', text: 'Tell what happened in the order it happened, as one journey with clear chapters; each chapter opens with a caption.' },
  { key: 'C', name: 'rhythm', text: 'Build three acts on the music: calm wide opening, faster middle with matched motion directions, strong close on a hero wide and the closing statement; cuts land on beats.' },
]
const sbPrompt = a => `${BRIEF}

You are the editor. Draft one storyboard / EDL from the "${a.name}" angle: ${a.text}
Shot logs (JSON; shots = [in_s, out_s, subject, shot_type, quality, HERO, motion, description, problems]; sheets are image paths you may Read to confirm a shot):
${LOGS_JSON}
Requirements: shots in film order; file exactly as in the log; in_s/out_s inside a logged shot range (confirm uncertain ones on the sheets); sections with one caption line per language; title.lines one per language plus date_line; music.start_s chosen so the climax lands late in the film; notes list risks and anything the client must confirm.`
const props = (await parallel(ANGLES.map(a => () =>
  agent(sbPrompt(a), { label: `storyboard:${a.key}-${a.name}`, phase: 'Storyboard', schema: EDL_SCHEMA })
    .then(r => r && { key: a.key, name: a.name, edl: r })))).filter(Boolean)
if (!props.length) throw new Error('storyboard.js: no storyboard came back from any angle; nothing to judge')
log(`${props.length} storyboards: ${props.map(p => `${p.key} ${p.edl.shots.length} shots ~${p.edl.est_total_s}s`).join(' | ')}`)

// ---------- Judge ----------
phase('Judge')
const PROPS_JSON = JSON.stringify(props.map(p => ({ proposal: p.key, angle: p.name, edl: p.edl })))
const KEYS = props.map(p => p.key)
const JUDGE_SCHEMA = judgeSchema(KEYS)
const JUDGES = A.judges || [
  { key: 'viewer', lens: 'viewer and editor: does the opening hook, are the subjects clear, does pacing match the music, are transitions natural (motion continuity, no jump cuts or repeated framings), is the order easy to follow, is the ending strong. Read sheets to check the shots you doubt.' },
  { key: 'facts', lens: 'facts and compliance: every on-screen line backed by the facts list, translations accurate and idiomatic, no invented numbers / names / events, no third-party / low-res / vertical / screen footage, in/out inside logged ranges, recompute the total length yourself, people vs subject balance as briefed.' },
]
const judgeTask = j => `${BRIEF}

You are a judge (${j.lens}). Score each proposal 1-10 per criterion, pick a winner, list concrete grafts from the losing proposals worth moving into the winner (name shots / sections), and list factual problems.
Shot logs: ${LOGS_JSON}
Proposals: ${PROPS_JSON}`
const judges = (await parallel(JUDGES.map(j => () => (j.key === 'facts'
  ? auditor(judgeTask(j), JUDGE_SCHEMA, [], 'judge-facts', { label: `judge:${j.key}`, phase: 'Judge' })
  : agent(judgeTask(j), { label: `judge:${j.key}`, phase: 'Judge', schema: JUDGE_SCHEMA }))))).filter(Boolean)
  .filter(j => {
    const failed = [j.winner, ...(j.factual_problems || [])].some(x => String(x).startsWith('AUDITOR-FAILED'))
    if (failed) log(`WARNING: external auditor failed, its judgement is dropped: ${j.winner}`)
    return !failed
  })
if (judges.length < JUDGES.length) log(`WARNING: ${JUDGES.length - judges.length} of ${JUDGES.length} judges returned no usable verdict`)
if (!judges.length) log(`WARNING: no judge returned a usable verdict; the synthesis starts from proposal ${KEYS[0]} without judge input`)

// ---------- Synthesize ----------
phase('Synthesize')
const tally = {}
for (const j of judges) for (const s of j.scores || []) {
  if (!KEYS.includes(s.proposal)) continue
  tally[s.proposal] = (tally[s.proposal] || 0) + s.brief_fit + s.flow_and_transitions + s.shot_quality + s.caption_accuracy + s.pacing_music
}
const winnerKey = Object.entries(tally).sort((a, b) => b[1] - a[1])[0]?.[0] || KEYS[0]
log(`score tally ${JSON.stringify(tally)} -> base ${winnerKey}`)
const synth = await agent(`${BRIEF}

You are the supervising editor. Start from proposal ${winnerKey}, apply the judges' grafts, fix every factual problem and weakness they raise, and return the final EDL.
Recompute est_total_s with the formula in the house rules and stay within ${TMIN}-${TMAX} s. Read sheets whenever you need to confirm a picture.
Shot logs: ${LOGS_JSON}
Proposals: ${PROPS_JSON}
Judges: ${JSON.stringify(judges)}`, { label: 'synthesize', phase: 'Synthesize', schema: EDL_SCHEMA })

// ---------- Verify / Repair ----------
const verifyPrompt = shots => `You are checking the real picture of every shot in an EDL. Footage library: ${LIB}
For each shot extract 3 frames from the source (in_s+0.3, midpoint, out_s-0.3) with Bash, e.g.
  mkdir -p ${WORK}/verify && ffmpeg -v error -y -ss <t> -i "${LIB}/<file>" -frames:v 1 -vf scale=960:-2 ${WORK}/verify/<n>_<tag>.jpg
(a same-stem .LRF proxy, if present, has the same timeline and decodes faster), then Read the frames.
A shot with still=true is a picture: Read "${LIB}/<file>" itself, and judge the kb crop (centre cx, cy as fractions, zoom 1.0 = whole picture in a 16:9 window) at its start and end framing.
Judge: does the picture match desc; any shake, tilted horizon, bad exposure, screen close-ups, faces too close or cut by the crop, fisheye, camera swinging to something unrelated mid-shot, readable personal data (names, screens, whiteboards), cash gifts or unsafe practice (missing hard hats on a working deck, hot work near flammables); does a slowed (speed 0.5) range stay smooth.
verdict: ok = usable; adjust = small in/out change inside the same source (give new_in_s/new_out_s, keep the length, re-check the new range; for a still use ok with the better kb in problems, or replace); replace = unusable. observed = what you actually saw.
Shots: ${JSON.stringify(shots)}`
const verifyRows = async (shots, tag) => {
  const groups = []
  for (let i = 0; i < shots.length; i += 6) groups.push(shots.slice(i, i + 6))
  return (await parallel(groups.map((g, i) => () =>
    agent(verifyPrompt(g), { label: `${tag}:${i + 1}/${groups.length}`, phase: tag === 'verify' ? 'Verify' : 'Repair', schema: VERIFY_SCHEMA }))))
    .filter(Boolean).flatMap(r => r.results)
}
const applyAdjust = (e, vs) => ({ ...e, shots: e.shots.map(s => {
  const v = vs.find(x => x.n === s.n)
  return v && v.verdict === 'adjust' && v.new_in_s != null && v.new_out_s != null ? { ...s, in_s: v.new_in_s, out_s: v.new_out_s } : s
}) })

phase('Verify')
let edl = synth
if (!edl) {
  log(`WARNING: the synthesis returned nothing; verifying proposal ${winnerKey} as it stands (no grafts, no judge fixes)`)
  edl = props.find(p => p.key === winnerKey).edl
}
let verdicts = await verifyRows(edl.shots, 'verify')
edl = applyAdjust(edl, verdicts)
let rejected = verdicts.filter(v => v.verdict === 'replace')
const unchecked = edl.shots.filter(s => !verdicts.find(v => v.n === s.n)).map(s => s.n)
if (unchecked.length) log(`WARNING: shots not verified: ${unchecked.join(',')}`)
log(`verify: ${verdicts.filter(v => v.verdict === 'ok').length} ok, ${verdicts.filter(v => v.verdict === 'adjust').length} adjusted, ${rejected.length} rejected`)

phase('Repair')
for (let round = 1; round <= 2 && rejected.length; round++) {
  const repaired = await agent(`${BRIEF}

Some shots of the final EDL failed the frame check (replace). Replace ONLY those shots (same section, similar length and motion, better material from the logs; Read sheets to confirm); keep every other shot and all text unchanged; keep the replaced shots' n; recompute est_total_s.
Rejected: ${JSON.stringify(rejected)}
Current EDL: ${JSON.stringify(edl)}
Shot logs: ${LOGS_JSON}`, { label: `repair:${round}`, phase: 'Repair', schema: EDL_SCHEMA })
  if (!repaired) break
  const ns = new Set(rejected.map(r => r.n))
  const re = await verifyRows(repaired.shots.filter(s => ns.has(s.n)), 'reverify')
  edl = applyAdjust(repaired, re)
  verdicts = verdicts.filter(v => !ns.has(v.n)).concat(re)
  rejected = re.filter(v => v.verdict === 'replace')
  log(`repair round ${round}: ${rejected.length} still rejected`)
}
if (rejected.length) log(`WARNING: shots still rejected after repair: ${rejected.map(r => r.n).join(',')}`)

return { edl, verdicts, judges, tally, winnerKey, proposals: props, logs, missing, stillRejected: rejected }
