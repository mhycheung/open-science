# Open-science project framework: design review

Date: 2026-09-23. Answers two questions about the proposed framework: (A) does it make sense and
what would I change, (B) how many repos and skills to build.

**What I checked before writing:** the current skills `new-project`, `new-plan`,
`context-management`, `update-from-template`, `slack`; the `default_setup` template (README and
layout, HEAD `9435b31`); the silencio site campaign and its leak gate
(`silencio/src/silencio/site/leakgate.py`); `~/.claude/settings.json`. Statements about external
services (Zenodo, GitHub, Claude Code features) that I did not test here are marked **(unverified)**.

---

## Short answer

**A.** Yes, the framework makes sense. Most of it is the current `default_setup` with three
additions: a project-level layer (map, log, citations, rules), a publish pipeline, and a
personal index page. I would change nine things. The four most important:

1. **Make the public repo an allowlist export, not a filtered copy.** Only paths listed in a
   publish manifest leave the private repo. Deterministic secret and path scans run over the
   *whole* export every time. The LLM review for tone, citations and status runs on the diff
   only. A human approves each release before it is pushed.
2. **Write for the public from day one.** A rule in the private repo that everything is
   written as if public (no judgements about people, status labels on every result) removes
   most of the filter's work. The filter becomes a backstop, not the main defence.
3. **Store the graph edges next to the tasks and generate the map from them.** Each task
   declares `status`, `depends_on`, `supersedes`, `related` in a small header. A script
   builds the graph. The hand-written part of the map is only the short narrative. This
   stops the map from drifting away from the tasks, which is the usual failure of a
   hand-maintained graph.
4. **Do not rename the old skills. Ship the new ones as a Claude Code plugin.** (Decided 2026-09-23: plugin, old skills untouched.) Plugin skills
   get a namespace prefix (you already have `superpowers:brainstorming`), so there is no name
   collision. Renaming breaks things I found: 3 hooks in `~/.claude/settings.json` call
   scripts under `skills/context-management/scripts/`, 6 other skills cite these skills by
   name, and 18 existing projects cite `context-management` in their `CLAUDE.md` or
   `ONBOARDING_MAIN.md`.

**B.** One framework repo (template + skills plugin + tools + framework docs). Per project: one
private repo, one public repo (which also serves the project site through GitHub Actions), and
Zenodo records. The personal index page is a component of the framework repo that is copied
into your existing `github.io` repo. Nine core skills, plus optional SLURM resurrection.

---

## A. Component-by-component review

### What is already right

- A resume document separate from an append-only log. The current `context-management` skill
  mixes "what is true now" with some history; splitting them is correct.
- Rules opened on demand through pointers instead of loaded every session. This matches the
  current design principle ("context is the scarce resource", `default_setup/README.md`).
- `src/` shared between tasks, with tasks working on branches. The current template already
  puts core code in `src/`.
- Recording failed routes. This is the part other people cannot get anywhere else, and it is
  the part most projects throw away.

### (a) `CLAUDE.md` / `AGENTS.md`

Make `AGENTS.md` the main file, because the framework is meant for humans and non-Claude
agents too. `CLAUDE.md` then holds one import line, `@AGENTS.md`, plus Claude-only material
(dispatch tiers). **(unverified: Claude Code's `@path` import in CLAUDE.md; I believe it works
but did not test it in this session.)**

For a public template:
- No absolute paths. Use paths relative to the repo root everywhere. The current template
  writes absolute paths into onboarding blocks (`new-plan` step 5); that must change.
- Model names in `.claude/agents/*.md` are a user setting. Ship them with a comment saying
  "set these to the models you have".
- A short `USER_GUIDE.md` (see "User guide" below). Nobody will follow a 400-line agent
  contract; users need one page.

### (b), (c) contracts

Keep the dispatch rules. Move the project-agnostic mechanics (dispatch tiers, reporting format)
into the plugin skills and keep the contracts in the template short. Reason: a rule inside a
copied file needs `update-from-template` to propagate; a rule inside a skill updates for every
project at once.

### (d) project map

Use a directory, `map/`:

```
map/
  README.md        narrative: the logic of the project in <150 lines, links to task maps
  graph.md         GENERATED from task headers (mermaid), never hand-edited
  dead_ends.md     GENERATED: every task with status failed/superseded/abandoned + one-line reason
```

Each task directory starts its main context file with a header:

```yaml
---
id: t07-mode-fit-v2
title: Mode fit with the corrected likelihood
status: active        # active | done | failed | superseded | abandoned | paused
depends_on: [t03-noise-model]
supersedes: [t05-mode-fit-v1]
related: [t06-start-time-scan]
publish: yes          # yes | no | embargo
summary: One sentence on what this task established or why it failed.
---
```

A script (`map build`) reads every header and writes `graph.md` and `dead_ends.md`. The edges
are then stored in one place, next to the task that owns them. The narrative `README.md` is the
only hand-maintained part and has a line cap. `dead_ends.md` makes the failed routes visible on
the site without extra work.

### (e) citations

Use a directory, `citations/`:

```
citations/
  used.bib         works and software actively used (software with version and DOI)
  consulted.md     works read but not used, one line each: key, what it was checked for
```

