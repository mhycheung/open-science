# slurm-resurrect (optional plugin)

For development on a compute node of a computing cluster that uses the SLURM
scheduler, with Claude Code (or Codex) running in tmux inside a batch job. When the batch
job reaches its time limit, this plugin rebuilds your tmux session in a new job and resumes every Claude Code session that was running in
it, with `--resume`, and every Codex session it can identify, with `codex resume` (section
"Codex" below). Windows, panes, layout and working directories are
restored. Nothing in the open-science plugins depends on this plugin.

How it works is described in `reference/mechanism.md`. The optional coupling
to the session jumps of the open-science-context plugin is described in `reference/jump-hook.md`.

## Requirements

- **tmux is required.** The unit of resurrection is a tmux session. Claude Code or Codex
  must run inside tmux, and the tmux server must run inside the SLURM job.
- A SLURM batch job. Account, partition and time limit are read from the
  running job each time you register; you do not configure them.
- `bash`, `jq`, `flock`, `setsid`, `sbatch`, `squeue`, `scancel`.
- The state directory must be on a filesystem that the compute nodes share.
  Default: `${XDG_STATE_HOME:-$HOME/.local/state}/slurm-resurrect`; set
  `RR_STATE_DIR` to use another one.

## Enable

Only you can register a session. Agents cannot: the skill is user-only
(`disable-model-invocation`), and the script refuses `register`, `reset`, `set`
and `set-notify` when it runs under Claude Code (other than through the prompt
hook below) or Codex. The refusal is logged. This stops an agent from registering by
accident; it is not a security boundary.

From any Claude Code pane in the tmux session, type:

```
/slurm-resurrect:resurrect register
```

The plugin's `UserPromptSubmit` hook runs the command before the model sees
the prompt. Or run it in a plain terminal pane of the session (use this with Codex,
which has no slash command for it):

```
bash <plugin dir>/scripts/rr_registry.sh register
```

The first `register` only shows a warning (next section) and registers
nothing. Run it again to register. The warning is not shown again.

Options:

- `--permission-mode MODE` (Claude Code only): the widest permission mode a resumed session
  may get. Without it, each Claude pane resumes in the mode it was started with. See
  "Remote Control and permission mode".
- `--remote-control on|off` (Claude Code only): Remote Control for the resumed sessions. Default on.
- `session ...`: register named tmux sessions instead of the current one.

Other commands: `status`, `timeleft`, `reset [N]` (new hop budget; the default
cap is 10 hops), `set KEY VALUE` (run `set` alone for the keys),
`set-notify CMD` (run `CMD` with the message in `$RR_MSG` at each notice),
`set auto_trust true|false` (see "Folder trust" below).
Agents may run `note "<text>"`, which delivers a message to their own pane
after the hop.

To resume with a command other than `claude` (for example a wrapper that sets
the config directory), run `set launch_cmd <command>`. The resumed process gets
back the `CLAUDE_CONFIG_DIR` the original process had. For Codex, use
`set codex_launch_cmd <command>`.

## Codex

Codex panes in a registered tmux session are resumed too, with these differences:

- **Register from a plain terminal pane.** The prompt hook is Claude Code's; under Codex
  the script refuses `register` like any agent call (`CODEX_THREAD_ID` set, or a `codex`
  process among the ancestors).
- **The thread must be known.** Codex keeps no state file that maps a process to its
  thread. The open-science-context plugin's Codex hook (`cx_hook.sh`) records it per pane;
  without that hook, or before it has run in the pane, the pane is rebuilt as a plain
  shell and the successor's notice names it.
- **Options are the TUI's own.** The resume command is
  `[CODEX_HOME=...] codex resume <the original options> -m <model> <thread>`. Sandbox,
  approval policy, profile and `-c` overrides are copied from the running process
  exactly; `--permission-mode` and Remote Control are Claude Code settings and do not
  apply. Nothing that widens permissions is added. A process with an option the plugin
  does not know, or started with `--worktree`, is not resumed.
- **Nothing is typed into Codex.** The wind-down message (busy panes only) and notes go
  through `codex queue --thread`. A Codex folder-trust question is never answered: Codex
  saves that answer in your configuration, so the plugin leaves it to you and notifies.
- A Codex session jump in flight at the limit is not finished after the hop; the thread is
  resumed as it was.
- `set codex_launch_cmd <command>` resumes with a command other than `codex`.

## Disable

- `remove [session]`: take one tmux session out.
- `stop`: end the lineage and cancel the queued successor job.
- Disabling or uninstalling the plugin does **not** cancel a successor that is
  already queued: queued jobs run a copy of the scripts kept in the state
  directory. Run `stop` first.

## Remote Control and permission mode

A resumed session runs with no one watching. The first time you register, the
plugin says so and explains the settings below, which apply to Claude Code panes only
(Codex panes keep their own options; see "Codex" above):

