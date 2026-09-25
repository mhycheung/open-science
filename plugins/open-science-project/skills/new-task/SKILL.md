---
name: new-task
description: Start a new task in an open-science project - settle the design with the user, write tasks/<id>/plan.md from the plan template, and create the task's context, log and subcontext. Use when the user asks to start a task, plan a piece of work, start a campaign, or track a piece of work that will outlive one session. Also for small tasks and explorations that need no plan; without asking, when the user says "brainstorm" or asks to explore an idea or question in the project (a brainstorm task); and, without asking, when the user asks to audit, check, reproduce or adversely review work that is already done (a verification task).
---

# New task

A task is a directory, `tasks/<id>/`: node header and state in `context.md`, the plan in
`plan.md`, history in `log.md`, working documents in `subcontext/`. The id is short, lower
case, and starts with a sequence number (`t07-mode-fit-v2`).

## Philosophy of a plan

- The complete specification up front; then the executing agent runs. Each subtask section
  carries everything that subtask needs, with numbers and paths inline.
- No "verify each step", no self-review or second-agent review. A control case (an input
  that must fail) goes inside the subtask's own check.
- Gates only where a mistake is expensive or irreversible: deleting or overwriting data,
  large compute, a claim reaching the user or the public repo.
- No hedging and no numbered micro-steps for routine mechanics. A step states the outcome,
  the artifact and the acceptance criterion.
- Every subtask has a tier and a cost estimate with a ±2× band. Only parallel tracks are
  dispatched, at `med-effort` by default. Sequential work and open-ended debugging stay with
  the main agent.
- **Run to completion.** Once the user approves the plan, the main agent does not stop
  until it is done, except at a hold point or on something fatal (`contracts/main.md` §4).

## Procedure

1. **Settle the design.** Ask the user only the decisions that are theirs: goal, what counts
   as done, constraints, budget, autonomy level. Propose one recommended approach and name
   the alternatives in a line each. The design goes into the plan; there are no spec files.
   Settle the privacy tier too ("Privacy tier" below), and a short name: a few words, lower
   case and hyphens, at most 24 characters (`pp-real`, `noise-model`). Sessions driving the
   task show it after the project name, as `<project> · <short name>`.

2. **Create the task directory:**

   ```bash
   opsci task new <id> --title "<title>" --short-name <short> --plan \
       [--privacy public|soft-private|hard-private] \
       [--depends-on <id>...] [--related <id>...] [--supersedes <id>...] \
       [--autonomy autonomous|checkpoints|collaborative] [--hold-at S2 ...]
   ```

   It writes `context.md` with a validated node header, `map.md` (the task's graph, one
   node to start with), `results/README.md` (the task's results page, generated),
   `log.md`, `subcontext/`, and
   `plan.md` from the plan template (the header repeated, plus `autonomy` and `hold_at`). It
   refuses a bad or existing id and an edge to a node that does not exist. Without `--plan`
   you get a task with no plan: right for small work and explorations.

3. **Write the plan** into `plan.md`, keeping the template's sections in order: Goal (a
   falsifiable "done"), Design (with rejected alternatives, one line each), Constraints
   (conventions, environment, compute read from `config/site.local.yaml` and never written
   into the plan, frozen files, rule ids), Delegation, one section per subtask (output
   directory `tasks/<id>/<S>/`, delivers, steps, gate, **Fatal if**), Escalate only if
   fatal, Budget. No absolute paths and no submit lines in the plan.

4. **Fill `context.md`:** summary in the header, goal, the next step a fresh session would
   execute. Add the task to the project `context.md` task table. If the work changes shared
   code while other work runs, give it its own branch and worktree
   (`git worktree add ../<project>-<id> -b <id>`) and name it in the task context.

5. **Freshness test:** could a fresh session act from `AGENTS.md`, the project `context.md`
   and this task's `context.md` alone? Anything it would have to ask belongs in one of them.
   Run `opsci map build` and `opsci context check`.

6. **Switch the session to the new task**, right after `opsci task new`, for every kind of
   task (planned, no plan, brainstorm, verification), unless the user says to stay on the
   current one. With the `open-science-context` plugin installed, register the pane for the
   new task's `context.md` (`open-science-context:context-management`, "Pane registration"):
   this replaces the previous task's registration, so a jump or `/clear` resumes the new task,
   and with session names on the session is renamed `<project> · <short name>` at the
   user's next prompt. Say in one line that the session now drives the new task.

