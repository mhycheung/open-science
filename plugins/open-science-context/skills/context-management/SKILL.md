---
name: context-management
description: Clear the session (a "jump") and resume from the context files instead of letting context grow, register the tmux pane, and checkpoint subagents. Use at session start when driving a task, after every finished subtask, before dispatching subagents or long jobs, and when a hook reports a context size or a cache-cold notice.
---

# Context management

Session jumps for a project made with the `open-science-project` plugin. Keeping the context
files themselves current (caps, when to update, the four questions, amending) is
`open-science-project:context-files`; load both at session start. A jump is only as good as
the context file it resumes from.

## Pane registration

Each tmux pane records which context file it drives, so `open-science-context:continue-context`
works after a jump with no file named:

```bash
PC="${CLAUDE_PLUGIN_ROOT}/scripts/pane_context.sh"
bash "$PC" set tasks/<id>/context.md    # when you start driving a task
bash "$PC" get                          # prints the registered path, or exits 1
```

Subagents never call `set`: they inherit the main agent's pane.

**Session names** (on when the user said yes in onboarding: `OPSCI_SESSION_NAMES=1` in the
`env` block of the Claude `settings.json`). A hook names the session after the project
(`quad-ratio`, `quad-ratio-2` for a second live session) and, once the pane has a registered
file, adds the task's `short_name` from its header (`quad-ratio-2 · pp-real`). The name
changes at the user's next prompt after `set`, not at once. Nothing to do by hand: give every
new task a short name (`opsci task new --short-name`) and register the pane.

## Jumps

A jump clears this session and resumes from the context file. Jumping costs one context
reload; not jumping costs the whole conversation re-read on every turn. Three kinds:

| jump | when | command |
|---|---|---|
| **active** | context above ~250k tokens (the Stop hook tells you), a subtask finished, or before a fan-out of subagents | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" active <context file>` |
| **wait** | only background work is left (subagents, a background shell, a SLURM job) and it will outlast ~45 min | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" wait <context file>` |
| **cache-cold** | the hook types `[open-science] cache-cold: ...` after 45 min idle with work still running | a wait jump, now |

**An active jump needs a next step you will run yourself.** The fresh session starts by
running `open-science-context:continue-context` and then carries on with the next step in the context file. If
this turn ends waiting for the owner (a question, a decision, a hold point, something only
the owner can do), do not jump, whatever the context size: save the state, ask, and end the
turn. The Stop hook's size notice does not apply to such a turn; reply to it in one line
and stop. Jump after the owner answers, if the answer leaves work for you to do.

**Before either command, in the same turn, write the jump's record:**
1. The task `context.md`: state, in flight (agent/job ids, output paths, how to check), and
   the exact next step. Summarize what this conversation established that is not yet on disk.
2. The project `context.md`, if the task table, in-flight list or open questions changed.
3. One line in `tasks/<id>/log.md`.
4. `opsci notify "<text>" [file]` for anything the owner would otherwise miss (with no
   notification setup, it writes a file in `messages/`).

Then run `jump.sh` as the last tool call of the turn and end the turn with one line saying
so. The Stop hook starts the clear when the turn ends; for an active jump it then types
`/open-science-context:continue-context <context file>`. `jump.sh` refuses when the context file was
not saved in the last 15 minutes, when not in tmux, and an active jump below 100k tokens
without `--force`. At the stop, a wait jump with nothing running that could wake the session
is refused: start the waker or do an active jump instead.

**What wakes a cleared session:** a background subagent's report, a background Bash task
exiting, a Monitor event. For SLURM jobs, start the waker as a background Bash task before
the wait jump: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait_slurm.sh" <jobid> [<jobid>...]`.

**Do not jump** while an optional component owns the pane (`jump.sh` says so), and a
subagent never jumps. `jump.sh cancel` drops a pending request; `jump.sh status` shows it.

Settings (environment): `OPSCI_JUMP_THRESHOLD` (250000), `OPSCI_ACTIVE_JUMP_FLOOR` (100000),
`OPSCI_CACHE_COLD_MIN` (45), `OPSCI_JUMP_FRESH_MIN` (15), `OPSCI_STATE_DIR`.

## Subagents

Dispatch in the background. Every dispatch prompt states:

- the path of its `subcontext/subagent_<slug>.md` (`open-science-project:context-files`);
- **above 200k tokens of its own context it stops at the next clean boundary**, brings the
  document up to date, and ends its report `PAUSED - <doc path> - <exact next step>`;
  a finished task ends `DONE`;
- a job whose wait is over ~45 min is submitted, recorded, and reported as
  `SUBMITTED - <job id> - <doc path> - <check command> - <next step>`; the main agent owns
  the wait;
- every report ends with: `If you have no context, use the open-science-context:continue-context skill.`

On `PAUSED` or `SUBMITTED`, dispatch a fresh subagent against the same document; do not
resume the old one.
