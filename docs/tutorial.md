# Tutorial

Every step on this page is a prompt you give to Claude Code. To do a step by hand without an
agent, follow the link in that section. Every step the skills run is also a command of
`opsci`, which you can run yourself ([The opsci command](cli.md)).

## 1. Onboarding

Install the plugin, then start Claude Code:

```bash
claude plugin marketplace add mhycheung/open-science
claude plugin install open-science@open-science
claude
```

In Claude Code, type:

```
/open-science:onboard
```

Claude first checks what your machine already has. It then asks which components you want
and how agents should reach you (Notion is recommended), and installs what you choose. It
does not change any of your settings without asking you first. API tokens are never pasted
into the chat: you write them by running a script in your own terminal. When it finishes,
start a new Claude Code session so the new plugins load.

Without an agent: install `opsci` ([The opsci command](cli.md)), then set up each component
from its page, listed in [Get started](index.md#components).

## 2. Start a new project

```
Start a new open-science project in <directory>.
```

Claude asks for a short name, a title and what the project is about. A short answer is
fine, and you can leave parts for later. It creates the project from the template and makes
the first commit of a private git repository. With Notion, it also creates the project's
Notion page.

To bring in a project you already have, see [section 3](#3-migrate-an-existing-project).

Without an agent: [Project template and layout](project-template.md#creating-a-project).

## 3. Migrate an existing project

Migrating moves a project you already have into the open-science layout, without losing a
file.

```
Migrate this project into the open-science layout.
```

Claude works on a new git branch, so your original stays as it is until you merge. It
proposes where each file goes and moves nothing until you approve. Notes go to
`private-docs/`, which is never published. At the end it checks that no file was lost and
asks you to approve the merge.

Claude makes a context file for the migration, so a large migration can run over several
sessions: prompt `Continue the migration.`

Without an agent: [Project skills: migrate-project](project-skills.md#open-science-projectmigrate-project).

## 4. Brainstorm in a project

Start Claude Code in the project directory and prompt:

```
Brainstorm: <the idea or question>.
```

Claude makes a brainstorm task under `brainstorm/tasks/`. It is private by default and is
not published unless you choose to publish it. When an idea is ready to become real work,
ask Claude to turn it into a task.

Without an agent: [brainstorm/, docs/ and private-docs/](project-template.md#brainstorm-docs-and-private-docs).

## 5. Start a new task

```
Start a new task: <what you want done and what counts as done>.
```

Claude settles the design with you: the goal, when the task is done, constraints, budget,
and whether the task is public or private. You also choose how much it does without asking:

- **autonomous** (the default): it runs the whole plan and stops only for fatal problems.
- **checkpoints**: it also stops at the points you name.
- **collaborative**: it also asks you whenever two readings of the plan would lead to
  different work.

It then writes `tasks/<id>/plan.md` and the task's context file, and starts once you
approve the plan. For small work or an exploration, say `no plan needed`.

To pick up the task in a later session, prompt `Continue tasks/<id>/context.md.`

Without an agent: [Project skills: new-task](project-skills.md#open-science-projectnew-task).

## 6. Use Notion

If you chose Notion during onboarding, each new project gets a Notion page. It holds the
project's context, map, log, a Tasks database with each task's plan and plots, and a
**Feed**. Agents post results, questions and blockers to the Feed, and Notion notifies you.
The messages only go one way: answer in the Claude Code session, not in Notion. Make changes
through Claude too, since the pages are rewritten from the project files on every sync.

Useful prompts:

```
Mirror this project to Notion.          # an existing project
Sync Notion.
Remake the plots for t03 and update them in Notion.
```

Without an agent: [Notion mirror and Feed](notion.md).

## 7. Publish to a public repo and the project site

Publishing copies the parts of your private project that you allow to a public GitHub
repository, and builds a website from them.

First, create an empty public repository on github.com. Then prompt:

```
Publish this project.
```

Claude exports only the files that `publish/manifest.yaml` allows, and checks them for
private material, secrets and broken links. It shows you a report and pushes only after you
approve it. Each publish also rebuilds the project site. After the first publish, turn the
site on once in the public repository: **Settings → Pages → Source: GitHub Actions**.

Useful prompts:

```
What is unpublished?
Preview the project site.
```

Without an agent: [Publishing and the filter](publishing.md#before-the-first-publish).

## 8. Publish data on Zenodo

Zenodo stores a copy of your data permanently and gives it a DOI, so that others can cite
and download it.

```
Release the data to Zenodo as version 1.0.
```

Claude first makes a test release on the Zenodo sandbox, which is not public, and shows you
the files. It makes the real release only after you confirm. A real release cannot be
deleted, so make sure to check carefully before publishing. The DOI is added to the project
and shown on its results pages.

Without an agent: [Releasing data to Zenodo](zenodo.md).
