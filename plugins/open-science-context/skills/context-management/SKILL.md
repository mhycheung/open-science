---
name: context-management
description: Clear the session (a "jump") and resume from the context files instead of letting context grow, register the session's task, and checkpoint subagents. Use at session start when driving a task, after every finished subtask, before dispatching subagents or long jobs, and when a hook reports a context size or a cache-cold notice.
---

# Context management

**Codex** follows this same skill: every rule below applies. Read `codex.md` next to this
file now: it lists only the Claude Code mechanics that Codex replaces.

Session jumps for a project made with the `open-science-project` plugin. Keeping the context
files themselves current (caps, when to update, the four questions, amending) is
`open-science-project:context-files`; load both at session start. A jump is only as good as
the context file it resumes from.

**Who does the clearing.** On Claude Code the plugin's mod (`hooks/context_mod.js`, a Claude
Code mod: code Claude Code runs inside itself) clears the session, runs the resume command,
wakes a waiting session, renames it and counts tokens, all from inside Claude Code, with no
typing and no tmux. It needs a Claude Code that runs mods (2.1.287 or later, with mods
switched on for the account). Where the mod is not loaded, the plugin falls back to typing
into the tmux pane. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" status` says which one
this session has. The steps below are the same either way.

## Registration

Each session records which context file it drives, so `open-science-context:continue-context`
works after a jump with no file named:

```bash
PC="${CLAUDE_PLUGIN_ROOT}/scripts/pane_context.sh"
bash "$PC" set tasks/<id>/context.md    # when you start driving a task, or create one
bash "$PC" get                          # prints the registered path, or exits 1
```

The record follows the session through jumps, a `/clear` and a SLURM resurrection (with the
mod: by session id; in the fallback: by tmux pane). Register again whenever the session moves
to another task, including a task it has just created (brainstorm and verification tasks
too), unless the user says to stay on the current one. A stale registration sends the next
jump, `/clear` or resurrection back to the old task and keeps the old task in the session
name. Subagents never call `set`: they inherit the main agent's registration.

**Session names** (on when the user said yes in onboarding: `OPSCI_SESSION_NAMES=1` in the
`env` block of the Claude `settings.json`). The plugin names the session after the project
(`quad-ratio`, `quad-ratio-2` for a second live session) and, once the session has a
registered file, adds the task's `short_name` from its header (`quad-ratio-2 · pp-real`).
The name is also the session's name in the Remote Control list. With the mod it changes when
the turn that ran `set` ends; in the fallback, at the user's next prompt. Nothing to do by
hand: give every new task a short name (`opsci task new --short-name`) and register.

## Jumps

**Jumps are optional; the user chose which in onboarding.** Check once per session:
`echo "${OPSCI_JUMPS:-all}"`. `all`: everything below. `wait`: no active jumps (ignore the
active row and keep working in this session however large it grows); wait and cache-cold
jumps as below. `off`: no jumps at all; skip the rest of this section, but still register
the pane, keep the context files current, and checkpoint subagents. `jump.sh` refuses what
the setting does not allow.

A jump clears this session and resumes from the context file. Jumping costs one context
reload; not jumping costs the whole conversation re-read on every turn. Three kinds:

| jump | when | command |
|---|---|---|
| **active** | context above ~250k tokens (the Stop hook tells you), a subtask finished, or before a fan-out of subagents | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" active <context file> --report "<report>"` |
| **wait** | only background work is left (subagents, a background shell, a SLURM job) and it will outlast ~45 min | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" wait <context file> --report "<report>"` |
| **cache-cold** | the plugin sends `[open-science] cache-cold: ...` after 58 min idle with work still running | a wait jump, now |

**An active jump needs a next step you will run yourself.** The fresh session starts by
running `open-science-context:continue-context` and then carries on with the next step in the context file. If
this turn ends waiting for the user (a question, a decision, a hold point, something only
the user can do), do not jump, whatever the context size: save the state, ask, and end the
turn. The Stop hook's size notice does not apply to such a turn; reply to it in one line
and stop. Jump after the user answers, if the answer leaves work for you to do.

