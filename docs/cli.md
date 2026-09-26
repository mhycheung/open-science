# The `opsci` command

`opsci` is the command-line tool the skills call. It lives in `tools/` of the framework
repository and needs Python 3.11 or later.

```bash
pip install "git+<URL of the framework repository>@<tag>#subdirectory=tools"
pip install -e tools        # or, from a clone of the framework repository
opsci --help
```

In the framework repository itself, `pixi run opsci ...` runs it in the pinned environment.

Every command prints `error: ...` on standard error and exits with status 1 when it fails or
refuses (exceptions are noted). A `ROOT` argument is the project root and defaults to the
current directory.

| command | what |
|---|---|
| [`opsci template`](#opsci-template) | copy the project template; check a project made from it |
| [`opsci task`](#opsci-task) | create a task directory |
| [`opsci map`](#opsci-map) | build the project graph and the list of dead ends |
| [`opsci context`](#opsci-context) | check the context files against their line caps |
| [`opsci publish`](#opsci-publish) | export, check and push the public part of a project |
| [`opsci site`](#opsci-site) | build the project website |
| [`opsci zenodo`](#opsci-zenodo) | release data to Zenodo |
| [`opsci notify`](#opsci-notify) | send a message, and optionally a file, to the user |
| [`opsci projects-page`](#opsci-projects-page) | check the personal projects page |
| [`opsci migrate`](#opsci-migrate) | check that a migration lost no file |
| [`opsci guide`](#opsci-guide) | check the framework's user guide |

## `opsci template`

```
opsci template instantiate DEST --name NAME --title TITLE --author AUTHOR
                           [--template TEMPLATE] [--date DATE]
                           [--framework-repo FRAMEWORK_REPO] [--no-context-management]
                           [--notion] [--framework-line]
opsci template check [ROOT]
```

- `instantiate` copies the template into `DEST` and fills its placeholders. `--name` is the
  project slug (lower case, digits, hyphens). `--template` is the framework's `template/`
  directory (default: the one next to the installed `opsci`, if there is one). `--date` is
  the creation date, `YYYY-MM-DD` (default today). `--framework-repo` is the repository URL
  to record (default: the template checkout's `origin`, or `local copy`).
  `--no-context-management` leaves out the context-management component.
  `--framework-line` adds the README line naming the framework, with a link to its web
  page; pass it only with the user's consent. It refuses a
  non-empty `DEST` and leaves nothing behind on failure.
- `check` reports missing required files, unfilled placeholders, leftover component markers,
  absolute paths, and `.gitignore` rules that do not ignore what they should.

See [Project template and layout](project-template.md).

## `opsci task`

```
opsci task new ID --title TITLE [--summary SUMMARY] [--goal GOAL]
               [--depends-on [ID ...]] [--related [ID ...]] [--supersedes [ID ...]]
               [--plan] [--autonomy {autonomous,checkpoints,collaborative}]
               [--hold-at [POINT ...]] [--verifies ID [ID ...]] [--root ROOT]
```

Creates `tasks/ID/` with `context.md` (node header), `map.md`, `results/README.md`, `log.md`, `subcontext/` and, with
`--plan`, `plan.md` from the plan template. `--summary` is the header's one-sentence summary
(default a `TODO`); `--goal` fills the Goal section of `context.md`. `--hold-at` needs
`--autonomy checkpoints`. `--root` is the project root (default `.`). It refuses a directory
that is not a project root, a bad or existing id, a title that is not one non-empty line,
and an edge to a node that does not exist.

`--verifies` makes a verification task (an audit, check or adverse review of finished work)
of the named nodes. It goes in `tasks/<id>/verifications/ID/` when every named node lies in
that one task, else in `verifications/ID/`, and its privacy defaults to the strictest privacy
of the named nodes. See [Verification tasks](project-template.md#verification-tasks).

## `opsci map`

```
opsci map build [--check] [ROOT]
```

Reads every node header and writes `map/graph.md`, `map/dead_ends.md` and `map/claims.md` (the
claims graph), and the same three files in `brainstorm/map/` for the brainstorm nodes alone.
It writes the results pages: `results/README.md` (the milestone results) and
`tasks/<id>/results/README.md` for every task. It warns about every live result that rests
on failed or superseded work, and about a committed artifact that changed after its result
file. Brainstorm nodes are part of the
project graph, drawn in a box of their own. ROOT may be the project or its `brainstorm/`
directory; both build the same files. It also writes a starting `map.md` (the task's graph,
one node) for any task that has none, and never rewrites an existing one. It reports header
errors and writes nothing if there are any. Verification tasks are drawn as cards with a
double border, with a dashed "verified by" arrow from each node they check; it warns when one
is not where `opsci task new` would put it, or is less private than a node it verifies.
`--check` writes nothing and fails if the generated files are out of date.

The project graph and the claims graph are images beside their pages: `map/graph.svg` and
`map/claims.svg` (for the project site and GitHub), and a PNG of each (for Notion). Graphviz
places the cards and arrows and pdflatex typesets them, so `$...$` in a title is set as
LaTeX (a title whose LaTeX does not compile is set as plain text). Every arrow points
forward: from a node to what depends on it ("used by"), from a node to the node that
superseded it ("superseded by"), and from a node to the verification task that checked it
("verified by"). Task boxes have a solid grey border, brainstorm boxes a dashed orange one.
The committed images, which the Notion mirror shows, label every node and task box by
privacy: "public" (its files are exported), "soft private" (the public map names it, but its
task or header is not public or the manifest does not include it) or "hard private" (the
public map leaves it out). The exported images leave out the hard-private nodes and label
the soft-private ones "not published". In the claims graph, assumptions and the nodes
the results start from are drawn quieter and milestones stronger than other results. An
image is redrawn only when its graph or the drawing code changed. Drawing needs Graphviz
(`dot`), `pdflatex` with the TikZ, standalone, lmodern and xcolor packages, and Poppler
(`pdftocairo`, `pdftoppm`); without them `map build` warns and leaves the images out of date,
and `opsci publish check` refuses the export. `opsci publish` redraws each exported image
from the published nodes only.

## `opsci context`

```
opsci context check [-v] [ROOT]
```

Fails if the project `context.md` or a task `context.md` (verification tasks included) is over 200 lines, or
`map/README.md` over 150. `-v` prints every file with its line count.

## `opsci publish`

```
opsci publish status [--public-repo PUBLIC_REPO] [ROOT]
opsci publish check [--commit COMMIT] [ROOT]
opsci publish push --export-id EXPORT_ID [--public-repo PUBLIC_REPO] [--commit COMMIT] [ROOT]
opsci publish export --out OUT [--commit COMMIT] [ROOT]
opsci publish pull-public [--public-repo PUBLIC_REPO] [ROOT]
```

- `status`: public changes the private repository lacks (drift; exit status 1), and
  unpublished changes.
- `check`: export, run every check, write the review report and diff under
  `publish/reports/`. Prints the report path and the export id.
- `push`: after the user approves the report, push the export with that id to the public
  repository. `--public-repo` defaults to `public_repo` in the manifest.
- `export`: write the files the manifest allows, from one commit, into the empty directory
  `OUT`.
- `pull-public`: bring public-side changes into a new private branch for review.

`--commit` defaults to `HEAD`. See [Publishing and the filter](publishing.md).

## `opsci site`

```
opsci site build [--out OUT] [SRC]
opsci site preview [--commit COMMIT] [--out OUT] [ROOT]
```

- `build`: build the website of a public repository checkout `SRC` (default `.`) with MkDocs
  into `OUT` (default `_site`), in strict mode, and leak-scan the result.
- `preview`: the same for the export of this project's `COMMIT` (default `HEAD`), before
  publishing.

## `opsci zenodo`

```
opsci zenodo release [--version VERSION] [--dry-run] [--production] [--api-url API_URL]
                     [--token-file TOKEN_FILE] [--build-dir BUILD_DIR] [--write-citation] [ROOT]
opsci zenodo check-token [--production] [--api-url API_URL] [--token-file TOKEN_FILE]
opsci zenodo checksum [--root ROOT] PATHS...
```

- `release`: pack `data/` into tar groups and publish a Zenodo version, on the sandbox unless
  `--production`. `--version` is required except with `--dry-run`, which prints the plan with
  no network access and writes nothing. `--token-file` defaults to
  `~/.config/opsci/zenodo-sandbox.token`, or `zenodo.token` with `--production`; it must be
  mode 600. `--build-dir` defaults to `data/.zenodo-build/<server>`. `--write-citation` also
  writes the DOIs to `CITATION.cff` and the datasets for a sandbox release.
- `check-token`: check that the token file is private and that Zenodo accepts the token.
  Creates nothing.
- `checksum`: the sha256 and size of the reproducible tar of each path under `data/`.

See [Zenodo releases](zenodo.md).

## `opsci notify`

```
opsci notify [--backend BACKEND] [--project-root PROJECT_ROOT] TEXT [FILE]
```

Sends `TEXT`, and optionally one `FILE`, to the user. `--backend` is `file` or `slack`
(default: `notify.backend` in the config, else `file`). `--project-root` defaults to the git
top level of the current directory. Exit status: 0 sent; 2 misconfiguration or bad input;
3 the Slack send failed. See [Notifications](notify.md).

## `opsci projects-page`

```
opsci projects-page check [DIR]
```

Validates `projects.yaml` and checks `index.html` for the banned styles, in `DIR` (default
`.`). See [Personal projects page](projects-page.md).

## `opsci migrate`

```
opsci migrate inventory -o OUTPUT [ROOT]
opsci migrate compare [-v] INVENTORY [ROOT]
```

- `inventory`: before a migration, record every file (tracked, plus untracked files git does
  not ignore) with its hash in `OUTPUT`. Keep the file outside the project.
- `compare`: after the migration, report kept, moved, modified and lost files; fail if any
  inventoried file's content is found nowhere. `-v` lists moved files.

See `open-science-project:migrate-project` in [Project skills](project-skills.md).

## `opsci guide`

```
opsci guide check [--repo REPO] [--max-words MAX_WORDS] [FILE]
```

Checks the framework's `USER_GUIDE.md` (or `FILE`): at most `MAX_WORDS` words (default 600),
and every path, `opsci` command and plugin skill it names exists. `--repo` is the framework
repository root (default: the checkout `opsci` runs from). This is a check for the framework
itself, not for projects.
