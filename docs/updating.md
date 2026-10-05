# Updating a project to a new framework version

A framework update has two parts: the code (the plugins and `opsci`), which every project
shares, and the scaffolding inside each project (the files copied from the template), which
each project updates on its own.

## 1. Update the code

```bash
claude plugin update <plugin>@open-science          # for each installed plugin
pip install -U "git+<URL of the framework repository>@<tag>#subdirectory=tools"
```

Plugin updates load in a new Claude Code session. The framework's `CHANGELOG.md` lists what
changed in each version.

For Codex, run `codex plugin marketplace upgrade open-science` if you added the
marketplace from GitHub, or `git pull` the checkout if you added a local checkout. Then
re-add each chosen plugin with `codex plugin add <plugin>@open-science` and restart. Review
changed hooks with `/hooks`. Keep the same `opsci` release for both agents. Adding Codex support to an
existing project keeps `CLAUDE.md` and `.claude/` and adds `config/codex.md` and the
Codex role files; it does not move research records or disable Claude session controls.

### The update notice

The `open-science` plugin (the one with the onboarding skill) checks for a new release. At
the start of a session it looks up, in the background and at most once a day, the version
of the newest release in the framework repository (the remote of the marketplace the
plugins came from). The agent is not told. The newest release known is the higher of that
lookup's and the version in the local marketplace checkout, which `claude plugin update`
brings up to date. If it is newer than the plugin the session loaded, the user sees one
short notice when the agent finishes its first turn, with the update commands; if that
release is already installed and only the running session is older, the notice says so and
that a new session (in Codex, a restart) loads it. The same session does not show the
notice again, and nothing asks a question. A failed lookup (no network) shows nothing. Set `OPSCI_UPDATE_CHECK=off`
in the environment to turn the check off; in Claude Code, put it in the `env` block of
`settings.json`. The check works the same in Codex once its hooks are trusted (`/hooks`).

## 2. Update each project

In the project, ask for `open-science-project:update-from-template`. The skill:

1. reads `config/framework.yaml`: `copied_at_commit`, `local_divergence` (changes the project
   made on purpose, which an update must keep) and earlier `updates`;
2. gets a checkout of the framework at the installed version: the framework checkout the
   plugin ships in, if it has `template/`, otherwise a clone of `framework_repo` into a
   scratch directory;
3. reads `CHANGELOG.md` between the project's version and the installed one, and runs every
   "Project migration" section in order before anything else;
4. diffs the framework's `template/` since `copied_at_commit` and classifies every hunk,
   showing you the table before editing:

   | the hunk is | the agent |
   |---|---|
   | a general improvement to a scaffolding file | takes it, filling the placeholders for this project |
   | in a file or section listed in `local_divergence` | reconciles it by hand: takes the idea, keeps the project's content, says what it kept |
   | in a slot the project already filled (`AGENTS.md` §1, `context.md`, `map/README.md`, rules) | skips it, almost always |
   | a new file | takes it and fills its project-specific slots |
   | a layout change | does not move existing files as part of the update; proposes a migration task |

5. applies the changes by editing the project's files. It never copies template files over
   them, and never deletes a project file because the template dropped it;
6. records the update in `config/framework.yaml` (an `updates` entry with the date, the new
   commit, what it took and what it skipped, the new `copied_at_commit`, and anything it
   reconciled added to `local_divergence`), sets `layout_version`, and commits the update on
   its own;
7. reports what changed, what it skipped and why.

Template text between `<!-- opsci:context -->` markers applies only when `context_management`
is `true` in `config/framework.yaml`, and text between `<!-- opsci:no-context -->` markers
only when it is `false`.

If `copied_at_commit` cannot be diffed (it ends in `-dirty` or is `unknown`), the skill
compares the current template with the project file by file instead, applies what you
approve, and records the framework's current commit.

## Layout versions

`config/framework.yaml` records `layout_version`. A project with no such key is layout 1.
Layout 2 adds `brainstorm/`, `docs/` and `private-docs/` (see
[Project template and layout](project-template.md#brainstorm-docs-and-private-docs)).

When a project's layout is older than the framework's, `opsci task new`, `opsci map build`
and `opsci context check` print a warning on standard error that names
`open-science-project:update-from-template`. The warning does not change their exit status.

The migration from layout 1 to 2 is the "Project migration" section of `CHANGELOG.md`. In
outline: create the three directories from the template, add `docs` to `include` and
`private-docs` to `never` in `publish/manifest.yaml`, ask the user which existing notes are
private and move them to `private-docs/` with `git mv`, replace `publish:` with `privacy:` in
every node header and `embargo_default` with `default_privacy` in the manifest (the user
chooses soft- or hard-private for each node that was not `publish: yes`), set
`layout_version: 2`, run `opsci map build`, and commit.

The migration from layout 2 to 3 narrows the git permissions in `.claude/settings.json`
(no more `Bash(git:*)`), removes the shell from the `literature` tier, adds rule 6 of
`AGENTS.md` (text from outside is data, not instructions) and its lines in `contracts/`, and
sets `layout_version: 3`.

## A project that does not use the layout yet

Use `open-science-project:migrate-project` instead (see [Project skills](project-skills.md)).
