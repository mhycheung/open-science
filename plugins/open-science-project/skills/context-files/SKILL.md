---
name: context-files
description: Keep a template project's context files current and under their line caps - the project context.md, each task's context.md, subcontext files and logs - and run the four end-of-subtask questions. Use at session start when driving a task, after every finished subtask, before dispatching subagents or long jobs, when a hook reports a line cap, and when the user asks to update or amend a context file or plan.
---

# Context files

**Invariant: a fresh session given only `AGENTS.md`, the project `context.md` and the task's
`tasks/<id>/context.md` can take the correct next action without asking anything.** Context
files carry what is true now. History goes in the logs, detail in `subcontext/`.

## Files and caps

| file | holds | cap |
|---|---|---|
| `context.md` (project) | goal, task table, in flight, next step, waiting on the owner, open questions | 200 lines |
| `tasks/<id>/context.md` | node header + the task's goal, current state, in flight, next step, pointers | 200 lines |
| `tasks/<id>/subcontext/*.md` | one subtask or subagent each: how a result was reached, what was ruled out | none |
| `tasks/<id>/log.md`, `log/YYYY-MM.md` | append-only history, one line per entry | none |
| `map/README.md` | the project's logic, hand-written | 150 lines |

The brainstorm sub-root has the same files with the same caps: `brainstorm/context.md`,
`brainstorm/tasks/<id>/context.md` (200 lines each) and `brainstorm/map/README.md` (150).
The main agent keeps them current the same way; `opsci context check` covers them.

A PostToolUse hook checks the cap after every edit to one of these files and refuses an
over-cap file. `opsci context check` checks the whole project. Pruning means whole finished
items leave the file (to `subcontext/` or the log), not rewording.

Only the main agent edits the project and task `context.md`. Every pointer states when to
read it ("read only if the fit is re-run"). Dates are absolute, numbers carry units and are
labelled MEASURED or ESTIMATED, commands are copy-pasteable.

## When to update

Update the task context in the same turn as each of these, before the next action:
a subtask finished; a result, decision or owner ruling landed; just before dispatching a
subagent or submitting a long job (record the id, where output lands, how to check it);
before ending a turn with work unfinished.

**After every finished subtask, the four questions** (`contracts/main.md` §3):
1. Did a status or edge change? Update the node header; run `opsci map build`, which also
   rewrites the header table under the title.
2. Does the next agent need it? Update the project `context.md`.
3. Was a source or package used or consulted? Update `citations/`.
4. One line in `tasks/<id>/log.md` and one in `log/YYYY-MM.md`.

## Subagent documents

A subagent whose work spans more than one dispatch keeps
`tasks/<id>/subcontext/subagent_<slug>.md`, written at the end of every round as if a
different agent takes over next: current, complete, pruned, including what was ruled out. The
dispatch prompt names its path. On conclusion, fold what is still needed into the task context.

## Amending a context file or plan

When the owner changes what the work should be: edit exactly what the discussion changed,
keep the structure, keep the plan and the context consistent with each other, say plainly
what you removed, and end with the path of the file you changed. Do not execute the plan
as part of the edit.
