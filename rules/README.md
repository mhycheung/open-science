# Rules

One line per rule: its id, the rule, and when it applies. Rules too long for one line get
a detail file `rules/R<nn>-<name>.md`.

| id | rule | applies when |
|---|---|---|
| R01 | Run only the tests that the change affects (`pixi run --frozen python -m pytest -q -p no:cacheprovider tests/<file>.py`), not `tests/run_all`. | before committing a code change |
| R02 | Documentation-only changes (README, docs, figures) need no test run. | before committing a docs change |
