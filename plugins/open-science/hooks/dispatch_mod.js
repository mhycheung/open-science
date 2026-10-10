// open-science dispatch inside Claude Code (a Claude Code mod).
//
// A session that open-science:dispatch started with a prompt has OPSCI_DISPATCH_PROMPT set
// to a file in the state directory that holds the prompt. When the session starts, the mod
// claims the file (scripts/dispatch.sh claim: print it and remove it, once) and submits the
// prompt as the user's own words. A file already claimed (by dispatch.sh, which types the
// prompt into the pane when no mod claims it in time, or by an earlier start of this
// session) sends nothing, so the prompt is sent once.
//
// The host refuses $.prompt.submit for text beginning with `/`. A prompt that is a slash
// command of this session (`/quota-cleanup`, `/review 12`) is run with $.command.run
// instead. Any other prompt beginning with `/` (a path, a name the session does not know) is
// left unclaimed, for dispatch.sh to type into the pane as the person would.

// `/name args`: the name without its slash and the rest; null for any other text.
export function slashCommand(text) {
  const m = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(text.trim())
  return m ? { command: m[1], args: (m[2] ?? '').trim() } : null
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    const file = await $.env.get('OPSCI_DISPATCH_PROMPT')
    if (file) {
      // Not for the processes this session starts (a nested agent, a dispatch from here).
      await $.env.set('OPSCI_DISPATCH_PROMPT', undefined)
      $.clock.after(0, async () => {
        const script = [$.plugin.root, 'scripts', 'dispatch.sh'].join('/')
        const run = async (verb) => {
          try {
            const r = await $.process.run(['bash', script, verb, file], { timeoutMs: 10000 })
            return r.exitCode === 0 ? r.stdout.replace(/\n+$/, '') : ''
          } catch {
            return ''
          }
        }
        const cmd = slashCommand(await run('peek'))
        if (cmd && !(await $.command.list()).some((c) => c.name === cmd.command)) return
        const text = await run('claim')
        if (!text.trim()) return
        if (cmd) await $.command.run(slashCommand(text) ?? cmd)
        else await $.prompt.submit({ text, asUser: true })
      })
    }
    return next(e)
  })
}
