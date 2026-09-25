# Main-agent contract

Read this only if you are the main agent: the one session that drives a task, dispatches
subagents and reports to the user. Subagents do not read it.

<!-- opsci:context -->
The mechanics (context files, session jumps, dispatch templates) live in the
open-science plugin skills, so they update for every project at once. This file holds the
project's own choices.
<!-- /opsci:context -->
<!-- opsci:no-context -->
The mechanics (context files, dispatch templates) live in the open-science plugin
skills, so they update for every project at once. This file holds the project's own choices.
<!-- /opsci:no-context -->

## 1. What you own

- The task plan, the task context, the project `context.md`, and the log lines.
- Open-ended debugging, hands-on. When you are hunting a mechanism whose unknowns you
  cannot list, do it yourself: list the hypotheses (always include one about conventions or
  normalisation, and one that the test itself is broken), name the cheapest test that
  separates them, then run it.
- All communication with the user.
- The layout: you create `tasks/<id>/` and `data/<task-id>/` before dispatching into them,
  and you are the only one who moves files.

## 2. Dispatch

- **Dispatch only tracks that run in parallel** with each other or with your own work. A
<!-- opsci:context -->
  single sequential track is yours; session jumps keep your context small.
<!-- /opsci:context -->
<!-- opsci:no-context -->
  single sequential track is yours.
<!-- /opsci:no-context -->
- **`med-effort` is the default tier.** Go up to `high-effort` only after a `med-effort`
  attempt at the same task has provably failed; record the failure first. Go down to
  `low-effort` only when no decision is left in the task.
- **Every dispatch states:** the tier; the output paths; which sections of which context
  files and which rule ids to read; every number, trap and rule the agent needs (paste them
  in); what done means; the compute budget; where to stop.
- **Accept a subagent's result with its evidence.** Do not re-run it, and do not dispatch
  another agent to check it. If it contradicts something established, say so and decide.

## 3. After every finished subtask (the four questions)

1. Did a task's status or edges change, or did a subtask start, finish, fail or branch? →
   update its node header and `tasks/<id>/map.md`. Did a result land, change or fail? →
   write or update its file in `tasks/<id>/results/`. A result is something later work
   will rely on, or something that answers part of the task's goal; a debugging finding
   goes only in the task's `map.md`, unless it matters conceptually. Set `milestone: true`
   when the result obviously answers part of the project's question; if unsure, ask the
   user. Run `opsci map build`, and settle every result it reports as resting on failed or
   superseded work. (`tasks/README.md`, "Results".)
2. Does the next agent need to know? → update `context.md`.
3. Was a source or package used or consulted? → `citations/used.bib` or `consulted.md`. If a
   result relies on it, add its key to that result's `uses:`; an assumption taken from it
   is a result of its own, `kind: assumption`.
4. Append one line to `log/YYYY-MM.md` pointing at the task log.

## 4. Autonomy and hold points

The plan header sets the autonomy level (`autonomous`, `checkpoints`, `collaborative`) and any
`hold_at` points. Re-read it at each subtask boundary. Only a fatal problem stops the work:
a plan assumption shown false, the budget ceiling reached, an irreversible action the plan
does not cover, missing access, or a result that contradicts the goal. Anything else: fix it
within the plan's scope, record it, continue. Non-fatal questions go under "Open questions"
in `context.md` with the option you chose; notify the user and continue.

## 5. Reporting to the user

Report at subtask boundaries and whenever the user would want to know. Lead with the
outcome. Every result claim comes with its evidence: a plot with labelled axes and units, or
the command and its output. Report infrastructure progress as infrastructure, not as a
result.

## 6. Decisions that belong to the user

- Modelling choices, cuts, conventions and ranges that decide what a result means, beyond
  what the plan fixes. <!-- List this project's own here. -->
- Which task is active, and what counts as success.
- Large compute spends; anything destructive or hard to reverse; anything visible outside
  this repo (publishing, uploads, messages to other people).
- Setting `human-verified`.
