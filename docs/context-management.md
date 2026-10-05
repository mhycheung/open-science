# Context management and session jumps (`open-science-context`)

Codex shares the same context files and handoff skills as Claude Code; its session
controls differ and are described in [Codex session controls](#codex-session-controls).

An agent can hold only a limited amount of conversation. On long work it would otherwise
slow down or lose track. With the `open-science-context` plugin, the agent saves where the
work stands in the project's context files, clears its own conversation, and carries on from
those files. This is called a **jump**.

There are two kinds. In an **active jump**, the context is over 250k tokens (or a piece of
work is finished), so the agent saves its state to the task's context file and the plugin
clears the session and resumes it from that file. In a **wait jump**, the agent submits a
SLURM job, saves its state and clears; the idle session is woken when the job leaves the
queue and resumes from the context file. Clearing before a long wait matters because the
prompt cache expires while the session sits idle: waking a session that still holds a long
conversation would resend all of it uncached, which costs far more than a fresh start from
the context file. The cache-expiry explanation describes Claude Code; Codex does not use the
Claude cache-cold timer or its fixed context thresholds.

**On Claude Code, the jumps are done by a Claude Code mod.** A mod is code that Claude Code
runs inside itself (see Claude Code's [mods documentation](https://code.claude.com/docs/en/plugins/mods/overview)).
The plugin's mod clears the session with Claude Code's own `/clear`, runs the resume
command, wakes a waiting session when its SLURM jobs end, sends the cache-cold notice, names
the session after its task, and reads the context size of the session and of each subagent.
It also shows the context size and how long the prompt cache stays warm, and asks you before
a prompt you send to a cold cache ([The context bar](#the-context-bar-and-the-cold-cache-question)).
Nothing is typed into the terminal, and tmux is not needed. The mod needs Claude Code 2.1.287
or later, and mods must be switched on for your account (Anthropic is switching them on step
by step; `/open-science:onboard` checks). Where the mod is not loaded, the plugin falls back
to [typing into the agent's tmux pane](#without-the-mod-the-tmux-fallback), and you will see
the agent type into its own pane. `jump.sh status` says which one a session has.

**Jumps are optional.** Without them, the plugin still registers each session to its context
file, the agents still keep the context files current, and any session can take over a task
with `open-science-context:continue-context`. See [Choosing which jumps](#choosing-which-jumps).

```bash
claude plugin install open-science-context@open-science
```

Installing it also installs `open-science-project`: a jump is only as good as the context
file it resumes from. It needs `jq` and `opsci`. With the mod, tmux is optional; it is still
useful for keeping sessions alive after you disconnect, and [SLURM resurrection](slurm-resurrect.md)
needs it. [Working in tmux](tmux.md) shows how to arrange your work (one pane per task), how
to set tmux up for the mouse, and how to run it on a compute node of a cluster. Without the
mod, and for Codex's jumps ([Codex session controls](#codex-session-controls)), tmux is
required.

| skill | use it to |
|---|---|
| `open-science-context:context-management` | the rules for jumps, registration and subagent checkpoints; main agents load it at session start |
| `open-science-context:continue-context` | take over the work a context file describes; the first step after every jump |
| `open-science-context:advise-with-context` | ask questions about a context file or plan without acting on it |

## Registration

Each session records which context file it drives, so that
`open-science-context:continue-context` finds the right file after a jump with no file named.
When the main agent starts driving a task it runs:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/pane_context.sh" set tasks/<id>/context.md
```

Codex does not set `${CLAUDE_PLUGIN_ROOT}`; it runs the same script from the installed
plugin's `scripts/` directory. Outside tmux, Codex registers the file for its session
(`CODEX_THREAD_ID`) instead of the pane.

`pane_context.sh get` prints the registered path, and `pane_context.sh clear` drops it. The
registration follows the session through a jump, a plain `/clear` and a SLURM resurrection.
With the mod it is kept by session id: the mod hands it to the new session at every clear,
and a session resumed with `claude --resume` keeps its id. Without the mod it is kept by tmux
pane, and a new session in the same pane keeps it. Subagents never register; they use the
main agent's registration.

**Session names.** If you said yes in onboarding (`OPSCI_SESSION_NAMES=1`), the session is
named after the project and, once registered, the task's short name, for example
`quad-ratio-2 · pp-real`. This is also its name in the Remote Control list. With the mod the
name changes as soon as the turn that registered the task ends; without it, at your next
prompt.

To take over work in a pane yourself, type `/open-science-context:continue-context`, with or
without a file. With no file it uses the pane's registered file and prints which one; with
nothing registered it lists candidates, newest first, and asks you. **Typing it in the wrong
pane resumes the wrong work**, so check the line that names the file. In Codex, ask it to use
`open-science-context:continue-context`, with or without a file.

## Jumps

| jump | when | command |
|---|---|---|
| active | the context is above about 250k tokens, a subtask finished, or before a fan-out of subagents | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" active <context file> --report "<report>"` |
| wait | only background work is left (subagents, a background shell, a SLURM job) and it will take longer than about 45 minutes | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" wait <context file> --report "<report>"` |
| cache-cold | the plugin sends `[open-science] cache-cold: ...` to the session after 58 minutes idle with work still running | a wait jump, now |

Before either command, in the same turn, the agent writes the jump's record: the task
`context.md` (state, what is in flight and how to check it, the exact next step), the project
`context.md` if it changed, one line in the task log, and `opsci notify` for anything you
would otherwise miss. The `jump.sh` call is the last action of the turn.

Every jump also reports to you. A jump clears the conversation, so in the session you see
only `/clear`; `jump.sh` therefore requires `--report` and posts it with `opsci notify
--kind status --no-mention` (to the project's Feed in Notion, or to your configured back
end) before it requests the jump. The report gives a headline, what the session did, the
state, what is running and what comes next, followed by the kind of jump and the context
file. The agent does not wait for your approval. If the send fails, the jump still happens,
and the message is kept in `messages/`.

With the mod, the report is also shown at the top of the cleared session, right after
`/clear`, as the output of the mod's `/opsci-note` command, so the session shows what the
previous session said instead of only `/clear`, in the terminal and in Remote Control. After a
wait jump it begins by saying that the session was cleared and is waiting, and for how many
running tasks. Showing it starts no turn, so a session left waiting stays asleep. The agent of
the new session reads it with its next prompt, as it reads any command's output; the context
file stays the record it resumes from. Typing `/opsci-note` shows the last note again.

When the turn ends, the mod runs Claude Code's `/clear`, checks that a new session started,
hands the registration to it, and, for an active jump, runs
`/open-science-context:continue-context <context file>`. In our tests the resumed turn
started about half a second after the old turn ended.

A cleared session is woken by a background subagent's report, a background Bash task
exiting, a Monitor event, or a queued SLURM waker. For SLURM jobs the agent queues a waker
before a wait jump: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait_slurm.sh" --notify <jobid> [<jobid>...]`.
The mod checks the queue every 60 seconds (`OPSCI_WAIT_POLL`); when the jobs have left it,
it sends their final states to the session, which starts a turn. The waker follows the
session through jumps, and because it is kept by session id it also survives a SLURM
resurrection. A session that was waiting on background tasks when Claude Code restarted (a
resurrection, or a resume by hand) has lost them; the mod then runs
`/open-science-context:continue-context` for it at once.

### When the agent does not jump

- **While it waits for you.** If the turn ends with a question, a decision or a hold point,
  the agent saves the state, asks, and ends the turn, whatever the context size. It jumps
  after you answer, if work is left.
- **As a subagent.** Subagents never jump.
- **While another component owns the pane**, for example SLURM resurrection during its
  wind-down.

### What `jump.sh` refuses

| refusal | why |
|---|---|
| no `--report` text | a jump the user is not told about leaves them only `/clear` |
| `OPSCI_JUMPS` is `off`, or `wait` for an active jump | you switched these jumps off |
| not in tmux, without the mod | the fallback types into the pane |
| the context file is missing, or was not saved in the last 15 minutes | the state to resume from was not written |
| the session's state file cannot be found, without the mod | the clear could not be confirmed |
| an active jump below 100k tokens without `--force` | a jump costs a full reload; small contexts do not need one |
| an inhibit file exists | another component owns the pane |
| at the stop: a wait jump with nothing running that could wake the session | the session would never wake; start the waker or do an active jump |

`jump.sh cancel` drops a pending request; `jump.sh status` shows it.

### At every stop

At every stop of a main session that has registered a task, the plugin:

1. starts a pending jump, or refuses a wait jump that nothing would wake;
2. arms the cache-cold timer if something will wake the session (a running background task,
   a scheduled prompt or a queued waker), and stops it otherwise;
3. if the context is above the threshold (250k tokens) and no jump is pending, blocks the
   stop once with an instruction to do an active jump. It blocks the same session again only
   after its context has grown by another 50k tokens, so a session waiting for you is not
   stopped at every reply.

The mod reads the context size from Claude Code and runs these steps through the plugin's
Stop policy (`cm_stop.sh --mod`); without the mod, the Stop hook runs them itself and does
nothing outside tmux. With `OPSCI_JUMPS=wait` step 3 is skipped; with `OPSCI_JUMPS=off`
only a leftover timer is stopped.

### Choosing which jumps

Onboarding asks which jumps you want and records the answer as `OPSCI_JUMPS` in the `env`
block of the Claude `settings.json` (`$CLAUDE_CONFIG_DIR/settings.json` or
`~/.claude/settings.json`). Edit it there to change it; new sessions pick it up. Codex
reads `OPSCI_JUMPS` from the environment it is started in, for example
`OPSCI_JUMPS=wait codex --add-dir ~/.local/state/open-science`.

| `OPSCI_JUMPS` | what happens |
|---|---|
| `all` (the default, recommended) | active, wait and cache-cold jumps |
| `wait` | no active jumps and no size notice from the Stop hook: the conversation grows as long as it needs to. Wait jumps and the cache-cold notice still clear the session before a long wait, when resending the conversation uncached would cost the most |
| `off` | no jumps: `jump.sh` refuses every jump, and nothing is done at a stop but stopping a leftover cache-cold timer |

Jumps are recommended: they reduce usage and keep the agent working from the current state
of the work. Newer models cost less and work well with a long conversation, so keeping the
conversation (`wait` or `off`) is a reasonable choice. With every setting, registration,
the context files, `continue-context` and subagent checkpoints work as described on this page.

## The context bar and the cold-cache question

With the mod, every session shows a line with its context size and the state of the prompt
cache, above the prompt on the terminal and in the Code tab of the Claude Desktop app. The context size is the one the main agent's last model response left (what
it read plus what it wrote), updated at the end of every request, in thousands with one decimal
(`123.4k`) and as a plain count below 1000. The cache lives one hour from the start of the last
request that read it; the line's clock starts at the end of the main agent's last request (a
subagent's requests do not count), so it calls the cache cold at 59 minutes:

| idle since the last request | the line says |
|---|---|
| no request yet in this session (a new or cleared session) | `context 12.3k tokens · no cache yet` |
| under 59 minutes | `context 180.2k tokens · cache warm, 32 min left`, in green |
| 59 minutes or more | `⚠ context 180.2k tokens · cache cold (75 min idle)`, in yellow |

Remote Control (from claude.ai, the desktop app or the mobile app) does not show the line:
Claude Code draws a mod's interface only in the terminal and the Desktop app's Code tab. When
the cache of an idle session goes cold, the mod therefore also adds one row to the
conversation, which Remote Control shows too: the output of `/opsci-note`, `Cache cold: 59 min
since the last request. The next prompt reads the whole context (180.2k tokens) again at the
full price; /clear starts a fresh session.` It starts no turn; the model reads the line with
your next prompt.

The 59 minutes come from Claude Code's own transcripts: in 958 transcript files, every request sent
less than 60 minutes after the previous response still read the cache, and none sent later did.

When the cache is cold, a prompt you send to the idle session is held back, and the agent is
not woken until you answer:

- **Typed in the terminal:** the prompt is not sent, and a dialog opens with the question (`⚠
  The cache is cold (75 min since the last request). The whole context (180.2k tokens) will be
  read again at the full price. Are you sure you want to submit this prompt?`) and two buttons.
  **Submit** (or `1`) sends it as you typed it, shown in the conversation as a message from the
  plugin (Claude Code labels every prompt a plugin sends that way, and no plugin can change
  it); **Do not submit** (or `2`, or Esc) puts it back in the prompt box.
- **From Remote Control:** the app shows Claude Code's question with the same text and the
  answers **Submit** and **Do not submit**. The app adds its own free-text answers; anything but
  **Submit** leaves the prompt unsent. A typed prompt with an image attached gets the same
  question in the terminal.

Slash commands (`/clear` is the usual answer to a cold cache), task notifications and the
plugin's own prompts are not asked about.

This is separate from the cache-cold notice above. That notice is for a session that ended its
turn with work still running that will wake it; after 58 minutes, a minute before the line
turns cold, it tells the agent to do a wait jump while the cache is still warm. A session that
ended its turn waiting only for you gets no notice and is never woken by the plugin: whether to
clear or to continue is up to you, and the question above is asked when you do.

## Settings

Environment variables read by the scripts:

| variable | default | what |
|---|---|---|
| `OPSCI_JUMPS` | `all` | which jumps are allowed: `all`, `wait` or `off` ([Choosing which jumps](#choosing-which-jumps)) |
| `OPSCI_JUMP_THRESHOLD` | 250000 | context size (tokens) at which the Stop hook asks for an active jump |
| `OPSCI_JUMP_REPEAT` | 50000 | growth (tokens) before the hook asks the same session again |
| `OPSCI_ACTIVE_JUMP_FLOOR` | 100000 | below this, an active jump needs `--force` |
| `OPSCI_CACHE_COLD_MIN` | 58 | minutes idle, with work running, before the cache-cold notice |
| `OPSCI_CACHE_TTL_MIN` | 59 | with the mod: minutes idle after which the context bar says the cache is cold and prompts are asked about |
| `OPSCI_JUMP_FRESH_MIN` | 15 | the context file must have been saved within this many minutes |
| `OPSCI_SUBAGENT_LIMIT` | 200000 | with the mod: a subagent's context size (tokens) at which it is told to checkpoint |
| `OPSCI_WAIT_POLL` | 60 | seconds between two checks of a SLURM waker's jobs |
| `OPSCI_STATE_DIR` | `$XDG_STATE_HOME/open-science`, else `~/.local/state/open-science` | where registrations, requests, wakers, timers, locks and the log `cm.log` are kept |

## Subagents

The main agent dispatches subagents in the background. Every dispatch prompt states:

- the path of the subagent's working document, `tasks/<id>/subcontext/subagent_<slug>.md`,
  which it rewrites at the end of every round as if a different agent takes over next;
- above 200k tokens of its own context, it stops at the next clean boundary, updates the
  document, and ends its report `PAUSED - <doc path> - <exact next step>`; a finished task
  ends `DONE`;
- a job whose wait is over about 45 minutes is submitted, recorded, and reported as
  `SUBMITTED - <job id> - <doc path> - <check command> - <next step>`; the main agent owns
  the wait;
- every report ends with `If you have no context, use the open-science-context:continue-context skill.`

With the mod, the plugin also reads each subagent's context size from every request it sends
to the model. Above 200k tokens (`OPSCI_SUBAGENT_LIMIT`) it sends that subagent one message,
`open-science: your context is <n> tokens, above 200000. Stop at the next clean boundary,
...`, so the subagent does not have to estimate its own size. Without the mod, the subagent
checks its own size.

On `PAUSED` or `SUBMITTED` the main agent dispatches a fresh subagent against the same
document.

## Without the mod: the tmux fallback

If Claude Code does not load the mod (a version before 2.1.287, mods not yet switched on for
your account, or a session started with `--safe-mode`), the plugin's shell hooks do the same
work by typing into the agent's tmux pane:

- **Claude Code must run inside tmux.** Outside tmux, `jump.sh` refuses and the Stop hook does
  nothing.
- **Jumps:** when the turn ends, the Stop hook starts a worker that waits for the pane to be
  idle, types `/clear`, confirms that the session id changed, and, for an active jump, types
  `/open-science-context:continue-context <context file>`. You will see the agent type into
  its own pane. The agent never types into its own pane itself.
- **Cache-cold notice:** a timer types the notice into the pane.
- **SLURM wakers:** `wait_slurm.sh --notify` says the mod is not loaded; the agent runs
  `wait_slurm.sh <jobid>...` as a background Bash task instead, and its exit wakes the
  session.
- **Registration** is kept by pane. After a SLURM resurrection the session runs in a new
  pane; at its first stop the Stop hook copies the session's own record into the new pane's.
- **Session names** change at your next prompt, from the `UserPromptSubmit` hook.
- **Context size** is read from the session's transcript, one message behind.

The plugin decides per session: when the mod loads, it sets `OPSCI_MOD=1` for Claude Code
and everything it starts, and the shell hooks then leave the work to it. Nothing needs to be
configured to switch between the two.

## `open-science-context:advise-with-context`

For questions about work another session is driving. It reads the context file and what the
question needs, and answers with a `file:line` for every claim. It does not run the plan, fix
anything, or edit the context file or the plan unless you ask. Registering the file to the
pane is the one write it makes.

## Codex session controls

For Codex, install these plugins after adding the marketplace:

```bash
codex plugin add open-science-project@open-science
codex plugin add open-science-context@open-science
```

Restart Codex and review the hooks using `/hooks`. Start Codex from a shell in the tmux
pane, not with `exec codex`, since an automated jump needs that shell to launch the next
session. For ordinary handoff, a named context file works without tmux. Registration
without tmux is per Codex thread, so one session cannot inherit another's task by accident.

The state directory must be writable by both the hooks and sandboxed tools. The default
is `~/.local/state/open-science`; make it first and, when using Codex's workspace-write
sandbox, explicitly include only that directory with `--add-dir`. Keep your other model,
sandbox and approval settings:

```bash
mkdir -p ~/.local/state/open-science
codex --add-dir ~/.local/state/open-science
```

Do not broaden sandbox permissions merely to make a jump work. Without writable session
state, continue from an explicitly named context file and save changes in the project.
For SLURM recovery, the state directory must be on storage shared by the compute nodes.
The context plugin's `pane_context.sh check` tests write access and explains how to fix
it; registration or jump requests return exit 3 when the state directory is read-only.

An active Codex jump saves the research state, ends the old TUI after its turn, and starts
Codex again in the pane shell with the original launch options and a handoff prompt.
It never types Claude `/clear` into Codex. Unknown launch options and managed `--worktree`
sessions are refused. Model and permissions are preserved; a hook's `permission_mode`
field is not used to infer launch permissions.

A Codex wait jump needs the plugin's detached SLURM watcher, requested with
`wait_slurm.sh --notify <jobid>...` before `jump.sh wait <context file>`. When jobs leave
the queue, it uses `codex queue` to wake the new thread in that pane. Ordinary background
shells and subagents are not supported as wait-jump wakers. Recheck the scheduler's final
state and outputs; leaving the queue is not proof of success.

`OPSCI_JUMPS=all|wait|off` retains its meaning. Codex has no cache-cold timer, no context
bar, no cold-cache question or note, no jump report at the top of the new session, and no
Claude-style automatic session naming. Token usage is best effort from Codex's changing
rollout format; if unreadable, no size notice is issued. By default a notice occurs at
60% of the reported model window; `OPSCI_CODEX_JUMP_THRESHOLD` sets an explicit threshold.
The active-jump floor is 100000 tokens unless `OPSCI_CODEX_ACTIVE_JUMP_FLOOR` overrides it.
Manual requests can use `--force` after saving state; it bypasses only the size floor.