Add a `CITATION.cff` at the repo root for how to cite *this* project. GitHub shows it, and
Zenodo reads it for the metadata of the archived release **(unverified)**.

For the publish check "everything is well cited" to be deterministic, documents cite with keys
(`[@key]`) and the check is "every key resolves in `used.bib`". An LLM check of "is this
claim supported" is useful but probabilistic; keep it as a review note, not a gate.

### (f) project context

200-line cap, edited in place, main agent only. The current skill's invariant is the right
test: a fresh agent reads `AGENTS.md` and this file and takes the correct next action. It
should hold: goal (2 lines), task table (id, status, pointer), what is in flight, next step,
decisions waiting on the human, and pointers to the rule ids that apply now.

### (g) log

- One file per month: `log/2026-09.md`. Append only.
- One entry per completed subtask, 1–3 lines: date, task id, what changed, pointer (commit,
  file). Example:
  `2026-09-23 t07 — likelihood fix merged (src@a1b2c3d); v1 fit superseded. → tasks/t07/context.md`
- Agents do not read the log to resume; the context files do that. The log is for the history
  and the public record. Agents can read a file partially (offset/limit, `grep`, `tail`), so
  file length matters less than it used to, but monthly files keep each read small.
- Each task also has its own `log.md`. The project log gets one line per subtask, pointing to
  the task log for detail.

### (h) task directories

```
tasks/<id>/
  context.md       header (above) + task-level current state, <200 lines
  plan.md          optional
  log.md           append-only task log
  map.md           task-level map, only if the task has internal structure
  subcontext/      per-subtask or per-subagent context documents
  scripts/ plots/ logs/ ...   working files
```

Data produced by a task goes under `data/` (see (j)), not into git.

### (i) rules

Merge `PROJECT_PITFALLS.md` into this. Structure:

```
rules/
  README.md        index: one line per rule, with id (R01...) and trigger
  R07-units.md     detail file, only for rules too long for one line
```

Context files cite rule ids ("R03, R07 apply to this task"). One exception to "never loaded
automatically": the few rules that must never be broken (no secrets in the repo, write for
the public, never push to the public repo outside the publish skill) go into `AGENTS.md`,
because a pointer does nothing for a rule the agent needs before it knows to look.

### (j) data

- `data/` is in `.gitignore`. It can be a symlink to scratch.
- `data/MANIFEST.yaml` **is** in git: for each dataset, the path, size, checksum, source
  (URL, DOI, or the task and commit that produced it), which tasks use it, and its Zenodo
  status. Scratch is purged on most clusters; without a manifest a purged dataset cannot be
  rebuilt or even identified.
- Zenodo: publish data at releases, not continuously. Use the Zenodo sandbox to test first.
  Checked 2026-09-23 against developers.zenodo.org and help.zenodo.org:
  - API rate limit, authenticated: 100 requests/min, 5000 requests/hour. One new version takes
    roughly 5–10 calls plus uploads, so several versions a day are within the limit.
  - No documented limit on the number or frequency of versions. "Not documented" does not mean
    "allowed without limit"; there is also no stated policy against it.
  - Each version is a new record with its own permanent DOI, linked by a concept DOI that
    resolves to the newest version. Published records cannot be deleted; withdrawal leaves a
    tombstone page.
  - Only one unpublished new-version draft can exist at a time, so uploads must be serial.
  - Per record: 50 GB and 100 files. A new version can import unchanged files from the previous
    version without duplicating them.
  - Files of a published record can be modified without a new DOI only within 30 days and only
    for minor corrections.
  Because every version is permanent and public, the Zenodo upload is its own skill with an
  explicit human confirmation, and it runs at publish releases, not on every data change.
  The project site cites the concept DOI, which always resolves to the newest version.
- **Tar groups, to stay under 100 files per record** (user decision 2026-09-23). Data is
  packed into at most 100 tar groups, grouped by how often it changes (e.g. one per task or
  dataset; finished data apart from active data). Tars are reproducible, so unchanged input
  gives a byte-identical tar:
  `tar --sort=name --mtime='2000-01-01 00:00Z' --owner=0 --group=0 --numeric-owner -cf - <dir> | gzip -n > <group>.tar.gz`
  (GNU tar ≥ 1.28; check `tar --version`). A new version imports the previous version's
  files and replaces only the groups whose checksum changed. A file list (path, size,
  checksum, which tar) is uploaded next to the tars, not inside them. It is the same data
  as `data/MANIFEST.yaml`.
- Code DOIs: the Zenodo–GitHub integration archives each GitHub release of the public repo
  automatically **(unverified for the current integration)**. That covers code without extra
  tooling.

### Something missing: result provenance

"Reproducible and verifiable" needs, for every published result, a record of: the `src`
commit, the environment lock file (`pixi.lock` or equivalent), the command, and the checksums
of the inputs. A small `provenance.yaml` written next to each result by the run script is
enough. Without it, the public repo shows results but a reader cannot reproduce them. This is
also what makes the status label "verified" mean something: verified = has provenance and a
stated check.

### Something missing: status labels on everything published

