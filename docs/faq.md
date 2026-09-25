# Troubleshooting

Problems that come up in practice, and what to do. Each answer links to the page with the
details.

## Skills and plugins

**A skill I just installed does not appear.** New plugins load only in a new Claude Code
session. Start a new session after `claude plugin install` or after
`/open-science:onboard` installed plugins.

**The agent used a different skill from the one the plugin provides.** If your own skills
directory (`~/.claude/skills/`) has a skill with the same name as a plugin skill (for example
`context-management` or `new-project`), the model tends to pick the unprefixed personal
skill. Move that skill out of the directory or rename it, and call plugin skills with their
prefix, for example `/open-science-project:new-task`.

## Session jumps

**`jump.sh` refused the jump.** The refusals and their reasons are listed in
[Context management and session jumps](context-management.md#what-jumpsh-refuses). The most
common: the context file was not saved in the last 15 minutes (write it, then call `jump.sh`
again), an active jump below 100k tokens without `--force`, and a wait jump with nothing
running that could wake the session (start the waker, for example `wait_slurm.sh`, or do an
active jump).

**`continue-context` resumed the wrong work.** With no file named, it uses the file
registered for the tmux pane it runs in. Typed in another pane, it resumes that pane's work.
Check the line that names the file, and give the file explicitly if in doubt:
`/open-science-context:continue-context tasks/<id>/context.md`.

## Publishing

**`opsci publish check` fails.** Fix each problem at its source in the private repository,
commit, and check again. The leak scan has no override flag: if a legitimate string matches,
change the string. Do not remove a check, and do not widen `publish/manifest.yaml` without
the user's decision. The checks are listed in
[Publishing and the filter](publishing.md#the-checks).

**`opsci publish push` says the export id is not the reviewed one.** The export of the commit
changed after the report was written (a new commit, or a changed manifest). Run
`opsci publish check` again, review the new report, and approve the new export id.

**`opsci publish push` or `opsci publish status` reports drift.** The public repository has
changes the private one lacks, for example a merged pull request. Run
`opsci publish pull-public`: it puts the public changes on a new private branch
`pull-public/<date>-<commit>`, which you review and merge. Then check and publish again.

**The project website workflow stops with "opsci cannot be installed".** The workflow installs
`opsci` from `framework_repo` at `copied_at_commit` in `config/framework.yaml`. It needs a
public URL (`https://`, `git@` or `ssh://`) and a commit hash. Correct the file, then publish
again: the workflow is rewritten at each publish. See
[The project website](publishing.md#the-project-website).

**The site is not on GitHub Pages after the first publish.** Pages must be switched on once,
by hand: in the public repository on github.com, **Settings → Pages → Source: GitHub
Actions**.

## Zenodo

**`opsci zenodo release` says nothing changed.** Since the previous release no tar group
was added, removed or changed in checksum, so no new version is made. This is a refusal, not
an error in the data. `--dry-run` shows the decision for each group.

**A release stopped before it was published.** The draft id is kept in `data/MANIFEST.yaml`,
and the next `opsci zenodo release` continues with that draft (Zenodo allows only one
unpublished draft per record). See [Zenodo releases](zenodo.md).

**Is the token accepted?** `opsci zenodo check-token` (add `--production` for zenodo.org)
checks the token file's mode and makes one read-only request. It creates nothing.

## Notifications

**`opsci notify` fails.** The error messages and their causes are in the table in
[Notifications](notify.md#errors).
