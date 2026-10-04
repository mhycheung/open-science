// opsci: planted-leaks (this file uses fake absolute paths on purpose)
// Tests of the open-science context mod (hooks/context_mod.js), run with
// `claude plugin test plugins/open-science-context` (tests/test_cm_mod.py runs them).
// Claude Code is stubbed: the session id, the shell scripts the mod calls, the commands
// and prompts it starts. The scripts themselves are tested by tests/test_cm_mod.py.
import { expect, mock, test } from 'claude-code/testing'

type Calls = { commands: string[]; prompts: string[]; scripts: string[][]; sent: string[]; appended: { type: string; text: string }[]; order: string[]; status: string[]; logged: string[]; filled: string[] }

// Stubs shared by every test. `stop` is what cm_stop.sh --mod answers; `scripts` maps a
// script name to its answer. A /clear makes the session id change, as in Claude Code.
function setup(on, opts: { stop?: object; scripts?: Record<string, object>; registered?: boolean; store?: Record<string, unknown>; placed?: boolean } = {}) {
  const calls: Calls = { commands: [], prompts: [], scripts: [], sent: [], appended: [], order: [], status: [], logged: [], filled: [] }
  let sid = 'sid-old'
  const clock = mock.clock(on)
  mock.env(on, { OPSCI_STATE_DIR: '/state', HOME: '/home/u' })
  mock.store(on, opts.store ?? {})
  on('ui.open', ($, e) => { calls.order.push('open ' + e.id); return { value: { isPlaced: opts.placed ?? true } } })
  on('ui.close', ($, e) => { calls.order.push('close ' + e.id); return { value: undefined } })
  on('prompt.fill', ($, e) => { calls.filled.push(e.text); return { isFilled: true } })
  on('ui.log', ($, e) => { calls.logged.push(e.text); calls.order.push('log'); return { value: undefined } })
  on('env.set', () => ({ value: undefined }))
  on('session.id', () => ({ value: sid }))
  on('session.cwd', () => ({ value: '/work' }))
  on('session.usage', () => ({ value: { context: { tokens: 1234, window: 1000000, percent: 0 } } }))
  on('fs.exists', () => ({ value: opts.registered ?? true }))
  on('process.run', ($, e) => {
    const script = String(e.argv[1]).split('/').pop()
    calls.scripts.push([script, ...e.argv.slice(2)])
    if (script === 'cm_stop.sh') return { value: { exitCode: 0, stdout: JSON.stringify(opts.stop ?? { opsci: { cold: 0 } }), stderr: '' } }
    const a = opts.scripts?.[[script, e.argv[2]].join(' ')]
    return { value: a ?? { exitCode: 0, stdout: '', stderr: '' } }
  })
  on('session.append', ($, e) => {
    calls.appended.push({ type: e.message.type, text: e.message.content[0].text })
    calls.order.push('append ' + e.message.type)
    return { value: { message: e.message, uuid: 'u' + calls.appended.length } }
  })
  on('command.run', ($, e) => {
    calls.commands.push((e.command + ' ' + (e.args || '')).trim())
    calls.order.push('command ' + e.command)
    if (e.command === 'clear') sid = 'sid-new'
    return { text: '' }
  })
  on('prompt.submit', ($, e) => { calls.prompts.push(e.text); return { text: e.text } })
  on('session.send', ($, e) => { calls.sent.push(e.text); return { isDelivered: true } })
  on('session.start', () => ({ cwd: '/work' }))
  on('classic.Stop', () => ({}))
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  on('turn.complete', () => ({ text: '' }))
  on('session.end', ($, e) => ({ sessionId: e.sessionId }))
  return { calls, clock }
}

async function start($, clock) {
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
}

async function endTurn($, clock) {
  await $.classic.Stop({ session_id: 'sid-old', stop_hook_active: false, background_tasks: [], session_crons: [] })
  await $.turn.complete({ turnId: 't1', answer: '', durationMs: 1, isAborted: false, usage: null })
  await clock.settle()
}

test('an active jump clears, hands over, and runs the resume command', async ($, on) => {
  const { calls, clock } = setup(on, { stop: { opsci: { cold: 0, notice: 'n', jump: { kind: 'active', context: '/p/tasks/a/context.md', prompt: '/open-science-context:continue-context /p/tasks/a/context.md' } } } })
  await start($, clock)
  await endTurn($, clock)
  expect(calls.commands.filter(c => !c.startsWith('rename'))).toEqual(['clear', 'open-science-context:continue-context /p/tasks/a/context.md'])
  expect(calls.scripts).toContainEqual(['cm_mod.sh', 'handover', 'sid-old', 'sid-new'])
})

