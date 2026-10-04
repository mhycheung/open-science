// open-science context management inside Claude Code (a Claude Code mod).
//
// It does from inside Claude Code what the tmux path does by typing into the pane: clear
// the session and run the resume prompt (session jumps), wake a waiting session (queued
// SLURM wakers, the cache-cold notice), rename the session after its task, and read the
// context size, also each subagent's. It also shows the context size and how long the
// prompt cache stays warm (a line above the prompt, on the terminal and in the Desktop app;
// Remote Control shows neither a mod's drawing nor its transcript notices), and asks the user
// before a prompt they send to a cold cache. The policy and the records stay in the plugin's
// shell scripts, shared with the tmux path and Codex: `cm_stop.sh --mod` decides at each
// stop, `cm_mod.sh` keeps the records, `wait_slurm.sh --check` polls a waker,
// `session_name.sh want` names the session.
//
// Loading the mod sets OPSCI_MOD=1 for Claude Code and everything it starts, which turns
// the plain Stop and naming hooks off and makes jump.sh and wait_slurm.sh key their
// records by session id. A Claude Code that does not load the mod never sets it, and the
// tmux path runs as before.

const SUB_LIMIT_DEFAULT = 200000
const POLL_DEFAULT_S = 60
// The bar and the question call the cache cold from here (OPSCI_CACHE_TTL_MIN). The cache
// lives 60 min from the start of the last request that read it; the bar's clock starts at
// that request's end, so a minute less covers most responses. (The cache-cold notice to the
// agent comes a minute earlier still, so its turn starts warm: OPSCI_CACHE_COLD_MIN, 58, in
// cm_lib.sh.)
const TTL_MIN_DEFAULT = 59
const BAR_TICK_MS = 30000
const STORE_KEY = 'lastRequest' // { <session id>: ms of the main agent's last model request }

let sid = null              // the session id this process runs now
let busy = false            // a main-agent turn is running
let jumping = false         // a clear and resume is in progress
let waiting = false         // a wait jump left this session waiting for a waker
let clearedFrom = null      // the id a /clear the user typed left behind
let atStop = null           // what cm_stop.sh --mod decided, acted on at turn.complete
let coldTimer = null
let pollTimer = null
let polling = false
let lastName = null
let barSid = null           // the session lastReq belongs to
let lastReq = null          // ms of the main agent's last model request in barSid; null: none yet
let barText = ''            // what the bar shows now
let barPhase = 'none'       // none | warm | cold: the bar's color
let lastTokens = null       // the context after the main agent's last response, in barSid
let barTimer = null
const warned = new Set()    // subagents already told to checkpoint

const key = s => String(s).replace(/[^A-Za-z0-9._-]/g, '_')

async function stateDir($) {
  const d = await $.env.get('OPSCI_STATE_DIR')
  if (d) return d
  const x = await $.env.get('XDG_STATE_HOME')
  return (x || (await $.env.get('HOME')) + '/.local/state') + '/open-science'
}

async function sh($, script, args, stdin, timeoutMs) {
  try {
    return await $.process.run(['bash', $.plugin.root + '/scripts/' + script, ...args],
      { stdin: stdin || '', timeoutMs: timeoutMs || 30000 })
  } catch (err) {
    return { exitCode: -1, stdout: '', stderr: String(err) }
  }
}

const log = ($, text) => sh($, 'cm_mod.sh', ['log', text])

// A resume prompt is a slash command (`/open-science-context:continue-context <file>`):
// run it as the command it is. Anything else is a prompt.
async function runPrompt($, text) {
  const m = /^\/(\S+)\s*([\s\S]*)$/.exec(text.trim())
  if (m) return $.command.run({ command: m[1], args: m[2] })
  return $.prompt.submit({ text })
}

// The records cm_mod.sh and pane_context.sh keep, by session id.
const record = async ($, ...parts) => [await stateDir($), ...parts].join('/')

async function registered($, s) {
  return $.fs.exists(await record($, 'session_context', 'claude__' + key(s) + '.json'))
}

async function rename($, always) {
  const r = await sh($, 'session_name.sh', ['want', ...(always ? ['--always'] : []), sid, await $.session.cwd()])
  const name = r.stdout.trim()
  if (!name || (name === lastName && !always)) return
  lastName = name
  await $.command.run({ command: 'rename', args: name })
}

