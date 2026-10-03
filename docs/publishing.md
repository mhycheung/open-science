# Publishing and the filter (`open-science-publish`)

Each project has two repositories. The **private** one is where you work, with collaborators or agents if you use them; it
can hold drafts, notes and anything else. The **public** one holds only what you allowed,
after checks, and only after you approved the exact export. You never copy files to it by
hand.

```bash
claude plugin install open-science-publish@open-science
```

For Codex: `codex plugin add open-science-publish@open-science`. The same export and
approval procedure applies; see [Claude Code and Codex](agents.md).

The plugin has two skills: `open-science-publish:publish` (this page) and
`open-science-publish:zenodo-release` ([Zenodo releases](zenodo.md)). The tool behind them is
`opsci publish`. Publishing needs git, `opsci` and a GitHub account, and works with any git
repository. In a project made from the [template](project-template.md) the check also covers
the map and the node headers.

## The filter, step by step

1. **The manifest decides what can leave.** Only paths listed under `include` in
   `publish/manifest.yaml` are exported; paths under `never` are refused even when `include`
   covers them. A task directory is exported only if its `context.md` header says
   `privacy: public`; a node whose header says `soft-private` or `hard-private` stays
   private (see [Privacy tiers](project-template.md#privacy-tiers)). Some paths are never
   exported whatever the manifest says: `publish/`, `lit_cache/`, `data/`, `messages/`,
   `.opsci/`, `.env`, `config/site.local.yaml`, and `citations/consulted.md` (the works read
   but not used, soft-private).
2. **The export is a snapshot of one commit.** `opsci publish export` takes the committed
   tree of one commit (default `HEAD`), so uncommitted changes and the working tree never
   leak. It copies the allowed files and computes an **export id**, a hash of every exported
   path and its content, and of your overrides (see [Overriding a finding](#overriding-a-finding)). It refuses a symbolic link among the exported files. It replaces
   each redaction marker with `[redacted (<reason>)]` (see [Redaction](#redaction)), and
   drops each omission span (see [Omission](#omission)). It
   rebuilds `map/graph.md` and `map/dead_ends.md`: hard-private nodes are left out, and
   every other node whose files are not exported is named without a link (see
   [Unpublished nodes in the map](#unpublished-nodes-in-the-map)).
3. **The checks.** `opsci publish check` runs every check below on the export and writes a
   report. Any problem fails the publish, unless it is of a kind you may override and you
   have overridden it (see [Overriding a finding](#overriding-a-finding)).
4. **The review.** You read the diff since the last publish. If you publish with the
   `open-science-publish:publish` skill, the agent also reviews it with the review rubric
   and writes its findings into the report. The review never blocks the publish by itself;
   you decide.
5. **Your approval.** You approve this export id: by running the push with it, or, with the
   skill, in the conversation. An earlier general "go ahead" does not count.
6. **The push.** `opsci publish push --export-id <id> --message "<summary>"` copies the
   export into the public repository as a new commit, pushes it, and records the publish.
   The commit message is the summary (what this publish adds, written for readers of the
   public repository; with the skill, the agent proposes it and you approve it with the
   export), then the files added, changed and removed, and the private commit.

## The checks

| check | refuses |
|---|---|
| `policy` | `policy.collaborators_agreed` in the manifest is not `true`: confirm that co-authors agree to publishing shared work (or that there are none), then set it |
| `leak` | internal information in a file's name, its content (for a PNG, its text chunks; for a PDF, its dictionaries and strings): absolute paths, email addresses, IP addresses (not package versions such as `alsa-lib-1.2.16.1`), SLURM job identifiers, your user name, this machine's host name and domain (neither on a GitHub Actions runner), the values in `config/site.local.yaml` (scratch path, account, partition, `identifiers`), and the patterns in `publish/PRIVATE_POLICY.md` |
| `secret` | private keys; Slack, GitHub, AWS, Google, Anthropic and OpenAI tokens and keys; a token, password or key assigned to a variable; a password inside a URL. If `gitleaks` is installed, its findings are added |
| `citation` | a citation key (`[@key]` in markdown, `\cite{key}` in LaTeX) that is not in an exported `.bib` file |
| `map` | node header errors, and a `map/graph.md` or `map/dead_ends.md` that is out of date |
| `status` | an exported markdown file with no `status:` in a front-matter header, or a status that is not allowed, unless it is in `status_exempt` |
| `copyright` | a PDF of more than one page, or an EPUB or DjVu file, not covered by a `type: paper` node (a one-page PDF counts as a figure); a quotation of more than 150 words; a run of 40 or more words shared with a file in `lit_cache/` |
| `evidence` | a `verified` or `human-verified` node whose `evidence` file is not exported |
| `human-verified` | a node whose `verification: human-verified` line was last changed in an attributed agent commit (`Agent:`, `Claude-Session:`, `Codex-Session:`, or a Claude/Codex `Co-Authored-By:` line) |
| `references` | a node header whose `depends_on`, `supersedes` or `related` names a hard-private node (an edge to a soft-private node is allowed; the public map shows it); a link in an exported markdown or HTML file to a file or directory of the commit that is not exported. For a soft-private target the fix is a plain mention in backticks instead of the link |
| `private-content` | exported text that contains, from hard-private material only: the id of a hard-private node (only ids containing `-`, `_` or a digit are matched); its title, if the title has 3 or more words; the path of a hard-private task directory or file; a run of 12 words shared with a hard-private prose file. Text of the template and of the task skeleton is ignored in that comparison. Mentions of soft-private material are allowed; the report lists them as notes |
| `redaction` | a redaction marker with no closing `<!-- /redact -->`, or one with an empty reason |
| `omission` | an omission marker with no closing `<!-- /omit -->` |
| `site-link` | an exported `README.md` that does not link to the project site: the GitHub Pages URL of `public_repo` (`https://<owner>.github.io/<repo>/`), or `site_url` in the manifest. The export adds `The project site: <URL>` under the README's title when the README lacks the link (a note in the report says so), so this check fails only for a README the export cannot change; then add the line by hand |
| `site` | the project site of the export, built as the public repository's workflow builds it (`opsci site build`), fails: a broken link in strict mode, or a leak in the built pages and search index. Skipped with a note if `mkdocs` is not installed |
| `map-overrides` | in `publish/map_overrides.yaml`: a group or node entry that names a published, hard-private or unknown node, a group with fewer than two members or an id already in use, a node in two entries, a missing title or summary, an unknown key |

The `map` and `status` checks run only in a project made from the template (one with
`AGENTS.md` and `config/framework.yaml`); elsewhere the report notes that they were skipped.
A failed check is fixed at its source, never by removing the check or widening the manifest
without your decision. A few kinds of finding are often legitimate; you may accept those
instead (next section). For every other finding, if a legitimate string matches, change the
string.

## Overriding a finding

Some findings are worth a look but are not always wrong. You may accept them, kind by kind,
in `overrides:` in `publish/manifest.yaml`:

| check | kind | why it is flagged |
|---|---|---|
| `leak` | `slurm-job-id` | a SLURM job number (`SLURM` or `jobid=` followed by a number) records that work ran on a batch cluster; with other details it could tie the text to an account there, but by itself it names no person, machine or path |
| `leak` | `slurm-array-id` | a number such as `<job>_<task>` (six to nine digits, an underscore, a short number) looks like a SLURM array job and task number (it may also be an ordinary number) |
| `leak` | `slurm-out-file` | a SLURM log name such as `slurm-<job>.out` carries a job number |
| `copyright` | `long-quote` | a quotation of more than 150 words may copy more of another author's text than fair use allows; a quote marked as a quote and cited next to it is usually fine |
| `copyright` | `lit-cache-text` | a run of 40 or more words shared with a source in `lit_cache/` may copy another author's text without saying so; text marked as a quote and cited next to it is usually fine |

```yaml
overrides:
  - check: leak
    kind: slurm-job-id
    paths: [tasks/**]          # optional: only these paths or globs; omit for every file
    reason: Job numbers in run notes name no person or machine.
    date: 2026-10-02           # the date of your decision
  - check: copyright
    kind: long-quote
    reason: Quotes are marked as quotes and cited next to them.
    date: 2026-10-02
```

Each entry needs `check`, `kind`, `reason` and `date`; `paths` is optional. An overridden
finding does not fail the publish. The report lists every one of them under "Overridden by
the user", under the override that covers it, so your approval still shows them. A leak in
the built site (the `site` check) is overridden with the same text in the exported files, or
by an override of its pattern with no `paths`. The overrides are part of the export id: a
new override needs a new check and a new approval. The push writes the overridden leak
patterns into the site workflow (`opsci site build --allow-leak <pattern>`), so the site
build on the public repository accepts them too; `opsci site preview` accepts them as well.
The report notes an override that no longer covers any finding.

With the `open-science-publish:publish` skill, the agent lists the findings of each
overridable kind, tells you why the kind is flagged, and asks whether to fix them or accept
them. It writes an override only on your answer, never on its own judgement.

Every other check and kind is never overridden, and the manifest refuses an entry for it:

- `secret`: a token, key or password; once public it must be revoked;
- the other `leak` patterns: your user name, the host name and domain, the values in
  `config/site.local.yaml`, email addresses, IP addresses and absolute paths each name a
  person, a machine or an account. The patterns in `publish/PRIVATE_POLICY.md` are your own
  list: change the list instead;
- `private-content`, `references`, `redaction` and `omission`: they protect hard-private
  material and the markers that hide it;
- `human-verified`: only you set that level, in a commit of your own;
- `copyright` for a whole PDF, EPUB or DjVu outside a `type: paper` node: a copy of a work;
- `policy`, `citation`, `status`, `map`, `evidence`, `site-link`, `map-overrides`, and a
  `site` build that fails for a broken link: each has a direct fix in the private
  repository.

## The report

`opsci publish check` writes `publish/reports/<date>-<commit>.md` and, next to it, the diff
since the last publish as `.diff` (the whole export on the first publish). The report lists:

- the private commit, the export id, the date of the last publish;
- the number of files exported and excluded;
- the result of the checks, with every problem; the failures of a kind you may override,
  grouped by kind with why each kind is flagged; and every finding you overrode, under
  "Overridden by the user";
- the verification level of every exported node;
- every excluded file, with the reason (not in the manifest, listed under `never` or
  `hard_private`, the task's or node's `privacy` tier);
- notes: the mentions of soft-private material in the export (a count and the first few,
  with file and line), for the reviewer to check that each is in passing, and the number of
  redactions and omissions in each file;
- a section "Review (tone, claims)" that the agent fills in.

The review follows the rubric in the skill (`reference/review-rubric.md`). It reads only
added lines, quotes each flagged passage with its file and line, and suggests a rewrite. It
flags three kinds of passage:

- **tone**: a passage that judges people rather than work (their competence, honesty or
  motives), contempt or ridicule, insults, and private remarks (gossip, the content of private
  emails or referee reports). Normal technical criticism ("method X is biased in regime Y")
  is not flagged;
- **claims**: a result called done, confirmed or verified when its node header says
  `status: active` or `verification: unverified`; a number with no evidence pointer; a check
  said to pass with no record of it;
- **private material**: an added passage that paraphrases hard-private material (a result,
  a name, a collaboration, a plan or a number that appears only there), and a mention of
  soft-private material (a private task, `private-docs/`, `brainstorm/`, notes outside the
  manifest) that is more than a mention in passing. The `private-content` check catches
  exact copies of hard-private material only; this part of the review is there for
  paraphrase.

`publish/reports/` is tracked in the private repository: the approved report is the record
of your approval.

## Unpublished nodes in the map

The public map shows that private work exists without showing what it is. It names every
node that is not hard-private; a node whose files are not exported, such as a soft-private
task or a brainstorm idea, appears without a link, with its title, summary, status and
edges. Hard-private nodes and their edges do not appear at all.

A title or summary can say too much: a reader could reconstruct the work from "Acme
detector gain at 3.2 kV, March run". The report lists how each unpublished node appears,
under "Unpublished nodes in the public map". The agent, or you, then decides for each node
whether to show it as it is, to give it a more general title and summary, or to replace a
group of connected unpublished nodes with one node that names the kind of work only. The
choices go in `publish/map_overrides.yaml`, which is committed and never exported:

```yaml
groups:
  - id: private-calibration
    title: Private calibration work
    summary: Several private studies of the detector calibration.
    members: [t05-gain, t06-drift]
nodes:
  t07-residuals:
    title: A private cross-check
    summary: A cross-check of the fit.
```

A group takes the place of its members: edges to a member point to the group, and edges
between members disappear. The status of a group is its members' status if they agree,
otherwise `active` if any member is active, otherwise `done`; `status:` in the entry sets
it. Only the published copy of the map changes; the committed map keeps every node as it is.

## Hard-private mentions

When `references` or `private-content` finds hard-private material in the export, the agent
lists **all** the findings to you, each with its file, line and text, and asks you, for
each one, whether to change the wording, remove it, or redact it. It proposes redaction
only where removing the text would break it, for example the flow of a context document.
The agent never decides this alone. It applies your choices in the private repository,
commits, and runs the check again.

## Redaction

A redaction marker is written in the private source file:

```
<!-- redact: <reason> -->text<!-- /redact -->
```

The text may span lines. The export replaces the whole span, markers included, with
`[redacted (<reason>)]`; the private repository keeps the full text. The checks and the
rebuilt map run on the redacted text. The standard reasons are "proprietary data",
"unpublished work by collaborators" and "private information". The `redaction` check
refuses a marker that is not closed and one with an empty reason.

## What is science or code

The public release holds the science and the code. The publish skill's guide
(`plugins/open-science-publish/skills/publish/reference/science-or-code.md`) says what that
covers. In short: a task whose goal is not science or code (the results website,
infrastructure, tooling, release preparation) is `soft-private`, set when the task is
created and checked at publish; the website's source code is still exported. In a public
task, a bug that touched a result and every failed route stay; environment trouble, agent
mechanics, clock times, budget ceilings, deadlines, mood, reporting and details of anyone's
network or machine are omitted. A user's message is never published verbatim: it becomes a
dated ruling in plain words. Dates and measured costs stay. Each named person is listed for
you to decide.

## Omission

Housekeeping that the context files carry for you, such as "commit the plots?" in "Waiting
on the user", and passages that are not science or code are not published. The agent that
writes the file, or the publish skill before the approval, wraps each in an omission marker:

```
<!-- omit -->- Whether to commit the new figures?<!-- /omit -->
```

The export drops the span, markers included, and the lines it filled; a `## ` section left
empty loses its heading. Unlike a redaction it leaves no trace, and the agent adds it without
asking, listing each omission for your approval. Only markdown files are affected. An item
about the task's or the project's goal ("next: compute $X$ for the task goal", a scientific
decision you owe) stays unmarked. The private file keeps every item.

Reading every exported file for these passages, often split across many subagents, can use
millions of tokens. So after the first publish, the publish skill reads only what changed
since the last publish. You can ask it to fix only the findings of the checks instead, or to
read every exported file. On a first publish, it tells you how large the export is and asks
whether to read it all or fix only the findings of the checks. It asks before it starts
subagents. The deterministic checks run in full whatever the scope.

`opsci publish pull-public` cannot apply cleanly a public edit next to a redacted span,
because the public text differs from the private text there; bring such an edit in by hand.

## Commands

```bash
opsci publish status                        # 1. drift and pending changes
opsci publish check                         # 2. export HEAD, run the checks, write the report
opsci publish push --export-id <id> -m "<summary>"  # 6. after your approval
opsci publish export --out <empty dir>      # the export alone, without checks
opsci publish pull-public                   # bring public-side changes into a private branch
opsci site preview                          # build the project site of the current export
```

All take the project root as an optional argument (default `.`); `export`, `check`, `push`
and `site preview` take `--commit` (default `HEAD`).

### `opsci publish push`

It refuses when:

- the export of the commit has a different id from the one you approved (the export changed
  since the report; check and review again);
- any check fails (an overridden finding does not fail);
- the public repository has changes the private one lacks (drift);
- no public repository is set: set `public_repo:` in `publish/manifest.yaml` or pass
  `--public-repo <URL or path>`;
- the public repository already equals the export.

Otherwise it replaces the contents of its checkout of the public repository (`.opsci/public`,
git-ignored) with the export, keeping `.github/`; writes the site workflow
`.github/workflows/site.yml`; commits with the private repository's git identity; and pushes
`main`. Then it writes the private and public commits, the date and the export id to
`publish/LAST_PUBLISHED` and commits that file in the private repository.

The project's `.claude/settings.json` denies `git push public` and `git push --mirror`.
A Codex plugin hook of `open-science-project`, once trusted, guards those direct commands too. These checks prevent
accidental direct publication; they are not a complete boundary against arbitrary shell
programs. Both agents must use the publication skill and its approved export. Agent
commits carry `Agent: claude` or `Agent: codex`; unmarked commits cannot reliably be
distinguished from human commits by the attribution check.

### `opsci publish status`

The consistency check. It prints `drift` (changes in the public repository that the private
one lacks) and `pending` (private changes not yet published). Drift exits with status 1; the
skill runs this first and stops if there is drift.

### `opsci publish pull-public`

Collaborators may change the public repository directly (a merged pull request, an edit on
the web). This command takes the public changes since the last publish and applies them to
the last published private commit, on a new private branch `pull-public/<date>-<commit>`.
You review the branch and merge it. It refuses when nothing was published yet, when there are
no public changes, when the changes touch only `.github/`, and when they touch a path the
manifest does not export.

## The project website

Every publish writes `.github/workflows/site.yml` into the public repository (edits to it
are overwritten at the next publish). On each push to `main` the workflow installs `opsci`
over https from the framework repository in `config/framework.yaml` (`git@` and `ssh://`
addresses are converted), at the commit of the `opsci` that ran the publish (else at
`copied_at_commit`), runs `opsci site build . --out _site`, and deploys the result to GitHub
Pages. Without a public framework repository and a commit hash the workflow stops with a
message.

Once, after the first publish, open the public repository on github.com and set
**Settings → Pages → Source: GitHub Actions**. Then every publish rebuilds the site.

`opsci site build [SRC] --out DIR` (default `_site`) builds the site of a public repository
checkout with MkDocs and the Material theme:

- the tabs are Home, Write-up, Results, Map, Dead ends, Tasks, Citations, Context, Log and
  About. Home is `README.md` until the user fills in `ABSTRACT.md`; then Home shows the
  project title, the abstract and the write-up (`WRITEUP.md`), and `README.md` moves to
  About. Write-up appears once `WRITEUP.md` is filled in. Until then each file says only
  `TODO` and is not on the site. Results starts with "Main results" (`results/README.md`), then each result page,
  grouped by task. Map is one page: the hand-written `map/README.md`, the claims graph and
  the project graph. Tasks has an overview table and one page per task holding the whole
  task: its context, results (each figure with its caption), the other figures with their
  captions, plan, map, working notes (`S*/*.md`, `subcontext/*.md`) and log. Citations is a
  table made from `citations/*.bib`: each reference in journal style (`B. P. Abbott et al.
  (LIGO Scientific, Virgo), Phys. Rev. D 93, 122003 (2016), arXiv:1602.03839 [gr-qc].`),
  linked to the DOI or URL and to arXiv, with the entry's `usage` field (else the results
  that use it); `[@key]` in a page links to its row. Log lists the entries of every
  `log/YYYY-MM.md` grouped by date, newest first, with task ids linked to their pages;
- other markdown files (`AGENTS.md`, `PROJECT.md`, `rules/`, `docs/`, `src/README.md`) are
  not pages; a link to one becomes plain text, a link to a file merged into a page points to
  its section there, and a link to a directory points to its page if it has one;
- a page with a node header gets a banner for its status (active, paused, failed, superseded,
  abandoned; a superseded page links to what replaces it) and a line with its verification
  level and evidence;
- `Not verified` and `unverified` labels are red and bold;
- a banner at the top of every page, which stays in view as the page scrolls, warns by
  default: "Warning: this is an ongoing, unpublished project. Many results are very
  preliminary and unverified." Before the first publish the publish skill asks you whether
  to keep it, and recommends it. Set `site_banner:` in `publish/manifest.yaml` to change the
  text, or to `""` to remove it (for example once the work is published). The manifest is
  not exported: the publish writes the text into the site workflow, so a change shows on
  the site after the next publish. `opsci site build --banner TEXT` sets it by hand;
- LaTeX is typeset with MathJax: inline `$...$` and displayed `$$...$$` on lines of their
  own, also in page titles in the navigation and table of contents. The browser tab and
  search results show a plain-text title instead (`$\iota_Q(t)$` becomes `ι_Q(t)`). In a table
  cell write `\lvert x \rvert`, not `|x|`: a bare `|` ends the cell;
- the site title comes from `CITATION.cff`, else the first heading of `README.md`;
- the build runs in strict mode, so a broken link fails it, and the built site must pass the
  leak scan.

`opsci site preview` builds the site of the export of `HEAD` into `_site/` without a public
repository or a push, so you can look at it before publishing. It uses the manifest's
`site_banner` (or `--banner TEXT`). `opsci publish check` runs the same build, as its `site` check.

## Before the first publish

1. Fill in `publish/manifest.yaml`: the `include` and `never` lists, `hard_private` (if
   any), `policy.default_privacy`, and `policy.collaborators_agreed: true` once co-authors
   agree.
2. List what must never become public in `publish/PRIVATE_POLICY.md`.
3. Check the `privacy` tier in the header of each task: `public` for each task that may go
   public, `soft-private` or `hard-private` for the rest.
4. Create the public repository: on github.com, **New repository**, public, empty. Put its
   URL in `public_repo:` in the manifest, or give it to the agent.
5. Ask for `open-science-publish:publish`.

## Configuration

| where | key | what |
|---|---|---|
| `publish/manifest.yaml` | `include`, `never`, `hard_private`, `policy`, `public_repo`, `status_exempt`, `overrides` | see [Project template and layout](project-template.md#publishmanifestyaml) |
| `publish/PRIVATE_POLICY.md` | the fenced block under "Patterns" | one regular expression per line, added to the leak scan |
| `config/site.local.yaml` | `scratch`, `batch.account`, `batch.partition`, `identifiers` | literal values added to the leak scan |
| `publish/LAST_PUBLISHED` | written by `opsci publish push` | the private and public commits of the last publish; `none` before the first |
