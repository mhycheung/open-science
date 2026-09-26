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
| `context.md` (project) | goal, task table, in flight, next step, waiting on the user, open questions | 200 lines |
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

**Housekeeping stays out of the published context.** The context files are published with
the project, while everything stays in the private files. In "Waiting on the user", "Next
step" and "Open questions", wrap each item that is housekeeping for the user and not part of
the task's or project's goal in an omission marker, for example "commit the plots?", "redo
the figure with larger labels?", "which file name?":

```
<!-- omit -->- Whether to commit `lit_cache/2609.07873/` (28 MB, untracked).<!-- /omit -->
```

The export drops the span (and a section it empties). Keep unmarked what a reader of the
project needs: a scientific decision, the choice of the next task, "next: compute $X$ for
the task goal".

Only the main agent edits the project and task `context.md`. Every pointer states when to
read it ("read only if the fit is re-run"). Dates are absolute, numbers carry units and are
labelled MEASURED or ESTIMATED, commands are copy-pasteable.

## When to update

Update the task context in the same turn as each of these, before the next action:
a subtask finished; a result, decision or user ruling landed; just before dispatching a
subagent or submitting a long job (record the id, where output lands, how to check it);
before ending a turn with work unfinished.

**After every finished subtask, the four questions** (`contracts/main.md` §3):
1. Did a status or edge change, or did a subtask start, finish, fail or branch? Update the
   node header and the task's `map.md`. Did a result land, change or fail? Write or update
   its file in `tasks/<id>/results/`. A result is something later work will rely on, or
   something that answers part of the task's goal; a debugging finding goes only in the
   task's `map.md`, unless it matters conceptually. Set `milestone: true` when the result
   obviously answers part of the project's question; if unsure, ask the user
   (`tasks/README.md`, "Results"). Run `opsci map build`, which also rewrites the header
   table under the title and the results pages, and settle every result it reports as
   resting on failed or superseded work.
2. Does the next agent need it? Update the project `context.md`.
3. Was a source or package used or consulted? Update `citations/`. If a result relies on
   it, add its key to that result's `uses:`; an assumption taken from it is a result of
   its own, `kind: assumption`.
4. One line in `tasks/<id>/log.md` and one in `log/YYYY-MM.md`.

## Subagent documents

A subagent whose work spans more than one dispatch keeps
`tasks/<id>/subcontext/subagent_<slug>.md`, written at the end of every round as if a
different agent takes over next: current, complete, pruned, including what was ruled out. The
dispatch prompt names its path. On conclusion, fold what is still needed into the task context.

## Amending a context file or plan

When the user changes what the work should be: edit exactly what the discussion changed,
keep the structure, keep the plan and the context consistent with each other, say plainly
what you removed, and end with the path of the file you changed. Do not execute the plan
as part of the edit.