// What the user sees of a jump note: the stored notice, or the transcript line the mod
// falls back to. (This build's test kit does not route a plugin's $.session.append to the
// test's hooks, so here it is the fallback; the stored rows were checked in a live session.)
const shownNotes = (c: Calls) => [...c.appended.filter(a => a.type === 'system').map(a => a.text), ...c.logged]

test('an active jump writes its report at the top of the new session before resuming', async ($, on) => {
  const { calls, clock } = setup(on, { stop: { opsci: { cold: 0, jump: { kind: 'active', context: '/p/c.md', prompt: '/open-science-context:continue-context /p/c.md', report: 'Fit done.\n\nActive jump: the session is cleared and resumes from c.md.', wakers: 0 } } } })
  await start($, clock)
  await endTurn($, clock)
  expect(shownNotes(calls)).toEqual(['[open-science] Fit done.\n\nActive jump: the session is cleared and resumes from c.md.'])
  const o = calls.order.filter(x => !x.startsWith('command rename') && x !== 'append user')
  expect(o.indexOf('command clear')).toBe(0)
  expect(o.slice(1)).toEqual([o[1], 'command open-science-context:continue-context'])
  expect(['append system', 'log']).toContain(o[1])
})

test('a wait jump says the session is waiting, and starts no turn', async ($, on) => {
  const { calls, clock } = setup(on, { stop: { opsci: { cold: 0, jump: { kind: 'wait', context: '/p/c.md', prompt: '', report: 'Runs queued.', wakers: 2 } } } })
  await start($, clock)
  await endTurn($, clock)
  const [note] = shownNotes(calls)
  expect(note).toContain('Wait jump: this session was cleared and is waiting for 2 running background tasks')
  expect(note).toContain('Runs queued.')
  expect(calls.prompts).toEqual([])
})

test('a jump with no report writes nothing for an active jump', async ($, on) => {
  const { calls, clock } = setup(on, { stop: { opsci: { cold: 0, jump: { kind: 'active', context: '/p/c.md', prompt: '/x' } } } })
  await start($, clock)
  await endTurn($, clock)
  expect(shownNotes(calls)).toEqual([])
})

test('the stop policy gets the context size from Claude Code', async ($, on) => {
  const { calls, clock } = setup(on)
  await start($, clock)
  await endTurn($, clock)
  expect(calls.scripts).toContainEqual(['cm_stop.sh', '--mod'])
})

test('a wait jump clears and leaves the session waiting', async ($, on) => {
  const { calls, clock } = setup(on, { stop: { opsci: { cold: 0, jump: { kind: 'wait', context: '/p/c.md', prompt: '' } } } })
  await start($, clock)
  await endTurn($, clock)
  expect(calls.commands.filter(c => !c.startsWith('rename'))).toEqual(['clear'])
  expect(calls.scripts).toContainEqual(['cm_mod.sh', 'waiting', 'sid-new', '/p/c.md'])
  expect(calls.prompts).toEqual([])
})

test('a block from the stop policy reaches Claude Code', async ($, on) => {
  setup(on, { stop: { decision: 'block', reason: 'context is big', opsci: { cold: 0 } } })
  const r = await $.classic.Stop({ session_id: 'sid-old', stop_hook_active: false, background_tasks: [], session_crons: [] })
  expect(r.block).toBe('context is big')
})

test('the cache-cold notice fires after its time, and not after a new turn', async ($, on) => {
  const { calls, clock } = setup(on, { stop: { opsci: { cold: 40, notice: '[open-science] cache-cold: 40 s idle' } } })
  await start($, clock)
  await endTurn($, clock)
  await clock.advance(39000)
  expect(calls.prompts).toEqual([])
  await clock.advance(2000)
  expect(calls.prompts).toEqual(['[open-science] cache-cold: 40 s idle'])

  await endTurn($, clock)
  await $.turn.start({ turnId: 't2' })
  await clock.advance(60000)
  expect(calls.prompts.length).toBe(1)
})

