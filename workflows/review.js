export const meta = {
  name: 'footage-to-film-review',
  description: 'Review a rendered film revision through 4 lenses (on-screen text, transitions and story order, viewer, technical), then adversarially verify every medium/high finding against real frames and audio',
  whenToUse: 'After build_film.py rendered a revision and review_prep.py wrote its manifest.json',
  phases: [
    { title: 'Review', detail: 'text, transitions, viewer and technical lenses' },
    { title: 'Verify', detail: 'try to refute each medium/high finding with frames or audio measurements' },
  ],
}

// args: the whole content of <scratch_dir>/review_Rev<N>[_draft][_shotsA-B]/manifest.json written by review_prep.py
//   dir, send, master, timeline, tech, filmImages[], transitionImages[], textImages[], sendLimitMb, loudness
// plus project specifics:
//   brief, facts, audience       strings; facts = the only statements allowed on screen
//   creditOnScreen (false)
//   logsPath                     optional shot-log JSON (storyboard.js result.logs saved to a file) for replacements
//   auditorCmd, auditorWaitCmd, auditorImageFlag, forwarderModel   optional external reviewer for the text and
//                                transition lenses (default: Claude agents); see SKILL.md "External auditor"
const A = args || {}
const missingArgs = ['dir', 'send', 'master', 'timeline', 'tech'].filter(k => !A[k])
  .concat(['filmImages', 'transitionImages', 'textImages'].filter(k => !(A[k] || []).length))
if (missingArgs.length) {
  throw new Error(`review.js: missing or empty args: ${missingArgs.join(', ')}. Pass the whole manifest.json written by review_prep.py, not its printed summary.`)
}
const R = A.dir
const LIMIT = (A.sendLimitMb || 500) * 1024 * 1024
const LOUD = A.loudness || { I: -15, TP: -1.5 }

const FINDINGS = {
  type: 'object', additionalProperties: false, required: ['overall', 'findings'],
  properties: {
    overall: { type: 'string' },
    findings: { type: 'array', items: {
      type: 'object', additionalProperties: false, required: ['time_s', 'category', 'severity', 'issue', 'fix'],
      properties: {
        time_s: { type: ['number', 'null'] }, category: { type: 'string' },
        severity: { type: 'string', enum: ['high', 'medium', 'low'] }, issue: { type: 'string' }, fix: { type: 'string' },
      } } },
  },
}
const VERDICT = {
  type: 'object', required: ['verdict', 'evidence', 'refined_fix'],
  properties: { verdict: { type: 'string', enum: ['real', 'not_real', 'uncertain'] }, evidence: { type: 'string' }, refined_fix: { type: 'string' } },
}

const fill = (tpl, vars) => tpl.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m))
const imageArgs = imgs => imgs.map(i => `${A.auditorImageFlag ? A.auditorImageFlag + ' ' : ''}'${i}'`).join(' ')
const forwardPrompt = (task, schema, images, tag) => {
  const pf = `${R}/audit/${tag}.prompt.md`, sf = `${R}/audit/${tag}.schema.json`
  const poll = A.auditorWaitCmd
    ? `The command prints a job id on its last stdout line. Poll with one Bash call at a time (each must return within 150 s): ${fill(A.auditorWaitCmd, { job: '<job id>' })}; echo "EXIT=$?"  - EXIT=5 means still running: repeat, at most 30 times.`
    : 'Run it with a Bash timeout of 170000 ms (a call that blocks longer stalls the workflow; long reviewers need auditorWaitCmd).'
  return `You are a forwarding shell. Do not review anything yourself and do not read the files.
1) mkdir -p ${R}/audit, then write the task text between the markers to ${pf} with the Write tool, unchanged:
<<<TASK
${task}
TASK>>>
2) Write this JSON schema to ${sf} unchanged: ${JSON.stringify(schema)}
3) Run: ${fill(A.auditorCmd, { prompt_file: pf, schema_file: sf, images: imageArgs(images) })}
   ${poll}
4) On success, return the reviewer's stdout JSON unchanged via StructuredOutput. On failure, return overall="AUDITOR-FAILED: <last stderr line>" and findings=[].`
}
const auditor = (task, images, tag, opts) => A.auditorCmd
  ? agent(forwardPrompt(task, FINDINGS, images, tag), { ...opts, agentType: 'general-purpose', effort: 'low', schema: FINDINGS, ...(A.forwarderModel ? { model: A.forwarderModel } : {}) })
  : agent(`${task}\nRead every one of these images first: ${images.join(' , ')}`, { ...opts, schema: FINDINGS })

