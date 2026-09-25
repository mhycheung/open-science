---
name: update-from-template
description: Bring an open-science project's scaffolding up to date with the framework template - diff the framework's history since the commit the project was copied from, take the general improvements, keep what the project changed on purpose, and run the layout migrations in the framework's CHANGELOG.md. Use when the user says the template or framework changed, when a warning says the project's layout is older than the framework's, or when the user asks what a project is missing relative to the template.
---

# Update from template

A project records the framework commit it was copied from, and the changes it made on
purpose, in `config/framework.yaml`. **Updating is a diff against that commit, applied by
hand**, never a re-copy, which would discard the project's own changes. The main agent does
this itself; the judgement is per hunk.

## Procedure

1. **Read `config/framework.yaml`**: `copied_at_commit`, `layout_version` (no key means
   layout 1), `local_divergence` (changes an update must keep) and earlier `updates`.

2. **Get a framework checkout `FW` with its history.** An installed plugin holds only its
   own directory, so `${CLAUDE_PLUGIN_ROOT}/../..` is a framework checkout only if it has a
   `template/` directory. Otherwise clone `framework_repo` into a scratch directory, at the
   release matching the installed plugin (the `version` in
   `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`):

   ```bash
   V=$(jq -r .version "${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json")
   git clone -q <framework_repo> <scratch>/fw && FW=<scratch>/fw
   git -C "$FW" rev-parse -q --verify "refs/tags/v$V" && git -C "$FW" checkout -q "v$V"
   ```

   The framework maintainer creates the tag `v<version>` when a release is made. Without
   a tag `v$V`, stay on the default branch and say so in the report.

3. **Read `$FW/CHANGELOG.md`** from the release after the project's up to the target, and
   **run every "Project migration" section** whose layout is above the project's
   `layout_version`, in order, exactly as written. Each ends by setting `layout_version`
   and committing. Files a migration created are done; skip their hunks in step 5.

4. **See what changed** in the framework's `template/` since the project's commit:

   ```bash
   FROM=<copied_at_commit>
   git -C "$FW" log --oneline "$FROM"..HEAD -- template/
   git -C "$FW" diff "$FROM"..HEAD -- template/
   ```

   Empty: the project is current. Say so and stop. A commit ending `-dirty` or `unknown`
   cannot be diffed: see "No usable commit".

5. **Classify every hunk before editing**, and show the user the table:

   | the hunk is | do |
   |---|---|
   | a general improvement to a scaffolding file | take it, filling placeholders (`{{PROJECT_NAME}}` …) for this project |
   | in a file or section listed in `local_divergence` | reconcile by hand: take the idea, keep the project's content, say what you kept |
   | in a slot the project already filled (`AGENTS.md` §1, `context.md`, `map/README.md`, rules) | skip, almost always |
   | a new file | take it and fill its project-specific slots |
   | a layout change | done in step 3 if a migration covers it; otherwise do not move existing files, propose a migration task |

   Template text between `<!-- opsci:context -->` and `<!-- /opsci:context -->` applies only
   if `config/framework.yaml` has `context_management: true`; text between
   `<!-- opsci:no-context -->` markers only if it is false. In the same way,
   `<!-- opsci:notion -->` text applies only with `notion: true` and `<!-- opsci:no-notion -->`
   text only without it (a project with no `notion:` key is not mirrored). Never copy the
   marker lines. To start mirroring a project to Notion, use `open-science-project:notion`
   (`opsci notion enable`), not this skill.

6. **Apply by editing the project's files.** Never copy template files over them, and never
   delete a project file because the template dropped it.

7. **Record it** in `config/framework.yaml`: an `updates` entry (date, `to_commit`, what you
   took, what you skipped and why), `copied_at_commit` set to the new commit, anything you
   reconciled added to `local_divergence`, and `layout_version` equal to the one in
   `$FW/template/config/framework.yaml`. Commit the update on its own.

8. **Report** what changed, what you skipped and why, and any proposed migration.

## No usable commit

Run the migrations (step 3) first. Then compare the current template with the project file
by file, classify the differences as in step 5, apply what the user approves, and record
the framework's current commit as `copied_at_commit`, with `local_divergence` filled in from
what you found.

A lesson that would help every project belongs in the framework repo's `template/`,
committed there, not only in this project.

## Updating the code

This skill updates the project's files. The plugins and `opsci` are updated separately, by
the user or with their approval: `claude plugin marketplace update open-science`, then
`claude plugin update <plugin>@open-science` for each installed plugin (restart Claude
Code to load it), and
`pip install -U "git+<framework_repo>@v<version>#subdirectory=tools"` (or `pip install -e
tools` from a clone) for `opsci`. Update the code first, so that the plugin version names the
release this skill updates the project to.