// The jump's report (jump.sh --report), written at the top of the cleared session: a notice
// for the user, who otherwise sees only the clear (the model never reads a notice), and the
// same text as a user-role row the user does not see, which the agent reads with its next
// prompt. Neither starts a turn, so a wait jump's session stays asleep.
function jumpNote(j) {
  const report = String(j.report || '').trim()
  if (j.kind === 'active') return report
  const n = Number(j.wakers) || 0
  const what = n ? n + ' running background task' + (n === 1 ? '' : 's') + ', cron' + (n === 1 ? '' : 's') + ' or SLURM waker' + (n === 1 ? '' : 's') : 'its background work'
  return 'Wait jump: this session was cleared and is waiting for ' + what +
    '. It resumes by itself when that work reports back; until then it does nothing.' + (report ? '\n\n' + report : '')
}

async function writeNote($, j) {
  const text = jumpNote(j)
  if (!text) return
  const append = async (type, body) => {
    try { return await $.session.append({ message: { type, content: [{ type: 'text', text: body }] } }) } catch (err) { return { deny: String(err) } }
  }
  const shown = await append('system', '[open-science] ' + text)
  // A Claude Code that refuses the notice still shows the user a transcript line.
  if (shown.deny) $.ui.log('[open-science] ' + text)
  const read = await append('user', '[open-science] The previous session wrote this for the user when it jumped (the user sees it above):\n\n' + text)
  if (shown.deny || read.deny) await log($, 'jump note not stored: ' + (shown.deny || read.deny))
}

// Clear the session with Claude Code's own /clear, hand the registration and wakers to
// the new session, write the jump's report at its top, then run the resume prompt
// (active) or leave it waiting (wait).
function jump($, j) {
  jumping = true
  coldTimer?.cancel(); coldTimer = null
  $.clock.after(0, async () => {
    try {
      const old = await $.session.id()
      await $.command.run({ command: 'clear', args: '' })
      const now = await $.session.id()
      if (now === old) {
        await log($, 'jump FAILED: /clear did not start a new session (still ' + old.slice(0, 8) + ')')
        void $.prompt.submit({ text: '[open-science] session jump FAILED before anything was changed; this session keeps running: the clear did not happen.' })
        return
      }
      sid = now
      await sh($, 'cm_mod.sh', ['handover', old, now])
      await log($, j.kind + ' jump: cleared ' + old.slice(0, 8) + ' -> ' + now.slice(0, 8))
      try { await writeNote($, j) } catch (err) { await log($, 'jump note FAILED: ' + err) }
      if (j.kind === 'active' && j.prompt) {
        await runPrompt($, j.prompt)
      } else {
        waiting = true
        await sh($, 'cm_mod.sh', ['waiting', now, j.context || ''])
      }
      await rename($)
    } catch (err) {
      await log($, 'jump FAILED: ' + err)
    } finally {
      jumping = false
    }
  })
}

// One cache-cold timer per process. It fires only into the session it was armed for,
// with no turn and no jump since.
function armCold($, secs, notice) {
  coldTimer?.cancel(); coldTimer = null
  if (!secs || !notice) return
  const armedFor = sid
  coldTimer = $.clock.after(secs * 1000, async () => {
    coldTimer = null
    if (busy || jumping || (await $.session.id()) !== armedFor) return
    await log($, 'cache-cold notice sent to ' + armedFor.slice(0, 8))
    void $.prompt.submit({ text: notice })
  })
}

