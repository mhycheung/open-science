// Does nothing but answer one command; its test passes only where Claude Code runs mods.
export function register(on) {
  on('command.run', { command: 'open-science-mod-probe' }, async () => ({ text: 'mods run' }))
}
