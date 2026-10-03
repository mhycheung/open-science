# What is science or code, and what is soft-private

The public release holds the science and the code. What is about neither stays in the
private repo: a whole task is made `soft-private` (its directory is not exported; other
documents may name it), and a passage inside a public file is wrapped in an omission marker
(`<!-- omit -->...<!-- /omit -->`; the export drops the span). Both apply: the task's goal
decides the task, and the passages of a public task are checked one by one. These rules are
the user's rulings of 2026-10-02.

## Whole tasks

A task follows its goal.

| goal | privacy |
|---|---|
| a scientific result: a calculation, a fit, a measurement, a derivation, a failed route | `public` |
| the project's analysis code, and code a result depends on | `public` |
| the results website: building it, its layout, viewer tweaks, start-up failures | `soft-private` |
| infrastructure and operations: environments, quota cleanup, SLURM or agent tooling | `soft-private` |
| framework or agent tooling, release preparation | `soft-private` |

- **Site code is code.** The website's source is exported like any other code; only the
  tasks that built or debugged it are soft-private. Science runs that feed the site are
  public.
- **A mixed task stays with its goal.** A science task whose log is half environment or
  SLURM trouble stays public; the infrastructure passages are omitted (below).
- **Decide at creation, check at publish.** `open-science-project:new-task` sets
  `--privacy soft-private` for a task whose goal is in a soft-private row and says so to the
  user. At publish, check each task's `privacy:` against this table and list every task
  whose tier looks wrong for the user in step 9; do not change a tier the user set.

## Passages in public files

| keep | omit |
|---|---|
| a bug or fix that changed, or could have changed, a result, or that explains a method choice | environment and tooling trouble: module loads, harness crashes, quota errors, permission problems |
| failed routes and dead ends: what was tried, why it failed, with numbers | agent mechanics: session jumps, context size, skills lists, watchers, onboarding blocks, `PAUSED` lines, "for a fresh session" |
| a user's decision as a dated ruling in plain words, with its reason | a user's message verbatim, even when it is the only statement of a decision: rewrite it as a ruling first |
| dates of rulings and results; measured costs and wall times ("515 SU", "40 h per unit") | clock times of day, budget ceilings ("cap at 2000 SU"), "SU left" and allocation remarks |
| the technical change a non-technical message caused ("switched to method $Y$: $X$ needs ~40 h per unit") | deadlines and urgency, the user's availability, impatience, mood, personal remarks |
| | reporting and communication: Slack, Notion, "report to the user", permission or authorization grants |
| | details of anyone's network, machine, account or setup (a DNS filter on one viewer's network) |

- **Verbatim user messages.** Never publish one. Where a quote is the only record of a
  decision, rewrite it in the private file as a ruling ("ruling 4 (2026-10-02): the window
  starts at $10\,M$, because ..."), then omit or delete the quote. The review flags any quote
  left.
- **People.** List every named person in the export for the user, with the passage, and
  ask whether to name them, describe them without the name, or omit the passage. Never
  decide this yourself. Collaborators' unpublished work is hard-private regardless
  (`AGENTS.md` §6, skill step 4).
- **A sentence that mixes the two** and cannot be split stays, and is listed for the user
  in step 9.

## Finding candidates

Search the export for `Slack`, `Notion`, `tonight`, `ASAP`, `away`, `allocation`, `quota`,
`SU left`, `budget`, `too long`, `User:`, `user said`, `remind`, `jump`, `permission`, clock
times (`[0-9]{1,2}:[0-9]{2}`), quotation marks around a user's words, and people's names; then
read the context files, plans, logs and `subcontext/` documents. Add the markers in the
private files yourself, list each one for the user in step 9, commit, and go back to step 2.
