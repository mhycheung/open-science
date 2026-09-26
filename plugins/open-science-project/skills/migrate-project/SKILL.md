---
name: migrate-project
description: Move an existing research project, ongoing or completed, into the open-science layout without losing a file - on a branch, with an approved mapping, a before/after file inventory, the task maps and the results and claims graph filled in if the user wants them, and a migration context file so that a large migration can run over several sessions. Use when the user asks to migrate, convert or bring an existing project into the framework, or to continue a migration.
---

# Migrate a project

The template and the other skills define the target layout (`AGENTS.md` §5 of any new
project). This skill only says how to get there safely.

## The migration context file

Every migration, small or large, is a task in the migrated project, `tasks/t00-migration/`
(step 2), so that another session can take it over. Its `context.md` names the original
checkout, the worktree (the working directory for every step), the branch, the inventory,
running job ids, the user's answers and the next step. Its `plan.md` holds the approved
mapping and subtasks S1 to S6, one per step below (a large step split into S4a, S4b, ...).
After every subtask, update `context.md` and `log.md` (`open-science-project:context-files`)
and commit in the worktree. Another session resumes with
`open-science-context:continue-context` on that file.

## Procedure

1. **Branch and inventory.** Work in a worktree on a new branch, so the original stays
   untouched until the user merges. If the project is not a git repo, `git init -b main` it
   (another branch name only if the user asks). Before the first commit, write a
   `.gitignore` for large and generated files (data, chains, outputs, caches), and ask the
   user where to draw the line. Then commit the rest. A worktree holds only committed files,
   so commit any untracked file worth keeping before you branch. Record every file before
   anything moves, keeping the inventory outside the project:

   ```bash
   git -C <project> worktree add ../<project>-migrate -b migrate-open-science
   opsci migrate inventory ../<project>-migrate -o <scratch>/inventory.json
   ```

   Files git ignores are not in the worktree or the inventory. List them (`git -C <project>
   status --ignored --short`) with their size (`du -sh`) for the mapping in step 3, and ask
   whether, after the merge, the user wants them copied to their new paths (the originals
   stay; this needs the space twice) or moved; do not touch them before. List each dataset
   that ends up under `data/` in `data/MANIFEST.yaml`. Nothing ignored is deleted. Note the
   ids of running jobs or subagents for the migration context; do not wait for them.

2. **Add the scaffolding and the migration task** without overwriting any existing file:
   instantiate the template into a scratch directory (`opsci template instantiate`, as in
   `open-science-project:new-project`, without `--notion`: Notion comes after the merge,
   step 6), then copy over only the files the project does not have (`cp -rn`). Keep the
   `config/framework.yaml` it wrote: it records the framework commit. Ask the user whether
   the README may name the framework (`open-science-project:new-project`, step 1). With a
   yes, use `--framework-line`, or, if the project keeps its own README, add the scratch
   copy's line below its opening paragraph. Without a yes, add nothing.

   Then create the migration task in the worktree, and write into it what step 1 recorded:

   ```bash
   opsci task new t00-migration --title "Migrate into the open-science layout" \
       --short-name migrate --plan --autonomy checkpoints --hold-at S3 S5 \
       --privacy soft-private --summary "Moves the project into the open-science layout."
   ```

   (another id if taken). The task is soft-private: it names private files and machine
   paths. Register the pane for its `context.md` in the worktree
   (`open-science-context:context-management`, "Pane registration"). Then ask how much of
   the old work step 4 writes up, and record the answer in `context.md`. Recommend the full
   write-up (task maps, results, claims graph), but say that it reads every task's files,
   so on a large project it costs many more tokens. Lighter options:
   - **Tasks without details**: each task gets its directory and node header (status, a
     one-sentence summary), but no `map.md` of its subtasks and routes.
   - **No results graph**: no result files, so no claims graph (`map/claims.md`: each
     figure, value or statement the work established, or assumption it took, with what it
     rests on and what uses it, milestones marked). Results can be added later.

