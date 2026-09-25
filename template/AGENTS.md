# {{PROJECT_TITLE}} ({{PROJECT_NAME}})

Instructions for every agent that works in this repository, and a summary for people. This
file is loaded at the start of every session. Keep it short: every line here costs every
session.

## 0. Rules that are never broken

1. **No secrets in the repo.** Tokens, passwords and keys live in environment variables or
   in a git-ignored file outside the repo. Never commit, print or paste one.
2. **Write for the public.** Everything in this repo may become public unless it is marked
   private (§6). Write every document so that a stranger can read it: no private remarks
   about people, no judgements of other people's work beyond normal technical criticism. A
   public document mentions soft-private material at most in passing, and never mentions
   hard-private material.
3. **Nothing reaches the public repo except through the `open-science-publish:publish` skill**, and
   only after the owner approves the publish report.
4. **No site-specific details in tracked files.** No absolute paths, usernames, hostnames,
   accounts, partitions or emails. Site settings live in `config/site.local.yaml`
   (git-ignored); scripts read them from there. Paths are relative to the repo root.
5. **Only the owner sets `verification: human-verified`.** An agent may set `verified`, with
   an `evidence:` pointer.

## 1. What this project is

<!-- 3–8 lines, a summary of PROJECT.md: the question the project answers, what counts as
success, and the key external sources with their identifiers. -->

The owner's full description: `PROJECT.md`. Where things stand now: `context.md`. The logic of the project: `map/README.md` and the
generated `map/graph.md`. Rules: `rules/README.md`.

## 2. How to work

- **Scope.** Deliver what was asked, at the scope asked. Make routine judgement calls
  yourself. If a request looks mistaken, say so in one sentence and continue as asked.
- **Record the work.** A request for work in this project (an idea to explore, a question to
  investigate, a derivation, a computation) belongs to a task. If it fits an existing task,
  work there. Otherwise make one with `open-science-project:new-task` without asking whether
  to: a brainstorm task when the owner says "brainstorm" or the idea is exploratory, a
  project task for planned project work. When a request may not be work (a quick question,
  a question about the tools), answer it, then ask at the end whether to start a task that
  records it. Never make the owner say "task" or "not a task" before they get an answer.
- **Literature.** Every source read in full is saved in `lit_cache/`, named by its
  identifier, and listed in `citations/consulted.md` or `citations/used.bib`. When more
  than one paper is consulted, subagents read the full texts (the `literature` tier in
  Claude Code); the main agent works from their reports.
- **Proportionality.** Match a check's tightness to what the result decides.
- **No self-verification rounds.** Put a control case (an input that must fail) inside the
  original check. Do not re-run finished work to confirm it.
- **Stop digging.** After two rounds on one anomaly with no confirmed mechanism, write the
  state down honestly and re-plan.
- **Language.** Write plain, direct English: one idea per sentence, active voice, the
  outcome first. Say what you mean; avoid metaphor where a literal phrase exists.

## 3. Scientific rigour

1. Every claim has evidence: data, code output or a derivation. Label each number
   **MEASURED** (with the command that produced it) or **ESTIMATED** (with the reasoning).
2. A hypothesis is tested, not assumed. Correlation is not causation.
3. Say "I don't know" when you don't know.
4. Quote load-bearing external sources verbatim, with an identifier (DOI, arXiv id, URL,
   commit) and the equation, section or line. Cite with keys (`[@key]`) that resolve in
   `citations/used.bib`.
5. When a claim is proven wrong, say so at once, and record it: the node's `status` becomes
   `failed` or `superseded`, with the reason in its `summary`. Failed routes are kept.
6. Every published result has provenance: a `provenance.yaml` beside it with the `src`
   commit, the environment lock file, the command, and the input checksums.

## 4. Your role, and what to read

| role | you are | read (beyond this file) |
|---|---|---|
| main agent | driving a task: planning, dispatching, debugging, reporting | `contracts/main.md`, `context.md`, your task's `tasks/<id>/context.md` |
| subagent | dispatched with a spec | your spec; `contracts/subagent.md` if your tier says so |
| side quest | a one-off request that is not project work (§2, "Record the work") | nothing more |

Do not read more than your role's list. Rules in `rules/` are opened when a context file or
a dispatch names their id, not before.

## 5. Where things are