test('a waker whose jobs left the queue wakes the session with its report', async ($, on) => {
  const { calls, clock } = setup(on, { scripts: {
    'cm_mod.sh wakers': { exitCode: 0, stdout: '/state/wakers/k/1.json\n', stderr: '' },
    'wait_slurm.sh --check': { exitCode: 0, stdout: 'open-science wait_slurm: jobs 7 have left the queue.\njob 7: COMPLETED\n', stderr: '' },
  } })
  await start($, clock)
  await clock.advance(60000)
  expect(calls.prompts).toEqual(['[open-science] open-science wait_slurm: jobs 7 have left the queue. job 7: COMPLETED'])
})

test('wakers done in the same poll with the same report wake the session once', async ($, on) => {
  const { calls, clock } = setup(on, { scripts: {
    'cm_mod.sh wakers': { exitCode: 0, stdout: '/state/wakers/k/1.json\n/state/wakers/k/2.json\n/state/wakers/k/3.json\n', stderr: '' },
    'wait_slurm.sh --check': { exitCode: 0, stdout: 'open-science wait_slurm: jobs 7 have left the queue.\njob 7: COMPLETED\n', stderr: '' },
  } })
  await start($, clock)
  await clock.advance(60000)
  expect(calls.prompts).toEqual(['[open-science] open-science wait_slurm: jobs 7 have left the queue. job 7: COMPLETED'])
})

test('a waker whose jobs still run sends nothing', async ($, on) => {
  const { calls, clock } = setup(on, { scripts: {
    'cm_mod.sh wakers': { exitCode: 0, stdout: '/state/wakers/k/1.json\n', stderr: '' },
    'wait_slurm.sh --check': { exitCode: 10, stdout: '', stderr: '' },
  } })
  await start($, clock)
  await clock.advance(180000)
  expect(calls.prompts).toEqual([])
})

test('a session left waiting by another process is woken at load', async ($, on) => {
  const { calls, clock } = setup(on, { scripts: {
    'cm_mod.sh resumed': { exitCode: 0, stdout: '/open-science-context:continue-context /p/c.md\n', stderr: '' },
  } })
  await start($, clock)
  expect(calls.commands).toContain('open-science-context:continue-context /p/c.md')
})

test('a /clear the user types hands the registration over', async ($, on) => {
  const { calls, clock } = setup(on)
  await start($, clock)
  await $.session.end({ reason: 'clear', sessionId: 'sid-old', resume: {} })
  await $.command.run({ command: 'clear', args: '' })
  await $.turn.start({ turnId: 't2' })
  expect(calls.scripts).toContainEqual(['cm_mod.sh', 'handover', 'sid-old', 'sid-new'])
})

test('the session is renamed when its name should change', async ($, on) => {
  const { calls, clock } = setup(on, { scripts: {
    'session_name.sh want': { exitCode: 0, stdout: 'proj · task-a', stderr: '' },
  } })
  await start($, clock)
  expect(calls.commands).toContain('rename proj · task-a')
})

// session.start fires at every reload of a mod: the session is renamed only when the naming
// script says the name should change, never forced.
test('a start or a reload never forces a rename', async ($, on) => {
  const { calls, clock } = setup(on)
  await start($, clock)
  await start($, clock)
  const wants = calls.scripts.filter(x => x[0] === 'session_name.sh' && x[1] === 'want')
  expect(wants.length).toBeGreaterThan(0)
  expect(wants.some(x => x.includes('--always'))).toBe(false)
  expect(calls.commands.filter(c => c.startsWith('rename'))).toEqual([])
})

const step = (tokens) => async function* ($, e) {
  return { turnId: e.turnId, index: e.index, answer: '', toolUses: [], stopReason: 'end_turn',
    usage: { input_tokens: tokens, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 } }
}

async function drain(stream) {
  let s = await stream.next()
  while (s.done !== true) s = await stream.next()
  return s.value
}

test('a subagent above the limit is told once to checkpoint', async ($, on) => {
  const { calls, clock } = setup(on)
  on('turn.step', step(250000))
  await start($, clock)
  await drain($.turn.step({ turnId: 't', index: 0, model: 'm', messageCount: 1, agentId: 'a1' }))
  await drain($.turn.step({ turnId: 't', index: 1, model: 'm', messageCount: 2, agentId: 'a1' }))
  expect(calls.sent.length).toBe(1)
  expect(calls.sent[0]).toContain('PAUSED')
})

