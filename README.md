# open-science

Tools for doing research in the open. You work in a private repository and publish from it
a public record of the project: what you did and why, the results, the routes that failed,
the sources, and the data, released with a DOI. Nothing leaves the private repository unless
you listed it for publication, it passed the checks for private material and secrets, and
you approved that exact export.

You do not need an AI agent to use it. A project is plain files in git, and every step
(creating a project and its tasks, building the project map, publishing, building the project
website, releasing data on Zenodo) is a command of the `opsci` tool. If you use Claude Code,
plugins add skills that run the same steps with you, and optional components that help
agents keep track of long work. Use as much or as little of that as you like.

**Start with the [user guide](USER_GUIDE.md)**: what you do and what you will see, in about
three minutes of reading.

**Documentation:** <https://mhycheung.github.io/open-science/>, starting with the
[Get started](docs/index.md) page (source in `docs/`).

## Install

The framework has three components. Use any combination; each works without the others,
except context management, which needs project management.

| # | component | plugin | what | needs |
|---|---|---|---|---|
| 1 | project management | `open-science-project` | the project template: description, tasks, map, sources, rules, context files and their caps (`new-project`, `new-task`, `context-files`, `migrate-project`, `update-from-template`) | git, `opsci` |
| 2 | context management (for agents) | `open-science-context` | Claude Code agents clear their own conversation and resume from the context files (`context-management`, `continue-context`, `advise-with-context`) | project management (installed with it), tmux, `opsci` |
| 3 | publishing | `open-science-publish` | a private and a public copy of each project; a checked, approved export; a project site; Zenodo releases (`publish`, `zenodo-release`) | git, `opsci`, a GitHub account; any git repository |

There are also two [optional extras](#optional-extras).

`opsci` is the command-line tool in `tools/`; it needs Python 3.11 or later.

**Without Claude Code.** Clone this repository and install `opsci` from the clone; the
project template is taken from it:

```bash
git clone https://github.com/mhycheung/open-science.git
pip install -e open-science/tools
opsci template instantiate my-project --name my-project --title "My project" --author "Your Name"
cd my-project && git init
opsci task new --title "First question" first-question
opsci map build
```

Publishing is `opsci publish check`, then `opsci publish push --export-id <id>` with the id
from the check's report ([Publishing and the filter](docs/publishing.md)).

**With Claude Code.** Add this repository as a plugin marketplace, install the
`open-science` plugin, and run its onboarding skill:

```bash
claude plugin marketplace add mhycheung/open-science   # or the path to a local checkout
claude plugin install open-science@open-science
```

Then, in a new Claude Code session, type `/open-science:onboard`. It checks what your machine
already has, asks which components you want (explaining each), installs their plugins and
`opsci`, and sets up GitHub access, notifications and tokens. It changes none of your
settings without asking, and never asks you to paste a token into the chat.

**With Claude Code, by hand.**

1. Install the plugins you want: `claude plugin install <plugin>@open-science`.
   Installing `open-science-context` also installs `open-science-project`. Skills are called
   with the plugin prefix, for example `/open-science-project:new-task`.
2. If your own skills directory (`~/.claude/skills/`) has a skill with the same name as a
   plugin skill (`context-management`, `new-project`, ...), move it out of that directory or
   rename it. When both exist, the model tends to pick the unprefixed personal skill, not
   the plugin's (seen in 2 of 2 test runs; see `docs/design/verification.md`, section 2).
3. Install `opsci` into the Python environment your projects use:
   `pip install "git+<URL of this repository>@<tag>#subdirectory=tools"`, or, from a clone,
   `pip install -e tools`. Check with `opsci --help`.
4. One-time setup (tmux mouse mode, notifications): see the
   [user guide](USER_GUIDE.md#one-time-setup). Slack details are in `docs/notify.md`.

## Updates

`CHANGELOG.md` lists what each release changed. To update, first the code:
`claude plugin marketplace update open-science`, then
`claude plugin update <plugin>@open-science` for each installed plugin, and `pip install -U`
of `opsci` (as in step 3 above). Then, in each project, run
`/open-science-project:update-from-template`: it applies the template changes and the
changelog's "Project migration" steps. `opsci` warns when a project's layout is older than
the framework's.

## Optional extras

Both are in `extras/`, and nothing in the three components depends on them.

| extra | where | what | needs |
|---|---|---|---|
| projects list | `extras/projects-page/` (no plugin) | one page on your personal GitHub site listing your projects | a GitHub Pages site; `opsci` only to check the file |
| SLURM resurrection | plugin `slurm-resurrect`, in `extras/slurm-resurrect/` | when a SLURM job reaches its time limit, rebuild the tmux session in a new job and resume its Claude sessions; see `extras/slurm-resurrect/README.md` | tmux inside a SLURM batch job; `jq`, `flock`, `setsid`, `sbatch`, `squeue`, `scancel` |

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

- **Command-line name: `opsci`** (short for "open science"). The name `osf` was avoided
  because the Open Science Framework (osf.io) already uses it.
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

## Running the tests

```bash
pixi install
tests/run_all                 # every automated test
tests/run_all --run-manual    # also tests that need a real service, scheduler or session
```

## Licences

Code: MIT (`LICENSE`). Documentation and other text: CC BY 4.0 (`LICENSE-docs`).
