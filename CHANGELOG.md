# Changelog

One section per release, newest first. The plugins share the release number (the `version`
in each plugin's `.claude-plugin/plugin.json`, under `plugins/` and `extras/`); a release is tagged `v<version>`.

A release that changes the project layout raises `LAYOUT_VERSION` (`tools/opsci/layout.py`)
and has a **Project migration** section: exact steps that bring an existing project to the
new layout. The `open-science-project:update-from-template` skill runs every migration
section between the project's `layout_version` (in `config/framework.yaml`; no key means
layout 1) and the framework's, in order, before it applies the other template changes.

## Unreleased

- The project graph (`map/graph.md`) and the claims graph (`map/claims.md`) are drawn as
  images instead of Mermaid: `map/graph.svg` and `map/claims.svg` for the site and GitHub,
  and a PNG of each for Notion. Graphviz places the cards and arrows and pdflatex typesets
  them in a serif style, so LaTeX in titles is set properly. Every arrow now points forward
  in time: a superseded node points to the node that superseded it ("superseded by"), and
  a checked node to its verification task ("verified by"); the headers still say
  `supersedes` and `verifies`. An arrow that a longer path already implies is left out of
  the image. The claims graph shows the external works a result uses in brackets on its
  card, not as boxes with arrows. Brainstorm nodes sit in an orange dashed box.
  Verification tasks have a double border, not a hexagon shape. The pages list every edge as
  text: the project graph's node table gains `title` and `depends on` columns, and both
  pages end with an "Other links" list. `opsci map build` redraws an image only when its
  graph changed. Drawing needs Graphviz, pdflatex (TikZ, standalone, lmodern, xcolor) and
  Poppler; `pixi.toml` now includes Graphviz. `opsci publish` redraws each exported image
  from the published nodes, and refuses the export if it cannot. No layout change: the
  next `opsci map build` in a project writes the images; commit them.
- The project site shows a banner at the top of every page that stays in view as the page
  scrolls. By default it warns that the project is ongoing, unpublished and preliminary;
  `site_banner:` in `publish/manifest.yaml` changes the text, and `""` removes it. The
  manifest is not exported, so `opsci publish push` writes the text into the site workflow.
  `opsci site build` and `site preview` take `--banner`. The site also typesets LaTeX with
  MathJax (`pymdownx.arithmatex`): inline `$...$` and displayed `$$...$$`, also in the
  titles shown in the navigation, table of contents and header; the browser tab and the
  search results get a plain-text title (`ι_Q(t)`). The template's
  `AGENTS.md` says to write `\lvert x \rvert` in table cells, as a bare `|` ends the cell.
  No layout change: without `site_banner` a project gets the default banner.
- The site workflow installs `opsci` over https (a `git@` or `ssh://` framework repo is
  converted) at the commit of the `opsci` that ran the publish, not `copied_at_commit`. A
  push retried after the remote rejected the first one no longer reports "nothing to
  publish". In the built site the leak scan unescapes HTML entities before matching, and on
  a GitHub Actions runner it no longer treats the runner's user and host names as site
  identifiers. `opsci publish check` builds the site of the export (the new `site` check).
- The README line naming the framework is opt-in. The new-project and migrate-project
  skills show the user the line and add it only with their consent (`opsci template
  instantiate --framework-line`, a new `framework-line` component). It now links the
  framework's web page (`https://github.com/...`, derived from the recorded repo, new
  placeholder `{{FRAMEWORK_URL}}`) instead of printing the raw remote, which could be a
  `git@` address. New projects and migrated repos start on a branch named `main`
  (`git init -b main`) unless the user asks for another name. No layout change: a project
  that has the old line keeps it.

