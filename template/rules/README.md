# Rules

One line per rule: its id, the rule, and when it applies. Context files and dispatches cite
rules by id ("R01, R04 apply"); agents open a rule's detail file only when it is cited.
Rules too long for one line get a detail file `rules/R<nn>-<name>.md`. The user and agents
add rules; a rule is added when a real incident or decision paid for it. The rules that must
never be broken are in `AGENTS.md` §0, not here.

| id | rule | applies when |
|---|---|---|
| R01 | State the convention (units, sign, normalisation) before any comparison. | comparing two numbers from different sources |
| R02 | Design every test so that it could refute the project's own thesis. | planning a check |
| R03 | External results are checks, not inputs: derive, cite and test what the project relies on. | using another paper's result |
| R04 | A hard-coded value is declared in one place, cited, and asserted where it is used. | writing constants into code |
| R05 | Every artifact records how it was made: script, parameters, date, inputs. Published results get a `provenance.yaml`. | producing any output |
| R06 | Heavy work goes to the batch system; site settings come from `config/site.local.yaml`. | anything above a few minutes of compute |
| R07 | Reuse caches and existing outputs before recomputing. | starting a computation |
