# Rules

One line per rule: its id, the rule, and when it applies. Rules too long for one line get
a detail file `rules/R<nn>-<name>.md`.

| id | rule | applies when |
|---|---|---|
| R01 | Run only the tests that the change affects (`pixi run --frozen python -m pytest -q -p no:cacheprovider tests/<file>.py`), not `tests/run_all`. | before committing a code change |
| R02 | Documentation-only changes (README, docs, figures) need no test run. | before committing a docs change |
| R03 | Claude Code and Codex get the same behaviour: same steps, questions and user-facing texts; only agent mechanics differ, written as substitutions. Tests that involve the agent harness cover both. Docs cover both, Claude Code first. Details: `rules/R03-claude-and-codex.md`. | every update or new feature, its tests and its docs |
| R04 | Every change, docs and releases included, goes through a branch in its own worktree (`../open_science-<short-name>`) and a pull request, merged once the `tests` check passes; never commit to `main` or switch the main checkout's branch. Details: `rules/R04-branches-and-prs.md`. | every change |
