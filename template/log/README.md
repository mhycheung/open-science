# Project log

One file per month, `log/YYYY-MM.md`, append only. One entry per finished subtask, one to
three lines: the date, the task id, what changed, and a pointer (commit or file). Example:

    2026-09-23 t07 — likelihood fix merged (src@a1b2c3d); v1 fit superseded. → tasks/t07/log.md

Agents do not read the log to resume work; the context files serve that. The log is the
history and the public record. Each task keeps its own detailed `tasks/<id>/log.md`.
