# Working in tmux

**Context management requires tmux.** Claude Code must run inside a tmux pane for session
jumps to work: a jump clears the conversation by typing into the agent's own pane, and the
pane records which context file it drives. Outside tmux, `jump.sh` refuses to run and the
plugin's Stop hook does nothing, so the agent's conversation only grows. Project management
and publishing work without tmux.

tmux keeps terminal sessions running on a machine after you disconnect, and splits one
terminal into several. This page covers what you need of it: connecting to a compute node
of a cluster, arranging your work in windows and panes, and using tmux with the mouse. The
onboarding skill `open-science:onboard` sets up the configuration below for you, asking
first.

## Terms

| term | what it is |
|---|---|
| session | a set of windows that keeps running when you disconnect; you reattach to it later |
| window | one full screen of a session, like a browser tab; listed in the status bar at the bottom |
| pane | one rectangle of a window; each pane runs its own shell, or its own Claude Code |

## Arranging your work: one pane per task

Keep **one tmux session** for your research work, **one window per project**, and **one pane
per task** of that project. Each pane runs one Claude Code session that drives one task.

```
tmux session "work"
├── window 0 "dark-matter"      (project)
│   ├── pane: Claude on task fit-profiles
│   └── pane: Claude on task compare-sims
└── window 1 "qnm-catalog"      (project)
    └── pane: Claude on task real-data-pe
```

Why this matters:

- **Each pane is registered to one context file.** When the agent starts driving a task it
  records `tasks/<id>/context.md` for its pane, and after a jump it resumes from that file.
  Two tasks in one pane would overwrite each other's registration; typing
  `/open-science-context:continue-context` in the wrong pane resumes the wrong task. It
  prints which file it uses, so check that line.
- **You can see every task at a glance.** Name each window after its project, and the status
  bar shows what runs where.
- **One session is one unit.** On a cluster, SLURM resurrection restores a whole tmux
  session, with its windows, panes, layout and working directories.

To start a task: in the project's window, open a new pane, `cd` to the project, start
`claude`, and ask it to work on the task (or type
`/open-science-context:continue-context tasks/<id>/context.md` for a task that already has a
context file). When a task is finished, close its pane.

## Keys and mouse

Every tmux key starts with the prefix **Ctrl-b**: press Ctrl and b together, let go, then
press the next key. With the mouse set up (next section), most of this can also be done by
clicking.

| to | keys | with the mouse |
|---|---|---|
| start a session named `work` | `tmux new -s work` (in a shell) | |
| leave it running and disconnect | Ctrl-b d | |
| reattach later | `tmux attach -t work` (in a shell) | |
| list sessions | `tmux ls` (in a shell) | |
| new window | Ctrl-b c | right-click a window name in the status bar, "New After" |
| rename the window | Ctrl-b , | right-click its name in the status bar, "Rename" |
| switch window | Ctrl-b 1, 2, ... or Ctrl-b n / p (next / previous) | click its name in the status bar |
| split the pane side by side | Ctrl-b % | right-click the pane, "Horizontal Split" |
| split the pane top and bottom | Ctrl-b " | right-click the pane, "Vertical Split" |
| move to another pane | Ctrl-b and an arrow key | click the pane |
| resize a pane | | drag its border |
| enlarge a pane to the full window and back | Ctrl-b z | right-click the pane, "Zoom" |
| close a pane | Ctrl-b x, then y | right-click the pane, "Kill" |
| see all sessions, windows and panes | Ctrl-b w | |
| scroll back | Ctrl-b [ , then arrow or Page Up keys; q to leave | the scroll wheel |

The mouse column was checked against the default key table of tmux 3.7b (`tmux list-keys`).
Window numbers start at 0 unless you set `base-index`.

## Configuration for mouse use

Put these lines in `~/.tmux.conf`:

```bash
set -g mouse on                  # click panes and windows, drag borders, scroll with the wheel
set -g set-clipboard on          # text copied in tmux also goes to your computer's clipboard
set -g history-limit 50000       # lines of scroll-back kept per pane
set -g default-terminal "tmux-256color"
set -ag terminal-overrides ",xterm-256color:RGB"   # full colour, as in the terminal outside
```

Then run `tmux source-file ~/.tmux.conf` in any pane, or start a new tmux server.

**Copying text.** With `mouse on`, dragging with the mouse selects text inside the pane and
copies it when you let go. `set-clipboard on` also sends it to your computer's clipboard,
through an escape sequence (OSC 52) that your terminal program must allow; many do, some
need it switched on in their settings. To use your terminal's own selection instead, hold a
modifier key while dragging: Shift in most Linux and Windows terminals, Option in iTerm2.
If neither works, see your terminal's documentation for "mouse reporting".

## On a computing cluster

On a cluster, the login node is for editing and submitting jobs; long agent work belongs on
a compute node, inside a batch job. The tmux server must run inside the job, so that its
panes run on the node with the job's resources, and so that SLURM resurrection can restore
it. The steps below are for SLURM; account and partition names, time limits, and whether you
may `ssh` to a compute node differ between clusters, so check your cluster's documentation.

1. **Log in** to the cluster: `ssh <user>@<login node>`.
2. **Submit a job that keeps a tmux session running.** Save this as `tmux-job.sh`, fill in
   your account, partition, time and resources, and run `sbatch tmux-job.sh`:

   ```bash
   #!/bin/bash
   #SBATCH --job-name=tmux-work
   #SBATCH --account=<account>
   #SBATCH --partition=<partition>
   #SBATCH --time=48:00:00
   #SBATCH --nodes=1
   #SBATCH --ntasks=1
   #SBATCH --cpus-per-task=8
   tmux new-session -d -s work
   while tmux has-session -t work 2>/dev/null; do sleep 60; done
   ```

   The job ends when you close the session, or at its time limit.
3. **Find the node** once the job runs: `squeue --me -o "%i %N %T"` prints the job id, the
   node name and the state (`RUNNING`).
4. **Connect to the node and attach:** from the login node, `ssh <node>`, then
   `tmux attach -t work`. On most SLURM clusters you may `ssh` to a node only while you have
   a job running on it.
5. **Work, then detach** with Ctrl-b d. The session, and the agents in it, keep running when
   you disconnect or your laptop sleeps. To come back, repeat steps 3 and 4.

From your own computer you can go to the node in one command, through the login node:
`ssh -J <user>@<login node> <user>@<node>`, then `tmux attach -t work`.

When the job reaches its time limit, the tmux session and every agent in it stop. The
optional [SLURM resurrection](slurm-resurrect.md) extra queues a new job before that and
resumes the sessions in it.
