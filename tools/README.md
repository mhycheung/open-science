# opsci

Command-line tools for the `open-science` framework. Install from the framework repo:

```bash
pip install "git+<framework repo URL>@<tag>#subdirectory=tools"
```

Commands:

- `opsci map build [ROOT]` — read every node header in a project, write `map/graph.md` and
  `map/dead_ends.md`, and report bad headers. `--check` reports without writing and fails
  if the generated files are out of date. The project graph includes the `brainstorm/`
  nodes, in a box of their own, and leaves out `private-docs/` (and `data/`, `lit_cache/`);
  it also writes `brainstorm/map/` with the brainstorm nodes alone. `opsci map build
  brainstorm` does the same.
- `opsci template instantiate DEST --name ... --title ... --author ...` — copy the project
  template into `DEST` and fill its placeholders.
- `opsci task new ID --title ... [--plan] [--privacy TIER] [--depends-on ID...]` — create
  `tasks/ID/` with `context.md` (node header), `log.md`, `subcontext/` and, with `--plan`,
  `plan.md` from the plan template (`--autonomy`, `--hold-at`). `--privacy` is `public`
  (default), `soft-private` or `hard-private`. Refuses a bad or existing id and edges to
  unknown nodes. `--root brainstorm` makes a brainstorm task; its edges can name only
  brainstorm nodes.
- `opsci context check [ROOT]` — fail if `context.md`, a `tasks/*/context.md` (200 lines) or
  `map/README.md` (150), or the same file under `brainstorm/`, is over its line cap.

`opsci task new`, `opsci map build`, `opsci context check` and `opsci publish check` print a
warning (exit status unchanged; the publish report also notes it) when the project's `layout_version` in `config/framework.yaml` (no key: 1) is
below the framework's (`LAYOUT_VERSION` in `opsci/layout.py`). The fix is the
`open-science-project:update-from-template` skill, which runs the migrations in
`CHANGELOG.md`.

- `opsci publish check [ROOT]` — export the committed public part (the files the manifest
  and node headers allow; the map rebuilt from the published nodes), run every check and
  write the review report. Besides the leak, secret, citation, status, copyright,
  verification, map and policy checks, `references` refuses header edges to hard-private
  nodes and links to files that are not exported, `private-content` refuses ids, titles,
  paths and 12-word runs of hard-private material (soft-private mentions are listed as
  notes), and `redaction` refuses an unclosed redaction marker or one with an empty
  reason. The export replaces each `<!-- redact: <reason> -->...<!-- /redact -->` span
  with `[redacted (<reason>)]`. See the
  `open-science-publish:publish` skill for export, push, status and pull-public.
- `opsci migrate inventory [ROOT] -o FILE` / `opsci migrate compare FILE [ROOT]` — record
  every file with its hash before a migration; afterwards fail if any file's content is
  found nowhere (moves are allowed).
- `opsci guide check [FILE]` — fail if the user guide is over 600 words or names a path,
  `opsci` command or skill that does not exist.
- `opsci zenodo release [ROOT] --version V` — pack `data/` into reproducible tar groups and
  publish a new Zenodo version (sandbox by default; `--production` for zenodo.org).
  `--dry-run` prints the plan without network access. `opsci zenodo checksum PATH` prints the
  checksum of a path's reproducible tar. See `docs/zenodo.md`.
