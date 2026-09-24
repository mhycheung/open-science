# How slurm-resurrect works

## Scripts

| script | role |
|---|---|
| `rr_common.sh` | Sourced library: state paths, config, job metadata, the user-only gate, pane detection, pane input, the coupling to the core's session jumps, notifications. |
| `rr_registry.sh` | The command line: `register remove note list count timeleft status stop reset set set-notify`. Starts this job's coordinator. |
| `rr_prompt_hook.sh` | `UserPromptSubmit` hook. Runs `/slurm-resurrect:resurrect ...` for the user before the model sees it. |
| `rr_coordinator.sh` | One per job, elected with `flock`. Submits the successor, snapshots, sends the wind-down message, takes the final snapshot. |
| `rr_snapshot.sh` | Captures one tmux session as JSON. |
| `rr_successor.sh` | Body of the successor job. |
| `rr_rebuild.sh` | Rebuilds one tmux session from a snapshot and starts Claude in its Claude panes. |
| `rr_deliver.sh` | After all sessions are rebuilt: trust dialogs, interrupted session jumps, notes, Remote Control check. |

## One hop

1. **Register.** `register` writes `registry/<job>/<session>.json` with the
   permission mode and Remote Control setting, copies the scripts to
   `<state>/scripts`, re-reads account, partition and time limit from the job,
   and starts the coordinator if none is running.
2. **Submit.** The coordinator submits one successor job as soon as the
   registry is not empty: `afterany` on this job, or `--begin` at
   `end - early_lead_seconds` in `early` mode. The batch script only sets
   `RR_STATE_DIR` and runs `<state>/scripts/rr_successor.sh`, so fixes to the
   copied scripts reach a successor that is already queued. If the registry
   empties, the successor is cancelled.
3. **Snapshot.** Every `snapshot_interval_seconds` the coordinator snapshots
   each registered session. This also records which Claude session runs in
   which pane (`registry/<job>/panes/`), so a session is still identified if
   its state file disappears before the final snapshot.
4. **Wind down.** At `winddown_threshold_seconds` of runway, each busy Claude
   pane gets one message (idle panes are not disturbed). From then on the
   core's session jumps are inhibited in the registered panes
   (`reference/jump-hook.md`).
5. **Final snapshot.** At `pause_threshold_seconds`, or when an early
   successor asks for a handoff (`handoff_<job>.request`), the coordinator
   takes the final snapshot, writes `paused_<job>` (and `handoff_<job>.done`),
   runs optional hooks in `<state>/hooks/`, and exits.
6. **Successor.** If the source job is still running (early mode), the
   successor requests the handoff, waits up to `handoff_timeout_seconds`, then
   cancels the source. It then rebuilds each session on its own tmux socket,
   copies the registrations to the new job, runs `rr_deliver.sh`, sends a
   notice with the attach command, and starts the new job's coordinator.

The hop counter rises by one at each submission (and falls back if the
successor is cancelled). At `max_resurrections` (default 10) no successor is
submitted until `reset`.

## Detecting a Claude pane

A pane is a Claude pane only if a process named `claude` runs in its process
tree at snapshot time. Its state file,
`<config dir>/sessions/<pid>.json`, gives the session id and status. The config
dir is the process's `CLAUDE_CONFIG_DIR`, or `~/.claude` if unset. The model
comes from the session transcript, plus any context-window suffix on the
process's `--model` argument (`context_window_suffix` can add one per model
family). A pane with no live `claude` process is rebuilt as a plain shell.

## Resume command

In each Claude pane of the rebuilt session:

```
[CLAUDE_CONFIG_DIR=<original value>] [CLAUDE_CODE_SESSION_NAME=<name>] \
  <launch_cmd> [--model M] --resume <session id> \
  --permission-mode <mode> [--remote-control]
```

`CLAUDE_CONFIG_DIR` is set only if the original process had it set. The
session name and `--remote-control` are added only when Remote Control is on.
`launch_cmd` defaults to `claude`.

## Notes

`note "<text>"` saves the note under `registry/<job>/notes/`, keyed by the
tmux pane (or `notes/<session id>.txt` outside a job's pane). After the hop the
note is typed into that pane as a prompt and deleted. A note that could not be
delivered is kept.

## State directory

`$RR_STATE_DIR`, default `${XDG_STATE_HOME:-$HOME/.local/state}/slurm-resurrect`.

```
rr_config.json                 settings, hop counter, lineage, pending successor
registry/<job>/<session>.json  one per registered tmux session
registry/<job>/snapshot/       snapshots
registry/<job>/panes/          pane -> Claude session cache
registry/<job>/notes/          notes delivered after the hop, keyed by pane
notes/<session id>.txt         notes keyed by session id
scripts/                       copy of the scripts used by queued successors
locks/                         coordinator election
coordinator_<job>.log          coordinator log
rr_respawn_from_<job>.slurm    generated successor batch script (and .out)
rr_manifest_<job>.jsonl        Claude panes rebuilt by the successor
handoff_<job>.request/.done    early-mode handoff
inhibit_<job>.list             inhibit files written for the core
winddown_<job>, paused_<job>   one-shot markers
warning_shown                  first-use warning was shown
refused.log                    refused agent attempts at user-only commands
notifications.log              every notice
```

## Tests

`tests/slurm_resurrect/` runs on a private tmux socket with private state
directories and a stub scheduler; no SLURM or Claude is needed.
`real_hop.sh` does one real hop per queue mode on SLURM (manual test).
