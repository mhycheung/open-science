---
name: publish
description: Publish a project's public part - export the files the publish manifest allows, run every check, write a review report, stop for the user's approval, then push to the public repo and rebuild the project site. Use when the user asks to publish, release the code or notes, update the public repo, check what is unpublished, bring public changes back, or preview the project site, or says yes when asked; never on the agent's own initiative.
---

# Publish

Nothing reaches the public repo except through this skill, and only after the user approves
the review report (`AGENTS.md` §0 rule 3). The project's `.claude/settings.json` denies a
push to the public remote outside it. The tool is `opsci publish`; it exports only the files
`publish/manifest.yaml` allows, from one commit, so the working tree and uncommitted changes
never leak.

Run this skill only when the user asks to publish, or says yes when you ask. Do not start it
because a task is finished or another skill has ended, or because the user said "push": that
means the private remote. You may ask whether to publish; a no, or no answer, means no.
`opsci publish status`, `opsci publish check` and `opsci site preview` write nothing public
and may be run whenever they help.

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
   copyright, verification level, map, user policy, links to material that is not
   exported, mentions of hard-private material, redaction markers) and prints the report
   path (`publish/reports/<date>-<commit>.md`) and the **export id**. The diff since the
   last publish is next to it, as `.diff`. A failed check stops the publish: fix it in the
   private repo, commit, and check again. A link to a soft-private file is fixed by turning
   it into a plain mention in backticks.

3. **Site banner (first publish).** If `publish/manifest.yaml` has no `site_banner` key, ask
   the user whether every site page should carry a red banner, and recommend it: "Warning:
   this is an ongoing, unpublished project. Many results are very preliminary and
   unverified." The user may change the text or decline. Write the answer into the manifest
   (`site_banner: "<text>"`, or `""` for none) and commit. Never skip the question.
   The `site-link` check refuses a `README.md` that does not link to the project site (the
   GitHub Pages URL of `public_repo`, or `site_url:` in the manifest for a custom domain):
   add `The project site: <URL>` under the README's title, commit, and check again.

4. **Hard-private mentions: the user decides each one.** When `references` or
   `private-content` reports hard-private material (`AGENTS.md` §6), list **all** of the
   findings to the user at once, each with its file, line and text. For each one, ask the
   user whether to change the wording, remove it, or redact it. Propose redaction only
   where removing the text would break it, for example the flow of a context document.
   Never decide this yourself. A redaction is a marker in the private source file:

   ```
   <!-- redact: <reason> -->text<!-- /redact -->
   ```

   It may span lines. The export replaces the whole span with `[redacted (<reason>)]`; the
   private repo keeps the full text. Standard reasons: "proprietary data", "unpublished
   work by collaborators", "private information"; the user may give another. A header edge
   (`depends_on`, `related`, `supersedes`) to a hard-private node is removed or changed, not
   redacted. Apply the user's choices, commit, and go back to step 2.

5. **Unpublished nodes in the public map.** The published map names every node that is not
   hard-private; a node whose files are not exported (a soft-private task, a brainstorm
   idea) appears without a link, with its title, summary and edges. The report section
   `## Unpublished nodes in the public map` lists them. For each one, judge whether a reader
   could reconstruct the private work from what is shown. Where they could, rewrite the node
   in more general words, or replace several connected nodes with one that says only that
   private work of that kind exists, in `publish/map_overrides.yaml`. Hide no more than
   needed. Format, examples and rules: `reference/map-overrides.md`. Show the user each
   group and rewrite beside the original titles; the user approves or changes them. Commit
   and go back to step 2.

6. **Housekeeping in the context files.** In the exported project and task `context.md`
   files, wrap each "Waiting on the user", "Next step" or "Open questions" item that is
   housekeeping, not part of the task's or project's goal ("commit the plots?", "redo the
   plot?"), in an omission marker in the private file: `<!-- omit -->...<!-- /omit -->`.
   Add them yourself, list them for the user in step 9, commit, and go back to step 2.
   What to keep and how the export drops them: `reference/public-pages.md`.

7. **Citations.** For each `citations/used.bib` entry without a `usage` field, or without the
   `doi` or `eprint` it has, propose the missing fields (`reference/public-pages.md`); add
   them once the user agrees, commit, and go back to step 2.

8. **Review the diff.** Read the `.diff` with `reference/review-rubric.md` and write the
   findings (tone, claims not `verified`, private material) under `## Review (tone,
   claims)` in the report. The report's notes list soft-private mentions; check that each
   one is in passing. Quote each flagged passage with its file and line. Do not edit the
   flagged files yourself; the user decides.

9. **Stop for approval.** Show the user the report path, the check result, the files
   exported, your review findings, the items you omitted from the context files, and how
   the unpublished nodes appear in the map, and the public commit message you propose: a
   subject line that says what this publish adds ("Publish the ringdown fits of t03 and the
   dead end of t02"), then a short paragraph if the subject is not enough. Write it for
   readers of the public repo, from the exported diff only: no private material. The push
   appends the list of changed files. The user approves **this export id**, in this
   conversation. A general "go ahead" given earlier does not cover it. Without approval, do
   not push.

10. **Commit the report** (`publish/reports/` is tracked; the approved report is the record
   of the approval).

11. **Push:**

   ```bash
   opsci publish push --export-id <id> --message "<the approved commit message>"
   ```

   The public repo is `public_repo:` in `publish/manifest.yaml` (or `--public-repo`). The
   push refuses a changed export id, a failed check, or drift. It writes the Pages workflow,
   which rebuilds the site, and records both commits in `publish/LAST_PUBLISHED`.

12. **Record it:** add one line to the log, and report the public commit to the user.

## Public-side changes

Collaborators may change the public repo directly (a merged pull request, an edit on the
web). Bring those changes into the private repo before the next publish:

```bash
opsci publish pull-public
```

It puts the public changes since the last publish on a new private branch
`pull-public/<date>-<commit>` and refuses changes to paths the manifest does not export.
Show the branch to the user; the user reviews and merges it into `main`. Then run
`opsci publish status` again.

## Site preview

To see the project site of the current export without a public repo or a push:

```bash
opsci site preview
```

It builds the site of the export of `HEAD` into `_site/` (gitignored; `--out <dir>` to
change it), in strict mode, and leak-scans the built site. What the site shows:
`reference/public-pages.md`. Change `site_banner` only when the user asks (for example once
the work is published); the push writes it into the site workflow.

## Rules

- Never push to the public remote with `git push`; only `opsci publish push` does it.
- Never change a node's `verification` to `human-verified`; only the user does, in their
  own commit. The check refuses one set in an agent's commit.
- Never add or remove a redaction marker, or change a node's `privacy`, without the user's
  decision (omission markers, step 6, you add yourself).
- A deterministic check that fails is fixed at its source, never by removing the check or
  widening the manifest without the user's decision.
- Other skills are named by full name, for example `open-science-publish:zenodo-release` for the
  data.