test('a subagent in a session that drives no task is left alone', async ($, on) => {
  const { calls, clock } = setup(on, { registered: false })
  on('turn.step', step(250000))
  await start($, clock)
  await drain($.turn.step({ turnId: 't', index: 0, model: 'm', messageCount: 1, agentId: 'a1' }))
  expect(calls.sent).toEqual([])
})

test('the main session\'s own requests never get the subagent message', async ($, on) => {
  const { calls, clock } = setup(on)
  on('turn.step', step(250000))
  await start($, clock)
  await drain($.turn.step({ turnId: 't', index: 0, model: 'm', messageCount: 1 }))
  expect(calls.sent).toEqual([])
})

// ---- the context bar and the cold-cache question ----
const MIN = 60000

async function mainRequest($) {
  await drain($.turn.step({ turnId: 't', index: 0, model: 'm', messageCount: 1 }))
}

const BAND_PROPS = { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120, scroll: { offset: 0, bodyRows: 9 }, view: {} }

// The bar as a surface draws it: its text and color.
async function barOn($, surface = 'terminal') {
  const ui = await $.ui.mount({ plugin: 'open-science-context', surface, component: 'AbovePrompt', props: BAND_PROPS, viewport: { columns: 120, rows: 40 } })
  const t = await ui.find({ type: 'Text' })
  await ui.unmount()
  return t ? { text: t.text, color: t.props?.color } : null
}

test('the bar counts down from the last request in green, then turns yellow and cold at 59 min', async ($, on) => {
  const { clock } = setup(on)
  on('turn.step', step(123456))
  await start($, clock)
  expect(await barOn($)).toEqual({ text: 'context 1.2k tokens · no cache yet', color: undefined })
  await mainRequest($)
  expect(await barOn($)).toEqual({ text: 'context 123.5k tokens · cache warm, 59 min left', color: 'green' })
  await clock.advance(20 * MIN)
  expect(await barOn($)).toEqual({ text: 'context 123.5k tokens · cache warm, 39 min left', color: 'green' })
  await clock.advance(38 * MIN)
  expect(await barOn($)).toEqual({ text: 'context 123.5k tokens · cache warm, 1 min left', color: 'green' })
  await clock.advance(MIN)
  expect(await barOn($)).toEqual({ text: '⚠ context 123.5k tokens · cache cold (59 min idle)', color: 'yellow' })
})

test('the bar is the same in the Desktop app', async ($, on) => {
  const { clock } = setup(on)
  on('turn.step', step(123456))
  await start($, clock)
  await mainRequest($)
  await clock.advance(60 * MIN)
  expect(await barOn($, 'desktop')).toEqual({ text: '⚠ context 123.5k tokens · cache cold (60 min idle)', color: 'yellow' })
})

test('below 1000 tokens the bar counts every token', async ($, on) => {
  const { clock } = setup(on)
  on('turn.step', step(900))
  await start($, clock)
  await mainRequest($)
  expect((await barOn($)).text).toBe('context 901 tokens · cache warm, 59 min left')
})

test('a subagent request does not restart the cache clock', async ($, on) => {
  const { clock } = setup(on)
  on('turn.step', step(1000))
  await start($, clock)
  await mainRequest($)
  await clock.advance(58 * MIN)
  await drain($.turn.step({ turnId: 't', index: 1, model: 'm', messageCount: 2, agentId: 'a1' }))
  await clock.advance(MIN)
  expect((await barOn($)).text).toContain('cache cold')
})

test('a resumed session keeps the time of its last request', async ($, on) => {
  const { clock } = setup(on, { store: { lastRequest: { 'sid-old': -50 * MIN } } })
  await start($, clock)
  expect(await barOn($)).toEqual({ text: 'context 1.2k tokens · cache warm, 9 min left', color: 'green' })
})

function askAnswers(on, answer: string | null, asked: string[]) {
  on('tool.call', { tool: 'AskUserQuestion' }, ($, e) => {
    asked.push(e.questions[0].question)
    if (answer === null) throw new Error('dismissed')
    return { text: answer, isError: false, value: { answers: { [e.questions[0].question]: answer } } }
  })
}