- Fewer false hits in the publish check, from the pilot project. The leak scan reads only
  the text chunks of a PNG, not its compressed pixel data (a chance path-like byte run in
  an IDAT chunk had matched `absolute-path`). The IPv4 pattern skips package versions: a match inside a
  longer dotted run or after `-` or `>=`/`==` (`alsa-lib-1.2.16.1`,
  `>=0.2026.6.22.1.23.34`), and allows the RFC 5737 documentation ranges. A partition in
  `config/site.local.yaml` named by a plain word (`shared`) matches only where it names the
  partition (`--partition=shared`, `-p shared`, `partition: shared`). Plot captions
  (`**/*.caption.md`) need no `status:` header, and the Notion mirror strips a front-matter
  header from a caption if it has one. The manifest's `status_exempt` now adds to the
  default list instead of replacing it. No layout change: a project that copied the default
  list into `status_exempt` can delete the copy.
- Figure PDFs no longer fail the publish check. The leak scan reads a PDF's dictionaries and
  strings (with object and metadata streams decompressed) instead of its raw bytes: glyph
  lists such as `/CharSet` and compressed page content had produced false `absolute-path`
  hits. Every pattern, email included, now applies to those strings. The copyright check
  treats a one-page PDF as a figure and lists how many it accepted in the report notes; a
  longer PDF still needs a `type: paper` node. New module `tools/opsci/pdf.py`.
- Feed titles are headlines. Notion's notification preview shows only the first ~10 words
  of a message, so the notion skill asks for a short title with the main point first (the
  finding, or what the user must do) and no task id, kind or context, with examples; the
  Feed table's templates no longer start the title with `<id>:`. The Notion section of
  `AGENTS.md` (template and `opsci notion enable`) and `docs/notion.md` say the same. No
  layout change: a project mirrored before this can add the sentence to its `AGENTS.md`
  "Notion" section by hand.
- Anything that waits on the user goes to the Feed without being asked: a new plan to
  approve, a hold point, a decision, a question. The agent syncs, then posts a `question`
  with `--task` and `--mention`. New bullet in the Notion section of `AGENTS.md` (template
  and `opsci notion enable`); the notion skill's Feed table and the new-task skill's step 7
  say the same. No layout change: a project mirrored to Notion before this adds the bullet
  to its `AGENTS.md` "Notion" section by hand.
- Agents switch the session to every task they create (planned, no plan, brainstorm,
  verification) unless the user says to stay on the current one: they register the pane for
  the new task, so jumps and `/clear` resume it and the session name shows its short name.
  The new-task skill makes this its own step 6 and the brainstorm and verification sections
  point to it; `opsci task new` prints a reminder when run inside tmux.
- Verification tasks: audits, checks, reproductions and adverse reviews of work that is
  already done. A verification task is a task whose header names what it checks in the new
  field `verifies:`. It lives in `tasks/<id>/verifications/<vid>/` when it checks one task's
  work, and in the project's new `verifications/<vid>/` directory when it checks the work of
  several tasks (`brainstorm/verifications/` for brainstorm work). `opsci task new
  --verifies ID...` chooses the place and gives it, by default, the strictest privacy of the
  nodes it verifies. `map/graph.md`, `map/claims.md`, the node tables and the Notion Tasks
  database label it `verification`; the graphs draw it as a hexagon with a dotted `verifies`
  arrow to each node it checks. `opsci map build` reports a task in a `verifications/`
  directory without `verifies` and a `verifies` outside one, and warns when a verification
  task is in the wrong place or less private than what it verifies. The publish filter
  exports a verification task inside a task only when both are public. Agents make a
  verification task without asking when the user asks for a check of finished work, and
  propose one when unsure (template `AGENTS.md` §2, the new-task skill). Template:
  `verifications/README.md`, `tasks/README.md` "Verification tasks", `AGENTS.md` §5, the
  manifest includes `verifications`, `.gitattributes` merges verification task logs.
- No project layout change. After updating, add `- path: verifications` under `include` in
  `publish/manifest.yaml`, and the four `verifications` lines to `.gitattributes`.

