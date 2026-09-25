# Onboarding texts

The words `open-science:onboard` shows the user. Assume the user has never heard of this
framework, of Claude Code plugins, or of tmux. Put the text of an option in that option's
`description` (the question widget shows it next to the choice); for a single-select
question, put the longer text in the option's `preview`. Keep the plain language; do not
shorten it into jargon.

## Intro (say this before the first question)

This sets up the open-science tools for you. They help you do research in the open: your
project is written down in a private repository (plans, results, failed attempts, sources),
and you publish a checked public copy of it when you choose. There are three parts, in any
combination (one of them needs another, which I will explain), and two optional extras. I
will first check what this computer already has, so I only ask about what is missing.
Nothing in your own settings changes unless you say yes to that change.

What will happen:
1. I check this computer (this changes nothing).
2. You confirm the name and email your commits will carry.
3. You pick the parts and extras you want.
4. I install them and set up what they need, asking before each change.
5. I end with a summary: what was set up, and what is left for you to do by hand.

## Q0 — commit identity

Question: "Every change saved in your projects (a git commit) is signed with a name and an
email. If you publish a project, that name and email become public. Should commits use
<name> <<email>>?"
- **Yes, use these**: commits will carry exactly this name and email.
- **Use a different name or email**: you type the ones to use. Pick an email you are happy to
  have public, for example a work address or GitHub's no-reply address.

## Q1 — which parts (multi-select, in this order)

Question: "Which parts do you want to use? You can pick any combination, and add others
later by running this again."
- **Project management**: A standard layout for each research project, so that you, your
  collaborators and any agent find things in the same place: a description of the project,
  one folder per task with its plan, where it stands and its history, a map of how the tasks
  depend on each other, the sources used, and written rules. The records are kept current as
  the work goes on, so anyone, or any new agent session, can pick up the work. Works with or
  without the other parts; with publishing, each task's record also says whether it may go
  public, and the publish check also looks at the map and the records.
- **Context management**: An AI agent can only hold a limited amount of conversation. On long
  work it would otherwise forget things or slow down. With this part, when its memory gets
  full, or before a long wait, the agent saves where the work stands in the project's
  records, clears its own conversation and carries on from the records. You will see the
  agent type into its own window; that is expected. It never does this while waiting for
  your answer. **Needs the project management part** (the records it resumes from) and tmux,
  a program that keeps terminal windows alive and lets the agent type into its own window.
- **Publishing**: Each project gets two copies. The private one is where you work; it can
  hold drafts, notes and data. The public one is what the world sees. You never copy files
  to it by hand: publishing collects only the files you allowed in a list, checks them for
  passwords, private paths and anything you listed as private, and shows you a report.
  Nothing goes public until you approve that report. The public copy also gets its own web
  page on GitHub, and data can be archived on Zenodo with a DOI (a permanent citable link).
  Works with any git repository. Needs a free GitHub account.

If the user picks context management without project management, say that it needs it and
ask: "add project management too" or "leave context management out".

## Q2 — optional extras (multi-select; ask in the same widget call as Q1)

Question: "There are also two optional extras. Do you want either?" The SLURM option
depends on the check:
- `batch_job` is a job number: offer it, and start its description with "You are running
  inside a SLURM job now, so this probably applies to you."
- `slurm=present` and `batch_job=no`: offer it as written (a cluster, outside a job).
- `slurm=missing`: do not offer it. Instead add this line to the question: "(There is a
  third extra, for surviving the time limits of SLURM jobs on computing clusters. This
  computer does not seem to use SLURM, so it is left out. Tell me if that is wrong.)"
- **List of your projects**: A single web page, on your personal GitHub site
  (<username>.github.io), that lists your projects with a short description, tags and links,
  and lets visitors filter and sort them. You keep the list in one small text file. Works on
  its own. Needs a free GitHub account.
- **Survive SLURM time limits**: Only for development on a compute node of a computing
  cluster, and this computer uses the SLURM job scheduler. A job stops at its time limit,
  which would end your agent sessions. With this extra, before the limit the agents are told
  to finish cleanly, a new job is queued, and when it starts your tmux windows are rebuilt
  and every agent session continues where it stopped. Needs tmux, with Claude running inside
  a SLURM batch job. Works with or without the other parts.

Choosing neither is the default; nothing is installed for them.

## Branch 1 — context management

Say first: "Context management needs Claude Code to run inside tmux, a program that keeps
terminal windows running after you disconnect and lets the agent type into its own window.
I will set up what is missing."

**1a — tmux settings** (skip if the check found them all): "These settings let you use tmux
with the mouse: click a pane or a window name to switch to it, drag a border to resize, scroll
with the wheel, and copy by selecting text. May I add them to `~/.tmux.conf`?" Show only the
lines for the missing settings:

