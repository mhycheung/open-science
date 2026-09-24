---
name: resurrect
description: User command for SLURM session resurrection - register the current tmux session so its Claude sessions resume in a new SLURM job when this job hits its time limit, or check, change, stop or reset that. Only the user can run it, by typing /slurm-resurrect:resurrect.
argument-hint: "[register [--remote-control on|off] [--permission-mode MODE] [session ...] | status | remove [session] | stop | reset [N] | set KEY VALUE | set-notify CMD | help]"
disable-model-invocation: true
allowed-tools: Read
---

# /slurm-resurrect:resurrect

The user typed `/slurm-resurrect:resurrect $ARGUMENTS`.

The plugin's `UserPromptSubmit` hook has **already run** this command for the user,
before this turn began. Its exit status and output are in your context, in a block
starting with `[slurm-resurrect]`.

What to do:

1. Show the user that output. Keep it short and do not change its meaning.
2. Do **not** run `rr_registry.sh` yourself to repeat, fix or extend the command.
   `register`, `reset`, `set` and `set-notify` are the user's decisions; the script
   refuses them when an agent runs them, and logs the attempt. If the output shows
   an error, tell the user what it says and how to retry.
3. If there is no `[slurm-resurrect]` block in your context, the hook did not run
   (the plugin's hooks may be disabled). Tell the user, and point them to the
   terminal form in the README: `bash ${CLAUDE_PLUGIN_ROOT}/scripts/rr_registry.sh <command>`,
   run in a plain terminal pane of the tmux session, outside Claude.

What the commands do (for answering questions; details in
`${CLAUDE_PLUGIN_ROOT}/README.md` and `${CLAUDE_PLUGIN_ROOT}/reference/mechanism.md`):

- `register`: opt the tmux session in. When this job reaches its time limit a
  successor job rebuilds the session (windows, panes, layout, working directories)
  and resumes every Claude pane with `--resume`. The first run only shows a
  warning; the second registers. Options: `--permission-mode` (default
  `bypassPermissions`), `--remote-control on|off` (default on).
- `status`, `remove [session]`, `stop` (end the lineage), `reset [N]` (new hop
  budget, default cap 10), `set KEY VALUE` (e.g. `set queue_mode early`),
  `set-notify CMD` (a command run with the message in `$RR_MSG`).

For agents inside a registered session (these need no user approval):
`bash ${CLAUDE_PLUGIN_ROOT}/scripts/rr_registry.sh note "<what to do next>"` saves a
message delivered to this pane after resurrection; `... timeleft` shows the runway.
