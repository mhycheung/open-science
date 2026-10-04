// The values the open-science context mod keeps in $.state, kept across a reload of the mod
// and gone when Claude Code restarts. `named`: the session id this Claude Code process has
// already named at its start.
export type Named = string

declare module 'claude-code' {
  interface PluginState {
    'open-science-context': { named: Named }
  }
}
