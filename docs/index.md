# Get started

The way we do science is changing rapidly, but it is more important than ever to keep
science open.

open-science is a framework for doing research in the open:

- **A complete research record.** How the methods were developed, the results, the
  approaches that failed, and the source code, in a public repository and on a project
  website, with the data archived on Zenodo with a DOI.
- **You decide what goes public, and when.** You work in a private repository and can mark
  any task, file or dataset as private. Nothing is released until you choose to publish.
  Each release contains only the files you allow, is checked for private material and
  secrets, and needs your approval.
- **Research others can continue.** Short project and task records explain what is known,
  what was tried, and what comes next, for collaborators and future readers.
- **Work your way.** Use plain files and the `opsci` command yourself, or the optional
  Claude Code and Codex plugins. The research record and publication checks are shared.

![How a project is organised and published](figures/project_flow.svg)

**Start with the [tutorial](tutorial.md).** The rest of this documentation covers each part
in full; see the navigation menu.

## Install

If you use Claude Code, install the plugin and start Claude Code:

```bash
claude plugin marketplace add mhycheung/open-science
claude plugin install open-science@open-science
claude
```

Then type `/open-science:onboard`. The [tutorial](tutorial.md) says what onboarding does and gives
the prompts for the next steps: starting a project, brainstorming, starting a task and using
Notion.

For Codex:

```bash
codex plugin marketplace add mhycheung/open-science
codex plugin add open-science@open-science
codex
```

Ask Codex to use `open-science:onboard`. Restart after installing components and review
their hooks with `/hooks`. [Claude Code and Codex](agents.md) covers setup and session differences.

If you are not using agents, install the `opsci` command
(`pip install "git+https://github.com/mhycheung/open-science#subdirectory=tools"`, see
[The opsci command](cli.md)) and follow the pages of the components you want ([Components](#components)).

## Components

The framework has three components. Use any combination; each works without the others,
except context management, which needs project management. Project management and
publishing are used through `opsci` and plain files; their Claude Code and Codex plugins are optional.
Context management supports agent handoffs. One more plugin, `open-science`, holds
the onboarding skill.

| # | component | what it does | pages |
|---|---|---|---|
| 1 | project management: the project template and the `open-science-project` plugin | the layout every project is copied from (description, tasks, map, rules, citations, context files, publish settings), and skills to create a project, start tasks, keep context files under their caps, migrate an old project, and take template updates | [Project template and layout](project-template.md), [Project skills](project-skills.md) |
| 2 | context management: the optional `open-science-context` plugin | Claude Code and Codex keep context files and take over tasks; automatic session controls depend on the agent | [Context management and session jumps](context-management.md), [Working in tmux](tmux.md) |
| 3 | publishing: `opsci publish` and the `open-science-publish` plugin | the checked, user-approved export to a public repository, the project website, and Zenodo data releases | [Publishing and the filter](publishing.md), [Zenodo releases](zenodo.md) |

Also part of the framework:

| part | what it does | page |
|---|---|---|
| `opsci` command | the command-line tool behind every step, run by you or by the skills: map build, tasks, context caps, publish, site, Zenodo, notifications, Notion | [The opsci command](cli.md), [Notifications](notify.md), [Notion](notion.md) |
| `open-science` plugin | the onboarding skill `open-science:onboard` | this page |

Two optional extras live in `extras/` of the repository; nothing in the three components
depends on them: a [personal projects page](projects-page.md) that lists your projects on
your GitHub Pages site, and [SLURM resurrection](slurm-resurrect.md), for development on a
compute node of a computing cluster that uses the SLURM scheduler: it resumes your Claude
Code sessions in a new batch job when the current one reaches its time limit.

## How a project is laid out and published

How to read the figure at the top of this page:

- **Private project repository.** Everything is committed here, including drafts, private
  notes and failed routes. Only files listed under `include` in `publish/manifest.yaml` can
  leave it, and a task directory leaves only if its node header says `privacy: public`. A
  `soft-private` task is left out of the release and the public map but may be mentioned by
  name; a `hard-private` task may not appear anywhere in the release. See
  [Project template and layout](project-template.md).
- **The filter.** `opsci publish check` exports one commit, runs every check, and writes a
  report. If you use an agent, it adds a review of tone and claims. Nothing is pushed until you approve that export by its
  id. See [Publishing and the filter](publishing.md).
- **Public outputs.** `opsci publish push` copies the approved export into the public
  repository as a new commit, so the private history never reaches it. The public repository
  builds the project website on GitHub Pages. Data in `data/` never goes to the public
  repository; it can be released on Zenodo with a DOI, after you confirm the release. Your
  personal projects page links to all three.
