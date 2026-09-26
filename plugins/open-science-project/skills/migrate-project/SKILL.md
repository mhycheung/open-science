---
name: migrate-project
description: Move an existing research project, ongoing or completed, into the open-science layout without losing a file - on a branch, with an approved mapping, and a before/after file inventory. Use when the user asks to migrate, convert or bring an existing project into the framework.
---

# Migrate a project

The template and the other skills define the target layout (`AGENTS.md` §5 of any new
project). This skill only says how to get there safely.

## Procedure

1. **Branch and inventory.** Work in a worktree on a new branch, so the original stays
   untouched until the user merges. If the project is not a git repo, `git init -b main` it
   (the default branch is `main` unless the user asks for another name).
   Before the first commit, write a `.gitignore` for large and generated files (data,
   chains, outputs, caches), and ask the user where to draw the line. Then commit the rest.
   A worktree holds only committed files, so commit any untracked file worth keeping
   before you branch.

   Files git ignores are not in the worktree and not in the inventory. List them with
   `git -C <project> status --ignored --short` and include them in the mapping in step 3.
   Do not move them during the migration: write the moves as a list of `mv` commands for
   the user to run in the original checkout after merging. List each dataset that ends up
   under `data/` in `data/MANIFEST.yaml`. Nothing ignored is deleted.

   Record every file before anything moves, keeping the inventory outside the project:

   ```bash
   git -C <project> worktree add ../<project>-migrate -b migrate-open-science
   opsci migrate inventory ../<project>-migrate -o <scratch>/inventory.json
   ```

   If jobs or subagents are running, note their ids for the new project context. Do not
   wait for them.

2. **Add the scaffolding** without overwriting any existing file: instantiate the template
   into a scratch directory (`opsci template instantiate`, as in `open-science-project:new-project`),
   then copy over only the files the project does not have (`cp -rn`). Keep the
   `config/framework.yaml` it wrote: it records the framework commit. If the user chose
   Notion (`opsci notion check` prints `backend=notion`), use `--notion` there, and after the
   migration is approved and committed run `opsci notion enable` (it also adds the hook to a
   settings file the project already had) and `opsci notion init`.
   Ask the user whether the README may name the framework (the line and the rule are in
   `open-science-project:new-project`, step 1). With a yes, use `--framework-line`; if the
   project keeps its own README, add the line from the scratch copy's README below its
   opening paragraph. Without a yes, add nothing.

3. **Propose a mapping and get the user's approval before moving anything.** Ask the user
   first whether the project lives in more than one place (a code repo, data on scratch, a
   paper on Overleaf or in another repo). Migrate one repo. Other pieces either move into
   it or stay where they are with a pointer: data in `data/MANIFEST.yaml` (`source:`), a
   paper in a line in `PROJECT.md`. Before bringing in another git repo together with its
   history, ask the user how.

   The mapping says which existing
   directories become `tasks/<id>/`; what goes to `src/`, `data/` (plus `data/MANIFEST.yaml`),
   `paper/`, `citations/`; what stays where it is. Old context documents and plans move into
   their task's `subcontext/` unchanged. A pitfalls or rules file becomes `rules/`. Ask the
   user which notes are private (meeting notes, correspondence, drafts, remarks about
   people): they go to `private-docs/`, which is committed but never exported
   (soft-private: other files may name them in passing, but not link to them). Documentation for readers goes to `docs/`, which is
   published, so nothing private may stay in an existing `docs/`. Ideas not yet started as
   work may go to `brainstorm/`. What fits nowhere goes to `archive/`. Ask the user the
   privacy tier of each task (`public`, `soft-private` or `hard-private`; definitions in
   `open-science-project:new-task`, "Privacy tier"); hard-private material outside a task
   goes under `hard_private:` in `publish/manifest.yaml`. Then move with
   `git mv`, so history follows the files.

   Moving files breaks references to them. After the moves, search the code, scripts,
   notebooks, configs and job scripts for the old paths and for imports of moved modules,
   and fix them. Run the project's tests, or one short script, if there are any. Report
   every reference you could not check.

4. **Write the new documents from what is there:** a node header per task (`opsci task new`
   for the directory skeleton where it helps, with `--privacy` as the user chose; status
   from the old context documents if there are any, otherwise from the user:
   finished work `done`, abandoned routes `failed` or `abandoned`), the project
   `context.md`, `PROJECT.md` (from the project's own descriptions: README, proposal, plans;
   `TODO:` where they say nothing, and ask the user to check it), and one log line: `<date> — migrated into the open-science layout, from
   commit <sha>.` Do not back-fill logs; git history has them. If the project's history or
   roadmap cannot be worked out from what is there, write the current state and stop: the
   aim is that the project follows the framework from now on, not a reconstructed past.
   Run `opsci map build`.

5. **Check and report:**

   ```bash
   opsci migrate compare <scratch>/inventory.json ../<project>-migrate   # fails on a lost file
   opsci map build ../<project>-migrate && opsci context check ../<project>-migrate
   ```

   A moved file counts as kept; a file whose content is found nowhere is lost and must be
   recovered before you report. Then apply the freshness test to the project context and
   report the mapping, the compare summary, and every `TODO:` left. The user merges.

## A completed project

Steps 1 and 2, the node headers and the map from step 4 (every node `done` or `failed`), and
a project `context.md` stating that the project is complete. No task plans.
