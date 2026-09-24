---
name: continue-context
description: Take over the work a context file describes and drive it as the main agent. Use when the user or a resume prompt names a context file (a task context.md, the project context.md) and asks to continue, resume or take over; when a session is woken with no context by a notification; and as the first step after any session jump.
---

# Continue from a context file

You are the main agent for the work the context file describes. Another session drove it
until now, possibly this one before a jump.

## Procedure

0. **Which file.** Resolve it (below) before anything else.
1. **Read it in full**, then what `AGENTS.md` §4 lists for the main agent:
   `contracts/main.md`, the project `context.md`, and the task's `plan.md` section for the
   current subtask if there is a plan.
2. **Check it against reality.** Jobs, subagents, files and commits it names: do they
   exist and are they in the state it says? If the file and the repo disagree, say so; do
   not silently pick one.
3. **Load `open-science-context:context-management`** and follow it for the rest of the session.
4. **Report in a few lines** where the work stands and what you do next, and anything stale.
   Then continue the work.

## Woken by a notification

A cleared session can be woken by a subagent's report, a background shell exiting, or a
watcher message. The context file's "In flight" section says what was expected.

- **The notice needs action** (a subagent reported, a job ended): do the procedure above,
  then act on it as "In flight" says.
- **It repeats something already handled** (a second notice from the same subagent, a job
  already recorded as done): do not reload everything. Check that a waker is still running
  for the work that is left (`jump.sh status`, the background tasks), then end the turn.

## Which file

```bash
PC="${CLAUDE_PLUGIN_ROOT}/scripts/pane_context.sh"
```

1. **A file was named** (by the user or the resume prompt) → use it and register it:
   `bash "$PC" set <path>`.
2. **None named** → `bash "$PC" get`. If it prints a path, use it and say which in one line.
3. **Nothing registered** → list candidates, newest first, and ask. Never pick one yourself,
   even when there is only one:

   ```bash
   ls -t context.md tasks/*/context.md 2>/dev/null | head -10
   ```

   Once the user picks, `bash "$PC" set <path>`.

## Rules

- You own execution now: launch runs, edit code, update the context files.
- Verify before trusting: the file records what was true when it was written.
