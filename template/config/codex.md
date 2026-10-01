# Codex only

`AGENTS.md` holds the instructions for every agent; read it first. This file holds what
Codex uses in place of `CLAUDE.md`, which Codex does not read. The sections Skills, Dispatch
tiers and Sessions are the same as in `CLAUDE.md`; keep them in step.

## Skills

The framework's skills come from the open-science plugins. **Always name them in full**
(`open-science-project:new-task`): a skill with the same short name installed elsewhere may
otherwise be chosen instead.

- Starting a task: `open-science-project:new-task`.
- Keeping context files current: `open-science-project:context-files`. Main agents load it at
  session start.
<!-- opsci:context -->
- Session jumps: `open-science-context:context-management`. Main agents load it at session
  start, with `open-science-project:context-files`.
- Taking over work from a context file: `open-science-context:continue-context`.
- Questions about a context file, without acting on it: `open-science-context:advise-with-context`.
<!-- /opsci:context -->
- Publishing: `open-science-publish:publish`; data releases: `open-science-publish:zenodo-release`
  (plugin `open-science-publish`, if installed).
<!-- opsci:notion -->
- Notion (this project is mirrored there, `AGENTS.md` section 10):
  `open-science-project:notion`. Main agents load it at session start.
<!-- /opsci:notion -->

## Dispatch tiers

Agent roles are defined in `.codex/agents/` (the same contracts as `.claude/agents/`). Every
dispatch names one explicitly.

| tier | use for |
|---|---|
| `med-effort` | the default: implement a specified change, run a specified sweep, review a diff, analyse a result |
| `high-effort` | only after a `med-effort` attempt at the same task has provably failed, with the failure recorded |
| `low-effort` | fully specified mechanics with no decision left: run a given command, a mechanical edit, pull numbers from a file |
| `literature` | read a source and judge it; find which section supports a claim |
| `text` | mechanical work on text: find a string, extract a table, assemble a document |

The model behind each tier is set in its file; change it to the models you have.

<!-- opsci:context -->
## Sessions

Agents clear their own conversation and resume from the context files ("jumps"); this is
expected. The `open-science-context:context-management` skill describes when and how.
<!-- /opsci:context -->

## Codex mechanics

Where a skill gives a Claude Code command or path, use the Codex substitution the skill
names (its `codex.md`, or its Codex note); every question, rule and step still applies.
`${CLAUDE_PLUGIN_ROOT}` is not set in Codex: use the installed plugin root, two parents
above the skill's directory. When a framework skill is unavailable, report that and use the
documented `opsci` command; never choose a similarly named skill instead.
<!-- opsci:context -->
Never send Claude Code terminal commands (such as `/clear`) to a Codex pane.
<!-- /opsci:context -->
<!-- opsci:notion -->
Run `opsci notion sync` after changes. Review and trust the project's Notion Stop hook with
`/hooks` before relying on automatic sync.
<!-- /opsci:notion -->

Before relying on plugin hooks, review and trust them using Codex `/hooks`. They check
patches for human-verification changes, context caps, and accidental public pushes.
Shell and custom-tool edits can bypass edit checks: also run `opsci context check`
and the publication checks. Do not treat hooks as a complete security boundary.
Publish only through the requested publish skill and its checked, approved export.
Credentials remain outside the project; only the user enters them in their own terminal.
If the sandbox refuses a required git operation, request approval for that operation.
If it cannot be approved, report the uncommitted state; never claim a commit succeeded.