- **Permission mode.** Each Claude pane is resumed in the permission mode its
  process was started with, read from its command line (`--permission-mode`, or
  `--dangerously-skip-permissions`, which counts as `bypassPermissions`). A pane
  started without a mode flag is resumed without one, so Claude Code's settings
  decide, as they did before. `--permission-mode MODE` at registration sets the
  widest mode any pane may get: a pane started in a wider mode is resumed in
  `MODE`, and a pane whose own mode cannot be read is resumed in `MODE`. Without
  it, a pane whose mode cannot be read is resumed without a mode flag. A pane
  started in `bypassPermissions` resumes in it unless you set a narrower `MODE`:
  in bypass mode the resumed session runs every command, including edits and
  deletions, without asking. If the registration record of a session is missing,
  its panes resume in at most `manual` and with Remote Control off. The order used,
  narrowest first: `plan`, `dontAsk`, `manual`, `acceptEdits`, `auto`,
  `bypassPermissions`.
- **Remote Control**, default on. The resumed session can be read and driven
  from any device logged in to your Claude account. Turn it off with
  `--remote-control off`.

Both are recorded per registered session and applied at every hop.

### Folder trust

A resumed Claude session can open in a folder Claude Code has not trusted yet,
for example when the pane's directory or the config directory changed. Claude
Code then asks "Do you trust the files in this folder?". The plugin answers
"Yes, I trust this folder" for you only if you chose so (`set auto_trust true`,
asked during setup). Accepting lets that folder's `.claude/settings.json` hooks
and MCP servers run without your review, so a folder holding code you did not
write (a cloned repository, for example) could run commands as you. With
`auto_trust` false, the default, the plugin presses nothing and notifies you
each time a session waits at the question. A Codex folder-trust question is
always left to you.

### What the plugin checks

- A tmux server is started, or talked to, only on a socket whose directory is
  a real directory owned by you with no group or other permissions. If the
  recorded directory fails this (on a fresh node another user can create
  the `tmux-<uid>` directory in `/tmp` first), the session is rebuilt on a socket in a new private
  directory, and the notice gives the attach command for it.
- Values typed into a pane's shell (session name, model, config directory,
  session id) are quoted, and a Claude session id must be a UUID.
- Notes and resume prompts typed into a pane have control characters removed
  and cannot start with `/`, `!` or `#` (only the core's
  `/open-science-context:continue-context` prompt can); a note is always
  delivered behind the prefix `[slurm-resurrect] Message you saved for yourself
  ...`. A `/slurm-resurrect:resurrect` prompt submitted while a script is typing
  into the pane is not run.
- If no Claude or Codex pane is alive after a hop, the lineage ends: the user is
  notified, no successor is queued and the job exits. A session with no live
  agent pane is not carried to the next job.

## Queueing

The successor job is submitted as soon as a session is registered. Choose how
it waits with `set queue_mode`:

- `afterany` (default): `--dependency=afterany:<job>`. The successor starts
  after the current job ends. On some clusters a job waiting on a dependency
  gains no age priority, so the gap between jobs can be long.
- `early`: `--begin=<end of current job - early_lead_seconds>` (default 4 h),
  no dependency. The successor becomes eligible, and gains priority, while the
  current job still runs. If it starts before the current job ends, it asks the
  current job for a final snapshot, cancels the current job, then rebuilds.

Before the limit (`winddown_threshold_seconds`, default 30 min) each active
Claude or Codex pane gets one message: the job is near its limit and the session will
be resumed; if the work in hand will not finish, stop cleanly and leave a note
for after the hop. At `pause_threshold_seconds` (default 120 s) the final
snapshot is taken.

## Session jumps

If the open-science-context plugin ("the core" here) is installed, the two plugins coordinate. From
the wind-down message until the hop, the core's session jumps are inhibited in
the registered panes. Where Claude Code runs the core's mod, the mod does the rest
itself: a resumed session keeps its session id, and with it its registered task and its
queued SLURM wakers, and a session that was waiting on background tasks (which died
with the old job) is woken with `/open-science-context:continue-context`. Where the mod
is not loaded, this plugin does it: after the hop, a jump that the time limit
interrupted is finished, and a pane that was waiting after a wait jump is woken with
`/open-science-context:continue-context`. Without the core, the plugin works the same
way and skips these steps. Details: `reference/jump-hook.md`.

## Known limits

- A session started with `--plugin-dir` loses that flag on resume unless
  `launch_cmd` includes it.
- The resume command line was checked with Claude Code 2.1.280 in a tmux pane
  (same session id, permission mode, Remote Control and session name), but not
  inside a real SLURM hop; the real-hop test uses a stand-in program.
- If a resumed session shows the workspace trust dialog and `auto_trust` is
  true, the plugin selects "Yes, I trust this folder"; if it cannot select it,
  it presses nothing and notifies you. With `auto_trust` false it presses
  nothing and notifies you.
- Registration is checked by process ancestry, not enforced by the operating
  system.
- The Codex resume was checked against a stand-in `codex` (tests/slurm_resurrect/
  test_codex_panes.sh), not with codex-cli inside a real SLURM hop.