// Queued SLURM wakers of this session: when their jobs have left the queue, send the
// report (the same text a Codex waker sends), which starts a turn once the session is idle.
// The reports of all wakers done in one poll go in one message, each distinct report once,
// so wakers queued twice for the same jobs wake the session once.
async function pollWakers($) {
  if (polling) return
  polling = true
  try {
    const cur = await $.session.id()
    if (!(await $.fs.exists(await record($, 'wakers', 'claude-sid__' + key(cur))))) return
    const files = (await sh($, 'cm_mod.sh', ['wakers', cur])).stdout.split('\n').filter(Boolean)
    const reports = new Set()
    for (const f of files) {
      const r = await sh($, 'wait_slurm.sh', ['--check', f], '', 120000)
      if (r.exitCode === 0 || r.exitCode === 1) {
        await log($, 'waker ' + f.split('/').pop() + ': jobs left the queue; report sent')
        reports.add(r.stdout.trim().replace(/\s*\n\s*/g, ' '))
      } else if (r.exitCode !== 10) {
        await log($, 'waker ' + f + ': check failed (' + r.exitCode + '): ' + r.stderr.trim())
      }
    }
    if (reports.size) void $.prompt.submit({ text: '[open-science] ' + [...reports].join(' ') })
  } finally {
    polling = false
  }
}

// ---- the context bar and the cold-cache question --------------------------------------
// The prompt cache lives TTL minutes from the last request that read it, so the clock runs
// from the main agent's last model request (each turn.step), not from the last prompt.

async function ttlMin($) {
  return Number(await $.env.get('OPSCI_CACHE_TTL_MIN')) || TTL_MIN_DEFAULT
}

// Follows the session through clears and resumes: a new session id reads its own record
// (a cleared session has none: no cache yet; a resumed one has its last request's time).
async function syncSid($) {
  const now = await $.session.id()
  if (now === barSid) return
  barSid = now
  lastTokens = null
  const all = (await $.store.get(STORE_KEY)) || {}
  lastReq = typeof all[now] === 'number' ? all[now] : null
}

// At the end of each main-agent request: its time, and the context it leaves (what it read
// plus what it wrote, which the next request reads).
async function noteRequest($, usage) {
  await syncSid($)
  lastReq = await $.clock.now()
  if (usage) lastTokens = (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0) +
    (usage.cache_creation_input_tokens || 0) + (usage.output_tokens || 0)
  const all = { ...((await $.store.get(STORE_KEY)) || {}) }
  delete all[barSid]
  all[barSid] = lastReq
  const keys = Object.keys(all)
  for (const k of keys.slice(0, Math.max(0, keys.length - 50))) delete all[k]
  await $.store.set(STORE_KEY, all)
}

// { phase: none | warm | cold, idleMin, leftMin, tokens }
async function cacheState($) {
  await syncSid($)
  const ttl = await ttlMin($)
  let tokens = lastTokens
  if (tokens === null) {
    try { tokens = (await $.session.usage()).context.tokens } catch { tokens = undefined }
  }
  if (lastReq === null) return { phase: 'none', tokens }
  if (busy) return { phase: 'warm', idleMin: 0, leftMin: ttl, tokens }
  const idleMin = Math.max(0, ((await $.clock.now()) - lastReq) / 60000)
  return { phase: idleMin >= ttl ? 'cold' : 'warm', idleMin, leftMin: Math.max(0, Math.ceil(ttl - idleMin)), tokens }
}

// 987, then 1.0k, 123.4k
const fmtTokens = n => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n)

function describe(st) {
  const ctx = typeof st.tokens === 'number' ? 'context ' + fmtTokens(st.tokens) + ' tokens' : 'context size unknown'
  if (st.phase === 'cold') return '⚠ ' + ctx + ' · cache cold (' + Math.floor(st.idleMin) + ' min idle)'
  return ctx + ' · ' + (st.phase === 'warm' ? 'cache warm, ' + st.leftMin + ' min left' : 'no cache yet')
}

const BAR_COLOR = { warm: 'green', cold: 'yellow' }

function bar($, e) {
  const { Text } = $.ui.resolve(e)
  const color = BAR_COLOR[barPhase]
  return h(Text, color ? { color } : { dimColor: true }, barText)
}

async function refreshBar($) {
  const st = await cacheState($)
  const text = describe(st)
  if (text === barText && st.phase === barPhase) return
  barText = text
  barPhase = st.phase
  $.ui.invalidate('ui.render')
}