**Before either command, in the same turn, write the jump's record:**
1. The task `context.md`: state, in flight (agent/job ids, output paths, how to check), and
   the exact next step. Summarize what this conversation established that is not yet on disk,
   in your own words: decisions and findings, never the user's messages verbatim, and
   nothing that is not about the science or the code (deadlines, availability, reminders,
   allocation remarks, mood; `open-science-project:context-files`, "Record content, not
   conversation").
2. The project `context.md`, if the task table, in-flight list or open questions changed.
3. One line in `tasks/<id>/log.md`.
4. `opsci notify "<text>" [file]` for anything the user would otherwise miss (with no
   notification setup, it writes a file in `messages/`).

**Every jump reports to the user.** A jump clears the conversation: all the user sees of it
is `/clear`, and the chat they were reading is gone. `--report` is required: a headline (the
main point, under about ten words), a blank line, then what this session did and found, the
state, what is running (job or agent ids), and what comes next. Write it for a user who did
not read the chat; use real newlines (a quoted multi-line argument), not `\n`. `jump.sh`
posts it with `opsci notify --kind status --no-mention` (the project's Feed in Notion, or
its configured back end) once its checks pass, and adds which jump it is and the context
file. With the Claude Code mod, the same text is also shown at the top of the cleared
session, as the output of `/opsci-note` (the user sees it in the terminal and in Remote
Control; you read it with your next prompt, as the previous session's account of the state:
the context file stays the record). It does not wait for the user. A failed send does not stop the jump; `jump.sh` prints a warning, and the message is
kept in `messages/`.

Then run `jump.sh` as the last tool call of the turn and end the turn with one line saying
so. When the turn ends, the plugin clears the session and, for an active jump, runs
`/open-science-context:continue-context <context file>` (the mod does both inside Claude
Code; the fallback types them into the pane). `jump.sh` refuses when the context file was
not saved in the last 15 minutes, an active jump below 100k tokens without `--force`, and,
without the mod, when not in tmux. At the stop, a wait jump with nothing that could wake the
session is refused: start the waker or do an active jump instead.

**What wakes a cleared session:** a background subagent's report, a background Bash task
exiting, a Monitor event, a queued SLURM waker. For SLURM jobs, queue the waker before the
wait jump: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait_slurm.sh" --notify <jobid> [<jobid>...]`.
When the jobs leave the queue, the plugin sends their states to the session, which starts a
turn; the waker follows the session through jumps and SLURM resurrections. If `--notify`
exits 4 (the mod is not loaded), run `wait_slurm.sh <jobid>...` as a background Bash task
instead. A session that was waiting on background tasks when Claude Code restarted (a
resurrection, a resume by hand) has lost them; the mod then runs
`/open-science-context:continue-context` for it at once.

**Do not jump** while an optional component owns the pane (`jump.sh` says so), and a
subagent never jumps. `jump.sh cancel` drops a pending request; `jump.sh status` shows it.

Settings (environment): `OPSCI_JUMPS` (all), `OPSCI_JUMP_THRESHOLD` (250000),
`OPSCI_ACTIVE_JUMP_FLOOR` (100000), `OPSCI_CACHE_COLD_MIN` (58), `OPSCI_JUMP_FRESH_MIN` (15),
`OPSCI_SUBAGENT_LIMIT` (200000), `OPSCI_WAIT_POLL` (60 s), `OPSCI_STATE_DIR`.

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

With the mod, the plugin also watches each subagent's context and, above 200k tokens, sends
it one message: `open-science: your context is <n> tokens, above 200000. Stop at the next
clean boundary, ...`. That message comes from the plugin, not from the user; the subagent
obeys it as the dispatch prompt says. Without the mod, the subagent checks its own size.

On `PAUSED` or `SUBMITTED`, dispatch a fresh subagent against the same document; do not
resume the old one.
