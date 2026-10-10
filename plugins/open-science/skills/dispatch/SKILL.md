---
name: dispatch
description: Start a new agent session in a new window of the current tmux session, with Remote Control on, in a directory the user names, and give it a prompt only if the user says what it should do. Use ONLY when the user explicitly asks to dispatch, launch or start an agent (or a Claude, or a session) somewhere; never on your own initiative, and never as a way to hand off or parallelize your own work. Works in any directory, not only open-science projects.
---

# Dispatch an agent

Codex follows this skill with the substitutions in `codex.md` (next to this file).

The dispatched agent is an ordinary session: it needs no open-science project and no other
open-science component. The directory can be anything the user names (a project, `~`, a
scratch folder).

## When

Only on an explicit request from the user: "dispatch an agent to ~/data", "start a Claude in
the qnmdet project and have it run the tests". Never dispatch because it would help your own
task, and never dispatch more than the user asked for. When unsure whether the user asked,
ask.

## Procedure

1. **Directory.** Resolve the directory the user named to an absolute path (`~` expanded; a
   project name to the project's directory). If none was named, or the name matches more than
   one directory or none, ask. Do not create a directory unless the user asked for it.
2. **Prompt.** Send a prompt only when the user said what the agent should do ("have it
   ...", "tell it to ...", "ask it to ..."). Then write the user's instruction as the
   agent's first prompt: the user's words, plus what the agent cannot know without this
   conversation (paths, names, constraints the user stated). Add no tasks of your own. If the
   user only asked to start an agent, send nothing.
3. **Launch.** With a prompt, write it to a temporary file first (`mktemp`), never on the
   command line. Then run:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/dispatch.sh" launch --agent claude --dir <dir> \
       [--name <name>] [--prompt-file <file>]
   ```

   `--name` only when the user named the agent. Otherwise the script names the window and
   the session after the project: `<project>` (the git top level's directory name, else the
   directory's), or `<project>-2`, `-3`, ..., the lowest number that no live session of
   this account holds. It opens the window in the background (the user's window stays
   current), types `${OPSCI_DISPATCH_CMD:-claude} --name <name> --remote-control <name>`
   into its shell, and with a prompt waits until the prompt is delivered (up to 3
   minutes) or the session asks whether to trust the folder. Remove the temporary file
   after the script returns; the prompt is kept in its own `queued` file.
4. **Folder trust.** If the output has `trust=asked`, the new session waits at Claude Code's
   question whether to trust the folder. Ask the user, with your question tool where you
   have one:

   > The new session in `<dir>` asks whether to trust this folder. Trusting it lets Claude
   > Code read, edit and run files there and lets the folder's own settings (hooks, MCP
   > servers) run. Answer yes for you?

   Options: **Yes, trust the folder** and **No, I'll answer it in the window**. On yes run

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/dispatch.sh" trust --pane <pane> --agent claude \
       [--queued <queued>]
   ```

   with `--queued` when the output had a `queued` line. It selects "Yes, I trust this
   folder" and presses Enter, or, if it cannot select that line, presses nothing and
   fails; then it delivers the prompt and prints `prompt=` as in step 3. On no, or if
   `trust` fails, leave the question to the user in that window: the mod sends a queued
   prompt when the session starts. Never answer the question without the user's yes.
5. **Report** in two or three lines, from the script's `key=value` output: the session's
   name (`name`), the window (`window`), the directory, whether the folder was trusted
   (when it was asked), and the prompt:
   - `none`: started without a prompt.
   - `mod`: the open-science mod took it and sent it as the user's prompt, or, for a slash
     command (`/quota-cleanup`), ran that command.
   - `typed`: no mod claimed it, so it was pasted into the prompt box and sent.
   - `pending`: not delivered yet; the window shows a question first (the folder-trust
     question, or another). Ask the user to answer it in that window; the mod sends the
     prompt when the session starts. The prompt waits in the `queued` file.

   Say that the session can be followed in the Claude app through Remote Control.

## When it fails

- `not inside tmux`: say that dispatch opens a tmux window, so this session must run inside
  tmux; do nothing else.
- The window shows an error instead of Claude Code (look with
  `tmux capture-pane -p -t <pane>`), for example `command not found` or a wrapper that
  refuses bare `claude`: the user starts Claude Code with another command. Ask which, and
  offer to set `OPSCI_DISPATCH_CMD` (for example `export OPSCI_DISPATCH_CMD=claude-personal`)
  in their shell profile; change the profile only with their yes. Close the failed window
  (`tmux kill-window -t <pane>`) only with their yes.

## Rules

- One agent per request unless the user asked for more.
- Never send the dispatched agent anything after its first prompt unless the user asks.
- Do not close, rename or type into other windows.