// A prompt the user sent (typed, or from Remote Control) to an idle session whose cache
// is cold is held, the model not woken, until they confirm it. A slash command passes:
// /clear is the usual answer to a cold cache.
//
// A typed prompt gets the mod's own dialog in the terminal, with exactly two answers. A hook
// may not wait on its own code for more than 10 s, so the prompt is dropped at once and the
// dialog sends it again on Submit, as the user's (resubmit, below). Claude Code's question
// dialog is used where the mod's cannot be: a prompt from Remote Control (the app draws only
// that dialog, and adds its own Other answers), or one with attachments, which a resubmit
// would lose.
const ASK_PANE = 'opsci-cold-ask'
let asking = null           // { question, text }: the typed prompt the dialog holds
let resubmit = null         // the text Submit sent again, to enter as the user's

function coldQuestion(st) {
  const ctx = typeof st.tokens === 'number' ? ' The whole context (' + fmtTokens(st.tokens) + ' tokens) will be read again at the full price.' : ''
  return 'The cache is cold (' + Math.floor(st.idleMin) + ' min since the last request).' + ctx + ' Are you sure you want to submit this prompt?'
}

async function answerAsk($, submit) {
  const held = asking
  asking = null
  await $.ui.close({ id: ASK_PANE })
  if (!held) return
  if (submit) {
    resubmit = held.text
    await $.prompt.submit({ text: held.text })
  } else {
    await $.prompt.fill({ text: held.text })
    await log($, 'cold-cache prompt held back')
  }
}

