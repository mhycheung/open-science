# R03: Claude Code and Codex work the same

Every update and new feature must work the same in Claude Code and in Codex.

- **Skills and hooks.** Both agents follow one procedure: the same steps, the same questions
  with the same options, and the same explanations to the user. Only agent mechanics may
  differ (install and update commands, `${CLAUDE_PLUGIN_ROOT}`, settings files, hook trust,
  how a session is cleared or restarted). Write those as substitutions, in the skill's Codex
  note or its `codex.md`; never as a separate Codex procedure that shortens or skips steps.
  A hook message has the same wording, under the same condition, for both agents.
- **Template.** An instruction that Claude Code gets from `CLAUDE.md` or `.claude/` also
  reaches Codex, through `AGENTS.md`, `config/codex.md` or `.codex/`.
- **Tests.** A test that involves the agent harness (hooks, jumps, pane registration,
  resurrection, plugin manifests, installation) covers both Claude Code and Codex.
- **Docs.** Docs describe both agents, with Claude Code first: Claude Code is the default
  path and Codex follows as a note or a later section.
