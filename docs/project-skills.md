# Project skills (`open-science-project`)

The `open-science-project` plugin is the project-structure component. It gives Claude Code
five skills and two hooks that work on a project made from the
[template](project-template.md). It needs git and `opsci`.

```bash
claude plugin install open-science-project@open-science
```

Always name the skills in full, for example `/open-science-project:new-task`. If your own
skills directory (`~/.claude/skills/`) has a skill with the same short name, the model tends
to pick that one instead; `open-science:onboard` offers to move such skills to an archive
folder.

| skill | use it to |
|---|---|
| `open-science-project:new-project` | create a new project from the template |
| `open-science-project:new-task` | start a task, with or without a plan |
| `open-science-project:context-files` | keep the context files current and under their line caps |
| `open-science-project:migrate-project` | move an existing project into the layout without losing a file |
| `open-science-project:update-from-template` | take framework improvements into a project made from an older template |

## `open-science-project:new-project`

Creates an empty project to start working in. It does not invent research goals, methods or
rules: a slot it was not given content for gets a one-line `TODO:`.

1. It asks for the directory, a short slug, a one-line title and the owner's name, and
   always asks what the project is about (the question, why it matters, the approach, what
   counts as success, the scope, the key sources). A short answer is fine; any part can be
   left for later.
2. It runs `opsci template instantiate` (see [Project template and layout](project-template.md)),
   with `--no-context-management` unless you use session jumps.
3. It makes the directory a git repository and commits everything.
4. It writes your answer into `PROJECT.md` under the matching headings, a 3 to 8 line
   summary into `AGENTS.md` §1, and the opening paragraph of `README.md`. If you named a
   scratch directory, batch account or notification channel, it copies
   `config/site.example.yaml` to `config/site.local.yaml` and fills those fields only.
5. It reports the directory, the framework commit recorded, and every `TODO:` left.

It does not create a public repository or add the project to your projects page unless you
ask. It never copies the template over an existing project: use
`open-science-project:migrate-project` or `open-science-project:update-from-template`.

## `open-science-project:new-task`

A task is a directory, `tasks/<id>/`. The id is short, lower case, and starts with a
sequence number (`t07-mode-fit-v2`).

1. **Design.** The agent asks you only the decisions that are yours: the goal, what counts
   as done, constraints, budget, autonomy level. It proposes one approach and names the
   alternatives.
2. **Directory.** It runs:

   ```bash
   opsci task new <id> --title "<title>" --plan [--depends-on <id>...] [--related <id>...] \
       [--supersedes <id>...] [--autonomy autonomous|checkpoints|collaborative] [--hold-at S2 ...]
   ```

   This writes `context.md` with a validated node header, `log.md`, `subcontext/` and, with
   `--plan`, `plan.md` from the plan template. It refuses an id that is not lower case
   letters, digits and hyphens, an id that exists, an edge to a node that does not exist,
   and `--hold-at` without `--autonomy checkpoints`. Without `--plan` the task has no plan,
   which suits small work and explorations.
3. **Plan.** The agent writes `plan.md`: goal (a "done" that can be shown false), design
   with rejected alternatives, constraints, delegation, one section per subtask (output
   directory, what it delivers, steps, gate, what is fatal), and budget. There are no
   absolute paths and no job-submission lines in the plan.
4. **Context.** It fills the task's `context.md`, adds the task to the project
   `context.md`, runs `opsci map build` and `opsci context check`, and commits.
5. **Stop.** It reports the plan to you and stops. Execution starts when you approve it.

The plan header sets how much the agent does without asking:

| `autonomy` | the main agent |
|---|---|
| `autonomous` (default) | runs the whole plan and escalates only fatal problems |
| `checkpoints` | also stops at each point listed in `hold_at` |
| `collaborative` | also asks whenever two readings of the plan would lead to materially different work |

Fatal means: a plan assumption shown false, the budget ceiling would be exceeded, an
irreversible action the plan does not cover, missing access, or a result that contradicts
the goal.

A brainstorm task lives under `brainstorm/tasks/` (`opsci task new <id> --root brainstorm`)
and is soft-private by default. Its node is part of the project graph, and edges may join it
to project tasks.
When an idea is ready to become project work, the agent creates a new project task that
restates it; it never links to the brainstorm task.

## `open-science-project:context-files`

The rule behind it: a fresh session given only `AGENTS.md`, the project `context.md` and the
task's `tasks/<id>/context.md` can take the correct next action without asking anything.
Context files hold what is true now; history goes in the logs, detail in `subcontext/`.

| file | holds | cap |
|---|---|---|
| `context.md` (project) | goal, task table, in flight, next step, waiting on the owner, open questions | 200 lines |
| `tasks/<id>/context.md` | node header and the task's goal, current state, in flight, next step, pointers | 200 lines |
| `tasks/<id>/subcontext/*.md` | one subtask or subagent each | none |
| `tasks/<id>/log.md`, `log/YYYY-MM.md` | append-only history | none |
| `map/README.md` | the project's logic, hand-written | 150 lines |

The same caps apply to `brainstorm/context.md` and `brainstorm/tasks/<id>/context.md`.

After every finished subtask the agent answers four questions (`contracts/main.md` §3):

1. Did a task's status or edges change? Update its node header and run `opsci map build`.
2. Does the next agent need to know? Update `context.md`.
3. Was a source or package used or consulted? Update `citations/`.
4. Append one line to the task log and one to `log/YYYY-MM.md`.

## `open-science-project:migrate-project`

Moves an existing project, ongoing or finished, into the layout without losing a file.

1. It works in a git worktree on a new branch, so the original is untouched until you
   merge, and records every file with its hash first:
   `opsci migrate inventory <worktree> -o <scratch>/inventory.json`.
2. It instantiates the template into a scratch directory and copies over only the files the
   project does not have.
3. It proposes a mapping (which directories become tasks; what goes to `src/`, `data/`,
   `paper/`, `citations/`, `rules/`, `archive/`) and **waits for your approval** before
   moving anything. Files move with `git mv`, so history follows them.
4. It writes a node header per task, the project `context.md`, `PROJECT.md` from the
   project's own descriptions, and one log line, then builds the map.
5. It runs `opsci migrate compare <scratch>/inventory.json <worktree>`, which fails if any
   file's content is found nowhere (a moved file counts as kept), and reports. You merge.

## `open-science-project:update-from-template`

Takes framework improvements into a project. It is a diff against the framework commit the
project was copied from, applied by hand, never a re-copy. See
[Updating a project](updating.md).

## Hooks

The plugin installs two hooks. Both act only inside a project made from the template (a
directory holding both `AGENTS.md` and `config/framework.yaml`); without `jq`, the line-cap
hook does nothing and the guard checks every edit.

| hook | when | what it does |
|---|---|---|
| line cap | after an agent's Edit or Write | when a `context.md` is over 200 lines or `map/README.md` over 150, reports it to the agent as an error with the instruction to prune it now; after a task context edit it reminds the agent of the four questions. Needs `jq` |
| human-verified guard | before an agent's Edit or Write | refuses an edit whose new content sets `verification: human-verified` |

Bash commands are not checked by the guard. The publish check is the second line: it refuses
a `human-verified` node whose `verification:` line was last changed in an agent's commit.
