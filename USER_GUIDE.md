# User guide

## The idea

Everything in a project is written down so that it can be made public, reproduced and
checked, including the routes that failed. Agents keep short "current state" files
(`context.md` for the project, `tasks/<id>/context.md` for each task), so any session, or any
person, can pick up the work from them.

## One-time setup

1. **Install and onboard.** Install the `open-science` plugin (see the README), then type
   `/open-science:onboard`. It explains the five components (publishing, project structure,
   context management, a projects list, SLURM resurrection), asks which you want, and sets
   them up, asking before any change to your settings.
2. **tmux.** Context management and SLURM resurrection need Claude Code running inside
   tmux. Put `set -g mouse on` in `~/.tmux.conf` so you can click, resize and scroll panes.
3. **Notifications.** With no setup, messages for you are written as files in the project's
   `messages/` directory. For Slack instead, create your own Slack app with only the
   `chat:write` and `files:write` scopes; onboarding stores its token in
   `~/.config/opsci/slack.env` with mode 600. Never commit or share that file, and never
   paste a token into the chat. Steps: `docs/notify.md`.

## Daily use

- **Start a project** with `open-science-project:new-project`, and each piece of work with
  `open-science-project:new-task`. You review a task's plan before work starts.
- **Each tmux pane is registered to one context file**, so it knows which work it drives.
  To take over work in a pane, type `/open-science-context:continue-context`. Doing this in the
  wrong pane resumes the wrong work; it prints which file it uses, so check that line.
- **Agents clear their own conversation and type a prompt into their own pane.** This is
  called a jump. It keeps the context small; they resume from the context files and the
  log. Anything meant for you is sent before the jump.
- **SLURM resurrection** (optional plugin) is something you turn on, from any Claude pane in
  the tmux session: `/slurm-resurrect:resurrect register`. Agents cannot do it. The first
  run only shows a warning about Remote Control and the permission mode the resumed
  sessions will use; run it again to register.

## What to read, and what you may edit

- `context.md`: where the project stands, what is running, and what waits on you.
- `map/README.md`: the logic of the project. `opsci map build` writes the full graph and
  the list of dead ends next to it.
- `rules/README.md`: the project's rules, one line each. Add or change rules here.
- `brainstorm/`: ideas not yet tasks. `private-docs/`: private notes. Both are soft-private
  (`AGENTS.md` §6).
- **Marking a result human-verified.** Every task and result has a header with a
  `verification:` line. Agents may set `verified`. After checking a result yourself, set
  `verification: human-verified` in its header and commit. Only you can: agents are blocked
  from setting it, and the publish check refuses one set in an agent's commit.

## Publishing

Nothing becomes public until you approve a publish. When you ask for one
(`open-science-publish:publish`), the agent exports what `publish/manifest.yaml` allows, runs every
check (leaks, secrets, citations, node status, verification level, your policy), and writes
a report under `publish/`. The report lists the files exported, the check results, the
diff since the last publish, and the agent's review of tone and unverified claims. You
approve that exact export by its id; an earlier "go ahead" does not count. Only then is it
pushed and the project site rebuilt. You decide on hard-private mentions.

List what must never become public (unpublished collaborations, data under agreements,
internal names) in `publish/PRIVATE_POLICY.md`. That file is never exported, and the publish
check refuses any file that matches its patterns.
