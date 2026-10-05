# Context management with Codex: substitutions

Codex follows `SKILL.md`: when to register, when to jump and when not to, the jump's
record (task and project context, log line, notification), and the subagent contract with
its `PAUSED`, `SUBMITTED` and `DONE` reports. `open-science-project:context-files` governs
the context files. This file lists only what differs in Codex.

Codex has no mods. Where `SKILL.md` says the mod does something (clearing, resuming,
waking, renaming, counting a subagent's tokens), Codex uses the tmux path and the hooks
below, as Claude Code does when its mod is not loaded.

`${CLAUDE_PLUGIN_ROOT}`: Codex does not set it. Resolve the installed plugin root from this
skill's directory (two parents). Below, `SCRIPTS` means that root's `scripts/` directory;
use its actual path in each shell call.

## Register and resume work

Run `bash "$SCRIPTS/pane_context.sh" set <context file>` when starting or switching tasks.
`get` returns the registered file. Registration can use `CODEX_THREAD_ID` without tmux;
never invent a session id or reuse another session's registration. Subagents do not register.
If a path was given explicitly, it remains the source for handoff even when registration
is unavailable: report that limitation, read it, and continue with durable file updates.

State is stored under `OPSCI_STATE_DIR` (default `~/.local/state/open-science`). Tool
calls write only its `inbox` subdirectory, which the tool sandbox must allow writing to
register or request automated handoffs; the Codex hook turns what is there into records
when the turn ends. Do not bypass the sandbox or silently widen permissions, and never ask
for the whole state directory to be writable. See the framework's agent setup guide for a
scoped launch configuration. Hooks and tool calls must use the same state directory.
Run `bash "$SCRIPTS/pane_context.sh" check` to test access. Exit 3 explains the setup
needed; use an explicit context-file path until the user provides that access.

## Optional session jumps

These require Codex in a tmux pane, started from a shell (not replacing the shell with
`exec codex`), and the context plugin hooks reviewed and trusted using `/hooks`.
Keep the user's `OPSCI_JUMPS` choice: `all`, `wait`, or `off`. Do not change it yourself.
No tmux or no trusted hook: keep context files current and use a new session with an
explicit context path; do not attempt terminal automation.

Before a jump, save the task and project state, in-flight job ids, next step and log.
Every jump carries the report for the user that `SKILL.md` describes (`--report`, required):
the new session starts without the conversation, so the report is all the user learns of
it. Run the request as the last tool call, then end the turn:

- Active: `bash "$SCRIPTS/jump.sh" active <context file> --report "<report>"`.
- Waiting on SLURM: first `bash "$SCRIPTS/wait_slurm.sh" --notify <jobid>...`, then
  `bash "$SCRIPTS/jump.sh" wait <context file> --report "<report>"`. The Stop hook starts
  the detached waker.

The trusted Stop hook ends the old TUI and starts a fresh Codex session in the same
pane shell with its saved launch options and a prompt to read the context. It never sends
Claude `/clear` sequences to Codex. The SLURM waker uses `codex queue` to contact the
pane's new thread when the jobs leave the queue. Recheck job results; leaving the queue
does not imply success. Ordinary background tool calls or subagent completion alone are
not a supported Codex wait-jump waker. Do not jump while waiting for the user's answer.

Unknown launch options and managed `--worktree` sessions are refused rather than guessed.
Permissions are preserved from the launch command, never inferred from a hook's
`permission_mode`. Do not retry a refusal with broader permissions. Report the reason and
continue from saved context manually when needed.

The usage reader is best effort: Codex rollout format can change. A missing reading gives
no size notice. The default notice threshold is 60% of the reported context window;
`OPSCI_CODEX_JUMP_THRESHOLD` overrides it. There is no Codex cache-cold timer or automatic
Claude-style session naming. Do not apply Claude's cache lifetime assumptions to Codex.

## Subagents

Use the project's `.codex/agents/` roles. Every dispatch prompt states what `SKILL.md`
lists (subcontext path, the 200k-token stop, `PAUSED`/`SUBMITTED`/`DONE` reports, the
closing line). Codex does not expose Claude's transcript or token counters to subagents:
the subagent estimates its own size. The main agent owns the SLURM waker.