| path | what | who edits |
|---|---|---|
| `PROJECT.md` | what the project is about: question, motivation, approach, success, scope, sources | owner |
| `context.md` | project state now: goal, task table, in flight, next step, open questions. ≤200 lines | main agent |
| `log/YYYY-MM.md` | append-only project log, one line per finished subtask | anyone, append only |
| `map/README.md` | hand-written narrative of the project's logic, ≤150 lines | main agent, owner |
| `map/graph.md`, `map/dead_ends.md` | GENERATED by `opsci map build`; never edit | nobody |
| `tasks/<id>/` | one task: `context.md` (node header), `plan.md`, `log.md`, `subcontext/`, working files | that task's agents |
| `src/` | shared code; never an output path | via worktree branches |
| `data/` | git-ignored; `data/<task-id>/` holds each task's outputs; `data/MANIFEST.yaml` is tracked | the producing task |
| `citations/` | `used.bib` (works and software used), `consulted.md` (read but not used) | anyone |
| `rules/` | one line per rule in `README.md` (R01…); long rules in `R07-*.md` | owner, agents |
| `contracts/` | how the main agent and subagents work | owner |
| `docs/` | documentation for readers and users; published | anyone |
| `paper/` | optional, laid out by the owner | owner |
| `lit_cache/` | full texts of sources; never published | anyone |
| `archive/` | retired material | main agent |
| `private-docs/` | private notes; committed, never exported | anyone |
| `brainstorm/` | ideas before they become project work, with its own `context.md`, `tasks/`, `map/`, `log/`; not published unless the owner adds it to the manifest | anyone |
| `config/` | `site.example.yaml` (tracked), `site.local.yaml` (git-ignored), `framework.yaml` | owner |
| `publish/` | publish allowlist, private policy (never exported), last published commit | owner, `publish` skill |

Small outputs go in `tasks/<id>/`; large data in `data/<task-id>/`. Names carry an ISO date
and the parameters that distinguish them. A new run gets a new name; nothing committed or
published is overwritten.

**`brainstorm/` and `private-docs/` are soft-private (§6).** No file outside them links to a
file in them, so the public project has no broken links; a mention in passing, in backticks,
is allowed. The generated `map/graph.md` is the exception: its published copy links only to
published files. An idea from `brainstorm/` becomes project work as a new task that restates it
(see the README in that directory).

## 6. Node headers

Every task, result, paper, site page and published dataset carries a node header: YAML
front matter at the top of its main document, or a `node.yaml` beside a non-markdown
artifact. Fields and allowed values: `tasks/README.md`. After changing a header, run
`opsci map build`. Nodes under `brainstorm/` are part of the project graph, drawn in a box of
their own, and edges may join them to project nodes; `opsci map build` also writes
`brainstorm/map/` with the brainstorm nodes alone. A brainstorm task is soft-private unless
its header says otherwise.

The header field `privacy:` grades a node (absent: `policy.default_privacy` in
`publish/manifest.yaml`, `public` in the template):

| `privacy` | meaning | examples |
|---|---|---|
| `public` (default) | may be released; a public task's directory is exported | |
| `soft-private` | not released, but may be mentioned by name elsewhere; the public map names it without a link, grouped with others or reworded if its header gives too much away | private notes, brainstorm ideas, work too messy to release |
| `hard-private` | must not appear anywhere in the release, not even by name | proprietary data, unpublished ideas, collaborators' unpublished work, private information about people |

A soft- or hard-private task keeps its work inside `tasks/<id>/`, so that it is easy to keep
out. Hard-private material outside such a task is listed under `hard_private:` in
`publish/manifest.yaml`.

## 7. Concurrent work

Each person or main agent working at the same time uses its own git worktree and branch.
Logs merge automatically (`.gitattributes`: `merge=union`); the map is rebuilt, not merged.
`context.md` is the one file that needs a real merge; whoever merges checks its line cap.

## 8. Escalation

Stop and report when the task cannot be done as specified, a named file or dataset is
missing or wrong, or a decision belongs to the owner. Report what was tried, what happened
(verbatim output), the evidence, and two or three options, none carried out. A clean
escalation is a good outcome; a silent workaround is a failure.

## 9. Waking with no context

A notification can wake a session whose conversation was cleared on purpose. The state is on
<!-- opsci:context -->
disk. If the notification needs action, run the `open-science-context:continue-context` skill first.
<!-- /opsci:context -->
<!-- opsci:no-context -->
disk. If the notification needs action, read this file, the project `context.md` and the task's
`context.md` first.
<!-- /opsci:no-context -->
If it repeats something already handled, do not reload; check that a wake-up is still
armed, then wait.
