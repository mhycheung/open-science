# Tutorial

Every step on this page is a prompt you give to Claude Code. To do a step by hand without an
agent, follow the link in that section.

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

Without an agent: [Get started](index.md#install-the-onboarding-skill).

## 2. Start a new project

```
Start a new open-science project in <directory>.
```

Claude asks for a short name, a title and what the project is about. A short answer is
fine, and you can leave parts for later. It creates the project from the template and makes
the first commit of a private git repository. With Notion, it also creates the project's
Notion page.

To bring in a project you already have, prompt `Migrate this project into the open-science
layout.`

Without an agent: [Project template and layout](project-template.md#creating-a-project).

## 3. Brainstorm in a project

Start Claude Code in the project directory and prompt:

```
Brainstorm: <the idea or question>.
```

Claude makes a brainstorm task under `brainstorm/tasks/`. It is private by default and is
not published unless you choose to publish it. When an idea is ready to become real work,
ask Claude to turn it into a task.

Without an agent: [brainstorm/, docs/ and private-docs/](project-template.md#brainstorm-docs-and-private-docs).

## 4. Start a new task

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

## 5. Use Notion

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