7. Commit, and **report the plan to the user and stop.** Execution starts when the user
   approves it. Report it where the user reads: in a project mirrored to Notion (an
   `AGENTS.md` "Notion" section), run `opsci notion sync` and post
   `opsci notion post --kind question --task <id> --mention "<id>: plan ready for approval\n\n<goal, subtasks, cost in a few lines>"`;
   otherwise `opsci notify` it. Do this unasked.

## Privacy tier

A task is `public` by default (`--privacy` omitted); a brainstorm task is `soft-private`. Do not ask about the tier when nothing
points the other way. If what the user described obviously looks soft- or hard-private
(private notes, work too messy to release; proprietary data, unpublished ideas,
collaborators' unpublished work, private information about people), ask before creating the
task, with both definitions in the question:

> This task looks like it may be private. Which tier should it have?
> - `public` (default): may be released; the task directory is exported.
> - `soft-private`: not released, but other documents may mention it by name, and the public
>   map names it without a link. For example private notes, brainstorm ideas, work too messy
>   to release.
> - `hard-private`: must not appear anywhere in the release, not even by name. For example
>   proprietary data, unpublished ideas, collaborators' unpublished work, private information
>   about people.

A soft- or hard-private task keeps its work inside `tasks/<id>/` (large data in
`data/<task-id>/` as usual), so that it is easy to keep out. A public task mentions
soft-private material at most in passing and never mentions hard-private material
(`AGENTS.md` §0, §6). The committed `map/graph.md` shows every node. The published copy
leaves out hard-private nodes and names soft-private ones without a link; at publish time
the publish skill groups or rewords a soft-private node whose title or summary gives too much
away, so a private task's header needs no rewording now. The project `context.md` and `log/` are published: a line there that
names a hard-private task is flagged at the next publish, and the user decides then how to
handle it (`open-science-publish:publish`).

## Brainstorm tasks

An idea not yet ready to be project work goes under `brainstorm/` (its own context and
tasks; its nodes are part of the project graph). Same procedure, with `brainstorm` as the
root: `opsci task new b01-<slug> --title "<title>" --root brainstorm`, `opsci map build`,
`opsci context check brainstorm`. Ids start with `b`. Edges may join brainstorm and project
nodes in either direction: add one when the idea builds on project work or project work
comes from the idea. A brainstorm task is soft-private by default; give `--privacy public`
only when the user asks for the idea to be published, and `--privacy hard-private` for a
hard-private idea. The idea goes in the brainstorm `context.md` table, not
the project one.

A brainstorm task needs no plan and no approval. Make it as soon as the user says
"brainstorm" or starts exploring an idea: skip step 1's questions, create it without
`--plan`, record the question and what has been found so far in its `context.md` and in
the brainstorm `context.md` table, switch the session to it (step 6), commit, and carry on
with the conversation. Step 7's stop does not apply. Keep its `context.md` current as the discussion goes on.

A side question about existing work, not a new idea, is a private investigation instead
(`open-science-project:private-investigation`).

**Graduating an idea** is a new project task (no `--root`) whose `context.md` or `plan.md`
restates the idea: the question, what was found, the evidence, copied or rewritten so that
the task stands alone. It never links to `brainstorm/`; it may mention the idea in passing
(`AGENTS.md` §5). Then mark the brainstorm node `done`, naming the new task in its
`summary`.

## Verification tasks

A verification task audits, checks, reproduces or adversely reviews finished work (a task,
a result, a dataset). When the user asks for one, make it without asking; when it is unclear
whether the request checks finished work or asks for new work, propose one and ask. Never
start one to recheck your own work (`AGENTS.md` §2). Same procedure, with
`opsci task new v01-<slug> --title "<title>" --verifies <id>...`: it goes in
`tasks/<id>/verifications/` when every verified node lies in that task, else in
`verifications/`; its privacy is the strictest of the verified nodes (do not ask). Its
findings are results in its own `results/`; a confirmed node gets `verification: verified`
with `evidence:` there, a refuted one a new `status` (`tasks/README.md`). Step 6 applies:
the session switches to the verification task.

## Autonomy (plan header)

| `autonomy` | the main agent |
|---|---|
| `autonomous` (default) | runs the whole plan and escalates only fatal problems |
| `checkpoints` | also stops at each point listed in `hold_at` |
| `collaborative` | also asks whenever two readings of the plan would lead to materially different work |

Fatal: a plan assumption shown false, the budget ceiling would be exceeded, an irreversible
action the plan does not cover, missing access, a result that contradicts the goal. A failed
gate or a subtask over its estimate is not fatal by itself.
