# Notion: the project mirror and Feed

```
opsci notion check [--send-test | --remove-test PAGE_ID]
opsci notion init            # once per project: create its pages, then sync
opsci notion enable          # an existing project: AGENTS.md section, hook, flag, .gitignore
opsci notion sync [--only KEY ...] [--plots-only] [--dry-run]
opsci notion diff [--strict]
opsci notion post [--kind KIND] [--task ID] [--mention] [--file PLOT ...] "text"
opsci notion prune [--days N]
```

With Notion chosen in onboarding, each project gets its own page in your Notion workspace,
kept up to date as the agents work. Messages from the agents arrive in the project's
**Feed**, and Notion notifies you when one needs you. It is the recommended way for agents
to reach you. The other notification back ends are Slack and files ([Notifications](notify.md)).

## What you see in Notion

Under your projects page (for example "Research projects") each project has a page with:

| page | from |
|---|---|
| Project | `PROJECT.md` |
| Context | `context.md` |
| Map | `map/README.md`, the project graph (`map/graph.md`) and the claims graph (`map/claims.md`) as images (their PNG files), each node table in a closed toggle, `map/dead_ends.md` |
| Milestone results | `results/README.md`, with its figures |
| Log | `log/*.md`, newest month first |
| Rules | `rules/README.md` |
| Brainstorm context | `brainstorm/context.md` |
| Private docs | every `private-docs/**/*.md`, one toggle each |
| **Tasks** (a database) | one row per task in `tasks/` and `brainstorm/tasks/`: properties from the node header (status, area, privacy, verification, summary), and a page with the task's `context.md`, then its results page (`results/README.md`), plan, task map, log and subcontext files as toggles, then its plots |
| **Results** (a database) | one row per result (`type: result` in `tasks/<id>/results/` or `results/`): properties from its header (kind, status, milestone, verification, task, summary), and a page with the result's file and its figures |
| **Feed** | messages from the agents, newest first |

A figure in these files (a line `![alt](path)` whose file is in the project) is uploaded and
shown as an image. Every mention of a task or result links to its page: its full id
(`t02-posterior-inclination`, `r-t02-near-edge-on-at-merger`), a task's
short id (`t02`, when only one task starts with it), and a path naming it
(`tasks/t02-posterior-inclination/context.md`), in every page, table and Feed message. The
text stays as written. The graph images are not linked; the node tables under them are.

Mathematics written as `$...$` or `$$...$$` in the project files appears as equations.
Tables, lists, code, Mermaid diagrams and links are converted too. A link to a project file
is shown as its path, since Notion does not have the files.

The mirror includes private material (`private-docs/`, brainstorm tasks), because it lives
in your own workspace. Anyone you share the project page with sees all of it.

## Plots and captions

Every image or PDF under `tasks/<id>/` (not `data/`) appears in the task's page. Under each
plot is its caption: the file with the same stem and `.caption.md` beside it, for example
`bands_2026-09-25.caption.md` beside `bands_2026-09-25.png`. A caption is markdown with
LaTeX, and it stands on its own: what is plotted, the axes and units, every line, band and
colour, the data, the background needed to read it, and what to see in it (template
`AGENTS.md` section 2). `opsci notion diff` lists every plot without a caption file.

