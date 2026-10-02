import { expect, test } from 'claude-code/testing'

test('this Claude Code runs mods', async ($) => {
  const answer = await $.command.run({ command: 'open-science-mod-probe', args: '' })
  expect(answer.text).toBe('mods run')
})