- When something is a result: something later work will rely on, or that answers part of
  the task's goal. Debugging findings go only in the task's `map.md`, unless they matter
  conceptually. Agents set `milestone: true` when a result obviously answers part of the
  project's question and ask the user when unsure. A result that relies on outside work
  names it in `uses:` (added to `citations/used.bib` in the same step); an assumption taken
  from outside is a result of its own, `kind: assumption`. In template `tasks/README.md`,
  `AGENTS.md` rule 7, `contracts/main.md` §3 (questions 1 and 3), the context-files skill
  and the task-context hook.
- No project layout change.

- Results. A scientific result (a figure, table, value, statement, concept, or an
  assumption taken as given; not a debugging plot) is a `type: result` node with its own
  file in `tasks/<id>/results/`, or in the project's `results/` when it combines several
  tasks. New header fields: `kind`, `milestone`, `artifacts` (where the result is stored),
  `code`, `uses` (keys in `citations/used.bib`). `opsci map build` checks the paths and keys
  and generates `tasks/<id>/results/README.md` (the task's results with their figures) and
  `results/README.md` (the milestone results). `opsci task new` creates the task's page.
- The claims graph, `map/claims.md`, generated by `opsci map build`: every result in a box
  per task, with arrows from the results, tasks, datasets and external works it rests on
  to what uses it, and a table with where each is stored, its code and its verification
  chain. A live result that rests on failed or superseded work, directly or through other
  results, is marked and listed, and `opsci map build` warns about it; it also warns when a
  committed artifact changed after its result file. Results in `results/` directories are
  drawn there instead of in `map/graph.md`.
- The first of the four questions after a finished subtask now asks whether a result
  landed, changed or failed. Template `AGENTS.md` §1, §3 (rule 7) and §5, `tasks/README.md`,
  `map/README.md` and `README.md` describe results; the publish manifest includes `results`.
- No project layout change. After updating, add `- path: results` under `include` in
  `publish/manifest.yaml` and run `opsci map build`: it adds `results/README.md`,
  `map/claims.md` and a results page to every task.

- "Owner" is now "user" everywhere in the framework: skills, hooks, the template, the docs
  and messages (for example the project `context.md` section "Waiting on the user"). The
  meaning is unchanged: the person the project belongs to.
- No project layout change. `open-science-project:update-from-template` takes the new
  wording like any other template change.

- Notion: a project can be mirrored to the user's Notion workspace, and agents' messages go
  to the project's **Feed** there (`opsci notion`, `docs/notion.md`, skill
  `open-science-project:notion`). The mirror has Project, Context, Map (the graph as a
  Mermaid diagram), Log, Rules, Brainstorm and Private docs pages, and a Tasks database with
  one page per task holding its context, plan, map, log, subcontext files and plots with
  their captions. Mathematics in `$...$` becomes Notion equations. Pages are written by the
  user's own Notion integration through the REST API; its @mentions notify the user.
  A remade plot (a new dated name) replaces the old one in place. Feed messages are removed
  after three days and kept in `messages/notion-feed.jsonl`.
- The Notion mirror includes the claims graph (on the Map page), the milestone results
  (`results/README.md`), each task's results page, and a Results database with one page per
  result node. Figures in these files (`![alt](path)`) are uploaded and shown as images, and
  result ids link to their pages like task ids.
- In the Notion mirror, every mention of a task (full id, unique short id such as `t02`, or a
  path under `tasks/<id>/`) links to the task's page, in pages, tables and Feed messages. A
  sync creates new tasks' rows first, so that every page can link to them.
- `opsci notify` has a `notion` back end, and onboarding offers Notion first (recommended),
  then Slack, then files. Choosing Notion leads straight into a step-by-step setup (the
  integration, `secret_file.sh notion` for the token, a shared parent page, a test
  notification), which can be deferred; until it is done, messages go to files.
