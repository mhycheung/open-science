# Coupling to the session jumps of the open-science-context plugin ("the core")

This coupling is optional. slurm-resurrect reads and writes files in the core's
state directory and runs the core's own `jump.sh`. The core does not know
about slurm-resurrect and has no code for it. If the core is not installed,
every step below is skipped or falls back to typing a prompt.

## What the two plugins share

| item | where | used for |
|---|---|---|
| core state dir | `$OPSCI_STATE_DIR`, default `${XDG_STATE_HOME:-$HOME/.local/state}/open-science` | everything below |
| pane key | `<socket>__<pane id>`, each part with `[^A-Za-z0-9._-]` replaced by `_` | file names |
| jump request | `jump/<key>.json` | a jump in progress |
| finished jumps | `jump/done/<key>-<epoch>.json` | finding a wait jump |
| inhibit file | `inhibit_jump_<key>` | stopping new jumps at wind-down |
| pane lock | `lock/<key>.lock` | only one plugin types into a pane at a time |
| worker | `<core scripts>/jump.sh --worker <request>` | finishing a jump after the hop |

Jump phases, as the core writes them: `requested`, `launched`, `running`,
`unconfirmed`, `cleared`; then the record moves to `jump/done/` with phase
`done` or `failed`. The core's `Stop` hook starts the worker only for phase
`requested`.

## Finding the core's scripts

In order: `RR_CORE_SCRIPTS_DIR`; config key `core_scripts_dir`
(`set core_scripts_dir <dir>`); a `--plugin-dir` on the Claude process's
command line; the newest `plugins/cache/*/open-science-context/*/scripts` under the
Claude config dir. A directory counts only if it holds `jump.sh` and
`cm_lib.sh`.

## At snapshot: which record belongs to a pane

For each Claude pane, with Claude process id `pid`:

1. If `jump/<key>.json` exists and its `state_file` is
   `<config>/sessions/<pid>.json`, it is a jump in progress.
2. Otherwise the newest `jump/done/<key>-*.json` for that `pid` is used, only
   if it is a wait jump (`kind` wait, phase `done` or `cleared`), no earlier hop
   handled it (`rr_handled` unset), and the session transcript has not changed
   since the jump (120 s slack, `RR_JUMP_ACTIVITY_SLACK`). Such a pane is
   waiting for a waker that runs in the old job and will die with it.

The process id check stops a record from an earlier job matching a new pane
that has the same socket path and pane id. The record is stored in the
snapshot.

## At wind-down: inhibit

When the coordinator sends the wind-down message, it writes
`inhibit_jump_<key>` for each Claude pane of the registered sessions. The file
holds the message `jump.sh` shows when it refuses. A jump started this late
would be cut off by the time limit. The files are listed in
`<rr state>/inhibit_<job>.list` and removed:

- by the successor, before it finishes interrupted jumps;
- at the final snapshot, if no successor is queued (after `stop`, at the hop
  cap, or with an empty registry), since nothing will resume those panes;
- by the next coordinator, for jobs no longer in the queue.

## After the hop: finish the jump

`rr_deliver.sh` waits for each rebuilt pane to be idle, then:

| record | action |
|---|---|
| in progress, phase `requested`, `launched`, `running` or `unconfirmed`, and the session id has not changed | **re-drive**: write a new `jump/<new key>.json` for the rebuilt pane (kind `active`, the record's prompt, or `/open-science-context:continue-context <context>` for a wait jump; new socket, pane, key, state file and `old_sid`; phase `launched`) and start `jump.sh --worker` on it. Phase `launched` keeps the core's `Stop` hook from starting a second worker. |
| in progress, phase `cleared`, or the session id changed | the clear already happened: type the resume prompt |
| finished wait jump | **wake**: type `/open-science-context:continue-context <context>` |
| core not found | type the resume prompt without clearing, and log `open-science core not found` |

On success the old record is archived: an in-progress record moves to
`jump/done/` with phase `done`, a finished one gets `rr_handled` set, so no
later hop acts on it again. On failure the user is notified with the prompt to
type by hand.

## Pane lock

slurm-resurrect takes the core's lock file `lock/<key>.lock` (with `flock`)
before it types into a pane, so it and the core's worker never type into the
same pane at the same time. The re-drive starts the worker after releasing
the lock.

## Tests

`tests/slurm_resurrect/test_jump_recovery.sh`. With the core present in the
repo (or `RR_TEST_CORE_SCRIPTS` set) it also runs the core's real `jump.sh`:
the worker re-drives a wait jump in a stand-in Claude pane, and the inhibit
files make `jump.sh` refuse, with a control case where they are absent.
