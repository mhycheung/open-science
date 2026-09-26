---
name: notion
description: Work in a project that is mirrored to Notion - keep the Notion pages in sync with the project files, keep task plots (with self-contained captions) current in the task pages, post results, questions, blockers and status to the project's Notion Feed, and set up the mirror for a new or existing project. Use when a project's AGENTS.md has a "Notion" section, when the user asks to sync, update or show something in Notion, to remake or update plots in Notion, to post or notify through Notion, or to mirror a project to Notion.
---

# Notion

A project with `notion: true` in `config/framework.yaml` is mirrored to Notion, and the user
reads it there. `opsci notion` writes the pages as the user's Notion integration (a bot), so
an @mention from it notifies the user. Full reference: `docs/notion.md` in the framework
repo.

What is in Notion, under the project's page:
- Project, Context, Map (the project graph and the claims graph as images),
  Milestone results, Log, Rules, Brainstorm context and Private docs pages.
- The **Tasks** database: one row per task, holding the task's context, then its results
  page, plan, task map, task log and subcontext files, then its plots.
- The **Results** database: one row per result, holding the result's file and figures.
- The **Feed**: messages to the user, newest first, removed after 3 days.

## Rules

1. **Sync after every change.** After you change any project file (context files, plans,
   logs, rules, the map, a plot or a caption), run `opsci notion sync`. Then run
   `opsci notion diff`: it must print `in sync` and list no plot without a caption. A Stop
   hook also syncs when a turn ends. It does not replace your own sync before you report.
2. **Plots live in the task.** A plot that supports a result is saved under `tasks/<id>/`,
   usually `tasks/<id>/S<n>/<name>_<YYYY-MM-DD>.png`. Only plots there appear in the task's
   page. Nothing only in `data/`, `/tmp` or a message.
3. **Every plot has a self-contained caption** in `<same stem>.caption.md` beside it: what is
   plotted and the question it answers; each axis with units; every line, band, marker and
   colour; the data (which sample, how many, which model); the background needed to read it
   (definitions, caveats); and what to see in it, with the numbers. Short paragraphs, no
   headings.
4. **Mathematics in LaTeX**, everywhere: inline `$\iota_Q(t)$`, displayed `$$ ... $$` on lines
   of their own. This covers context files, plans, logs, node summaries, captions and Feed
   messages. Notion renders it.
5. **A remade plot gets a new dated name** (`bands_2026-09-28.png` after
   `bands_2026-09-25.png`) and its own caption file. The sync treats the two as versions of
   one plot and replaces the old one in the same place in the page. Nothing else moves.
   When the user asks to update or remake plots: make the new files and captions, run
   `opsci notion sync --plots-only`, then check `opsci notion diff`.
6. **Post to the Feed** at the points the user cares about:

   | when | command |
   |---|---|
   | a subtask finished, with its result | `opsci notion post --kind result --task <id> --mention --file <key plot> "<headline>\n\n<result with numbers>"` |
   | a plan to approve, a hold point, a question or decision for the user: anything the turn ends waiting on | `--kind question --task <id> --mention "<headline: what is needed>\n\n<the choice or the plan in a few lines>"`, after a sync |
   | a blocker | `--kind blocker --mention` |
   | a long job submitted or finished | `--kind status` (no `--mention`) |

   Post these unasked: an answer the user must give that is only in the chat may never be
   seen. While work runs, post a `status` at least once per working session. Do not post
   routine steps. Set `--author` to who you are (`main:<task-id>`, `subagent:<name>`). The first
   paragraph is the title; the rest is the body. Both take markdown and `$LaTeX$`.

   **The title is a headline.** The Notion notification and inbox preview show only about the
   first ten words, so those words must carry the message on their own: the finding, the
   number, or what the user must do. Write it in plain words, under about ten words, with the
   main point first. Do not start with the task id, the kind, a greeting, "Update:" or
   context: `--task`, `--kind` and the time are already on the line under the title. Keep
   LaTeX, file paths and long identifiers out of the title; they preview badly. Examples:
   `Inclination peaks at 20 degrees, not edge-on` (result); `Approve plan: rerun with 4
   modes?` (question); `Blocked: GWOSC strain download fails` (blocker); `PE run 3 submitted,
   done in ~6 h` (status). Details go in the body.
   `opsci notify` posts to the Feed too (kind `note`, with a mention).
7. **Messages expire.** Feed messages are removed after 3 days. A result, decision or plot
   that must last goes in the project files first, and so in the task page.
8. **Never edit the Notion pages by hand or through another tool.** The next sync overwrites
   them. Edit the project files.

## Setting up a project

- **New project:** `open-science-project:new-project` does it when `opsci notion check`
  prints `backend=notion`: `opsci template instantiate ... --notion`, then in the project
  `opsci notion init`.
- **Existing project** (the user asks to mirror it): run `opsci notion check`. If the token or
  parent page is missing, send the user to `open-science:onboard` (Notion setup). Then run
  `opsci notion enable`. It adds AGENTS.md section 10, the Stop hook in
  `.claude/settings.json`, `notion: true` in `config/framework.yaml` and the `.gitignore`
  line. Review and commit those changes, then run `opsci notion init`. Report the page link
  it prints.
- `config/notion.local.yaml` holds the page ids (git-ignored). If it is lost, the pages
  cannot be found again: say so; do not run `init` a second time without the user.

## Errors

| message | what to do |
|---|---|
| `no Notion pages yet` | `opsci notion init` (once per project) |
| `NOTION_TOKEN missing`, `credentials file not found` | the user runs the token step of `open-science:onboard` |
| `group or others have access` | the user runs `chmod 600` on the token file |
| `object_not_found` on the parent page | the user shares the page with the integration (••• → Connections) |
| `no caption: <plot>` | write the caption file, then sync |

The hook logs to `messages/notion-sync.log`. Read it when the pages look out of date.
