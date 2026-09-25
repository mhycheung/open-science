---
name: publish
description: Publish a project's public part - export the files the publish manifest allows, run every check, write a review report, stop for the owner's approval, then push to the public repo and rebuild the project site. Use when the user asks to publish, release the code or notes, update the public repo, check what is unpublished, bring public changes back, or preview the project site.
---

# Publish

Nothing reaches the public repo except through this skill, and only after the owner approves
the review report (`AGENTS.md` §0 rule 3). The project's `.claude/settings.json` denies a
push to the public remote outside it. The tool is `opsci publish`; it exports only the files
`publish/manifest.yaml` allows, from one commit, so the working tree and uncommitted changes
never leak.

## Procedure

1. **Consistency check first.** The public repo must hold nothing the private repo lacks:

   ```bash
   opsci publish status
   ```

   It lists `drift` (public changes the private repo lacks) and `pending` (unpublished
   private changes). If there is drift, stop and bring it in first (see "Public-side
   changes" below). Before the first publish there is no public repo yet; skip this step.

2. **Check** the commit to publish (default `HEAD`; commit first, since uncommitted changes
   are not exported):

   ```bash
   opsci publish check
   ```

   It exports, runs every check (leak scan, secrets scan, citation keys, node status,
   copyright, verification level, map, owner policy, links to material that is not
   exported, mentions of hard-private material, redaction markers) and prints the report
   path (`publish/reports/<date>-<commit>.md`) and the **export id**. The diff since the
   last publish is next to it, as `.diff`. A failed check stops the publish: fix it in the
   private repo, commit, and check again. A link to a soft-private file is fixed by turning
   it into a plain mention in backticks.

3. **Hard-private mentions: the owner decides each one.** When `references` or
   `private-content` reports hard-private material (`AGENTS.md` §6), list **all** of the
   findings to the owner at once, each with its file, line and text. For each one, ask the
   owner whether to change the wording, remove it, or redact it. Propose redaction only
   where removing the text would break it, for example the flow of a context document.
   Never decide this yourself. A redaction is a marker in the private source file:

   ```
   <!-- redact: <reason> -->text<!-- /redact -->
   ```

   It may span lines. The export replaces the whole span with `[redacted (<reason>)]`; the
   private repo keeps the full text. Standard reasons: "proprietary data", "unpublished
   work by collaborators", "private information"; the owner may give another. A header edge
   (`depends_on`, `related`, `supersedes`) to a hard-private node is removed or changed, not
   redacted. Apply the owner's choices, commit, and go back to step 2.

4. **Unpublished nodes in the public map.** The published map names every node that is not
   hard-private; a node whose files are not exported (a soft-private task, a brainstorm
   idea) appears without a link, with its title, summary and edges. The report section
   `## Unpublished nodes in the public map` lists them. For each one, judge whether a reader
   could reconstruct the private work from what is shown. Where they could, rewrite the node
   in more general words, or replace several connected nodes with one that says only that
   private work of that kind exists, in `publish/map_overrides.yaml`. Hide no more than
   needed. Format, examples and rules: `reference/map-overrides.md`. Show the owner each
   group and rewrite beside the original titles; the owner approves or changes them. Commit
   and go back to step 2.

5. **Review the diff.** Read the `.diff` with `reference/review-rubric.md` and write the
   findings (tone, claims not `verified`, private material) under `## Review (tone,
   claims)` in the report. The report's notes list soft-private mentions; check that each
   one is in passing. Quote each flagged passage with its file and line. Do not edit the
   flagged files yourself; the owner decides.

6. **Stop for approval.** Show the owner the report path, the check result, the files
   exported, your review findings, and how the unpublished nodes appear in the map. The owner approves **this export id**, in this
   conversation. A general "go ahead" given earlier does not cover it. Without approval, do
   not push.

7. **Commit the report** (`publish/reports/` is tracked; the approved report is the record
   of the approval).

8. **Push:**

   ```bash
   opsci publish push --export-id <id>
   ```

   The public repo comes from `public_repo:` in `publish/manifest.yaml`; the first time,
   the owner sets it there or you pass `--public-repo <url or path>`. The push refuses if
   the export changed since the report (different id), if a check fails, or if the public
   repo has drift. It writes the Pages workflow, which rebuilds the project site, and it
   records the private and public commits in `publish/LAST_PUBLISHED` with a commit of its
   own.

9. **Record it:** add one line to the log, and report the public commit to the owner.

## Public-side changes

Collaborators may change the public repo directly (a merged pull request, an edit on the
web). Bring those changes into the private repo before the next publish:

```bash
opsci publish pull-public
```

It puts the public changes since the last publish on a new private branch
`pull-public/<date>-<commit>` and refuses changes to paths the manifest does not export.
Show the branch to the owner; the owner reviews and merges it into `main`. Then run
`opsci publish status` again.

## Site preview

To see the project site of the current export without a public repo or a push:

```bash
opsci site preview
```

It builds the site of the export of `HEAD` into `_site/` (gitignored; `--out <dir>` to
change it), in strict mode, and leak-scans the built site.

## Rules

- Never push to the public remote with `git push`; only `opsci publish push` does it.
- Never change a node's `verification` to `human-verified`; only the owner does, in their
  own commit. The check refuses one set in an agent's commit.
- Never add or remove a redaction marker, or change a node's `privacy`, without the owner's
  decision.
- A deterministic check that fails is fixed at its source, never by removing the check or
  widening the manifest without the owner's decision.
- Other skills are named by full name, for example `open-science-publish:zenodo-release` for the
  data.