- Template: `opsci template instantiate --notion` keeps a new `<!-- opsci:notion -->` block,
  `AGENTS.md` section 10 (rules for agents in a mirrored project) and a line in `CLAUDE.md`'s
  skill list that main agents load `open-science-project:notion` at session start, adds a Claude Code Stop
  hook that syncs the mirror when a turn ends (`opsci notion sync --hook || true`, never
  blocking), and records `notion: true|false` in `config/framework.yaml`. Only projects of a
  user who chose Notion get the section and the hook. `.gitignore` ignores
  `config/notion.local.yaml` (the page ids of the checkout).
- Template `AGENTS.md` section 2, for every project: mathematics is written in LaTeX in every
  file and message, and every plot has a self-contained caption file beside it
  (`<stem>.caption.md`).
- No project layout change. To mirror an existing project: `opsci notion enable`, commit,
  then `opsci notion init`. `open-science-project:update-from-template` brings in the two new
  rules of section 2.

- Every task has a graph, `tasks/<id>/map.md`: a Mermaid graph of its subtasks, their
  status and the arrows between them. `opsci task new` writes it with a single node;
  `opsci map build` writes that starting map for any task that has none and never rewrites
  an existing one. The project graph's node table links each task to its map. The first of
  the four questions after a finished subtask now includes the task map. Task maps need no
  `status` header in the publish check.
- The publish check's private-content comparison ignores the node-table marker text, which
  every task context shares.
- No project layout change. After updating, run `opsci map build`: it adds `map.md` to every
  existing task.

- A task's `context.md` and `plan.md` show the node header as a two-column table under the
  title, between `opsci:node-table` comment markers, so that it reads well in a Markdown
  viewer that hides front matter (VS Code, the project site). The YAML front matter stays the
  source: `opsci task new` writes the table and `opsci map build` rewrites it; `opsci map build
  --check` and the publish check report a table that is out of date. The table adds about 12
  lines to each task context.
- No project layout change. After updating, run `opsci map build`: it adds the table to every
  existing task.

- New skill `open-science-project:private-investigation`: a side question that needs
  recorded work but does not drive the project gets its own context file in
  `private-docs/investigations/<YYYY-MM-DD>-<slug>/`, soft-private by default. A public task
  context names the file in backticks without stating the question; a hard-private
  investigation is listed under `hard_private:` and named nowhere in the export. Template
  `AGENTS.md` §2, §4 and §5 and `private-docs/README.md` mention it.
- No project layout change: the directory is created when the first investigation is made.

- The default autonomy level `maximal` is renamed `autonomous` (`opsci task new --autonomy
  autonomous|checkpoints|collaborative`). Plans written before this release that say
  `autonomy: maximal` mean `autonomous`; edit the header to the new name.

- Brainstorm nodes are part of the project graph. `opsci map build` scans `brainstorm/`,
  draws its nodes in a box of their own in `map/graph.md`, and also writes `brainstorm/map/`
  with the brainstorm nodes alone (`opsci map build brainstorm` does the same). Edges may
  join brainstorm and project nodes in either direction, and a task id must be unique across
  both.
- `opsci task new --root brainstorm` gives `privacy: soft-private` by default. With
  `brainstorm` in the manifest, only brainstorm tasks marked `privacy: public` are exported.
- The published map names every node that is not hard-private. A node whose files are not
  exported is named without a link; before, soft-private nodes were left out. The new file
  `publish/map_overrides.yaml` (never exported) groups several unpublished nodes into one
  or gives one a more general title and summary in the published map; the new check
  `map-overrides` validates it. The publish report lists how each unpublished node appears,
  and the `open-science-publish:publish` skill judges which ones are too specific and asks
  the user.
- No project layout change. After updating, run `opsci map build`: the committed map now
  includes the brainstorm nodes, and the publish check reports it out of date until it is
  rebuilt. Brainstorm tasks made before this release say `privacy: public`; if `brainstorm`
  is in the manifest, check them.

