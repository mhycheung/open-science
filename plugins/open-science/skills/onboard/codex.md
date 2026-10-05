# Onboarding with Codex: substitutions

Codex follows `SKILL.md` step by step and asks every question with the texts in
`explanations.md`. Nothing is skipped or shortened. This file lists only the Claude Code
mechanics that differ in Codex; everything not listed here applies unchanged.

**Everywhere**
- Questions: use Codex's question tool when it is available, with the same options, order
  and texts. Otherwise ask in plain text: list every option with its full text from
  `explanations.md` and the recommended option first, and wait for the answer.
- `${CLAUDE_PLUGIN_ROOT}`: Codex does not set it. Use this skill's directory, two parents
  up (the installed plugin root), as an absolute path in each shell call.
- Where a text says "Claude" or "Claude Code", say "Codex".
- `<config>/settings.json` (`$CLAUDE_CONFIG_DIR` or `~/.claude`): never edit it from Codex.
  Codex settings are in `~/.codex/config.toml` (or `$CODEX_HOME`); keep existing keys.

**Step 2, checks.** `onboard_check.sh` reports Claude Code plugins. For Codex, take the
installed plugins from `codex plugin list` and the marketplace from
`codex plugin marketplace list`. The other keys apply unchanged.

**Step 5, install.** `codex plugin add <plugin>@open-science`. If the marketplace is
missing, first `codex plugin marketplace add mhycheung/open-science`. Codex does not install
dependencies: install `open-science-project` before `open-science-context`. New plugins load
only in a new Codex session. If this Codex has no `plugin` command, say it must be upgraded.

**Step 6, `opsci`.** Take the repository URL from `codex plugin marketplace list`.

**Branch 1, context management.** Codex has no mods: skip 1m and say the text that follows
it ("Until then, ...", without "Until then,"), with "Codex inside tmux". Codex must be started from a
shell in the pane (not `exec codex`), so that a jump can start a new session there.
- 1e, session names: Codex has no session names to set. Do not ask; say this in one line.
- 1f, jumps: ask the "1f" text, but leave out the sentence about Claude's one-hour cache
  (Codex has no cache-cold notice). Codex reads `OPSCI_JUMPS` from the environment: offer a
  launch command such as `OPSCI_JUMPS=<choice> codex --add-dir ~/.local/state/open-science/inbox`
  after `mkdir -p ~/.local/state/open-science/inbox` (the sandbox must be able to write the
  state directory's `inbox` and nothing else of it: the hooks act on the rest outside the
  sandbox; `--add-dir` grants only the inbox), and
  keep the user's own model, sandbox and approval options.
- Then the hooks: the context and project plugins have hooks that run only after the user
  trusts them with `/hooks` in Codex. Show what they run and ask the user to review and
  trust them. Never mark them trusted yourself or bypass the trust check.
- 1d: `onboard_check.sh` looks only in Claude's skills folder. Also list `~/.codex/skills/`
  (or `$CODEX_HOME/skills/`) for directories named like a framework skill, and treat them alike.

**Branch 4, SLURM resurrection.** 4a: Codex instead of Claude in the batch job. 4b: the
launch command is the user's `codex` command. 4c and 4d (permission mode, Remote Control)
are Claude Code settings: a Codex pane resumes with the sandbox, approval and other options
it was started with. Ask them only if the user will also run Claude Code in that tmux
session; otherwise say this in one line. 4f (folder trust): ask it with the same text and
options if the user will also run Claude Code there; either way say that a Codex pane's
folder-trust question is always left to the user, whatever they choose. Codex panes resume
only after the context plugin's Codex hook has run in them; say so. 4e, 4f and 4g: Codex has
no `/slurm-resurrect:resurrect` command. Give the terminal form, `bash <plugin root of
slurm-resurrect>/scripts/rr_registry.sh <command>`, to type in a plain shell pane of that
tmux session (outside Codex): for 4e `set queue_mode early`; for 4f `set auto_trust true` or
`set auto_trust false` if 4f was asked; for 4g `register` (with `--permission-mode <mode>`
if a limit was chosen in 4c, and `--remote-control off` if chosen in 4d), run twice as in
the text.

**Step 9, tokens.** `secret_file.sh` refuses to run inside Codex too. The deny rule
(`Read(~/.config/opsci/**)`) has no Codex equivalent: skip that offer and say so.

**Step 10, summary.** "Restart Codex" instead of "restart Claude Code". Add any hooks that
still wait for `/hooks` trust to the list of what is left for the user.

**Updates.** `codex plugin marketplace upgrade open-science` (a marketplace added from
GitHub; for a local checkout, `git pull` it instead), then `codex plugin add` each
installed plugin again and restart Codex; recheck hook trust afterwards.
