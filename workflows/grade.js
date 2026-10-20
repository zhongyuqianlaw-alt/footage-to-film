export const meta = {
  name: 'footage-to-film-grade',
  description: 'Per-shot colour grading toward a reference look (one agent per weak shot iterating an ffmpeg filter on real frames), then an audit of all before/after pairs for over-processing and consistency',
  whenToUse: 'After color_stats.py flagged weak shots and reference frames for the target look were picked',
  phases: [
    { title: 'Grade', detail: 'one agent per shot iterates an ffmpeg grade on real frames' },
    { title: 'Audit', detail: 'naturalness and consistency across all before/after pairs' },
  ],
}

// args (example in REFERENCE.md):
//   dir            work directory for frames and comparison sheets (absolute)
//   libraryDir     footage library root (project library_dir)
//   python         interpreter with numpy + opencv (the venv python from the README; default 'python3')
//   context        one paragraph about the film and its look
//   reference      {frames: [jpg...], stats: {L_mean, contrast, sat, chroma, a, b}, description}
//                  stats = color_stats.py run on the reference frames themselves (it accepts .jpg/.png)
//   shots          [{n, file, in_s, out_s, desc, grade, stats, why}]  grade = current filter chain (or omit);
//                  stats = the color_stats.py entry whose "shot" equals n (segment shot_NN_<hash>.mp4)
//   defaultGrade   project.json default_grade, used when a shot has no grade (default 'eq=contrast=1.04:saturation=1.07')
//   auditorCmd, auditorWaitCmd, auditorImageFlag, forwarderModel   optional external auditor (see SKILL.md)
const A = args || {}
const missingArgs = ['dir', 'libraryDir'].filter(k => !A[k]).concat((A.shots || []).length ? [] : ['shots'])
if (missingArgs.length) throw new Error(`grade.js: missing or empty args: ${missingArgs.join(', ')} (see REFERENCE.md)`)
const G = A.dir
const PY = A.python || 'python3'
const REF = A.reference || { frames: [], stats: {}, description: '' }
const DEFAULT = A.defaultGrade || 'eq=contrast=1.04:saturation=1.07'

const GRADE = {
  type: 'object', required: ['n', 'filter', 'before_after', 'stats_before', 'stats_after', 'notes'],
  properties: {
    n: { type: 'integer' },
    filter: { type: 'string', description: 'final ffmpeg filter chain (comma separated), applied after scaling' },
    before_after: { type: 'string', description: 'path of the comparison jpg (3 rows in/mid/out, left = current, right = new)' },
    stats_before: { type: 'string' }, stats_after: { type: 'string' }, notes: { type: 'string' },
  },
}
const FINDINGS = {
  type: 'object', additionalProperties: false, required: ['overall', 'findings'],
  properties: {
    overall: { type: 'string' },
    findings: { type: 'array', items: {
      type: 'object', additionalProperties: false, required: ['shot', 'severity', 'issue', 'fix'],
      properties: { shot: { type: ['integer', 'null'] }, severity: { type: 'string', enum: ['high', 'medium', 'low'] }, issue: { type: 'string' }, fix: { type: 'string' } },
    } },
  },
}

const fill = (tpl, vars) => tpl.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m))
const imageArgs = imgs => imgs.map(i => `${A.auditorImageFlag ? A.auditorImageFlag + ' ' : ''}'${i}'`).join(' ')
const forwardPrompt = (task, images, tag) => {
  const pf = `${G}/audit/${tag}.prompt.md`, sf = `${G}/audit/${tag}.schema.json`
  const poll = A.auditorWaitCmd
    ? `The command prints a job id on its last stdout line. Poll with one Bash call at a time (each must return within 150 s): ${fill(A.auditorWaitCmd, { job: '<job id>' })}; echo "EXIT=$?"  - EXIT=5 means still running: repeat, at most 30 times.`
    : 'Run it with a Bash timeout of 170000 ms (a call that blocks longer stalls the workflow; long reviewers need auditorWaitCmd).'
  return `You are a forwarding shell. Do not review anything yourself and do not read the files.
1) mkdir -p ${G}/audit, then write the task text between the markers to ${pf} with the Write tool, unchanged:
<<<TASK
${task}
TASK>>>
2) Write this JSON schema to ${sf} unchanged: ${JSON.stringify(FINDINGS)}
3) Run: ${fill(A.auditorCmd, { prompt_file: pf, schema_file: sf, images: imageArgs(images) })}
   ${poll}
4) On success, return the reviewer's stdout JSON unchanged via StructuredOutput. On failure, return overall="AUDITOR-FAILED: <last stderr line>" and findings=[].`
}