- The framework is described as three components, in this order: 1. project management
  (the project template and `open-science-project`; formerly "project structure"),
  2. context management (`open-science-context`), 3. publishing (`open-science-publish`).
  Plugin and skill names are unchanged.
- The projects page and SLURM resurrection are optional extras and moved to `extras/`:
  `projects-page/` is now `extras/projects-page/`, and the `slurm-resurrect` plugin moved
  from `plugins/slurm-resurrect/` to `extras/slurm-resurrect/` (the marketplace entry
  follows it; installed copies need `claude plugin marketplace update open-science`).
  Onboarding asks about the extras in a separate question.
- README, documentation and user guide lead with doing research in the open; agents are
  optional. README and Get started describe how to use `opsci` without Claude Code.
- Template `AGENTS.md` §2: "Record the work": a request for project work gets a task
  without asking (a brainstorm task when the user says "brainstorm"); for a request that
  may not be work, the agent answers first and asks at the end whether to record it.
  "Literature": sources read in full go to `lit_cache/` and the citation files, and when
  more than one paper is consulted, subagents read the full texts. The `new-task` skill
  makes brainstorm tasks without a plan or an approval stop; the `literature` agent saves
  what it fetches in `lit_cache/`.
- No project layout change.

## 0.3.0 - 2026-09-24

- Three new project directories. `brainstorm/`: ideas before they become project work, with
  its own `context.md`, `tasks/`, `map/` and `log/` (`opsci task new <id> --root
  brainstorm`, `opsci map build brainstorm`); not published unless the user adds it to the
  manifest. `docs/`: documentation, published. `private-docs/`: private notes, committed but
  never exported. Both are soft-private: nothing outside them may link to them.
- `opsci map build` and `opsci task new` ignore `brainstorm/` and `private-docs/` in the
  project graph; `opsci context check` also caps the brainstorm context files.
- `layout_version` in `config/framework.yaml`. `opsci task new`, `opsci map build`,
  `opsci context check` and `opsci publish check` warn when a project's layout is older than the framework's.
- Publishing: the exported map is built from the published nodes only, and two new checks,
  `references` and `private-content`, refuse an export that links to material that is not
  published or names or copies hard-private material (see the privacy tiers below). The
  review also compares the export with the excluded files. `docs/` and the brainstorm skeleton files need no `status:` header. When
  the user publishes `brainstorm/`, its task headers decide what is exported, as in `tasks/`.
- **Three privacy tiers.** The node header field `privacy: public | soft-private |
  hard-private` replaces `publish: yes | no | embargo`; `embargo` is gone, and a header that
  still has `publish:` fails `opsci map build` with a message naming `privacy`. `public`: may
  be released. `soft-private`: not released and not in the public map, but may be mentioned
  by name. `hard-private`: must not appear anywhere in the release, not even by name.
  `opsci task new --privacy <tier>` (default `public`, also with `--root brainstorm`). In
  `publish/manifest.yaml`, `policy.default_privacy` replaces `policy.embargo_default`, and
  the optional list `hard_private:` names hard-private paths outside a hard-private task.
  `references` refuses a header edge to a hard-private node and a link to any file that is
  not exported; `private-content` refuses ids, titles, paths and 12-word runs of
  hard-private material only, and lists soft-private mentions in the report's notes. A
  redaction marker in a private file, `<!-- redact: <reason> -->text<!-- /redact -->`, is
  replaced by `[redacted (<reason>)]` in the export; the new check `redaction` refuses an
  unclosed marker or an empty reason. `open-science-publish:publish` lists every
  hard-private mention to the user, who decides for each whether to change, remove or
  redact it. `open-science-project:new-task` asks for the tier, with both private tiers
  defined in the question, when a task obviously looks private.
- `open-science-project:update-from-template` works from an installed plugin (it clones the
  framework when the plugin has no `template/`), and runs the migrations below.
- This changelog.

### Project migration (layout 1 -> 2)

Run from the project root, on a branch, with `FW` a framework checkout at this release.