Every document that reaches the public repo carries `status:` in its header (the same
vocabulary as tasks). The site shows a banner for `active`, `failed`, `superseded`. The
publish check refuses a document with no status. This implements your point (d) about
flagging unverified or ongoing work as a deterministic check.

### The filter

Your proposal: screen for secrets, for embarrassing content, keep a private "do not publish"
file, check citations and status, run on the git diff, transfer manually. I agree with manual
transfer (a snapshot export, not `git pull`, so the private history never reaches the public
repo). Changes:

| check | method | scope |
|---|---|---|
| path is allowed to leave | `publish/manifest.yaml` allowlist + `publish: no/embargo` task headers | whole export |
| secrets (keys, tokens) | `gitleaks` or similar + custom patterns | whole export, every time |
| internal info (absolute paths, usernames, hostnames, allocation ids, emails) | regex list like silencio's `leakgate.py`, no override flag | whole export, every time |
| items in the private do-not-publish file | patterns from `publish/PRIVATE_POLICY.md` (this file is itself never exported) | whole export |
| citation keys resolve | script | whole export |
| status header present | script | whole export |
| tone: judgements of people or their work, offensive words | LLM review, writes a report | diff since last publish |
| claims flagged as done but unverified | LLM review, writes a report | diff since last publish |
| final approval | human reads the report and the diff | per release |

Why the deterministic checks run over everything and not the diff: a secret committed before
the first publish, or a file newly added to the allowlist, is not in the diff. These checks
take seconds. The LLM review is the expensive part, so it gets the diff. Record the last
published private commit in `publish/LAST_PUBLISHED` so the diff is `LAST_PUBLISHED..HEAD`.

Silencio's leak gate is a working example of the deterministic part (it scans file contents,
file names and image metadata and has no override flag). It should be generalized into the
framework tool rather than rewritten.

Also: secrets should not be in the private repo either. Tokens live in environment variables
or a git-ignored `.env`. The filter is the last line of defence, not the first.

### Policy decisions the framework cannot make for you

These go in the template as explicit fields the user fills in:

- **Embargo.** Publishing active tasks and failed routes before a paper exposes the idea
  early. The `publish: embargo` header handles it per task; the user decides the default.
- **Collaborators.** Co-authors should agree before shared work is made public. Content from
  others (unpublished data, emails, referee reports) is excluded by default.
- **Data under agreements.** Some data cannot be public (for example collaboration-internal
  data). The private policy file lists it.
- **Licenses.** Code (e.g. MIT/BSD) and text/data (e.g. CC-BY-4.0) need licenses, or nobody
  can legally reuse them. Zenodo asks for one.

### The project site

- Build it with GitHub Actions from the public repo's `main` and deploy with the Pages action.
  A `gh-pages` branch also works, but the Actions route does not need a second branch.
- Use a static generator that renders every markdown file in the repo with navigation
  (MkDocs with the Material theme is the simplest I know for this). Tabs: Results, Map,
  Dead ends, Tasks, Citations, Context, Log.
