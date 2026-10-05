# Dispatch with Codex: substitutions

Codex follows `SKILL.md` step by step, with the same questions and reports. Only these
mechanics differ.

- `${CLAUDE_PLUGIN_ROOT}`: Codex does not set it. Use this skill's directory, two parents
  up (the installed plugin root), as an absolute path.
- Step 3: pass `--agent codex`. The script types `${OPSCI_DISPATCH_CODEX_CMD:-codex}` into
  the window, with the prompt as the command's argument (Codex has no mods, so nothing is
  pasted). Codex sessions have no names: the name is the window's only. If the user asked
  for a Claude Code agent, pass `--agent claude` as in `SKILL.md`.
- Step 4: the prompt is `argument` (given on the command line) or `none`. Codex has no
  per-session Remote Control flag: say that the session can be followed remotely only if
  the user runs Codex's remote-control daemon (`codex remote-control start`), and do not
  start it yourself.
- When it fails: the variable to set is `OPSCI_DISPATCH_CODEX_CMD`.
