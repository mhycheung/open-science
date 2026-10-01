# Claude Code and Codex

open-science keeps a reproducible research record and lets you publish the part you
choose. You can use its files and `opsci` commands yourself. Claude Code and Codex are
optional ways to work with that same record; switching agents does not migrate the data.

## Install

Claude Code:

```bash
claude plugin marketplace add mhycheung/open-science
claude plugin install open-science@open-science
claude
```

Type `/open-science:onboard` and choose the components you want.

Codex:

```bash
codex plugin marketplace add mhycheung/open-science
codex plugin add open-science@open-science
codex
```

Ask Codex to use `open-science:onboard`. For a local checkout, pass its directory instead
of `mhycheung/open-science` to the marketplace command. Install individual components
with `codex plugin add open-science-project@open-science`, and similarly for
`open-science-context` and `open-science-publish`. Install project management before
context management. Restart Codex after installation.

Use a Codex CLI with plugin and hook support (the integration is tested with 0.159.3).
If `codex plugin` is unavailable, update Codex. No model name or subscription is fixed
by the framework; use a model available to your account.

Both agents need `opsci`, git, and the dependencies of the components they use. From a
framework checkout, `pip install -e tools` installs the CLI; `pixi run --frozen` uses
the pinned development environment. A cached plugin may not include the template, so
keep a checkout or let the new-project skill fetch the matching framework release.

## Hooks and configuration

Claude Code keeps its `.claude/settings.json`, plugin hooks, and `.claude/agents/`.
Codex uses separate plugin hook definitions and `.codex/agents/`. The project entry
point is `AGENTS.md`; it directs Codex to `config/codex.md` and Claude Code to `CLAUDE.md`.

In Codex, use `/hooks` to review and trust the installed definitions. Hook files existing
on disk does not mean they run: new or changed definitions need review. Project-local
hooks also require a trusted project. Do not bypass trust during normal installation.
See the [Codex hook documentation](https://learn.chatgpt.com/docs/hooks).

The project hooks check file patches for `human-verified` assignments and context line
caps, and guard accidental direct pushes to the public remote. Keep running
`opsci context check` and `opsci publish check`: hooks do not cover every possible shell
or custom-tool edit. Every agent commit uses an `Agent: claude` or `Agent: codex` trailer;
the publication check uses attribution to reject agent-set human verification.

`opsci notion enable` adds auto-sync hooks for Claude Code and Codex while keeping
unrelated settings. With Codex, trust that project's Stop hook before relying on it.
Manual `opsci notion sync` remains available. Credential files stay outside the project,
and only the user enters credentials in their own terminal.

## Shared research workflows

The same prompts work with either agent:

```text
Start a new open-science project in <directory>.
Brainstorm: <question>.
Start a task to <goal>.
Continue tasks/<id>/context.md.
Sync Notion.
Preview the project site.
Publish this project.
Release the data to Zenodo as version <label>.
```

Name a framework skill in full, for example `open-science-project:new-task`, to avoid
confusing it with a personal skill of the same short name. Claude Code slash commands
are shown throughout the guides; in Codex select the installed skill or ask for it by
its full name. The approval and privacy rules are the same for both.

Codex's sandbox may require approval for git metadata writes, including initialization
and commits. Approve the specific command when appropriate; do not disable the sandbox
to complete setup. If approval is unavailable, the agent should report the files as
uncommitted.

The five dispatch roles carry the same research contracts. Their runtime configurations
are separate, and inherit a model from the agent being used. Do not copy Claude model
aliases into Codex settings.

## Sessions and concurrent work

Claude Code's tmux session jumps retain their existing behavior. Codex uses the Codex
instructions in the context skills; do not send Claude `/clear` sequences into Codex.
Saving a task's context and starting another session from its explicit path works
independently of terminal automation. Session-specific limitations are described in
[Context management](context-management.md) and [SLURM resurrection](slurm-resurrect.md).

If Claude Code and Codex work concurrently on the same project, use separate worktrees
and branches, just as for two human collaborators. Merge research records deliberately
and rebuild generated maps. Do not turn off the project's shared `context_management`
setting merely because one runtime lacks a particular session control.

## Existing projects and updates

Use `open-science-project:update-from-template` to add the Codex instructions and role
files while keeping local customizations and existing Claude files. No task directory,
result, citation, or public release needs to move. To add Notion hooks to an existing
mirrored project, run `opsci notion enable` again; it is idempotent.

For Claude Code, use its existing marketplace/plugin update commands. For Codex, refresh
the marketplace with `codex plugin marketplace upgrade open-science`, re-add the chosen
plugins, then restart. Update `opsci` from the same release. Review changed hook
definitions again. Never replace the whole settings file during an update.
