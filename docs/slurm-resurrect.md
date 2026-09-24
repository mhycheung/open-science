# SLURM resurrection (`slurm-resurrect`)

An optional extra (plugin `slurm-resurrect`, in `extras/slurm-resurrect/`) for work on a
cluster. When a SLURM batch job reaches its time limit, it
rebuilds your tmux session in a new job and resumes every Claude Code session that was
running in it, with `--resume`. Windows, panes, layout and working directories are restored.
Nothing in the other open-science plugins depends on it.

```bash
claude plugin install slurm-resurrect@open-science
```

The full reference is `extras/slurm-resurrect/README.md`; how it works is in
`extras/slurm-resurrect/reference/mechanism.md`.

## Requirements

- **tmux.** Claude must run inside tmux, and the tmux server must run inside the SLURM
  batch job.
- A SLURM batch job. Account, partition and time limit are read from the running job each
  time you register.
- `bash`, `jq`, `flock`, `setsid`, `sbatch`, `squeue`, `scancel`.
- A state directory on a filesystem that the compute nodes share. Default:
  `${XDG_STATE_HOME:-$HOME/.local/state}/slurm-resurrect`; set `RR_STATE_DIR` to use
  another one. A `tmpfs` or a path under `/tmp` is not shared.

## Turn it on

Only you can register a session. From any Claude pane in the tmux session, type:

```
/slurm-resurrect:resurrect register
```

The plugin's `UserPromptSubmit` hook runs the command before the model sees the prompt. The
first `register` only shows a warning about the two defaults below and registers nothing;
run it again to register. The successor job is queued as soon as a session is registered.

Agents cannot register: the skill cannot be invoked by the model, and the script refuses
`register`, `reset`, `set` and `set-notify` when it runs under Claude other than through the
prompt hook, and logs the attempt. This stops an agent from registering by accident; it is
not a security boundary.

Options of `register`:

| option | default | what |
|---|---|---|
| `--permission-mode MODE` | `bypassPermissions` | permission mode of the resumed sessions (any mode `claude --permission-mode` accepts, for example `acceptEdits`) |
| `--remote-control on\|off` | `on` | whether the resumed sessions can be read and driven from any device logged in to your Claude account |
| `session ...` | the current session | register named tmux sessions instead |

A resumed session runs with no one watching. In `bypassPermissions` mode it runs every
command, including edits and deletions, without asking. Both settings are recorded per
session and applied at every hop.

## Commands

Type them as `/slurm-resurrect:resurrect <command>`, or run
`bash <plugin dir>/scripts/rr_registry.sh <command>` in a plain terminal pane of the session.

| command | what |
|---|---|
| `register [options] [session ...]` | opt the tmux session in (see above) |
| `status` | print the lineage settings (including the queued successor job) and the tmux sessions registered in this job |
| `timeleft` | the time left in the current job |
| `remove [session]` | take one tmux session out |
| `stop` | end the lineage and cancel the queued successor job |
| `reset [N]` | a new hop budget (default cap 10 hops) |
| `set KEY VALUE` | change a setting; `set` alone lists the keys |
| `set-notify CMD` | run `CMD` with the message in `$RR_MSG` at each notice |
| `note "<text>"` | (agents may run this) save a message delivered to this pane after the hop |

Settings for `set`: `queue_mode`, `early_lead_seconds`, `handoff_timeout_seconds`,
`handoff_grace_seconds`, `snapshot_interval_seconds`, `pause_threshold_seconds`,
`winddown_threshold_seconds`, `launch_cmd`, `sbatch_extra`, `account`, `partition`,
`default_time_limit`, `nodes`, `ntasks`, `cpus_per_task`, `context_window_suffix`,
`core_scripts_dir`.

To resume with a command other than `claude` (for example a wrapper that sets the config
directory), run `set launch_cmd <command>`.

To send resurrection notices through `opsci notify` (useful with the Slack back end), type
`/slurm-resurrect:resurrect set-notify <path to opsci> notify "$RR_MSG"`, with the full path
from `command -v opsci`, since the batch job's `PATH` may differ.

## Queueing

| `queue_mode` | how the successor waits |
|---|---|
| `afterany` (default) | `--dependency=afterany:<job>`: it starts after the current job ends. On some clusters a job waiting on a dependency gains no age priority, so the gap can be long |
| `early` | `--begin=<end of current job - early_lead_seconds>` (default 4 hours), no dependency. If it starts before the current job ends, it asks the current job for a final snapshot, cancels it, then rebuilds |

## Before the limit

At `winddown_threshold_seconds` before the limit (default 30 minutes) each active Claude pane
gets one message: the job is near its limit and the session will be resumed; if the work in
hand will not finish, stop cleanly and leave a note for after the hop. At
`pause_threshold_seconds` (default 120 seconds) the final snapshot is taken.

## With context management

If `open-science-context` is installed, the two plugins coordinate. From the wind-down
message until the hop, session jumps are inhibited in the registered panes. After the hop, a
jump that the time limit interrupted is finished, and a pane that was waiting after a wait
jump is woken with `/open-science-context:continue-context`. Without `open-science-context`,
the plugin works the same way and skips these steps. Details:
`extras/slurm-resurrect/reference/jump-hook.md`.

## Turn it off

- `remove [session]` takes one tmux session out.
- `stop` ends the lineage and cancels the queued successor.
- Disabling or uninstalling the plugin does **not** cancel a successor that is already
  queued: queued jobs run a copy of the scripts kept in the state directory. Run `stop`
  first.

## Known limits

- A session started with `--plugin-dir` loses that flag on resume unless `launch_cmd`
  includes it.
- If a resumed session shows the workspace trust dialog, the plugin selects "Yes, I trust
  this folder". If it cannot select it, it presses nothing and notifies you.
- Registration is checked by process ancestry, not enforced by the operating system.
- Further limits of the tests are listed in the plugin's `README.md`.
