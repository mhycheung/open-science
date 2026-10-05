// opsci: planted-leaks (this file uses fake absolute paths on purpose)
// Tests of the dispatch mod (hooks/dispatch_mod.js), run with
// `claude plugin test plugins/open-science` (tests/test_dispatch.py runs them).
// Claude Code is stubbed: the environment, dispatch.sh claim, and the prompts the mod sends.
// dispatch.sh itself is tested by tests/test_dispatch.py.
import { expect, mock, test } from 'claude-code/testing'

function setup(on, opts: { env?: Record<string, string>; claim?: { exitCode: number; stdout: string } } = {}) {
  const calls = { prompts: [] as { text: string; asUser?: boolean }[], scripts: [] as string[][], unset: [] as string[] }
  const clock = mock.clock(on)
  mock.env(on, opts.env ?? {})
  on('env.set', ($, e) => { if (e.value === undefined) calls.unset.push(e.name); return { value: undefined } })
  on('process.run', ($, e) => {
    calls.scripts.push([String(e.argv[1]).split('/').pop(), ...e.argv.slice(2)])
    return { value: { exitCode: 0, stderr: '', ...(opts.claim ?? { exitCode: 1, stdout: '' }) } }
  })
  on('prompt.submit', ($, e) => { calls.prompts.push({ text: e.text, asUser: e.origin?.asUser }); return { text: e.text } })
  on('session.start', () => ({ cwd: '/work' }))
  return { calls, clock }
}

async function start($, clock) {
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
}

test('a dispatched session submits its prompt once, as the user', async ($, on) => {
  const { calls, clock } = setup(on, { env: { OPSCI_DISPATCH_PROMPT: '/s/dispatch/a.prompt' }, claim: { exitCode: 0, stdout: 'clean up the home directory\n' } })
  await start($, clock)
  expect(calls.scripts).toEqual([['dispatch.sh', 'claim', '/s/dispatch/a.prompt']])
  expect(calls.prompts).toEqual([{ text: 'clean up the home directory', asUser: true }])
  expect(calls.unset).toEqual(['OPSCI_DISPATCH_PROMPT'])
})

test('a prompt already claimed sends nothing', async ($, on) => {
  const { calls, clock } = setup(on, { env: { OPSCI_DISPATCH_PROMPT: '/s/dispatch/a.prompt' }, claim: { exitCode: 1, stdout: '' } })
  await start($, clock)
  expect(calls.scripts.length).toBe(1)
  expect(calls.prompts).toEqual([])
})

test('a session not started by dispatch does nothing', async ($, on) => {
  const { calls, clock } = setup(on)
  await start($, clock)
  expect(calls.scripts).toEqual([])
  expect(calls.prompts).toEqual([])
})