const gradePrompt = s => `You are a colourist. Find one ffmpeg filter chain for a single shot so it sits close to the reference look: natural, not over-processed.
Film: ${A.context || ''}
Reference look: ${REF.description || ''}. Read the reference frames first: ${(REF.frames || []).join(' , ')}. Reference statistics: ${JSON.stringify(REF.stats || {})} (L = Lab lightness 0-100, contrast = L std-dev, sat = HSV S mean x100, chroma = mean Lab distance from grey, a/b = Lab cast).
Shot ${s.n}: ${s.desc}
Source ${A.libraryDir}/${s.file}, in ${s.in_s} s, out ${s.out_s} s. Current filter: ${s.grade || DEFAULT}. Current statistics: ${JSON.stringify(s.stats || {})}.
Why it was flagged: ${s.why || ''}

Method (Bash only):
1. mkdir -p ${G}/s${s.n}; grab one raw frame at in+0.5, the midpoint and out-0.5: ffmpeg -v error -y -ss <t> -i "<source>" -frames:v 1 -vf "scale=1280:-2" ${G}/s${s.n}/raw_<k>.png
2. apply a candidate: ffmpeg -v error -y -i raw_<k>.png -vf "<candidate>" cand_<k>.png; measure with ${PY} + cv2 (Lab L mean / P2 / P98 / std, HSV S mean, a*/b* mean) and Read the images.
3. Allowed filters: eq (contrast / brightness / gamma / saturation), curves (e.g. a gentle S: curves=master='0/0 0.25/0.22 0.75/0.80 1/1'), vibrance (0-0.4, preferred over blanket saturation), colortemperature (5000-7000), colorbalance, colorlevels. No LUTs, no sharpening.
4. At most 5 iterations. Hard limits: P98 <= 96 (no clipped highlights), P2 >= 2 (no crushed shadows); skin, red-painted steel and vegetation must stay believable. An overcast shot stays overcast: aim for clear, not grey, and close to the reference rather than equal to it.
5. Build ${G}/s${s.n}/before_after.jpg: 3 rows (in / mid / out), left = current filter, right = new filter, each 640 px wide.
Return the filter (it is appended after scaling), the comparison path, before/after statistics and notes (what changed, why, remaining risk).`

phase('Grade')
const shots = A.shots || []
const graded = (await parallel(shots.map(s => () =>
  agent(gradePrompt(s), { label: `grade:${s.n}`, phase: 'Grade', schema: GRADE })))).filter(Boolean)
if (graded.length < shots.length) log(`WARNING: ${shots.length - graded.length} shots returned no grade`)
log(`graded ${graded.length}/${shots.length} shots`)

phase('Audit')
const listing = graded.map(g => `shot ${g.n}: ${(shots.find(s => s.n === g.n) || {}).desc || ''} | new filter ${g.filter} | ${g.stats_after}`).join('\n')
const auditTask = which => `You audit colour grades for a short film. Each comparison image has three rows (in / mid / out); left = before, right = after. The reference look is attached too.
Shots:
${listing}
For ${which}: is the result natural (no fake saturation, skin / red steel / greens believable, no clipped highlights or crushed shadows, no colour cast), clearer than before, consistent with the reference and with neighbouring shots? Report only real problems; give exact filter parameter changes as the fix.`
const groups = []
for (let i = 0; i < graded.length; i += 4) groups.push(graded.slice(i, i + 4))
const audits = (await parallel(groups.map((g, k) => () => {
  const which = g.map(x => `shot ${x.n}`).join(', ')
  const images = [...g.map(x => x.before_after), ...(REF.frames || []).slice(0, 1)]
  const opts = { label: `audit:${k + 1}/${groups.length}`, phase: 'Audit' }
  return A.auditorCmd
    ? agent(forwardPrompt(auditTask(which), images, `grade-audit-${k + 1}`), { ...opts, agentType: 'general-purpose', effort: 'low', schema: FINDINGS, ...(A.forwarderModel ? { model: A.forwarderModel } : {}) })
    : agent(`${auditTask(which)}\nRead every one of these images first: ${images.join(' , ')}`, { ...opts, schema: FINDINGS })
}))).filter(Boolean)
const failedAudits = audits.filter(a => String(a.overall).startsWith('AUDITOR-FAILED'))
if (failedAudits.length) log(`WARNING: ${failedAudits.length} audit group(s) failed and were NOT reviewed: ${failedAudits.map(a => a.overall).join(' | ')}`)
if (audits.length < groups.length) log(`WARNING: ${groups.length - audits.length} audit group(s) returned nothing`)

return { graded, audits, failedAudits: failedAudits.length }
