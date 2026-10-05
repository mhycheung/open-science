---
name: resurrect
description: User command for SLURM session resurrection - register the current tmux session so its Claude sessions resume in a new SLURM job when this job hits its time limit, or check, change, stop or reset that. Only the user can run it, by typing /slurm-resurrect:resurrect.
argument-hint: "[register [--remote-control on|off] [--permission-mode MODE] [session ...] | status | remove [session] | stop | reset [N] | set KEY VALUE | set auto_trust true|false | set-notify CMD | help]"
disable-model-invocation: true
allowed-tools: Read
---

# /slurm-resurrect:resurrect

In Claude Code, follow the hook procedure below. In Codex, no user-command hook runs and
there is no `[slurm-resurrect]` block: instead of steps 1, 2 and 4, give the user the terminal form
`bash <plugin root>/scripts/rr_registry.sh <command>` (plugin root: two parents above this
skill's directory), to run in a plain terminal pane of the tmux session, outside Codex.
Do not claim registration succeeded or run user-only commands on their behalf. Step 3 (the
folder-trust question) applies to Codex too: when you give the user the `register`
command, ask its question as well and give the matching terminal line
(`set auto_trust true` or `set auto_trust false`). The descriptions of the commands below apply to both agents.

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
3. If the output contains `Folder trust: not chosen yet`, ask the user this question,
   with these two options, and do not choose for them:

   "A resumed session can open in a folder that Claude Code has not trusted yet, for
   example when the pane's folder or the Claude config folder changed. Claude Code then
   asks whether you trust the folder, and the session waits until someone answers. The
   plugin can answer 'Yes, I trust this folder' for you. That lets the folder's
   `.claude/settings.json` hooks and MCP servers run without your review: if the folder
   holds code you did not write, for example a repository you cloned, that code could run
   commands as you. Should the plugin answer for you?" (For Codex panes the plugin never
   answers: the question is always left to the user.)
   - **Leave it to me (the default)**: the plugin presses nothing and sends a notice each
     time a resumed session waits at the question.
   - **Accept automatically**: the plugin selects "Yes, I trust this folder".

   You cannot set it. Give the user the line to type:
   `/slurm-resurrect:resurrect set auto_trust false` or `... set auto_trust true`.
4. If there is no `[slurm-resurrect]` block in your context, the hook did not run
   (the plugin's hooks may be disabled). Tell the user, and point them to the
   terminal form in the README: `bash ${CLAUDE_PLUGIN_ROOT}/scripts/rr_registry.sh <command>`,
   run in a plain terminal pane of the tmux session, outside Claude.

What the commands do (for answering questions; details in
`${CLAUDE_PLUGIN_ROOT}/README.md` and `${CLAUDE_PLUGIN_ROOT}/reference/mechanism.md`):

- `register`: opt the tmux session in. When this job reaches its time limit a
  successor job rebuilds the session (windows, panes, layout, working directories)
  and resumes every Claude pane with `--resume` (and every Codex pane whose thread
  the open-science Codex hook recorded, with `codex resume`). The first run only shows a
  warning; the second registers. Each Claude pane resumes in the permission mode
  it was started with, never a wider one. Options: `--permission-mode MODE` (the
  widest mode a resumed pane may get, also used when a pane's own mode cannot be
  read; default none), `--remote-control on|off` (default on).
- `set auto_trust true|false`: whether the plugin answers Claude Code's folder-trust
  question in a resumed session for the user (default: no; see step 3).
- `status`, `remove [session]`, `stop` (end the lineage), `reset [N]` (new hop
  budget, default cap 10), `set KEY VALUE` (e.g. `set queue_mode early`),
  `set-notify CMD` (a command run with the message in `$RR_MSG`).

For agents inside a registered session (these need no user approval):
`bash ${CLAUDE_PLUGIN_ROOT}/scripts/rr_registry.sh note "<what to do next>"` saves a
message delivered to this pane after resurrection; `... timeleft` shows the runway.