phase('Review')
const tl = (await agent(`Read ${A.timeline} with the Read tool and return its full text unchanged as "content".`,
  { label: 'load-timeline', phase: 'Review', effort: 'low', schema: { type: 'object', required: ['content'], properties: { content: { type: 'string' } } } }))?.content
if (!tl) throw new Error(`review.js: could not read the timeline ${A.timeline}`)

const CONTEXT = `Film brief: ${A.brief || ''}
Audience: ${A.audience || 'general'}
Facts (the only statements allowed on screen):
${A.facts || '(none given)'}
Timeline (shots with start/end seconds, transition into each shot, on-screen text definitions):
${tl}`

const textTask = `You proof-read the on-screen text of a short film. The images are every frame that shows text (title, chapter captions, place tags, closing statement, end card); each file name says which text and the film time.
${CONTEXT}
Report only real problems:
1. spelling, punctuation, capitalisation, spacing in mixed scripts, accents and grammar, idiomatic wording in every language
2. the languages say the same thing; each caption fits the picture and the chapter it opens
3. facts: names of organisations, places and vessels spelled consistently and exactly as in the facts. The facts list is not complete: a statement it does not contain is "unverified" (category facts-unverified, severity medium, fix = which source document would settle it), not wrong; the editor checks the source documents before deleting a caption
4. layout: text clipped or outside the title-safe area, low contrast, logo plate incomplete or blurred
5. ${A.creditOnScreen ? 'the music credit is present and correct on the end card' : 'no music title or credit appears anywhere on screen (it goes in the post text)'}
Each finding: time_s (film seconds or null), category, severity (high = must fix, medium = should, low = optional), issue, fix (the exact replacement text).`

const flowTask = `You review the cutting of a short film: are transitions natural and does the story read in order?
${CONTEXT}
The images show every transition as three frames: pre (0.6 s before), mid (middle of the transition), post (after it), labelled like "T5 mid 41.7s".
Check:
1. motion direction, speed, shot size and colour continue across each transition; a dissolve midpoint must not show a muddy double exposure (two horizons crossing, faces over faces, large bright/dark clashes); no flash or stray black frame
2. near-identical framings back to back; more than three stills in a row, or group photo after group photo (reads like an album)
3. events are in the order they happened, without jumping back and forth between places or states
4. dissolves are the default (clients read hard cuts as unnatural): fix a muddy one by reordering similar compositions next to each other or shortening it to 0.5-0.7 s (dissimilar shots, people); a fade through black only for a real time jump; propose a hard cut only as a question for the client
Each finding: time_s, category, severity, issue, fix (name the transition T#, the new type and length, or the replacement shot).`

