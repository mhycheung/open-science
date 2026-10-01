# Context management with Codex

Claude Code follows the main skill's procedure. Codex uses this reference instead for
session mechanics; `open-science-project:context-files` still governs the research record.
Resolve the installed plugin root from this skill's directory (two parents). In the commands
below, `SCRIPTS` means that root's `scripts/` directory; use its actual path in each shell call.

## Register and resume work

Run `bash "$SCRIPTS/pane_context.sh" set <context file>` when starting or switching tasks.
`get` returns the registered file. Registration can use `CODEX_THREAD_ID` without tmux;
never invent a session id or reuse another session's registration. Subagents do not register.
If a path was given explicitly, it remains the source for handoff even when registration
is unavailable: report that limitation, read it, and continue with durable file updates.

State is stored under `OPSCI_STATE_DIR` (default `~/.local/state/open-science`). The tool
sandbox must allow writing there to register or request automated handoffs. Do not bypass
the sandbox or silently widen permissions. See the framework's agent setup guide for a
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
Run the request as the last tool call, then end the turn:

- Active: `bash "$SCRIPTS/jump.sh" active <context file>`.
- Waiting on SLURM: first `bash "$SCRIPTS/wait_slurm.sh" --notify <jobid>...`, then
  `bash "$SCRIPTS/jump.sh" wait <context file>`. The Stop hook starts the detached waker.

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

Use the project's `.codex/agents/` roles with the shared dispatch contracts. Give every
agent its output paths, relevant context sections, budget and stopping condition. Keep
durable subcontext notes; do not assume Codex exposes Claude's transcript/token interfaces.
For long SLURM jobs, return the job id and next step to the main agent, which owns the waker.