const ASK_PROPS = { title: 'Cache cold', isFocused: true, bodyColumns: 100, placement: 'inline', scroll: { offset: 0, bodyRows: 5 }, view: {} }
const askPane = $ => $.ui.mount({ plugin: 'open-science-context', surface: 'terminal', component: 'Pane', requestId: 'opsci-cold-ask', props: ASK_PROPS, viewport: { columns: 100, rows: 40 } })

async function coldSession($, on, opts = {}) {
  const ctx = setup(on, opts)
  on('turn.step', step(1000))
  await start($, ctx.clock)
  await mainRequest($)
  await ctx.clock.advance(60 * MIN)
  return ctx
}

test('a typed prompt to a cold cache is held, and the two-button dialog sends it on Submit', async ($, on) => {
  const asked: string[] = []
  askAnswers(on, 'Submit', asked)
  const { calls } = await coldSession($, on)
  const r = await $.prompt.submit({ text: 'go on', wait: false, origin: { kind: 'composer' } })
  expect(r.drop).toContain('Not sent yet')
  expect(calls.prompts).toEqual([])
  expect(calls.order).toContain('open opsci-cold-ask')
  const ui = await askPane($)
  expect((await ui.find({ type: 'Text' })).text).toContain('The cache is cold (60 min since the last request)')
  const buttons = ['submit', 'cancel'].map(async k => (await ui.find({ key: k }))?.props?.label)
  expect(await Promise.all(buttons)).toEqual(['Submit', 'Do not submit'])
  await ui.press({ key: 'submit' })
  expect(calls.prompts).toEqual(['go on'])
  expect(asked).toEqual([])              // Claude Code's question (with its Other answers) is not used
})

test('Do not submit in the dialog sends nothing and puts the prompt back in the box', async ($, on) => {
  const { calls } = await coldSession($, on)
  await $.prompt.submit({ text: 'go on', wait: false, origin: { kind: 'composer' } })
  const ui = await askPane($)
  await ui.press({ key: 'cancel' })
  expect(calls.prompts).toEqual([])
  expect(calls.filled).toEqual(['go on'])
})

test('a typed prompt waits for no question while the cache is warm', async ($, on) => {
  const { calls, clock } = setup(on)
  on('turn.step', step(1000))
  await start($, clock)
  await mainRequest($)
  await clock.advance(58 * MIN)
  const r = await $.prompt.submit({ text: 'warm', wait: false, origin: { kind: 'composer' } })
  expect(r.drop).toBeUndefined()
  expect(calls.prompts).toEqual(['warm'])
  expect(calls.order).not.toContain('open opsci-cold-ask')
})

test('where the dialog cannot be placed, Claude Code asks instead', async ($, on) => {
  const asked: string[] = []
  askAnswers(on, 'Do not submit', asked)
  const { calls } = await coldSession($, on, { placed: false })
  const r = await $.prompt.submit({ text: 'go on', wait: false, origin: { kind: 'composer' } })
  expect(asked.length).toBe(1)
  expect(r.drop).toContain('Not submitted')
  expect(calls.filled).toEqual(['go on'])
})

test('a Remote Control prompt is asked about too', async ($, on) => {
  const { calls, clock } = setup(on)
  on('turn.step', step(1000))
  const asked: string[] = []
  askAnswers(on, null, asked)
  await start($, clock)
  await mainRequest($)
  await clock.advance(60 * MIN)
  const r = await $.prompt.submit({ text: 'go on', wait: false, origin: { kind: 'bridge' } })
  expect(asked.length).toBe(1)
  expect(r.drop).toContain('Not submitted')
  expect(calls.prompts).toEqual([])
})

test('a warm cache, a slash command and a task notification pass without a question', async ($, on) => {
  const { calls, clock } = setup(on)
  on('turn.step', step(1000))
  const asked: string[] = []
  askAnswers(on, 'Do not submit', asked)
  await start($, clock)
  await mainRequest($)
  await clock.advance(10 * MIN)
  await $.prompt.submit({ text: 'warm', wait: false, origin: { kind: 'composer' } })
  await clock.advance(60 * MIN)
  await $.prompt.submit({ text: '/clear', wait: false, origin: { kind: 'composer' } })
  await $.prompt.submit({ text: 'task done', wait: false, origin: { kind: 'task-notification' } })
  expect(asked).toEqual([])
  expect(calls.prompts).toEqual(['warm', '/clear', 'task done'])
})