const LENSES = [
  { key: 'text', label: 'on-screen text', run: () => auditor(textTask, A.textImages, 'text', { label: 'review:text', phase: 'Review' }) },
  { key: 'flow', label: 'transitions and order', run: () => auditor(flowTask, A.transitionImages, 'flow', { label: 'review:flow', phase: 'Review' }) },
  {
    key: 'viewer', label: 'viewer', run: () => agent(`You are a senior promo editor watching the film as its audience would.
${CONTEXT}
Read every per-second sheet first (the corner label is film time): ${A.filmImages.join(' , ')}
Judge: (1) are the main subjects clear and dominant; (2) pacing and chapter order, anything dragging or choppy; (3) per shot: shake, tilted horizon, exposure, noise, faces too close, phone or remote screens, camera swinging to something unrelated, near-identical framings back to back; (4) captions match what is on screen; (5) is the ending strong; (6) rights and safety: footage that may belong to a third party, readable personal data (badges, documents, screens, whiteboards, number plates, personal names), cash gifts, practices an industrial client would flag (hot work beside flammables, missing PPE such as hard hats on a working deck, people under suspended loads) - check stills (timeline still=true) as carefully as footage, plus faces cut by a still's crop; (7) music: if it changes mood in parts, does the switch sit on a visual turning point.
For detail: ffmpeg -v error -ss <s> -i "${A.send}" -frames:v 1 -vf scale=1280:-2 ${R}/viewer_<s>.jpg, then Read it.
${A.logsPath ? `Shot logs with alternative material: ${A.logsPath}` : ''}
Each finding: time_s, severity, and a concrete fix (other source range, new in/out, new order).`, { label: 'review:viewer', phase: 'Review', schema: FINDINGS }),
  },
  {
    key: 'tech', label: 'technical', run: () => agent(`You are the technical QC for a film. Send version ${A.send}; master ${A.master}; timeline ${A.timeline}; measured metrics ${A.tech} (blackdetect, freezedetect, loudness, true peak, RMS per second).
Check with ffprobe / ffmpeg and report problems:
1. both versions agree on duration and frame rate; send version <= ${LIMIT} bytes, H.264 High, level 4.1; master resolution as intended
2. black frames only at the start, at fade-through-black transitions and at the end
3. freezes: slowed aerials can false-alarm, confirm with frames
4. integrated loudness near ${LOUD.I} LUFS (below -20 is too quiet for phones), true peak <= ${LOUD.TP} dBTP; a kept source sound (timeline source_audio=true) is audible, in sync with its picture and not jarringly louder than the music; the music ends naturally, not cut off
5. end card: ${A.creditOnScreen ? 'music credit present' : 'no music title or credit on screen'}; extract frames and Read them
6. title and captions inside the title-safe area, not clipped
In overall, list every check you ran, including the ones that passed.`, { label: 'review:tech', phase: 'Review', schema: FINDINGS }),
  },
]

const refute = (lens, f) => `You re-check one review finding and try hard to REFUTE it; only call it real with evidence.
Send version ${A.send}; master ${A.master}; timeline ${A.timeline}.
Source lens: ${lens}
Finding: ${JSON.stringify(f)}
Method: extract frames at the relevant times (ffmpeg -v error -ss <s> -i <file> -frames:v 1 -vf scale=1280:-2 ${R}/verify/<name>.jpg after mkdir -p ${R}/verify) or measure the audio, and Read the frames.
- the description does not match the real picture or sound, or the problem is too small for a viewer to notice -> not_real
- it is there -> real, with refined_fix that is executable (shot number, source range, in/out, filter parameters)
- cannot tell -> uncertain
evidence = what you actually saw or measured.`

const results = await pipeline(
  LENSES,
  l => l.run(),
  (r, l) => {
    if (!r) {
      log(`WARNING: ${l.label}: no result, this lens was NOT reviewed`)
      return { lens: l.key, overall: 'NO RESULT', verified: [], low: [], failed: true }
    }
    if (String(r.overall).startsWith('AUDITOR-FAILED')) {
      log(`WARNING: ${l.label}: the external auditor failed, this lens was NOT reviewed: ${r.overall}`)
      return { lens: l.key, overall: r.overall, verified: [], low: [], failed: true }
    }
    const todo = r.findings.filter(f => f.severity !== 'low')
    log(`${l.label}: ${r.findings.length} findings (${todo.length} medium/high go to verification)`)
    return parallel(todo.map((f, i) => () =>
      agent(refute(l.label, f), { label: `verify:${l.key}:${i + 1}`, phase: 'Verify', schema: VERDICT }).then(v => ({ ...f, verdict: v }))))
      .then(vs => ({ lens: l.key, overall: r.overall, verified: vs.filter(Boolean), low: r.findings.filter(f => f.severity === 'low') }))
  },
)
const unreviewed = results.filter(r => !r || r.failed)
if (unreviewed.length) log(`WARNING: ${unreviewed.length} of ${LENSES.length} lenses were NOT reviewed: ${unreviewed.map(r => (r ? r.lens : 'unknown')).join(', ')}`)
return results.filter(Boolean)