```bash
set -g mouse on                  # click panes and windows, drag borders, scroll with the wheel
set -g set-clipboard on          # text copied in tmux also goes to your computer's clipboard
set -g history-limit 50000       # lines of scroll-back kept per pane
set -g default-terminal "tmux-256color"
set -ag terminal-overrides ",xterm-256color:RGB"   # full colour, as in the terminal outside
```

**1b — how to arrange your work** (no question): "Keep one tmux session for your work, one
window per project, and one pane per task. Each pane runs one agent on one task and remembers
which task that is, so after the agent clears its conversation it resumes the right work.
The keys start with Ctrl-b: Ctrl-b c makes a new window, Ctrl-b % splits a pane, Ctrl-b d
leaves tmux running and disconnects, and `tmux attach` brings you back. With the mouse
settings you can also right-click a pane or a window name for a menu. The full guide is the
page 'Working in tmux' of the documentation."

**1c, local — starting tmux** (not in tmux, no SLURM): "Start tmux with `tmux new -s work`,
then start Claude inside it. Next time, `tmux attach -t work` brings you back to the same
windows."

**1c, cluster — tmux on a compute node** (not in tmux, SLURM found, not in a batch job):
"This computer is part of a cluster that uses the SLURM scheduler. Long agent work should run
on a compute node, inside a batch job, not on the login node. I can write a small job script
that keeps a tmux session running on a compute node for the length of the job." The script:

```bash
#!/bin/bash
#SBATCH --job-name=tmux-work
#SBATCH --account=<account>
#SBATCH --partition=<partition>
#SBATCH --time=<time limit>
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=<cores>
tmux new-session -d -s work
while tmux has-session -t work 2>/dev/null; do sleep 60; done
```

Then: "Submit it with `sbatch ~/tmux-job.sh`. When it runs, `squeue --me -o "%i %N %T"`
shows the node name. Connect with `ssh <node>`, then `tmux attach -t work`, and start Claude
there. On most clusters you may connect to a node only while your job runs on it. The job,
and everything in its tmux session, ends at its time limit; the SLURM extra can resume it in
a new job."

**1e — session names** (skip if already on): "Each Claude session has a name, shown in the
list of sessions in the Claude app and on claude.ai when you use Remote Control, and in the
box where you type. I can make that name say what the session works on: the project name
(`quad-ratio`, then `quad-ratio-2` for a second session in the same project), and once the
session works on a task, the task's short name after it (`quad-ratio-2 · pp-real`). A new
task name appears after your next message. Turn this on?"
- **Yes (recommended)**: sessions are named after their project and task.
- **No**: Claude Code keeps its own names, such as `quad-ratio-3f`.

**1d — older skills with the same names** (skip if the check found none)
- **Move them to an archive folder (recommended)**: they are moved, not deleted, and can be
  moved back.
- **Keep them**: the old ones win whenever a skill is called by its short name, so you must
  always type the full name, for example `/open-science-context:continue-context`.

## Branch 2 — publishing

Say this first: "Setting this up publishes nothing. Later, nothing goes public (a push to a
public repository, a web page, a Zenodo release) until you have read the check report and
said yes to that exact publication."

**2b — how git reaches GitHub** (skip if the SSH check says ok)
- **SSH key (recommended)**: A pair of files on this computer proves to GitHub that you are
  you. You add the public half to your GitHub account once. Nothing secret is typed or stored
  by these tools, and it works for every repository you own.
- **Access token (HTTPS)**: A password-like string made on GitHub. Make it "fine-grained",
  limited to only the repositories you choose, with "Contents: read and write" and
  "Workflows: read and write" (publishing adds the file that builds the web page; GitHub's
  names for these permissions may differ slightly). Git keeps it in its credential store. Use this only if SSH is blocked on your network.

**2c — who creates each public repository**
- **I create it on github.com (recommended)**: about one minute per project, in the browser:
  New repository, public, empty. The agent then needs no right to create repositories.
  Either way the repository stays empty until you approve the first publication.
- **The agent creates it**: needs a token that may create repositories in your account. If
  that token leaked, someone could create or change repositories. Choose this only if you
  make many projects.

**2d — where the private copy lives**
- **A private GitHub repository (recommended)**: an off-site backup that only you (and
  people you invite) can see. The agent pushes to it; the public copy stays separate.
- **Only on this computer**: simplest. Back it up yourself.
- **Another server**: a git server you already use.

**2e — the web page** (no question): after the first publish, open the public repository on
github.com → Settings → Pages → Source: "GitHub Actions". Then every publish rebuilds the page.

**2f — Zenodo**: "Zenodo is a free archive run by CERN. It gives a dataset a DOI, a permanent
link people can cite, and keeps it for decades. A release there can never be deleted, so the
agent never makes one on its own: it shows you exactly what would be released and waits for
your explicit yes. Do you want to be able to archive project data there?"
- **Yes, test site first (recommended)**: you make a token on sandbox.zenodo.org, a practice
  copy of Zenodo where nothing is permanent, and we run a test release there (after your yes).
