# open-science

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
- **With or without agents.** It works the same whether an agent does a small part of the
  work, most of it, or none of it.

![How a project is organised and published](docs/figures/project_flow.svg)

- **Context management for agentic work.** Agents keep a short context file for the project
  and for each task. They clear their conversation on their own and resume from that
  file, so a long session is not resent in full on every turn or after the prompt cache
  expires, which reduces usage. The same files let collaborators and other researchers
  pick up an ongoing project straight away.

![Two agents clearing their context and resuming on their own](docs/figures/context_jumps.svg)

Left: the context is over 250k tokens, so the agent saves its state to the task's context
file and the plugin clears the session and resumes it from that file. Right: the agent
submits a SLURM job, saves its state and clears; the idle session is woken when the job
leaves the queue and resumes from the context file. Clearing before a long wait matters
because the prompt cache expires while the session sits idle: waking a session that still
holds a long conversation would resend all of it uncached, which costs far more than a
fresh start from the context file.

**Start with the [user guide](USER_GUIDE.md)**: what you do and what you will see, in about
three minutes of reading.

**Documentation:** <https://mhycheung.github.io/open-science/>, starting with the
[Get started](docs/index.md) page (source in `docs/`).

## Install

Add this repository as a Claude Code plugin marketplace, install the `open-science` plugin,
and run its onboarding skill:

```bash
claude plugin marketplace add mhycheung/open-science   # or the path to a local checkout
claude plugin install open-science@open-science
```

Then, in a new Claude Code session, type `/open-science:onboard`.

The framework has three components. Use any combination; each works without the others,
except context management, which needs project management.

| # | component | plugin | what | needs |
|---|---|---|---|---|
| 1 | project management | `open-science-project` | the project template: description, tasks, map, sources, rules, context files and their caps (`new-project`, `new-task`, `context-files`, `migrate-project`, `update-from-template`, `private-investigation`) | git, `opsci` |
| 2 | context management (for agents) | `open-science-context` | Claude Code agents clear their own conversation and resume from the context files (`context-management`, `continue-context`, `advise-with-context`) | project management (installed with it), Claude Code running inside tmux, `opsci` |
| 3 | publishing | `open-science-publish` | a private and a public copy of each project; a checked, approved export; a project site; Zenodo releases (`publish`, `zenodo-release`) | git, `opsci`, a GitHub account; any git repository |

There are also two [optional extras](#optional-extras).

Context management needs Claude Code to run inside tmux; the
[tmux guide](docs/tmux.md) shows how to set it up, including on a cluster's compute node.

## Optional extras

Both are in `extras/`, and nothing in the three components depends on them.

| extra | where | what | needs |
|---|---|---|---|
| projects list | `extras/projects-page/` (no plugin) | one page on your personal GitHub site listing your projects | a GitHub Pages site; `opsci` only to check the file |
| SLURM resurrection | plugin `slurm-resurrect`, in `extras/slurm-resurrect/` | for development on a compute node of a computing cluster that uses the SLURM scheduler: when the batch job reaches its time limit, rebuild the tmux session in a new job and resume its Claude sessions; see `extras/slurm-resurrect/README.md` | a SLURM cluster, with tmux and Claude running inside a batch job; `jq`, `flock`, `setsid`, `sbatch`, `squeue`, `scancel` |

## What is in this repository

| path | what |
|---|---|
| `template/` | the project skeleton that a new project is copied from |
| `plugins/` | optional Claude Code plugins: `open-science` (onboarding) and one per component |
| `extras/` | the optional extras: the projects page and the `slurm-resurrect` plugin |
| `tools/` | the `opsci` Python package and command line (map build, publish, sync, Zenodo, notify, site) |
| `tests/` | `tests/run_all` runs every automated test |
| `docs/` | the documentation website (`mkdocs.yml`), one page per component |
| `docs/design/` | the design report, the original request, the build plan, and the verification results |
| `CHANGELOG.md` | what each release changed, and how to migrate a project to a new layout |

## Choices recorded here

- **Command-line name: `opsci`** (short for "open science").
- **Environment: [pixi](https://pixi.sh).** `pixi.toml` pins Python ≥3.11 and the test
  dependencies; `pixi.lock` records the exact versions. `pixi run test` runs the tests.

## The `opsci` command

| command | what |
|---|---|
| `opsci template instantiate` | copy the project template into a new directory |
| `opsci task new` | create a task directory, optionally with a plan |
| `opsci map build` | build the project graph and the list of dead ends from the node headers |
| `opsci context check` | check the context files against their line caps |
| `opsci publish` | export, check and push the public part of a project |
| `opsci site` | build the project site |
| `opsci zenodo` | release data to Zenodo (sandbox by default); see `docs/zenodo.md` |
| `opsci notify` | send a message, and optionally a file, to the user; see `docs/notify.md` |
| `opsci projects-page` | check the personal projects page |
| `opsci migrate` | check that a migration lost no file |
| `opsci guide check` | check that the user guide is short and names only things that exist |

`opsci <command> --help` gives the options; `tools/README.md` describes each command.

## Licences

Code: MIT (`LICENSE`). Documentation and other text: CC BY 4.0 (`LICENSE-docs`).
