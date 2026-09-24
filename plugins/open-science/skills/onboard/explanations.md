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

Question: "There are also two optional extras. Do you want either?" Offer the SLURM option
only if the check found SLURM.
- **List of your projects**: A single web page, on your personal GitHub site
  (<username>.github.io), that lists your projects with a short description, tags and links,
  and lets visitors filter and sort them. You keep the list in one small text file. Works on
  its own. Needs a free GitHub account.
- **Survive SLURM time limits**: This computer uses the SLURM job scheduler. A job stops at
  its time limit, which would end your agent sessions. With this extra, before the limit the
  agents are told to finish cleanly, a new job is queued, and when it starts your tmux
  windows are rebuilt and every agent session continues where it stopped. Needs tmux, with
  Claude running inside a SLURM batch job. Works with or without the other parts.

Choosing neither is the default; nothing is installed for them.

## Branch 1 — context management

**1a — tmux** (skip if already inside tmux): explain: "tmux keeps your terminal windows
running even if you disconnect, and lets an agent type into its own window. Start it with
`tmux new -s work`, then start Claude inside it." Offer to add `set -g mouse on` to
`~/.tmux.conf` so the mouse can click, resize and scroll windows.

**1b — older skills with the same names** (skip if the check found none)
- **Move them to an archive folder (recommended)**: they are moved, not deleted, and can be
  moved back.
- **Keep them**: the old ones win whenever a skill is called by its short name, so you must
  always type the full name, for example `/open-science-context:continue-context`.

## Branch 2 — publishing

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
- **The agent creates it**: needs a token that may create repositories in your account. If
  that token leaked, someone could create or change repositories. Choose this only if you
  make many projects.

**2d — where the private copy lives**
- **Only on this computer**: simplest. Back it up yourself.
- **A private GitHub repository**: an off-site backup that only you (and people you invite)
  can see. The agent pushes to it; the public copy stays separate.
- **Another server**: a git server you already use.

**2e — the web page** (no question): after the first publish, open the public repository on
github.com → Settings → Pages → Source: "GitHub Actions". Then every publish rebuilds the page.

**2f — Zenodo**: "Zenodo is a free archive run by CERN. It gives a dataset a DOI, a permanent
link people can cite, and keeps it for decades. A release there can never be deleted. Do you
want to be able to archive project data there?"
- **Yes, test site first (recommended)**: you make a token on sandbox.zenodo.org, a practice
  copy of Zenodo where nothing is permanent, and we run a test release there.
- **Yes, real site now**: a token from zenodo.org as well. The agent still never releases
  without your yes for that exact release.
- **Not now**.

## Branch 3 — list of projects (optional extra)

**3a — personal site**
- **I have <username>.github.io**: give the folder where it is on this computer; the page is
  added as one more page.
- **I do not have one**: on github.com make a public repository named exactly
  <username>.github.io; GitHub serves it as your personal site.

## Branch 4 — SLURM time limits (optional extra)

**4c — permission mode of resumed sessions**
- **Ask before edits (acceptEdits)**: the resumed agent may edit files but asks before
  running other commands. Safer, but it stops and waits when nobody is watching.
- **Run everything (bypassPermissions, the default)**: the resumed agent runs every command
  without asking, including deleting files. Needed for work that must continue unattended.
- **Ask for everything (manual)**: safest, but an unattended session will mostly wait.

**4d — Remote Control**
- **On (default)**: you can watch and steer the resumed sessions from the Claude app on your
  phone or another computer, logged in to your account.
- **Off**: the resumed sessions can only be reached from the cluster.

**4e — queueing**
- **After the current job ends (default)**: the next job is queued to start when this one
  ends. On some clusters it then waits a long time in the queue.
- **Early start**: the next job may start up to about 4 hours before this one ends. When it
  starts, it takes over and ends the old job. Less waiting in the queue, but the old job's
  remaining time is given up.

## Notifications

"The agents send you short messages: a result is ready, a job finished, something needs your
decision."
- **Files (default)**: each message is saved as a small file in the project's `messages/`
  folder. Nothing to set up.
- **Slack**: messages arrive in a Slack channel. You create your own small Slack app that may
  only post messages and files, and nothing else. About 10 minutes, steps in `docs/notify.md`.

## Tokens (say this whenever a token is needed)

"A token is like a password for one service. Please never paste it into this chat: the chat
is stored and sent to the AI model. Instead, in a separate terminal window, run the command I
give you. It opens a private file in a text editor; paste the token there, save and quit. The
file is readable only by you, and the tools read it themselves and send it only to the
service it belongs to. If a token might have leaked, delete it on the service's website and
make a new one."