- The Results tab is a slot: project-specific pages (like silencio's interactive site) plug
  in there. The template does not try to provide them.
- Large assets go to Zenodo or object storage. GitHub Pages has a site size limit (silencio's
  notes record 1 GB).

### The personal projects page

- A single page plus a `projects.yaml` data file, droppable into an existing GitHub Pages site
  (your silencio site is under `mhycheung.github.io`, so a user site probably exists; I did
  not check its repo).
- Each entry: title, human-written description, tags, status, links (repo, site, DOI).
- Filtering and sorting by tag in client-side JavaScript; no build step needed.
- The page's `AGENTS.md` contains your sentence verbatim: "Do not use a cream or off-white
  background, italic accent words in headlines, numbered "01/02/03" section labels, monospace
  labels, or pill-shaped buttons."
- The description field ships empty with a comment asking the human to write it. An agent
  may propose tags and links, not the description.

### Your notes 1–5

1. **Renaming old skills** — see the short answer: use a plugin namespace instead. If you still
   want the rename, the hooks in `~/.claude/settings.json` (lines 62, 104, 126) and the
   cross-references in `new-plan`, `new-context`, `continue-context`, `advise-with-context`,
   `new-project`, `slurm-self-resurrect` must be updated in the same change, and the 18
   existing projects will then cite a skill name that no longer exists.
2. **Git without approval** — put `Bash(git:*)` in the template's `.claude/settings.json`.
   Exception: pushing to the public remote only through the publish skill. A deny rule on the
   public remote URL is the simplest way to enforce it.
3. **After each subtask, check map / project context / citations** — make it a fixed
   four-question checklist at the end of the context-management skill's "subtask done"
   step: (i) did a task status or edge change → task header; (ii) does the next agent need
   to know → project context; (iii) did I use or consult a source or package → citations;
   (iv) one log line. A hook can remind the agent when a task `context.md` is edited
   **(the hook itself is untested)**. Keep the check cheap, or agents will skip it.
4. **No brainstorming/spec/planning superpowers** — agreed. The new `new-task` skill writes the
   plan directly, from a short template.
5. **`src/` and worktrees** — superseded by the 2026-09-23 decision below ("Decisions, round
   2", item 1): every concurrent line of work (each human, each main agent) uses its own
   worktree for everything, not only `src/`.

---

## B. Repos and skills

### Repos

| repo | visibility | contents |
|---|---|---|
| **framework** (one repo) | public | `template/` project skeleton; `plugin/` the Claude Code skills; `tools/` the Python package (map build, publish export + checks, site build, Zenodo upload); `projects-page/` the personal page component; `docs/` the framework description |
| per project: **private** | private | the working repo, created from `template/` |
| per project: **public** | public | snapshot exports + project site (Actions → Pages) |
| your **github.io** repo | public | already exists (not checked); receives `projects-page/` once |

Why one framework repo: a project then records one framework commit, and `update-from-template`
diffs one history. Three separate repos (template, skills, tools) would need three
provenance records that must stay compatible.

Why the tools are installed, not copied: a copied publish script in each project must be
updated project by project. An installed, version-pinned package (`pip install
git+...@v0.3`) updates in one place and the pin keeps old projects reproducible.

Name: avoid "Open Science Framework"; that is an existing platform (osf.io).

### Skills (in the plugin)

| skill | does | replaces |
|---|---|---|
| `new-project` | copy `template/` (local copy, else clone the framework repo), fill placeholders, record framework commit, init private repo, optionally create public repo and a projects-page entry | `new-project` |
| `new-task` | create `tasks/<id>/` with header, context, log, optional plan (generic template, no superpowers); create `src` branch + worktree if the task changes code | `new-plan`, `new-context` |
| `context-management` | upkeep: task and project context caps, log entries, the four-question checklist, rule pointers, the three jumps | `context-management` |
| `continue-context` | take over the work a context file describes and drive it; the entry point every jump and watcher prompt starts with | `continue-context` |
| `advise-with-context` | answer the user's questions about a context file or plan, as an advisor: no execution, no edits to the documents | `advise-with-context` |
| `publish` | export via manifest, run all checks, write review report, stop for human approval, push, rebuild site | new |
| `zenodo-release` | upload data listed in the manifest, sandbox first, explicit confirmation, write DOI back to manifest and `CITATION.cff` | new |
| `migrate-project` | move an ongoing or completed project into the framework (see "`migrate-project` skill" below) | new |
| `update-from-template` | same as now, against the framework repo | `update-from-template` |

`continue-context` and `advise-with-context` are separate skills (user decision 2026-09-23;
`continue-context` must be one anyway, since every jump prompt invokes it by name). Both
resolve the context file from the pane registration when none is named. `modify-context`
could be folded into `context-management`. Optional component, outside the core:
`slurm-resurrect`.

### Optional component: SLURM session resurrection (added 2026-09-23)

A generalized version of the current `slurm-self-resurrect` skill, shipped **inside the
framework repo** as an optional component. No framework skill or workflow may depend on it.
**tmux is required** (its unit of resurrection is a tmux session).

Requirements (user decisions, 2026-09-23):
- **Only the user registers, by hand.** The user runs the skill from any pane of a tmux session
  in a SLURM job, and that registers the whole tmux session. Agents never register themselves.
  To be removed in the new version: the `here` subcommand as an agent instruction, the
  `SessionStart` hook `rr_session_start_hook.sh`, and the awareness system prompt that the
  `claude-*` wrappers append (`_claude_rr_awareness` in `~/.bashrc`). The job that hook did
  (caching which session runs in which pane, before Claude's state file can disappear) should
  move to the coordinator, as a periodic refresh.
- **Generic.** No user paths, accounts, cluster names, or wrapper names. Account, partition and
  time limit are read from the running job, as now. The `claude-personal` / `claude-work`
  detection is replaced by a generic mechanism: restore the Claude process's
  `CLAUDE_CONFIG_DIR`, and use a configurable launch command (default `claude`).
- **Queueing is configurable, default `afterany`.** As now, the successor is queued as soon as a
  job starts. The second mode to build is an early-eligible successor: queued with `--begin`
  some hours before the current job's end so it builds priority early. If it starts while the
  old job is still running, it snapshots and ends the old job, then rebuilds. Why it matters:
  on this cluster a job waiting on `afterany` does not build age priority (`PriorityFlags`
  empty, `EligibleTime=Unknown`), and the 09-18 → 09-21 hop left a 2.7-day gap.
- **Keep a default hop cap** (currently 10), reset by hand.
- **Keep the wind-down message and self-note.** The message explains itself, so agents need no
  prior knowledge of the skill.
- **Keep session-jump recovery,** coupled to the framework's context-management skill through
  a documented optional hook, not through hard-coded paths.
- **The current skill stays untouched** until the new one has been tested on a scratch tmux
  server and a real hop. The running lineage depends on it.
- **Remote Control and permission mode are user choices** when registering. Default: Remote
  Control on, `bypassPermissions`. The first time a user runs the skill, it warns them. A
  resurrected session runs unattended, and in bypass mode it runs every command without asking.
  With Remote Control on, the session can be driven from any device logged in to their Claude
  account. The skill records that the warning was shown and does not repeat it.

### Context management v2 (added 2026-09-23)

User design: exactly three kinds of jump.

| jump | when | how the fresh session starts |
|---|---|---|
| **active** | starting a new subtask, starting to execute a written plan, or any point where the transcript is mostly not needed; and when context passes the threshold | immediately, with a `/continue-context` prompt |
| **wait** | after dispatching subagents, background shell jobs or SLURM jobs, when the main agent expects ≥45 min idle (jump if unsure) | the subagent report, the shell job's final output, or the SLURM watcher tells it to run `continue-context` |
| **cache-cold** | the agent stops while something that will wake it later is still running | a watcher pokes it after 45 min; it then jumps. The timer restarts at each stop while something is still running, and is killed when nothing is |

Each project's `AGENTS.md` says: if you are woken with no context by one of these watchers,
use the `continue-context` skill.

At every jump the agent writes: a short summary of the transcript, the project state (task
context, project context, one log line), and sends the user anything they would otherwise
miss (figures not sent another way, messages that would be lost if the conversation ends).

**Review notes on this design:**

1. **The wait jump depends on something not yet tested.** A wait jump clears the conversation
   while subagents or background shell jobs are still running. Whether their completion
   notices reach the *new* conversation after `/clear` is unknown. The current skill avoids
   the question: T3 jumps *before* fan-out and dispatches from the fresh session
   (`references/session-jump.md`). The same reference records that Monitors survive a clear.
   The current `PreToolUse:Agent` hook message goes further and states that "a background
   subagent survives the clear AND its report is delivered to the fresh session"; I did not
   find the test behind that statement. **Test this first.** If notices do
   not survive, a wait jump for Agent-tool subagents needs the subagent to write its report to
   a file and an external watcher to poke the pane.
2. **Track wakers with hooks, not the agent's memory.** The cache-cold watcher needs to know
   whether anything is still running. Claude Code has `SubagentStart` and `SubagentStop` hooks
   (checked in the hooks docs 2026-09-23), so subagents can be counted mechanically.
   SLURM jobs are counted through the watcher's own registry. The docs list no hook for
   background shell or Monitor completion; those might be detected as child processes of the
   Claude process (untested). The `Stop` hook then re-arms or kills the timer. An agent-kept
   list will drift.
3. **Assume a 1-hour prompt cache** (user decision 2026-09-23). The 45-min timer is fixed on
   that basis. The 5-minute-cache case is not supported.
4. **Put a size floor on the active jump.** A jump costs the fresh session's onboarding reads
   (`AGENTS.md`, contexts, rules). Below some transcript size, jumping costs more than it
   saves. Suggest: an active jump at a subtask boundary only above ~100k tokens (a setting).
   This figure is an estimate, not measured.
5. **Detect jump intent from actions, not prose.** The current `jump_tripwire.sh` fired in this
   session on a message that announced no jump. It scans the final message's wording. The
   new version should check what the agent did (did it call the jump script), not what it
   wrote.
6. **(Adjusted: panes keep their registration; see "User guide".) Drop the per-pane context registry, or scope it to the session.** In this session the
   pane was still registered to another project's document
   (`code_rewrite/.../2026-09-22-kerr-lorenz-julia-port/main_context.md`). A jump from here
   would have resumed the wrong campaign. Every resume prompt should name its context file
   explicitly.
7. **tmux is required** (user decision 2026-09-23). A detached worker types `/clear` and the
   prompt into the pane, so the context-management skill and the resurrection component both
   state in their docs and in the framework README: *tmux is required*.

### Notifications (added 2026-09-23)

A generic `notify` tool with swappable back ends; Slack first. The template ships **no**
token, workspace or channel. Each user connects their own.

**Findings on the current setup (checked 2026-09-23):**
- The `slack` skill passes the token as a command-line argument
  (`python3 ~/.slack_send.py "$SLACK_TOKEN" ...`). `/proc` on this cluster is mounted without
  `hidepid`, and `ps` shows other users' full command lines (I could read root's). So while
  the send runs, any user on the same node can read the token.
- `SLACK_TOKEN` is exported from `~/.bashrc`, so every process started from a shell inherits
  it: Claude, every subagent, every tool and package they run. If an agent ever prints its
  environment, the token lands in a transcript (stored on disk and sent to the model API).
- `~/.bashrc` is `-rw-r--r--`. It is protected only because the home directory is
  `drwx------`. Copying `.bashrc` into a dotfiles repo would publish the token.

**Recommended design (most secure option that still supports file uploads):**
1. Each user creates their own Slack app in their own workspace, with a bot token limited to
   `chat:write` and `files:write`, and no read scopes. If they only need text, an incoming
   webhook is narrower still (it can post to one channel and nothing else), but it cannot
   upload files.
2. Credentials go in `~/.config/<tool>/notify.env`, mode `600`, in a `700` directory. They are
   not in `.bashrc`, not exported, and not in any repo.
3. The sender script reads that file itself and sends the token in an HTTPS `Authorization`
   header. The token is never on a command line and never in the agent's environment.
4. A Claude Code `deny` rule on reading that file. It does not stop a determined same-user
   process, but it stops an agent from reading it by accident.
5. The publish filter's secret scan includes Slack token patterns (`xox[abp]-`).
6. The setup doc says how to revoke and replace the token.

Other back ends (email, Discord/Matrix webhooks, or a user-supplied command) plug into the
same interface later.

### Decisions, round 2 (2026-09-23)

Answers to the gaps raised after the first review.

1. **Concurrent work uses worktrees, for everything.** Each human or main agent working at
   the same time has its own worktree and branch, covering `src/`, task directories and
   project-level files. There are no file owners. Conflicts are resolved at merge. Two things
   make most merges automatic:
   - the map graph and dead-ends page are generated from task headers, so they are rebuilt
     after a merge instead of merged;
   - log files are append-only, so `.gitattributes` marks them `merge=union` (git's built-in
     driver that keeps both sides' lines).
   The project context is edited in place, so it is the one file that needs a real merge.
   Whoever merges resolves it, then checks the 200-line cap.
2. **Two verification levels: `verified` and `human-verified`.** `verified` needs an
   evidence pointer (the result's provenance record plus the check that was run) and can be
   set by an agent. `human-verified` is set only by a human. The site shows the two
   differently, and the publish filter reports which level each published result has.
3. **No human/AI authorship tracking.** Not needed.
4. **Copyrighted material is excluded.** `lit_cache/` (full texts of sources) is outside the
   publish manifest by default. The filter refuses PDFs and long verbatim excerpts that are
   not the project's own work.
5. **Papers and results are graph nodes.** Users add their own paper directory (e.g.
   `paper/` with LaTeX). The template does not prescribe its layout. Everything that
   carries a result is a node in the project graph: tasks, papers, the results pages of the
   project site, figures and datasets that are published on their own. So the header in
   (d) becomes a general node header with a `type` field
   (`task | result | paper | page | dataset`). It can sit at the top of any document or in
   a small `node.yaml` beside a non-markdown artifact. `map build` reads all of them. A
   test checks that every published result page and paper is in the graph.
6. **No site-specific details, in either repo.** The public repo contains no absolute
   paths, partitions, accounts, hostnames, usernames, or other details of one person's
   setup, in SLURM scripts or anywhere else. The publish filter enforces this. The private
   repo is encouraged to follow the same rule, to keep everything as general as possible:
   - site settings (partition, account, scratch path, module loads) go in one git-ignored
     local config file, e.g. `config/site.local.yaml`;
   - the repo ships `config/site.example.yaml` with placeholders;
   - scripts read the settings from the config;
   - paths are relative to the repo root, or come from that config.
7. **Dead ends stay within the project.** No searching across projects.
8. **Private and public repos are kept in agreement, in both directions.**
   - A consistency check compares the public repo with the private repo's filtered export,
     for the files in the manifest. Any difference is reported, other than private
     material and the difference since the last publish. It runs at every publish, and on
     demand.
   - A `pull-public` step brings public-side changes (merged outside pull requests, issue
     fixes) back into the private repo. It diffs the public repo against the last
     published snapshot and applies the changes to the private repo on a branch, for the
     user to review and merge. The next publish then includes them, so the two sides stay
     the same.
   - This is part of the `publish` skill, or a small `sync-public` skill.
9. **No "user rulings" section; rules only.** Anything that used to be a user ruling is
   written as a rule in `rules/`, by the user or by an agent. Context files point to rule
   ids.

### User guide (added 2026-09-23)

The framework repo has one guide for users, `USER_GUIDE.md`, linked first from the
README. It covers only what the user does or sees; the internal mechanics stay in the skills.
It must be readable in 3 minutes, which is about 600 words, and a test checks the word count.
The name is `USER_GUIDE.md`, not a "human" guide: the repo should not read as purely agentic
or frame its users as humans versus agents (user decision 2026-09-23).

Contents, in this order:

1. **The idea, in a few sentences.** Everything is written down so that it can be public,
   reproduced and checked, including failed routes. Agents keep short "current state" files so
   any session, or any person, can pick up the work from them. The conversation itself is
   disposable.
2. **One-time setup.**
   - tmux is required for context management and resurrection.
   - Put `set -g mouse on` in `~/.tmux.conf` so panes can be clicked, resized and scrolled
     with the mouse.
   - Notifications: create your own Slack app (only permission to post messages and upload
     files), put its token and channel in `~/.config/<tool>/notify.env` with mode 600, and
     never commit or share it. A short step list, with a link to Slack's docs.
3. **Daily use.**
   - Each tmux pane is registered to one context file, so it knows which work it drives.
     To take over work in a pane, run the `continue-context` skill. Doing this in the wrong
     pane resumes the wrong work.
   - **Agents will clear their own conversation and type a prompt into their own pane**
     (a "jump"). This is expected: they do it to keep the context small, and they are
     resuming from the context files. Nothing is lost. What the conversation contained is
     summarized in the context files and the log, and anything meant for you is sent to you
     before the jump.
   - Registering a session for SLURM resurrection is something you do, from any pane; agents
     never do it. The first use warns about Remote Control and permission mode.
4. **What to read, and what you may edit.** The project context to see where things stand,
   the map for the project's logic, `rules/` to add or change rules. How to mark a result
   `human-verified`.
5. **Publishing.** Nothing becomes public until you approve a publish. What the publish
   report shows you, and where the list of never-public material lives.

Review note 6 (context management) is adjusted accordingly: panes keep their registration,
which the guide explains. Resume prompts still name their context file explicitly, so a stale
pane registration cannot send a jump to the wrong work.

### Plans in `new-task` (added 2026-09-23)

Same philosophy and procedure as the current `new-plan` skill, without the superpowers. What
the superpowers did today: `brainstorming` settled the design with the user and could write a
separate spec; `writing-plans` supplied a format, which the current plan template already
overrides (its 2–5-minute TDD micro-steps contradict the Opus 5 guide). Without them, the
design discussion becomes one short step of the skill, and the design goes into the plan
itself. **No spec files anywhere in the framework** (user decision 2026-09-23: not useful).

**Philosophy** (unchanged, from the current `new-plan` skill and the Opus 5 prompting guide):
- Give the complete specification up front, then let the executing agent run. Each task section
  carries everything that task needs.
- No "verify each step", no self-review or second-agent review layers. A control case goes
  inside the task.
- Gates only where a mistake is expensive or irreversible: deleting or overwriting data, large
  compute, a claim reaching the user or the public repo.
- No paranoid or hedging wording, and no numbered micro-steps for routine mechanics. A step
  states the outcome, the artifact and the acceptance criterion.
- Every task has a tier and a cost estimate with a ±2× band. Only parallel tracks are
  dispatched, at `opus-med-effort` by default; sequential work and open-ended debugging stay
  with the main agent.
- The plan is reviewed by the user before execution starts.
- **Run to completion.** Once execution starts, the main agent does not pause or stop until
  the whole plan is done, unless the user set a hold point or something fatal happens (see
  "Autonomy" below).

**Procedure:**
1. Settle the design: ask the user only the decisions that are theirs (goal, what counts as
   done, constraints, budget). Propose one recommended approach, and name the alternatives
   in a line each.
2. Write `tasks/<id>/plan.md` from the template.
3. Create the rest of the task directory (context with node header, log, `subcontext/`), and
   a worktree if the work runs concurrently with other work.
4. Freshness test: can a fresh session act from `AGENTS.md`, the project context and this
   task's context alone?
5. Report the plan to the user and stop.

**Plan format:**

```markdown
---
(node header: id, title, status, depends_on, supersedes, related, publish, summary)
autonomy: autonomous     # autonomous | checkpoints | collaborative
hold_at: []              # e.g. [S2, before-publish]; only used with "checkpoints"
---
# <task title>: <one line on what gets built or measured>

## Goal
2–5 sentences. What "done" means, as a falsifiable criterion.

## Design
The chosen approach and why, in a few paragraphs. Rejected alternatives, one line each
(they become dead-end entries if they are later tried and fail).

## Constraints
Conventions (units, normalizations), environment, compute (read from the site config,
never written into the plan), frozen files, and the rule ids that apply (R03, R07).

## Delegation
Which subtasks run in parallel and are dispatched; everything else is hands-on.

## Subtask <S1>: <name> — <tier> — ~<cost> (±2×)
- Output directory: tasks/<id>/<S1>/
- Delivers: <artifact paths>
- Steps: outcome + artifact + acceptance criterion each, all numbers and paths inline
- Gate (only if expensive or irreversible): the check, the command, the pass bar
- Fatal if: <conditions specific to this subtask that make continuing wasteful or harmful>

## Escalate only if fatal
The assumptions that would invalidate the plan if false, and what to do instead.

## Budget
Estimate per subtask and a ceiling; measured values are recorded in the task context.
```

**Autonomy.** The user sets how autonomous execution is, in the plan header. Default
`autonomous`. The value names how much autonomy the agent has, not how much it escalates.

| level | the main agent |
|---|---|
| `autonomous` (default) | runs the whole plan without pausing and escalates only fatal problems |
| `checkpoints` | as `autonomous`, but also stops at the hold points listed in `hold_at` |
| `collaborative` | also asks whenever two readings of the plan would lead to materially different work |

- **Fatal** means continuing would waste the budget or cause harm:
  - an assumption the plan rests on is shown false;
  - the budget ceiling would be exceeded;
  - an irreversible action the plan does not cover;
  - missing access or credentials;
  - a result that contradicts the goal itself.

  A failed gate, or one subtask over its estimate, is not fatal by itself. The agent fixes
  it within the plan's scope, records it, and continues.
- **Non-fatal questions do not stop the work.** The agent picks the most reasonable option,
  records the question and its choice under "Open questions" in the project context,
  notifies the user, and continues. The user answers when convenient. A changed answer
  becomes a rule or a new subtask.
- The user can change the level at any time by editing the header. The executing agent
  re-reads it at each subtask boundary.

**Changes from the current template:**
- **One location.** Plan, context and outputs share `tasks/<id>/`. The current template
  uses three directories with a shared slug.
- **No "User rulings" section.** Rulings become rules in `rules/`, cited by id.
- **No git-authorization block.** git is allowed.
- **No absolute paths and no submit lines.** Compute settings come from the site config.
- **A Design section** replaces the separate spec file.
- **"Stop and report if" becomes "Fatal if".** The default is to continue, not to stop.

### `migrate-project` skill (added 2026-09-23)

Moves an ongoing or completed project into the framework. Kept as small as possible: the
template and the other skills already define the target, so this skill only states how to
get there.

1. **Work on a branch in a worktree** of the existing repo, so the original stays untouched
   until the user merges. If the project is not a git repo, `git init` it and commit the
   current state first. If jobs or subagents are running, record their ids in the new
   project context; do not wait for them.
2. **Add the scaffolding** from the template without overwriting any existing file, and
   record the framework commit.
3. **Propose a mapping and get approval before moving anything:** which existing
   directories become `tasks/<id>/`, what goes to `src/`, `data/` (plus manifest), `paper/`,
   `citations/`, and what stays where it is. Old context documents and plans move into
   their task's `subcontext/` and are not rewritten. Existing pitfalls or rules files become
   `rules/`. Anything that fits nowhere goes to `archive/`. Use `git mv` so file history is
   kept.
4. **Write the new documents from what is there:** a node header per task (status taken
   from the old context docs: completed work is `done`, abandoned routes are
   `failed`/`abandoned`), a project context, and one log entry: "migrated on <date>, from
   commit <sha>". No log is back-filled, since git history already records it. Then run
   `map build`.
5. **Check and report:** no file lost (compare file lists before and after), the map builds,
   and the project context passes the fresh-session test. Report the mapping and anything
   left `TODO`.

A **completed** project needs only steps 1–2, the headers and map from step 4 (all
nodes `done`/`failed`), and a project context stating that the project is complete.

### Suggested build order

1. Template layout + task header format + `map build` script (everything else depends on these).
2. `new-project`, `new-task`, `context-management`; test on one small real project.
3. `migrate-project`; migrate one existing project as the second test.
4. `publish` (generalize silencio's leak gate) and the project site.
5. `zenodo-release`, then the personal projects page.

### Tests (requirement added 2026-09-23)

Every component ships with tests, and a component counts as done only when its tests pass. The
framework repo has a `tests/` directory and one command that runs them all, plus CI (GitHub
Actions) for every test that doesn't need a cluster. Tests that need a real service or cluster
are marked, and run by hand before a release.

Each test has a **control case**: an input that must be refused, alongside one that must pass.
A gate that passes everything proves nothing.

| component | what the tests check |
|---|---|
| template | a fresh copy has every required file and directory; no placeholder or absolute path survives `new-project`; `.gitignore` excludes `data/` |
| task headers + `map build` | valid headers produce the expected graph and dead-ends page; a missing field, unknown status or dangling `depends_on` is reported |
| `new-project`, `new-task`, `migrate-project` | run on a scratch directory; the resulting layout matches the spec; the provenance commit is recorded; migrating a sample old-layout project loses no file |
| context-management | line caps are enforced; a resume from the context files alone reaches the stated next step (scripted fresh-session check); the Stop hook re-arms the cache-cold timer when a subagent/SLURM job is live and kills it when nothing is; the jump check does not fire on a message that only mentions jumps (the current tripwire fails this, see review note 5) |
| jumps (tmux) | on a scratch tmux server: active jump clears and delivers the resume prompt; wait jump is woken by each kind of waker (subagent report, background shell, SLURM watcher); cache-cold watcher fires after the timer. First test to run: whether a background subagent's report reaches the session after `/clear` |
| publish filter | planted secrets, Slack/API tokens, absolute paths, usernames, emails, a path outside the manifest, a document with no status, an unresolved citation key: each must be refused. A clean tree must pass. The LLM tone review is scored on a small labelled set (including normal technical criticism that must *not* be flagged) |
| private ↔ public sync | the consistency check reports a planted difference and passes on an identical pair; `pull-public` carries a public-side edit into a private branch without touching private-only files; an agent cannot set `human-verified` |
| project site | builds from a sample public repo; every markdown file is reachable; links resolve; no leak-scan hits in the built site |
| `zenodo-release` | runs against the Zenodo **sandbox** only: new version, reuse of unchanged tar groups, reproducible tar (same input gives the same checksum), 100-file limit refused before upload |
| notify | the token never appears in argv (checked with `ps` during a send) or in the agent's environment; a group-readable config file is refused; one real message per back end, marked manual |
| SLURM resurrection | existing scratch-server tests (`tests/test_jump_recovery.sh`) generalized; registration by the user only; first-use warning shown once; Remote Control and permission-mode settings carried to the resumed session; queueing modes (`afterany`, early-eligible) against a real scheduler, marked manual |
| user guide | `USER_GUIDE.md` is at most ~600 words; every command and path it names exists in the repo |
| personal projects page | tag filtering and sorting work; renders with an empty description; a style check for the banned elements (cream/off-white background, italic headline accents, numbered section labels, monospace labels, pill buttons) |

---

## What I do not know

- How well an LLM tone review works on scientific text in practice (false positives on
  normal technical criticism such as "method X is biased in regime Y"). This needs a test on
  real documents before it becomes a gate.
- Whether agents will keep the four-question checklist up to date in long sessions. The
  generated map reduces the damage if they do not, but I have no data on compliance.
- The Zenodo and Claude Code details marked (unverified) above.
