// opsci: planted-leaks (this file uses fake absolute paths on purpose)
// Tests of the open-science context mod (hooks/context_mod.js), run with
// `claude plugin test plugins/open-science-context` (tests/test_cm_mod.py runs them).
// Claude Code is stubbed: the session id, the shell scripts the mod calls, the commands
// and prompts it starts. The scripts themselves are tested by tests/test_cm_mod.py.
import { expect, mock, test } from 'claude-code/testing'

type Calls = { commands: string[]; prompts: string[]; scripts: string[][]; sent: string[] }

// Stubs shared by every test. `stop` is what cm_stop.sh --mod answers; `scripts` maps a
// script name to its answer. A /clear makes the session id change, as in Claude Code.
function setup(on, opts: { stop?: object; scripts?: Record<string, object>; registered?: boolean } = {}) {
  const calls: Calls = { commands: [], prompts: [], scripts: [], sent: [] }
  let sid = 'sid-old'
  const clock = mock.clock(on)
  mock.env(on, { OPSCI_STATE_DIR: '/state', HOME: '/home/u' })
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
  on('command.run', ($, e) => {
    calls.commands.push((e.command + ' ' + (e.args || '')).trim())
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
