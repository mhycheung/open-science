# Codex project instructions

The shared research rules are in `AGENTS.md`. Claude Code uses `CLAUDE.md` and
`.claude/agents/`; Codex uses this file and `.codex/agents/`.

Main agents load `open-science-project:context-files` at startup. Invoke the framework
skills by their installed full names; when a skill is unavailable, report that and use
the documented `opsci` command instead of silently choosing a similarly named skill.
The roles `med-effort`, `high-effort`, `low-effort`, `literature`, and `text` have the
same contracts as the Claude Code roles. Use `med-effort` by default; use `high-effort`
only after a recorded failed medium-effort attempt. The active Codex model is inherited.

<!-- opsci:context -->
Load `open-science-context:context-management` and follow its Codex instructions.
Durable context files are shared; session controls are specific to the running agent.
Never send Claude Code terminal commands to a Codex pane.
<!-- /opsci:context -->
<!-- opsci:notion -->
Load `open-science-project:notion`. Run `opsci notion sync` after changes. Use `/hooks`
to review and trust the project's Notion Stop hook before relying on automatic sync.
<!-- /opsci:notion -->

Before relying on plugin hooks, review and trust them using Codex `/hooks`. They check
patches for human-verification changes, context caps, and accidental public pushes.
Shell and custom-tool edits can bypass edit checks: also run `opsci context check`
and the publication checks. Do not treat hooks as a complete security boundary.
Publish only through the requested publish skill and its checked, approved export.
Credentials remain outside the project; only the user enters them in their own terminal.
If the sandbox refuses a required git operation, request approval for that operation.
If it cannot be approved, report the uncommitted state; never claim a commit succeeded.
