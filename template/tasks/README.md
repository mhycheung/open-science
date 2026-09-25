# Tasks

One directory per task, `tasks/<id>/`, created by the `open-science-project:new-task` skill:

    tasks/<id>/
      context.md     node header + the task's current state, at most 200 lines
      plan.md        the plan, if the task has one (it repeats the node header)
      log.md         append-only task log
      map.md         the task's graph: its subtasks and how they connect (every task has one)
      subcontext/    per-subtask and per-subagent context documents
      <subtask>/     working files: scripts, plots, logs, small outputs

Large data produced by a task goes in `data/<task-id>/`, not here.

## Node header

Every task, result, paper, site page and published dataset is a node in the project graph.
Its header is YAML front matter at the top of its main markdown file, or a `node.yaml`
beside a non-markdown artifact (for example in `paper/`). `opsci map build` reads every
header, writes `map/graph.md` and `map/dead_ends.md`, and reports bad headers.

## Task map

Every task has `map.md`: a Mermaid graph of what is inside the task, one node per subtask
or line of attack, with its status and the arrows between them. `opsci task new` writes it
with a single node, and `opsci map build` writes that starting map for any task that has
none; neither ever rewrites an existing map. The main agent keeps it current after every
finished subtask. A task with no internal structure keeps its single node. The project
graph links each task to its map.

```yaml
---
id: t07-mode-fit-v2          # unique; lower case, digits, hyphens; a task's id = its directory name
title: Mode fit with the corrected likelihood
short_name: mode-fit-v2       # optional; shown in session names as '<project> · <short_name>'
type: task                   # task | result | paper | page | dataset
status: active               # active | done | failed | superseded | abandoned | paused
depends_on: [t03-noise-model]
supersedes: [t05-mode-fit-v1]
related: [t06-start-time-scan]
privacy: public              # public | soft-private | hard-private   (absent = policy.default_privacy)
summary: One sentence on what this node established, or why it failed.
verification: unverified     # unverified | verified | human-verified
evidence: tasks/t07-mode-fit-v2/provenance.yaml   # required unless unverified
---
```

A task's `context.md` and `plan.md` also show the header as a table under the title, between
`opsci:node-table` comment markers, so that it reads well in a Markdown viewer. `opsci task new`
writes it and `opsci map build` rewrites it from the front matter; never edit the table.

Required: `id`, `title`, `type`, `status`, `summary`. A task may carry `short_name`; a plan
header may also carry `autonomy` and `hold_at`; any node may carry `tags`. Any other key is
an error, so a typo is caught. `verified` means a stated check was run against a provenance
record, and `evidence` points to both. Only the owner sets `human-verified`.

`privacy` grades the node: `public`, `soft-private` or `hard-private` (definitions in
`AGENTS.md` §6). Only a public task's directory is exported. A soft- or hard-private task
keeps its work inside its directory. `opsci task new <id> --title "..." --privacy <tier>`
sets it; the default is `public`, and `soft-private` for a brainstorm task (`--root brainstorm`).