3. **Propose a mapping and get the user's approval before moving anything.** Ask the user
   first whether the project lives in more than one place (a code repo, data on scratch, a
   paper on Overleaf or in another repo). Migrate one repo; other pieces move into it or
   stay where they are with a pointer: data in `data/MANIFEST.yaml` (`source:`), a paper in
   a line in `PROJECT.md`. Before bringing in another git repo with its history, ask how.

   The mapping says which existing directories become `tasks/<id>/`; what goes to `src/`,
   `data/` (plus `data/MANIFEST.yaml`), `paper/`, `citations/`; what stays where it is;
   where each git-ignored file goes. Old context documents and plans move unchanged into
   their task's `subcontext/`; a pitfalls or rules file becomes `rules/`. By default every
   note (meeting notes, correspondence, drafts, working notes, remarks about people) goes to
   `private-docs/`, committed but never exported (soft-private: other files may name them in
   passing, but not link to them). Ask whether any note should be public instead; only the
   ones the user names go to `docs/` or their task. Documentation for readers goes to
   `docs/`, which is published, so nothing private may stay there. Ideas not yet started as
   work may go to `brainstorm/`; what fits nowhere goes to `archive/`. Ask the privacy tier
   of each task (`public`, `soft-private` or `hard-private`; definitions in
   `open-science-project:new-task`, "Privacy tier"). A task whose work is writing notes
   (lecture or reading notes, write-ups, a notes document) is `soft-private` by default,
   like the notes themselves; propose that tier and ask only whether it should be public
   instead. Hard-private material outside a task goes under `hard_private:` in
   `publish/manifest.yaml`. Unless the user declined results (step 2), the mapping also
   lists each task's results: title, kind, the file that shows it, and whether you propose
   it as a milestone (ask when unsure). Write the approved mapping into the migration task's
   `plan.md`, split so that each subtask fits in one session. Then move with `git mv`, so
   history follows the files.

   Moving files breaks references: search the code, scripts, notebooks, configs and job
   scripts for the old paths and imports of moved modules, and fix them. Run the project's
   tests, or one short script, if any; report every reference you could not check.

4. **Write the new documents from what is there**, as far as step 2 chose. If the history
   or roadmap cannot be worked out from the files, write the current state and stop: the
   aim is that the project follows the framework from now on.

   - A node header per task (`opsci task new` for the directory skeleton where it helps,
     with `--privacy` as the user chose; status from the old context documents, else from
     the user: finished work `done`, abandoned routes `failed` or `abandoned`).
   - **The results** approved in step 3, one file each, as `tasks/README.md`, "Results"
     says: `artifacts` and `code` at the moved paths, `depends_on`, external works in
     `citations/used.bib` and `uses:`, literature values taken as given as `kind:
     assumption` results. Record only what the project's files show (`TODO:` for a result
     no file supports); `verification: unverified` unless a provenance record exists.
     `opsci map build` writes the claims graph (`map/claims.md`); fix every warning.
   - **The maps.** Each task's `map.md`: one node per subtask or line of attack that the
     old work shows (subdirectories, old plans, notes), with its status, failed routes
     included. `map/README.md` (always): the project's line of argument and how the tasks
     fit into it, in at most 150 lines.
   - The project `context.md`, `PROJECT.md` (from the project's own descriptions: README,
     proposal, plans; `TODO:` where they say nothing, and ask the user to check it), and
     one line in `log/<YYYY-MM>.md`: `<date> t00-migration — migrated into the
     open-science layout, from commit <sha>.` Do not back-fill logs; git history has them.

5. **Check and report:**

   ```bash
   opsci migrate compare <scratch>/inventory.json ../<project>-migrate   # fails on a lost file
   opsci map build ../<project>-migrate && opsci context check ../<project>-migrate
   ```

   A moved file counts as kept; a file whose content is found nowhere is lost and must be
   recovered before you report. Apply the freshness test to the project context; report the
   mapping, the compare summary, the results and milestones, and every `TODO:` left. Ask
   the user to approve the merge, and whether you merge or they do.

6. **After the merge.** Once the branch is merged into the original checkout, register the
   pane for the migration task's `context.md` there, and offer, each in one question:

   - to copy or move the git-ignored files as the user chose in step 1, showing the
     commands; after a yes, check that every file arrived (`find | wc -l`, `du -s`);
   - to mirror the project to Notion: `opsci notion enable` (the `AGENTS.md` section and
     the hook, also in an existing settings file), commit, then `opsci notion init`. If
     `opsci notion check` does not print `backend=notion`, point to `open-science:onboard`.

   Then set the migration task's `status: done` with a one-sentence summary, run
   `opsci map build`, and commit. Remove the worktree only when the user agrees.

## A completed project

Steps 1 to 3; from step 4 the node headers (every node `done` or `failed`), the results
and task maps as step 2 chose, `map/README.md` and a project `context.md` stating that the
project is complete. No task plans other than the migration task's. Then steps 5 and 6.