1. If the project already has a `brainstorm/` or `private-docs/` directory, stop and ask the
   user how to proceed: their files would drop out of the project graph and, for
   `private-docs/`, out of every publish.
2. Make the three directories from the template, without overwriting anything:
   ```bash
   opsci template instantiate <scratch>/fresh --name <slug> --title "<title>" --author "<user>" \
       --template "$FW/template"
   for d in brainstorm docs private-docs; do mkdir -p "$d" && cp -rn "<scratch>/fresh/$d/." "$d/"; done
   ```
   Add `--no-context-management` if `config/framework.yaml` has `context_management: false`.
3. Append to `.gitattributes`:
   ```
   brainstorm/log/*.md merge=union
   brainstorm/tasks/*/log.md merge=union
   ```
4. In `publish/manifest.yaml`: add `  - path: docs` under `include`, `  - private-docs` under
   `never`, and after the `include` entries the two comment lines
   `# - path: brainstorm   # private by default; add this line to publish the brainstorm` and
   `#                      # directory. It is soft-private: it may be named, not linked.`
   If `docs/` existed before step 2, ask the user whether everything in it may be public
   before you add the `include` entry.
5. Ask the user which existing notes are private (meeting notes, correspondence, drafts,
   remarks about people). Move each one with `git mv <file> private-docs/`. Then search the
   rest of the project for the old paths (`git grep -n '<old path>'`) and remove each
   reference or restate its content in public form.
6. Privacy tiers. In every node header (every `context.md`, `plan.md` and other file with a
   node header, and every `node.yaml`, under `tasks/`, `brainstorm/` and elsewhere; find
   them with `git grep -n '^publish:'`), replace `publish: yes` with `privacy: public`. For
   each node with `publish: no` or `publish: embargo`, ask the user whether it is
   `soft-private` (not released, not in the public map, may be mentioned by name) or
   `hard-private` (must not appear anywhere in the release, not even by name), and write
   `privacy: <tier>`; with no answer, write `hard-private`. In `publish/manifest.yaml`,
   replace `embargo_default: "<v>"` with `default_privacy: <tier>`: `yes` becomes `public`;
   for `no` or `embargo` ask the user as above (`hard-private` with no answer).
7. In `config/framework.yaml`, after `copied_on`, add:
   ```yaml
   # The project layout this project follows. CHANGELOG.md in the framework repo says how to
   # migrate from one layout to the next (open-science-project:update-from-template does it).
   layout_version: 2
   ```
8. Run `opsci map build`, `opsci map build brainstorm` and `opsci context check`; all must
   pass with no layout warning.
9. Commit: `git add -A && git commit -m "Migrate to project layout 2: brainstorm/, docs/, private-docs/, privacy tiers"`.

## 0.2.0 - 2026-09-24

- The framework is split into five components, each usable alone: `open-science-publish`,
  `open-science-project`, `open-science-context` (needs `open-science-project`), the projects
  page (files, no plugin) and `slurm-resurrect`. The `open-science` plugin now holds only
  onboarding: `/open-science:onboard`.
- **Plugin and skill names changed.** Skills that were `open-science:<skill>` are now
  `open-science-project:<skill>` (`new-project`, `new-task`, `migrate-project`,
  `update-from-template`, and the new `context-files`), `open-science-context:<skill>`
  (`context-management`, `continue-context`, `advise-with-context`) or
  `open-science-publish:<skill>` (`publish`, `zenodo-release`). Install the component
  plugins you use (`claude plugin install <plugin>@open-science`).
- `context_management:` in `config/framework.yaml` records whether the project uses session
  jumps; `opsci template instantiate --no-context-management` leaves them out.
- No layout migration. `open-science-project:update-from-template` brings the new skill
  names into `AGENTS.md`, `CLAUDE.md`, `context.md`, `contracts/` and the agent definitions
  (`.claude/agents/`), and adds the `context_management:` key (`true` if the project uses
  session jumps).
