# Context management and session jumps (`open-science-context`)

An agent can hold only a limited amount of conversation. On long work it would otherwise
slow down or lose track. With the `open-science-context` plugin, the agent saves where the
work stands in the project's context files, clears its own conversation, and carries on from
those files. This is called a **jump**. You will see the agent type into its own tmux pane;
that is expected.

```bash
claude plugin install open-science-context@open-science
```

Installing it also installs `open-science-project`: a jump is only as good as the context
file it resumes from. It needs `jq`, `opsci`, and tmux.

**Claude Code must run inside tmux.** A jump types into the agent's own tmux pane, and each
pane records which context file it drives. Outside tmux, `jump.sh` refuses and the Stop hook
does nothing. [Working in tmux](tmux.md) shows how to arrange your work (one pane per task),
how to set tmux up for the mouse, and how to run it on a compute node of a cluster.

| skill | use it to |
|---|---|
| `open-science-context:context-management` | the rules for jumps, pane registration and subagent checkpoints; main agents load it at session start |
| `open-science-context:continue-context` | take over the work a context file describes; the first step after every jump |
| `open-science-context:advise-with-context` | ask questions about a context file or plan without acting on it |

## Pane registration

Each tmux pane records which context file it drives, so that
`open-science-context:continue-context` finds the right file after a jump with no file named.
When the main agent starts driving a task it runs:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/pane_context.sh" set tasks/<id>/context.md
```

`pane_context.sh get` prints the registered path, and `pane_context.sh clear` drops it. A new
session in the same pane (after a jump, a SLURM resurrection or a plain `/clear`) keeps the
registration. Subagents never register; they use the main agent's pane.

To take over work in a pane yourself, type `/open-science-context:continue-context`, with or
without a file. With no file it uses the pane's registered file and prints which one; with
nothing registered it lists candidates, newest first, and asks you. **Typing it in the wrong
pane resumes the wrong work**, so check the line that names the file.

## Jumps

| jump | when | command |
|---|---|---|
| active | the context is above about 250k tokens, a subtask finished, or before a fan-out of subagents | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" active <context file>` |
| wait | only background work is left (subagents, a background shell, a SLURM job) and it will take longer than about 45 minutes | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh" wait <context file>` |
| cache-cold | the plugin types `[open-science] cache-cold: ...` into the pane after 45 minutes idle with work still running | a wait jump, now |

Before either command, in the same turn, the agent writes the jump's record: the task
`context.md` (state, what is in flight and how to check it, the exact next step), the project
`context.md` if it changed, one line in the task log, and `opsci notify` for anything you
would otherwise miss. The `jump.sh` call is the last action of the turn.

When the turn ends, the plugin's Stop hook starts the jump: it waits for the pane to be idle,
types `/clear`, confirms that the session id changed, and, for an active jump, types
`/open-science-context:continue-context <context file>`. The agent never types into its own
pane itself.

A cleared session is woken by a background subagent's report, a background Bash task
exiting, or a Monitor event. For SLURM jobs the agent starts a waker before a wait jump:
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait_slurm.sh" <jobid> [<jobid>...]`.

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
| not in tmux | the jump types into the pane |
| the context file is missing, or was not saved in the last 15 minutes | the state to resume from was not written |
| the session's state file cannot be found | the clear could not be confirmed |
| an active jump below 100k tokens without `--force` | a jump costs a full reload; small contexts do not need one |
| an inhibit file exists | another component owns the pane |
| at the stop: a wait jump with nothing running that could wake the session | the session would never wake; start the waker or do an active jump |

`jump.sh cancel` drops a pending request; `jump.sh status` shows it.

### The Stop hook

At every stop of a main session inside tmux, the Stop hook:

1. starts a pending jump, or refuses a wait jump that nothing would wake;
2. arms the cache-cold timer if something will wake the session (a running background task
   or a scheduled prompt), and stops it otherwise;
3. if the context is above the threshold (250k tokens) and no jump is pending, blocks the
   stop once with an instruction to do an active jump. It blocks the same session again only
   after its context has grown by another 50k tokens, so a session waiting for you is not
   stopped at every reply.

Outside tmux it does nothing.

## Settings

Environment variables read by the scripts:

| variable | default | what |
|---|---|---|
| `OPSCI_JUMP_THRESHOLD` | 250000 | context size (tokens) at which the Stop hook asks for an active jump |
| `OPSCI_JUMP_REPEAT` | 50000 | growth (tokens) before the hook asks the same session again |
| `OPSCI_ACTIVE_JUMP_FLOOR` | 100000 | below this, an active jump needs `--force` |
| `OPSCI_CACHE_COLD_MIN` | 45 | minutes idle, with work running, before the cache-cold notice |
| `OPSCI_JUMP_FRESH_MIN` | 15 | the context file must have been saved within this many minutes |
| `OPSCI_STATE_DIR` | `$XDG_STATE_HOME/open-science`, else `~/.local/state/open-science` | where requests, timers, locks and the log `cm.log` are kept |

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

On `PAUSED` or `SUBMITTED` the main agent dispatches a fresh subagent against the same
document.

## `open-science-context:advise-with-context`

For questions about work another session is driving. It reads the context file and what the
question needs, and answers with a `file:line` for every claim. It does not run the plan, fix
anything, or edit the context file or the plan unless you ask. Registering the file to the
pane is the one write it makes.
