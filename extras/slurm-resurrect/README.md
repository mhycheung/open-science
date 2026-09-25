# slurm-resurrect (optional plugin)

For development on a compute node of a computing cluster that uses the SLURM
scheduler, with Claude Code running in tmux inside a batch job. When the batch
job reaches its time limit, this plugin rebuilds your tmux session in a new job and resumes every Claude Code session that was running in
it, with `--resume`. Windows, panes, layout and working directories are
restored. Nothing in the open-science plugins depends on this plugin.

How it works is described in `reference/mechanism.md`. The optional coupling
to the session jumps of the open-science-context plugin is described in `reference/jump-hook.md`.

## Requirements

- **tmux is required.** The unit of resurrection is a tmux session. Claude must
  run inside tmux, and the tmux server must run inside the SLURM job.
- A SLURM batch job. Account, partition and time limit are read from the
  running job each time you register; you do not configure them.
- `bash`, `jq`, `flock`, `setsid`, `sbatch`, `squeue`, `scancel`.
- The state directory must be on a filesystem that the compute nodes share.
  Default: `${XDG_STATE_HOME:-$HOME/.local/state}/slurm-resurrect`; set
  `RR_STATE_DIR` to use another one.

## Enable

Only you can register a session. Agents cannot: the skill is user-only
(`disable-model-invocation`), and the script refuses `register`, `reset`, `set`
and `set-notify` when it runs under Claude, other than through the prompt hook
below. The refusal is logged. This stops an agent from registering by
accident; it is not a security boundary.

From any Claude pane in the tmux session, type:

```
/slurm-resurrect:resurrect register
```

The plugin's `UserPromptSubmit` hook runs the command before the model sees
the prompt. Or run it in a plain terminal pane of the session:

```
bash <plugin dir>/scripts/rr_registry.sh register
```

The first `register` only shows a warning (next section) and registers
nothing. Run it again to register. The warning is not shown again.

Options:

- `--permission-mode MODE`: permission mode of the resumed sessions. Default
  `bypassPermissions`.
- `--remote-control on|off`: Remote Control for the resumed sessions. Default on.
- `session ...`: register named tmux sessions instead of the current one.

Other commands: `status`, `timeleft`, `reset [N]` (new hop budget; the default
cap is 10 hops), `set KEY VALUE` (run `set` alone for the keys),
`set-notify CMD` (run `CMD` with the message in `$RR_MSG` at each notice).
Agents may run `note "<text>"`, which delivers a message to their own pane
after the hop.

To resume with a command other than `claude` (for example a wrapper that sets
the config directory), run `set launch_cmd <command>`. The resumed process gets
back the `CLAUDE_CONFIG_DIR` the original process had.

## Disable

- `remove [session]`: take one tmux session out.
- `stop`: end the lineage and cancel the queued successor job.
- Disabling or uninstalling the plugin does **not** cancel a successor that is
  already queued: queued jobs run a copy of the scripts kept in the state
  directory. Run `stop` first.

## Remote Control and permission mode

A resumed session runs with no one watching. The first time you register, the
plugin says so and explains the two defaults:

- **Permission mode**, default `bypassPermissions`. In bypass mode a resumed
  session runs every command, including edits and deletions, without asking.
  Choose `acceptEdits`, `manual` or another mode with `--permission-mode`
  (the modes `claude --permission-mode` accepts).
- **Remote Control**, default on. The resumed session can be read and driven
  from any device logged in to your Claude account. Turn it off with
  `--remote-control off`.

Both are recorded per registered session and applied at every hop.

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
Claude pane gets one message: the job is near its limit and the session will
be resumed; if the work in hand will not finish, stop cleanly and leave a note
for after the hop. At `pause_threshold_seconds` (default 120 s) the final
snapshot is taken.

## Session jumps

If the open-science-context plugin ("the core" here) is installed, the two plugins coordinate. From
the wind-down message until the hop, the core's session jumps are inhibited in
the registered panes. After the hop, a jump that the time limit interrupted is
finished, and a pane that was waiting after a wait jump is woken with
`/open-science-context:continue-context`. Without the core, the plugin works the same
way and skips these steps. Details: `reference/jump-hook.md`.

## Known limits

- A session started with `--plugin-dir` loses that flag on resume unless
  `launch_cmd` includes it.
- The resume command line was checked with Claude Code 2.1.280 in a tmux pane
  (same session id, permission mode, Remote Control and session name), but not
  inside a real SLURM hop; the real-hop test uses a stand-in program.
- If a resumed session shows the workspace trust dialog, the plugin selects
  "Yes, I trust this folder". If it cannot select it, it presses nothing and
  notifies you.
- Registration is checked by process ancestry, not enforced by the operating
  system.
