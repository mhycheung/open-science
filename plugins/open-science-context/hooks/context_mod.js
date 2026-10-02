// open-science context management inside Claude Code (a Claude Code mod).
//
// It does from inside Claude Code what the tmux path does by typing into the pane: clear
// the session and run the resume prompt (session jumps), wake a waiting session (queued
// SLURM wakers, the cache-cold notice), rename the session after its task, and read the
// context size, also each subagent's. The policy and the records stay in the plugin's
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

// Clear the session with Claude Code's own /clear, hand the registration and wakers to
// the new session, then run the resume prompt (active) or leave it waiting (wait).
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

export function register(on) {
  on('session.start', async ($, e, next) => {
    await $.env.set('OPSCI_MOD', '1')
    sid = await $.session.id()
    const poll = Number(await $.env.get('OPSCI_WAIT_POLL')) || POLL_DEFAULT_S
    pollTimer?.cancel()
    pollTimer = $.clock.every(poll * 1000, () => { void pollWakers($) })
    $.clock.after(0, async () => {
      // A session left waiting by another process (resumed after a SLURM resurrection or
      // by hand) lost the background tasks that were to wake it: wake it now.
      const r = await sh($, 'cm_mod.sh', ['resumed', sid])
      if (r.stdout.trim()) await runPrompt($, r.stdout.trim())
      await rename($, true)
    })
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

  // Each subagent's own context size, from each request it sends. Above the limit, it is
  // told once to checkpoint, as the dispatch contract asks (open-science-context:
  // context-management, "Subagents"). Only in a session that drives a task.
  on('turn.step', async function* ($, e, next) {
    const result = yield* next(e)
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