- **Yes, real site now**: a token from zenodo.org as well. The agent still never releases
  without your yes for that exact release.
- **Not now**.

## Branch 3 — list of projects (optional extra)

**3a — personal site**
- **I have <username>.github.io**: give the folder where it is on this computer; the page is
  added as one more page. I commit it only after your yes and never push it: you push it
  yourself when you want it online.
- **I do not have one**: on github.com make a public repository named exactly
  <username>.github.io; GitHub serves it as your personal site.

## Branch 4 — SLURM time limits (optional extra)

**4c — permission mode of resumed sessions**
- **Ask before edits (acceptEdits)**: the resumed agent may edit files but asks before
  running other commands. Safer, but it stops and waits when nobody is watching.
- **Automatic checks (auto)**: before each action runs, a separate automatic check decides
  whether it is safe. Ordinary work goes ahead without asking; actions that look risky are
  blocked and the agent looks for another way. A middle ground for unattended work. It may
  not be available on every Claude plan; if Claude Code refuses it, pick another mode.
- **Run everything (bypassPermissions, the default)**: the resumed agent runs every command
  without asking, including deleting files. Needed for work that must continue unattended.
- **Ask for everything (manual)**: safest, but an unattended session will mostly wait.

**4d — Remote Control**
- **On (default)**: you can watch and steer the resumed sessions from the Claude app on your
  phone or another computer, logged in to your account.
- **Off**: the resumed sessions can only be reached from the cluster.

**4e — queueing** (no question): the next job is queued to start when the current one ends
(the default, `queue_mode afterany`). Set nothing. List it in the summary with the way to
change it: `/slurm-resurrect:resurrect set queue_mode early` lets the next job start up to
about 4 hours before the current one ends and take over from it.

## Notifications

"The agents send you short messages: a result is ready, a job finished, something needs your
decision. This is not only a convenience. An agent sometimes clears its own conversation
(this is what context management does), and anything it
wrote to you in the chat disappears with it. The messages are kept in a separate place, so
nothing meant for you is lost."
- **Files (default)**: each message is saved as a small file in the project's `messages/`
  folder. Nothing to set up.
- **Slack**: messages arrive in a Slack channel. You create your own small Slack app that may
  only post messages and files, and nothing else, into one channel you choose. About 10
  minutes; I walk you through it step by step.

## Slack setup (step by step, only if the user picked Slack)

Give one step at a time. After each, wait until the user says it is done (or asks for help)
before giving the next. Do not paste the whole list at once.

1. **Workspace.** "Which Slack workspace should the messages go to? You need to be allowed
   to add apps there; some workplaces ask an administrator to approve each app."
2. **Channel.** "Which channel should the messages go to? Everyone in that channel will see
   them, and they can contain results, file paths and error messages from your work. I
   recommend a new private channel with only you in it, for example `#<name>-agents`. Please
   do not use a channel shared with other people unless you want them to see every
   message." Wait for the channel name; if it sounds shared (`#general`, a team or project
   channel), ask once more whether others should see the messages.
3. **Create the app.** "Go to https://api.slack.com/apps, choose Create New App, then From a
   manifest, pick the workspace, paste this, and create the app:" then show the manifest in
   `docs/notify.md` (section 1). "It may only post messages and files, nothing else."
4. **Install it.** "On the app's page, open OAuth & Permissions and choose Install to
   Workspace, then Allow. It shows a Bot User OAuth Token starting with `xoxb-`. Leave that
   page open; do not paste the token here. We store it in step 7."
5. **Invite the app to your channel.** "In Slack, in `#<channel>`, type
   `/invite @opsci-notify`. The app can only post in channels it has been invited to."
6. **Channel ID.** "Click the channel name at the top of `#<channel>`; at the bottom of the
   window that opens is the Channel ID, starting with C. Copy it. It is not secret; you can
   paste it here if you like."
7. **Store the token and channel ID**, as in "Tokens" below: the file has two lines to fill
   in, `SLACK_TOKEN=` (the `xoxb-` token) and `SLACK_CHANNEL=` (the channel ID).
8. **Test.** With the user's yes, set Slack as the way messages are sent, then send a test
   message. "Did it arrive in `#<channel>`, and nowhere else?"

## Tokens (say this whenever a token is needed)

"A token is like a password for one service. Please never paste it into this chat: the chat
is stored and sent to the AI model. Instead, in a separate terminal window, run the command I
give you. It opens a private file in a text editor; paste the token there, save and quit. The
file is readable only by you, and the tools read it themselves and send it only to the
service it belongs to. If a token might have leaked, delete it on the service's website and
make a new one."
