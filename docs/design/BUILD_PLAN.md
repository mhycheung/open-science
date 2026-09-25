---
id: build-open-science
title: Build the open-science framework
status: active
depends_on: []
supersedes: []
related: []
publish: no
summary: Build the framework repo (template, skills plugin, tools, projects page, optional SLURM resurrection plugin), with tests, as designed in REPORT_framework_design.md.
autonomy: autonomous
hold_at: [github, public-push, zenodo-production, live-config]
---
# Build `open-science`: template, skills, tools, projects page, with tests

Written 2026-09-23 at the end of the design conversation. Not started.

## Read first

1. **This plan.** It is the authority for what to build and in what order.
2. **`REPORT_framework_design.md`** (same directory). It is the design, with the reasons. Each
   subtask below names the report sections it implements. Read those sections when you reach
   the subtask, not all at once.
3. **`ORIGINAL_REQUEST.md`**, the user's first message, verbatim. Consult it when the report is
   silent.

**Precedence when documents disagree:** this plan > the report's later sections > its
earlier sections > the original request. Known superseded points, already resolved:
- The original request's note 0 (rename old skills to `*-old`) and the report's "Short
  answer" item 4: resolved by decision D1 below.
- "`src/` and worktrees" (report, "Your notes" 5): worktrees for everything ("Decisions,
  round 2", item 1).
- Context-management review note 6: panes keep their registration, and resume prompts
  still name the context file explicitly.
- The subtask output directory: small outputs in `tasks/<id>/`, large data in
  `data/<task-id>/` (see Layout below).
- "Seven core skills" (early Slack summary): nine core skills plus the optional
  resurrection plugin.
- "Stop and report if" (current template): "Fatal if".

## Goal

A public-ready git repo `open-science`, built in this directory, that anyone can use to run
a research project openly. It must be free of anything specific to this user or cluster.
"Done" means all of the following:
1. Every component listed under Subtasks exists.
2. `tests/run_all` passes, including a control case for every check (an input the check must
   refuse).
3. The pilot works end to end on a synthetic project and on a *copy* of a real project, as
   far as the hold points allow: new project → new task → migrate → map build → publish dry
   run into a local bare repo → site build.
4. The user has a short report listing what is waiting at each hold point.

## Decisions already made (do not reopen)

- **D1 (skill naming, user decision 2026-09-23):** ship the new skills as the `open-science`
  plugin, with its prefix (`open-science:new-task`). The user's current skills stay exactly as
  they are: no renaming, no `*-old` copies, no edits. This overrides note 0 of the original
  request.
- **D2:** repo name `open-science`. Build it in `<hub>/open_science/` (`<hub>` = the shared project directory; the real path is in the gitignored context document),
  alongside the design files, which stay where they are. Move the design files into
  `docs/design/` inside the repo.
- **D3:** creating GitHub repos, any push to a public remote, and real (non-sandbox) Zenodo
  uploads wait for the user.
- **D4:** test `migrate-project` on a synthetic project, and also on a copy of a real project.
  Use `git clone` of `<hub>/silencio` into a scratch directory, which
  copies tracked files and history only, no data. Never touch the original.
- **D5:** assume a 1-hour prompt cache. tmux is required for context management and
  resurrection. No spec files anywhere. No superpowers skills: do not invoke them while
  building, and the framework must not reference them.
- All other decisions are in the report, mostly in "Decisions, round 2", "Context management
  v2", "Plans in `new-task`", "User guide", "`migrate-project` skill", "Optional component",
  "Notifications" and "Tests".

## Design summary

### Framework repo layout (target)

```
open-science/
  README.md  USER_GUIDE.md  LICENSE (code: MIT)  LICENSE-docs (CC-BY-4.0)
  .claude-plugin/marketplace.json      lists two plugins: open-science, slurm-resurrect
  plugins/open-science/                core skills (+ hooks, scripts)
    .claude-plugin/plugin.json
    skills/{new-project,new-task,context-management,continue-context,advise-with-context,
            publish,zenodo-release,migrate-project,update-from-template}/
  plugins/slurm-resurrect/             optional; nothing in the core depends on it
  template/                            the project skeleton new-project copies
  tools/                               Python package + CLI (map build, publish, sync, zenodo, notify, site)
  projects-page/                       personal projects page component
  tests/                               run_all + per-component tests + fixtures
  docs/design/                         REPORT_framework_design.md, ORIGINAL_REQUEST.md, this plan
```

Choose the CLI name yourself (short, not `osf`), and record it in the README.

### Project layout (what `template/` produces)

```
AGENTS.md            main instructions; the few never-break rules live here
CLAUDE.md            @AGENTS.md + Claude-only material (verify that @-import works, S0)
README.md
context.md           project context, ≤200 lines, edited in place
log/YYYY-MM.md       append-only project log (.gitattributes: merge=union)
map/README.md        hand-written narrative ≤150 lines; graph.md, dead_ends.md GENERATED
citations/used.bib  citations/consulted.md   CITATION.cff
rules/README.md      one line per rule (R01…); rules/R07-*.md for long ones
contracts/main.md  contracts/subagent.md     (dispatch rules as in the current template)
tasks/<id>/          context.md (node header) plan.md log.md map.md subcontext/ <subtask>/…
src/                 shared code
data/                gitignored (may be a symlink to scratch); data/<task-id>/; data/MANIFEST.yaml tracked
paper/               optional, user-made
lit_cache/           never published
archive/
config/site.example.yaml   config/site.local.yaml (gitignored)
publish/manifest.yaml  publish/PRIVATE_POLICY.md (never exported)  publish/LAST_PUBLISHED
.claude/settings.json      git allowed; push to the public remote denied outside publish
.claude/agents/*.md        dispatch tiers; model names are a user setting
```

Node header: the report's (d), extended by round-2 item 5 (`type: task | result | paper |
page | dataset`) and item 2 (`verified` / `human-verified`).

## Constraints

- **Generality.** No absolute paths, usernames, accounts, partitions, hostnames or emails
  anywhere in the repo. Site settings come from `config/site.local.yaml`. The repo's own
  leak scan (S4) must pass over the framework repo itself, and this is part of the tests.
- **Environment.** System Python here is 3.6; do not use it. Use a pixi environment (pixi is
  installed) with Python ≥3.11 for `tools/` and tests. `mkdocs`, `gitleaks` and `gh` are not
  installed: add them through pixi/pip in the project environment, or choose alternatives.
  Record the choice in the README. GNU tar here is 1.30, so reproducible tars work.
- **Claude Code** is 2.1.280. `claude --plugin-dir <path>` loads a plugin for one session
  without installing it; use it for every plugin test. Tests that launch Claude use the
  command in `$OS_CLAUDE_CMD`; here, `claude-personal`. Run them on a separate tmux socket
  (`tmux -L os-test`), never in the user's sessions.
- **Do not modify the live setup.** That means: `~/.claude/skills/*` (a symlink to
  `<hub>/.claude/skills`), `~/.claude/settings.json`,
  `~/project/claude-personal/settings.json`, `~/.bashrc`, `~/.slurm_resurrect/`. A running
  resurrection lineage depends on the last one. Changes to these are hold point
  `live-config`. Prepare them as a patch plus instructions for the user.
- **Resurrection tests** use their own state directory (`RR_STATE_DIR` pointing at scratch)
  and short SLURM jobs (≤15 min). Stay under 2 SU in total for real-hop tests. Account and
  partition are read from the running job, as the skill itself does.
- **Notifications** during the build: `python3 ~/.slack_send.py "<text>" [file]`. The token
  is read from a private file. Never pass it on the command line, never print it.
- **git:** allowed without approval in this repo. Commit at the end of each subtask. Push
  nowhere until hold point `github`.
- **Rules from the design that the executing agent must obey:** no superpowers skills;
  nothing in the core may depend on `slurm-resurrect`; every check needs a refusal case.

## Delegation

The main agent does S0 and S1 hands-on, since everything depends on them. After S1, these
run in parallel as `opus-med-effort` subagents, each in its own git worktree and branch:
**S3 ∥ S5 ∥ S6 ∥ S7**. The main agent does S2 and S4 hands-on alongside them, then S8 and S9.
Open-ended debugging stays with the main agent. Each dispatch names its output paths, the
report sections to read, and its tests.

## Subtask S0: settle the unknowns (main agent, ~0.5M tokens ±2×) — DONE 2026-09-23 (`docs/design/verification.md`)

Test each of these and record the result, with evidence, in `docs/design/verification.md`:
- **@-import:** does `@AGENTS.md` in `CLAUDE.md` load AGENTS.md's content? (S1 depends on it.)
- **Plugins:** with `--plugin-dir`, do skills appear as `open-science:<skill>`? Do plugin hooks
  work? Does a plugin skill named like a personal skill (`context-management`) coexist
  with it, and which one does the model pick?
- **After `/clear` (the key test for the wait jump):** does a background Agent-tool
  subagent's completion notice reach the new conversation? Does a background Bash task's?
  A Monitor's? Test on a scratch tmux server with a real session.
- **Subagent hooks:** do `SubagentStart` and `SubagentStop` fire, and with what input?
- **Stop hook:** what does it receive? Can it see live background tasks?
- **Zenodo–GitHub integration:** is it still documented? Does `CITATION.cff` feed Zenodo
  metadata? (Docs lookup only.)

If a result invalidates part of the design, adapt that part (smallest change that keeps the
user's intent) and record the change and the reason in `verification.md`. That is not fatal.

## Subtask S1: repo skeleton, template, node headers, `map build` (main agent, ~1.5M ±2×) — DONE 2026-09-23 (`tests/run_all`: 58 passed)

Report sections: (a)–(j), "Something missing" ×2, round 2 items 1, 2, 5, 6.
- Deliver the repo layout above, `template/` complete, the node-header schema (a JSON
  Schema file), and the `map build` tool, which writes `graph.md` (mermaid) and
  `dead_ends.md` and reports bad headers.
- Also deliver `tests/run_all`, the pixi environment, and CI config (GitHub Actions workflow
  file; it runs only after the user pushes).
- **Tests:** the "template" and "task headers + map build" rows of the report's Tests table,
  plus "every published result page and paper is in the graph".

## Subtask S2: core skills (main agent, ~3M ±2×) — DONE 2026-09-23 (`tests/run_all`: 263 passed)

Report sections: "Skills", "Plans in `new-task`", "Context management v2", "User guide"
(for pane registration), "`migrate-project` skill", `update-from-template` (as the current
skill, against this repo), "Your notes" 2 and 3.
- Write the nine skills. Keep each `SKILL.md` short, in the style of the current skills
  (`~/.claude/skills/<name>/SKILL.md`, read-only reference). Reuse their scripts where they
  still fit, but copy them into the plugin and generalize them. Never edit the originals.
- **Context management v2:**
  - three jumps: active, wait, cache-cold;
  - the 45-minute cache-cold timer, re-armed at each stop while something that will wake the
    agent is running, and killed when nothing is;
  - wakers tracked by hooks (S0 decides which);
  - jump detection by action, not by wording;
  - resume prompts name their context file and start with `continue-context`;
  - a size floor for active jumps (setting, default about 100k tokens);
  - the jump writes: transcript summary, state updates, one log line, and a notification of
    anything the user would miss;
  - `AGENTS.md` line: "if you are woken without context by a watcher, use continue-context".
- **`new-task`:** the plan template exactly as in "Plans in `new-task`", including
  `autonomy`/`hold_at` and "Fatal if".
- **Tests:** the rows "new-project, new-task, migrate-project", "context-management",
  "jumps (tmux)", "human guide/user guide" of the Tests table.

## Subtask S3: notify (subagent, ~0.5M ±2×) — DONE 2026-09-23 (merged `1a832f3`)

Report section: "Notifications".
- Deliver a notify tool with a Slack back end and an interface for other back ends. Base it
  on the current `~/.slack_send.py`, which reads its token from a private file (read it; do
  not modify it).
- The setup doc tells a new user how to create their own Slack app with only post-message and
  file-upload permissions.
- **Tests:** the "notify" row. The real-send test is manual; the user runs it with their own
  token.

## Subtask S4: publish, sync, project site (main agent, ~2.5M ±2×) — DONE 2026-09-23 (`e7c978a`, `ecbf6f6`; tone review 30/30 on the labelled set, manual)

Report sections: "The filter", "Policy decisions", "The project site", round 2 items 4, 6, 8.
- **Export:** by allowlist manifest. **Deterministic scans:** generalize
  `silencio/src/silencio/site/leakgate.py` (read-only reference), plus a secrets scanner.
- **Checks:** citation keys resolve, status present, copyright material refused.
- **LLM review report:** tone and unverified claims, on the diff since `LAST_PUBLISHED`.
- **Private ↔ public:** the consistency check and `pull-public`.
- **Site:** built from a public repo, with an Actions workflow.
- **Tests:** the rows "publish filter", "private ↔ public sync", "project site". Check the
  LLM tone review on a small labelled set, including normal technical criticism that must
  not be flagged. Report its accuracy rather than gating on it.

## Subtask S5: zenodo-release (subagent, ~0.7M ±2×) — DONE 2026-09-23 (merged `8bd96ac`; sandbox run waits for the user's token)

Report section: (j) data, including tar groups.
- Against the Zenodo **sandbox** only. Implement: version creation, reuse of unchanged tar
  groups, reproducible tars, the 100-file and 50 GB limits checked before upload, and the DOI
  written back to the manifest and `CITATION.cff`.
- A sandbox token is needed: if none is available, build and test against a mocked API, and
  list "sandbox run" as waiting for the user.
- **Tests:** the "zenodo-release" row.

## Subtask S6: personal projects page (subagent, ~0.5M ±2×) — DONE 2026-09-23 (merged `cb9c89c`)

Report section: "The personal projects page".
- A single page plus `projects.yaml`, with tag filter and sort, that drops into any GitHub
  Pages site.
- Its `AGENTS.md` carries the style sentence verbatim. The description field ships empty,
  for the user to write.
- **Tests:** the "personal projects page" row.

## Subtask S7: `slurm-resurrect` plugin (subagent, ~2M ±2×) — DONE 2026-09-24 (merged `e568e88`; real SLURM hop passed in both modes: early mode run r3 2026-09-24, afterany run r4 2026-09-24 10 passed 0 failed; the handoff was not exercised in either run, stub tests cover it)

Report section: "Optional component: SLURM session resurrection", including the Remote
Control / permission-mode warning.
- Start from a copy of `~/.claude/skills/slurm-self-resurrect/` (read-only; the live lineage
  uses it) and generalize it.
- **Tests:** the "SLURM resurrection" row. Scratch-server tests go in CI-able form. For one
  real hop: a registered scratch tmux session, a short job, the successor rebuilds it, and
  queueing works in both modes.

## Subtask S8: user guide, README, docs (main agent, ~0.3M ±2×) — DONE 2026-09-24

Report section: "User guide".
- `USER_GUIDE.md` ≤600 words, readable in 3 minutes. The README covers install, both
  plugins, CLI and tests.
- **Test:** word count, plus every command and path the guide names exists.

## Subtask S9: pilot and handover (main agent, ~1M ±2×) — DONE 2026-09-24 (synthetic pilot and silencio-clone migration pass; `tests/run_all`: 350 passed)

- **End to end on a synthetic project:** new project → new task (with plan) → a small
  result with provenance → publish dry run into a local bare repo → consistency check → a
  public-side edit → `pull-public` → site build.
- **Migration:** run `migrate-project` on the silencio clone (D4). Report the mapping and
  whether the map builds.
- **Final:** run the leak scan over the framework repo itself; `tests/run_all` must be all
  green; commit.
- **Report to the user** through Slack and a `HANDOVER.md`:
  - what was built and the test summary;
  - the results of S0 and any design changes they caused;
  - what waits at each hold point, with exact commands: `github` (create the repo, push),
    `public-push`, `zenodo-production`, `live-config` (install the plugin into the user's
    Claude config, and whether to retire any old skill);
  - the old-skill and bashrc changes, prepared as patches.

## Hold points (`hold_at`)

The only places the main agent stops. Everything else runs to completion, under the autonomy
rules in the report.
- `github`: creating any GitHub repo, adding a remote, pushing.
- `public-push`: anything that makes content public.
- `zenodo-production`: any upload to zenodo.org (the sandbox is fine).
- `live-config`: any change to the live setup listed under Constraints.

At a hold point, prepare everything, record it in `HANDOVER.md`, notify the user, and
continue with the work that doesn't depend on it. The build stops only when everything left
depends on a hold point.

## Fatal if

- An S0 result makes a core mechanism impossible, and no adaptation keeps the user's intent
  (e.g. jumps cannot work at all).
- Work would require modifying the live setup before the user approves.
- Total spend passes 2× the ceiling below.

Everything else, including failed tests, S0 surprises and a subtask over its estimate: fix,
record, continue. Non-fatal questions go under "Open questions" in the context document, the
user is notified, and work continues.

## Budget

| subtask | estimate (M tokens, ±2×) |
|---|---|
| S0 | 0.5 |
| S1 | 1.5 |
| S2 | 3 |
| S3 | 0.5 |
| S4 | 2.5 |
| S5 | 0.7 |
| S6 | 0.5 |
| S7 | 2 |
| S8 | 0.3 |
| S9 | 1 |
| **ceiling** | **12.5** (SLURM: ≤2 SU) |

These are rough estimates with no measured basis. Record measured values in the context
document.
