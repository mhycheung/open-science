---
name: new-project
description: Create a new research project from the open-science template - copy it, fill its placeholders, record the framework commit, and make the first commit of a private git repo. Use when the user asks to start, create or set up a new project. For an existing project use open-science-project:migrate-project instead.
---

# New project

Scaffolding only. The user wants an empty project to start working in, not a designed one.
Do not invent research goals, methods or rules. A slot you were not given content for gets a
one-line `TODO:` and goes in your report.

## Procedure

1. **Ask only for what you cannot read off the environment:** the directory, a short slug
   (lower case, digits, hyphens), a one-line title, and the user's name (default
   `git config user.name`).

   **Always also ask what the project is about**, as one open question in the same message.
   Name the points `PROJECT.md` has room for: the question, why it matters, the approach,
   what counts as success, the scope, and the key sources. Tell the user that a short
   answer is fine and that any part can be left for later. Do not block on it: if the user
   skips it, create the project anyway and leave the `TODO:` lines.

   **Ask whether the README may name the framework.** With consent, the README gets the
   line "This project is run in the open with the open-science framework: <link>. Its
   plans, results, failed routes and sources are all written down, so that the work can be
   reproduced and checked." Show the user the line and ask; do not add it without a yes, as
   it would read as an advertisement the user did not choose.

2. **Find the template.** The plugin ships inside the framework repo:

   ```bash
   TPL="${CLAUDE_PLUGIN_ROOT}/../../template"
   ls "$TPL/AGENTS.md"
   ```

   If that is missing (the plugin was installed without the rest of the repo), clone the
   framework repo the plugin came from into a scratch directory and use its `template/`.
   `opsci` must be installed (`opsci --help`); if not, install it from the same repo:
   `pip install "git+<framework repo URL>#subdirectory=tools"`.

3. **Instantiate:**

   ```bash
   opsci template instantiate <dir> --name <slug> --title "<title>" --author "<name>" --template "$TPL"
   ```

   Add `--no-context-management` unless the user uses session jumps (the
   `open-science-context` plugin is installed, or onboarding recorded component 3): it drops
   the template's lines about jumps and records `context_management: false`.
   Add `--notion` if `opsci notion check` prints `backend=notion` (the user chose Notion in
   onboarding, even if its setup is not finished): it adds the Notion section to `AGENTS.md`
   and the auto-sync hook, and records `notion: true`.
   Add `--framework-line` only if the user agreed to the README line in step 1. It links
   the framework's web page (`https://...`, derived from the recorded repo, never a
   `git@` address).
   It refuses a non-empty directory, fills every placeholder, records the framework commit in
   `config/framework.yaml`, writes the first log entry, builds the map and checks the result.
   A non-zero exit leaves nothing behind; report its message.

4. **Make it a git repo** and commit everything:
   `git -C <dir> init -q -b main && git -C <dir> add -A && git -C <dir> commit -qm "Create project from the open-science template"`.
   With `--notion`: if `opsci notion check` printed no `problem` and no `missing`, run
   `opsci notion init` in `<dir>`, which creates the project's pages under the user's
   Notion page, and report the link it prints. Otherwise tell the user that messages go to
   files until they finish the Notion setup (`open-science:onboard`); then `opsci notion init`.

5. **Fill what you were given.** `PROJECT.md`: put the user's answer under the matching
   headings, in their substance and close to their words. Do not add goals, methods or
   sources they did not state, and leave `TODO:` under every heading they did not answer.
   `AGENTS.md` §1: a 3–8 line summary of `PROJECT.md`, or `TODO:`. `README.md`: its opening
   paragraph from the same answer, or leave the comment. If the user named a scratch directory,
   batch account or notification channel, copy `config/site.example.yaml` to
   `config/site.local.yaml` (git-ignored) and fill those fields only. Commit.

6. **Not automatic, only on request:**
   - A public repo is created only when the user asks, and nothing is pushed to it except
     through `open-science-publish:publish`.
   - An entry on the user's personal projects page (`projects.yaml` in their page
     directory): add it only if they keep one and ask.

7. **Report:** the directory, the framework commit recorded, what you filled, every `TODO:`
   left (the unanswered `PROJECT.md` sections by name). Tell the user about the three
   directories they may not expect: `docs/` (documentation, published), `private-docs/`
   (private notes, committed but never exported) and `brainstorm/` (ideas before they become
   tasks, with its own context and graph; not published unless added to the manifest). The
   first piece of work is started with `open-science-project:new-task`.

## Rules

- The default branch is `main`, here and in every repo the framework creates, unless the
  user asks for another name.

- Never copy the template over an existing project: that is `open-science-project:migrate-project`
  (a project without the layout) or `open-science-project:update-from-template` (a project that has it).
- An improvement to the template belongs in the framework repo, committed there, not in one
  project's copy.
