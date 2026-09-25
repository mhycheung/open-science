# Tasks

One directory per task, `tasks/<id>/`, created by the `open-science-project:new-task` skill:

    tasks/<id>/
      context.md     node header + the task's current state, at most 200 lines
      plan.md        the plan, if the task has one (it repeats the node header)
      log.md         append-only task log
      map.md         the task's graph: its subtasks and how they connect (every task has one)
      results/       the task's scientific results: one <result-id>.md each, and README.md
                     (generated: the results with their figures)
      subcontext/    per-subtask and per-subagent context documents
      <subtask>/     working files: scripts, plots, logs, small outputs

Large data produced by a task goes in `data/<task-id>/`, not here.

## Node header

Every task, result, paper, site page and published dataset is a node in the project graph.
Its header is YAML front matter at the top of its main markdown file, or a `node.yaml`
beside a non-markdown artifact (for example in `paper/`). `opsci map build` reads every
header, writes `map/graph.md`, `map/dead_ends.md`, `map/claims.md` and the results pages,
and reports bad headers.

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
header may also carry `autonomy` and `hold_at`; a result may carry `kind` and `milestone`
(see "Results"); any node may carry `artifacts`, `code`, `uses` and `tags`. Any other key is
an error, so a typo is caught. `verified` means a stated check was run against a provenance
record, and `evidence` points to both. Only the user sets `human-verified`.

## Results

A result is what the project would state in a paper: a figure, a table, a value, a statement
or a concept that the work established, or an assumption it takes as given. The plots and
numbers of debugging runs and checks are not results; they stay in the subtask directories.

Each result is a node of `type: result` with its own file, `tasks/<id>/results/<result-id>.md`:
the header, then the result shown and described (the figure, the table, the statement, how
it was obtained, its limits). A result that combines several tasks goes in the project's
`results/` directory instead. The figures and data themselves stay where the work put them;
the header points to them.

```yaml
---
id: r-mass-ratio
title: The mass ratio is 1.8 +/- 0.1
type: result
kind: value                  # figure | table | value | statement | concept | assumption
status: done
milestone: true              # a main result of the project: listed in results/README.md
depends_on: [r-noise-stationary, d-strain-gw150914]   # results, tasks or datasets it rests on
uses: [Isi2019]              # keys in citations/used.bib: the external works it relies on
artifacts: [tasks/t07-mode-fit-v2/S3/corner.png, data/t07-mode-fit-v2/chain.h5]
code: [tasks/t07-mode-fit-v2/S3/fit.py, src/qnm/likelihood.py]
summary: The mass ratio of GW150914 is 1.8 +/- 0.1 (90% credible), from the ringdown alone.
verification: verified
evidence: tasks/t07-mode-fit-v2/S3/provenance.yaml
---
```

`opsci map build` checks that every `artifacts` and `code` path exists (a missing path under
`data/`, which git ignores, is only a warning) and that every `uses` key is in
`citations/used.bib`. It writes, never to be edited by hand:

- `tasks/<id>/results/README.md`: the task's results, each with its figure, where it is
  stored, the code, what it rests on and what uses it; withdrawn results in a table below;
- `results/README.md`: the milestone results (`milestone: true`, and every result in
  `results/`);
- `map/claims.md`: the claims graph. Every result, grouped by the task it came from, with
  arrows from what it rests on (results, tasks, datasets, external works) and to what uses
  it, and a table with each result's verification and the weakest verification in its chain.

When a result fails or is superseded, change its `status` (and give the new result
`supersedes:`). `opsci map build` then warns about, and `map/claims.md` lists and marks, every
live result that rests on it, directly or through other results; settle each one. It also
warns when a committed artifact changed after the result file was last committed.

## Privacy

`privacy` grades the node: `public`, `soft-private` or `hard-private` (definitions in
`AGENTS.md` §6). Only a public task's directory is exported. A soft- or hard-private task
keeps its work inside its directory. `opsci task new <id> --title "..." --privacy <tier>`
sets it; the default is `public`, and `soft-private` for a brainstorm task (`--root brainstorm`).