async function coldGate($, e, next) {
  // Submit in the mod's dialog: the user's prompt, entered as theirs (an answer without the
  // plugin origin is read as the user's own).
  if (e.origin.kind === 'plugin' && resubmit !== null && e.text === resubmit) {
    resubmit = null
    const r = await next(e)
    if (r && !r.drop) { const { origin, ...rest } = r; return rest }
    return r
  }
  if (e.turnId || (e.origin.kind !== 'composer' && e.origin.kind !== 'bridge') || /^\s*\//.test(e.text)) return next(e)
  const st = await cacheState($)
  if (st.phase !== 'cold') return next(e)
  const q = coldQuestion(st)
  if (e.origin.kind === 'composer' && !(e.attachments && e.attachments.length)) {
    asking = { question: q, text: e.text }
    const opened = await $.ui.open({ id: ASK_PANE, title: 'Cache cold', focus: true, closeOnEscape: true, holdToasts: true, rows: 6 })
    if (opened.isPlaced) return { drop: 'Not sent yet: the cache is cold. Answer below.' }
    asking = null
  }
  let answer = ''
  try { answer = await $.ui.ask(q, { header: 'Cache cold', options: ['Submit', 'Do not submit'] }) } catch { answer = '' }
  if (answer === 'Submit') return next(e)
  if (e.origin.kind === 'composer') await $.prompt.fill({ text: e.text })
  await log($, 'cold-cache prompt held back (' + Math.floor(st.idleMin) + ' min idle)')
  return { drop: 'Not submitted: the cache is cold.' +
    (e.origin.kind === 'composer' ? ' Your prompt is back in the prompt box.' : '') + ' /clear starts a fresh session.' }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    await $.env.set('OPSCI_MOD', '1')
    sid = await $.session.id()
    const poll = Number(await $.env.get('OPSCI_WAIT_POLL')) || POLL_DEFAULT_S
    pollTimer?.cancel()
    pollTimer = $.clock.every(poll * 1000, () => { void pollWakers($) })
    barTimer?.cancel()
    barText = ''; barPhase = 'none'; barSid = null; lastTokens = null
    barTimer = $.clock.every(BAR_TICK_MS, () => { void refreshBar($) })
    $.clock.after(0, async () => {
      // A session left waiting by another process (resumed after a SLURM resurrection or
      // by hand) lost the background tasks that were to wake it: wake it now.
      const r = await sh($, 'cm_mod.sh', ['resumed', sid])
      if (r.stdout.trim()) await runPrompt($, r.stdout.trim())
      // Only when the name should change: session.start also fires at every reload of a mod,
      // and a resumed session keeps its name (the session record has it; a session without
      // one gets it here).
      await rename($)
    })
    $.clock.after(0, () => { void refreshBar($) })
    return next(e)
  })

  on('classic.Stop', async ($, e, next) => {
    if (e.agent_id) return next(e)
    let tokens
    try { tokens = (await $.session.usage()).context.tokens } catch { tokens = undefined }
    const r = await sh($, 'cm_stop.sh', ['--mod'], JSON.stringify({ ...e, opsci_tokens: tokens }))
    let out = {}
    try { out = JSON.parse(r.stdout || '{}') } catch { out = {} }
    // Claude Code refuses a command or prompt started from the Stop hook (it would wait on
    // the turn the hook holds), so the decision is carried out at turn.complete.
    atStop = out.opsci || null
    const res = await next(e)
    return out.decision === 'block' ? { ...(res || {}), block: out.reason } : res
  })

  on('turn.start', async ($, e, next) => {
    if (e.agentId) return next(e)
    busy = true
    coldTimer?.cancel(); coldTimer = null
    $.clock.after(0, () => { void refreshBar($) })
    const now = await $.session.id()
    if (clearedFrom && now !== clearedFrom && !jumping) {
      // A /clear the user typed: the registration stays with this process, as a pane's does.
      await sh($, 'cm_mod.sh', ['handover', clearedFrom, now])
      clearedFrom = null
    }
    sid = now
    if (waiting) { waiting = false; await sh($, 'cm_mod.sh', ['woken', now]) }
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId) return next(e)
    busy = false
    $.clock.after(0, () => { void refreshBar($) })
    const o = atStop || {}
    atStop = null
    if (o.jump) jump($, o.jump)
    else {
      armCold($, o.cold || 0, o.notice)
      $.clock.after(0, () => { void rename($) })
    }
    return next(e)
  })

  on('session.end', async ($, e, next) => {
    if (e.reason === 'clear' && !jumping) clearedFrom = e.sessionId
    return next(e)
  })

  on('prompt.submit', coldGate)
  on('ui.render', { component: 'Pane', requestId: ASK_PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    return h(Box, { flexDirection: 'column' },
      h(Text, { color: 'yellow' }, '⚠ ' + (asking ? asking.question : 'The cache is cold.')),
      h(Box, { flexDirection: 'row', gap: 2 },
        h(Button, { key: 'submit', label: 'Submit', hotkey: '1', variant: 'primary', autoFocus: true, onPress: () => answerAsk($, true) }),
        h(Button, { key: 'cancel', label: 'Do not submit', hotkey: '2', role: 'dismiss', onPress: () => answerAsk($, false) })))
  })
  // Esc, or the pane's close mark: not submitted.
  on('ui.close', async ($, e, next) => {
    const r = await next(e)
    if (e.id === ASK_PANE && asking && e.origin.kind !== 'plugin') {
      const held = asking
      asking = null
      await $.prompt.fill({ text: held.text })
      await log($, 'cold-cache prompt held back')
    }
    return r
  })

  // The bar: a line above the prompt on the terminal and in the Desktop app. Green while the
  // cache is warm, yellow with a warning sign once it is cold.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!barText || e.props.hasSurvey) return next(e)
    return bar($, e)
  })

  // Each subagent's own context size, from each request it sends. Above the limit, it is
  // told once to checkpoint, as the dispatch contract asks (open-science-context:
  // context-management, "Subagents"). Only in a session that drives a task.
  on('turn.step', async function* ($, e, next) {
    const result = yield* next(e)
    if (!e.agentId) { await noteRequest($, result && result.usage); await refreshBar($) }
    const u = result && result.usage
    if (e.agentId && u && !warned.has(e.agentId)) {
      const tokens = (u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0)
      const limit = Number(await $.env.get('OPSCI_SUBAGENT_LIMIT')) || SUB_LIMIT_DEFAULT
      if (tokens > limit && sid && (await registered($, sid))) {
        warned.add(e.agentId)
        const text = 'open-science: your context is ' + tokens + ' tokens, above ' + limit +
          '. Stop at the next clean boundary, bring your subcontext document up to date, and end your report PAUSED - <doc path> - <exact next step>.'
        const sent = await $.session.send({ to: { agentId: e.agentId }, text })
        await log($, 'subagent ' + e.agentId + ' at ' + tokens + ' tokens: checkpoint ' + (sent.isDelivered ? 'sent' : 'NOT delivered: ' + sent.reason))
      }
    }
    return result
  })
}
