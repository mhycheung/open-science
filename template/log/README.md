# Project log

One file per month, `log/YYYY-MM.md`, append only. One entry per finished subtask, one line
(at most three) in this form: the date, the task id, a colon, what changed, and a pointer
(commit or file) after `→`:

    - 2026-09-23 t07-likelihood: likelihood fix merged; the $\chi^2$ fit is superseded. → src@a1b2c3d

Write what changed for a reader of the project site, which shows the log as a list grouped
by date: the finding or the change, not housekeeping. Mathematics is in LaTeX (`AGENTS.md`
§2), as everywhere.

Agents do not read the log to resume work; the context files serve that. The log is the
history and the public record. Each task keeps its own detailed `tasks/<id>/log.md`.
