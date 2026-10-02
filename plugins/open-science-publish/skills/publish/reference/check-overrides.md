# Findings the user may override

Some findings of `opsci publish check` are often legitimate: a SLURM job number in run
notes, or a long quotation that is marked as a quote and cited. They fail the check like
any other finding, but the user may accept them instead of fixing them. The report lists
them under `### Findings the user may override`, grouped by kind, each kind with the reason
it is flagged (the text comes from the tool, `OVERRIDABLE` in `tools/opsci/publish.py`).

## What to ask

For each kind in that section:

1. List every finding of the kind: file, line and the matched text. A leak in the built
   site (`[site] ... leak in built site`) is the same text as a finding in an exported
   file; say so instead of listing it twice.
2. Say in one or two plain sentences why the kind is flagged: the report's text.
3. For `copyright` kinds, say for each finding whether the text is marked as a quote
   (blockquote or quotation marks) and cited next to it (a `[@key]` beside the quote). The
   user's usual rule: a marked and cited quote is fine; an unmarked copy is not.
4. Ask whether to fix the findings (change the text in the private repo) or to accept them
   as they are. The user may accept a kind everywhere or only in some paths.

Ask about every kind before you change anything. Never write an override on your own
judgement, and never propose one for a kind the report does not list as overridable.

## The manifest entry

On the user's answer, add one entry per accepted kind to `publish/manifest.yaml`, commit,
and run `opsci publish check` again:

```yaml
overrides:
  - check: leak
    kind: slurm-job-id            # also slurm-array-id, slurm-out-file
    paths: [tasks/**, docs/runs.md]  # optional: only these paths or globs; omit for every file
    reason: Job numbers in run notes name no person or machine.   # the user's reason
    date: 2026-10-02              # the date of the user's answer
  - check: copyright
    kind: long-quote              # also lit-cache-text
    reason: Quotes are marked as quotes and cited next to them.
    date: 2026-10-02
```

The overridden findings move to the report section `## Overridden by the user`, each listed
under its override, so the approval still shows them. An override changes the export id: the
user approves the export with its overrides. The push writes the overridden leak patterns
into the site workflow (`opsci site build --allow-leak <pattern>`), so the public site build
accepts them too. A note in the report names an override that no longer covers any finding;
ask the user whether to remove it.

## What is never overridden

The manifest refuses an override of any other check or kind. These are fixed at their
source:

- `secret`: a token, key or password. Once public it must be revoked.
- `leak` of a user name, host name or domain, a `config/site.local.yaml` value, an email
  address, an IP address, or an absolute path: each names a person, a machine or an
  account. A private-policy pattern is the user's own list; to change it, the user edits
  `publish/PRIVATE_POLICY.md`.
- `private-content`, `references` to hard-private material, `redaction`, `omission`: these
  protect hard-private material and the markers that hide it.
- `human-verified` set in an agent commit: only the user sets it.
- `copyright` for a PDF, EPUB or DjVu outside a `type: paper` node: a whole copy of a work.
- `policy`, `citation`, `status`, `map`, `evidence`, `site-link`, `map-overrides` and
  `site` build failures: each has a direct fix in the private repo.
