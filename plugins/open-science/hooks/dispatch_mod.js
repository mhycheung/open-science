// open-science dispatch inside Claude Code (a Claude Code mod).
//
// A session that open-science:dispatch started with a prompt has OPSCI_DISPATCH_PROMPT set
// to a file in the state directory that holds the prompt. When the session starts, the mod
// claims the file (scripts/dispatch.sh claim: print it and remove it, once) and submits the
// prompt as the user's own words. A file already claimed (by dispatch.sh, which types the
// prompt into the pane when no mod claims it in time, or by an earlier start of this
// session) sends nothing, so the prompt is sent once.

export function register(on) {
  on('session.start', async ($, e, next) => {
    const file = await $.env.get('OPSCI_DISPATCH_PROMPT')
    if (file) {
      // Not for the processes this session starts (a nested agent, a dispatch from here).
      await $.env.set('OPSCI_DISPATCH_PROMPT', undefined)
      $.clock.after(0, async () => {
        let r
        try {
          r = await $.process.run(['bash', [$.plugin.root, 'scripts', 'dispatch.sh'].join('/'), 'claim', file], { timeoutMs: 10000 })
        } catch {
          return
        }
        const text = r.exitCode === 0 ? r.stdout.replace(/\n+$/, '') : ''
        if (text.trim()) await $.prompt.submit({ text, asUser: true })
      })
    }
    return next(e)
  })
}