Files that differ only by an ISO date in their name are versions of one plot. A remade plot
gets a new dated name (`bands_2026-09-28.png` after `bands_2026-09-25.png`): the sync shows
the newest version in the place of the older one. The image block and the caption box are
updated in place, and nothing else on the page moves. A PDF with a PNG of the same stem is
the same figure and is not shown twice. Files over 20 MiB are listed but not uploaded (the
limit of Notion's single-part upload).

## The Feed

Each message is a coloured box: `result` green, `status` blue, `question` orange, `blocker`
red, `note` gray. The first paragraph of the text is the title. Notion's notification and inbox preview show
only about the first ten words, so the title is written as a short headline with the main
point first; the agent skill asks for this. A line under the title names
the kind, the author, the task and the time. Attached plots appear inline with their
captions. `--mention` @mentions you, so Notion notifies you, also on your phone if you have
the app. The messages are written by your integration, not by your account; Notion does not
notify you of your own edits.

Messages older than three days are removed from the Feed (`--days`) each time a message is
posted, or by `opsci notion prune`. Every message stays in `messages/notion-feed.jsonl`, and
results and plots stay in the task pages.

With `notify.backend: notion`, `opsci notify` posts to the Feed too (kind `note`, with a
mention). A project with no Notion pages yet, or a user who has not finished the Notion
setup, gets the message as a file in `messages/`, with a hint in the output.

## When the pages are updated

- The agents run `opsci notion sync` after every change (template `AGENTS.md` section 10).
- A Claude Code Stop hook in the project's `.claude/settings.json` runs
  `opsci notion sync --hook || true` when a turn ends. It starts a sync in the background (one
  at a time per checkout) and returns at once; it never blocks the session. Its output is in
  `messages/notion-sync.log`.
- A page whose text changed is rewritten. A task page whose text did not change but whose
  plots did is only updated in the plot blocks.
- Never edit the mirror in Notion: the next sync overwrites it. Edit the project files.

## Setting up

Onboarding (`open-science:onboard`) does this step by step. You can defer it; until then,
messages are written to files.

1. **An integration.** At <https://www.notion.so/profile/integrations>, **New integration**:
   type Internal, your workspace. Its name is shown as the author of every message.
   Capabilities: Read, Update and Insert content; Read and Insert comments; Read user
   information without email addresses.
2. **The token**, the Internal Integration Secret (starts with `ntn_`), in a private file,
   written in your own terminal, never in a chat:

   ```bash
   bash <framework>/plugins/open-science/scripts/secret_file.sh notion
   ```

   It writes `~/.config/opsci/notion.env` (mode 600, directory mode 700) with one line
   `NOTION_TOKEN=ntn_...`, and checks its shape without printing it. `opsci notion` refuses
   the file if others can read it, if it is not yours, or if it is inside a project. The
   token is sent only in the HTTPS `Authorization` header to `api.notion.com`; it is never
   on a command line, in the environment, or printed.
3. **A page for all your projects.** Make a page in Notion, open **••• → Connections** and
   add the integration. The integration can see that page and what is under it, and nothing
   else.
4. **Config** in `~/.config/opsci/config.yaml` (not secret):

   ```yaml
   notify:
     backend: notion
     notion:
       parent_page: https://www.notion.so/Research-projects-0123456789abcdef0123456789abcdef
       user: 01234567-89ab-cdef-0123-456789abcdef   # your Notion user id; opsci notion check lists them
       # credentials_file: ~/.config/opsci/notion.env   (the default)
   ```

5. **Check:** `opsci notion check` prints `backend=`, `token=`, `integration=`,
   `parent_page=` and `user=` lines. `opsci notion check --send-test` makes a page that
   mentions you; `--remove-test <id>` removes it.

Per project: `opsci template instantiate --notion` (what `open-science-project:new-project`
does for a user who chose Notion) or, for an existing project, `opsci notion enable`. Both
add template `AGENTS.md` section 10 (the rules for agents), the Stop hook,
`notion: true` in `config/framework.yaml`, and `config/notion.local.yaml` to `.gitignore`.
Then `opsci notion init` creates the project's page, its Tasks database and Feed, and
writes everything. The page ids are kept in `config/notion.local.yaml` (git-ignored, one per
checkout). Without that file the pages cannot be found again, so keep it.

## Errors

| message | what to do |
|---|---|
| `credentials file not found`, `NOTION_TOKEN missing` | step 2 |
| `group or others have access` | `chmod 600 ~/.config/opsci/notion.env` |
| `object_not_found` for the parent page | share the page with the integration (step 3) |
| `unauthorized` | the token is wrong or was revoked: make a new one on the integration's page, then step 2 |
| `no Notion pages yet` | `opsci notion init` in the project |
| `no caption: <plot>` | write the caption file, then `opsci notion sync` |

If a token may have leaked, open the integration at
<https://www.notion.so/profile/integrations>, refresh its secret there, and store the new one
with step 2.

Tests: `tests/test_notion.py` runs everything against a local stand-in for the Notion API
(`tests/notion_mock.py`); `tests/run_all --run-manual -k notion` also checks your real setup.
