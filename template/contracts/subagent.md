# Subagent contract

Read this if your dispatch tier says so (`med-effort`, `high-effort`). The `low-effort`,
`literature` and `text` tiers carry their contract in their own definition.

## 1. What to read

Your dispatch spec, and only the context-file sections and rule ids it names. Do not read
`contracts/main.md`, and do not read a context file whole. If you believe you need something
the spec does not name, ask.

## 2. Scope, and when to stop

Do exactly what the spec says; when it is done, stop. Doing the next stage because it seemed
useful is a scope change to report, not a favour. If the task turns into open-ended
debugging (a mechanism you cannot enumerate), stop and report: the main agent does that
hands-on. A clean escalation is a successful outcome.

## 3. Where your output goes

Write only where your dispatch says: small outputs, scripts, plots and logs in
`tasks/<id>/`; large data in `data/<task-id>/`. Never write to the repo root, `src/` (unless
the spec says so, on your own worktree branch), or another task's directory. Never move,
rename or delete existing files; report the problem instead. A new run gets a new name.

## 4. Waiting on long jobs

Judge the expected wait once, right after submitting.
- **Under about 45 minutes:** wait with one blocking command, not repeated status checks.
- **Over 45 minutes, or unknown:** do not wait. End your dispatch in the same turn and end
  your report with
  `SUBMITTED - <job id> - <your subcontext doc> - <how to check it> - <exact next step>`.
  The main agent owns the wait from there.

## 5. Your working document

If your task spans more than one round, keep `tasks/<id>/subcontext/subagent_<slug>.md` (the
path is in your spec). Rewrite it at the end of every round as if a different agent takes
over next: current state, what was ruled out and why, the exact next step. Above 200k tokens
of context, stop at the next clean boundary, bring that document up to date, and end your
report with `PAUSED - <doc path> - <exact next step>`. A finished task ends `DONE`.

## 6. Your report

Your final message is the report; the main agent files it. It contains:
- what was done, with every file touched and the output behind every success claim,
  labelled MEASURED or ESTIMATED;
- which checks ran, including the control case, and their result;
- what is still unverified;
- any deviation from the spec;
- a plot of any result worth seeing: labelled axes, units, what each series is. Plain is
  fine; no refinement rounds.

<!-- opsci:context -->
End every report and every message to the main agent with:
`If you have no context, use the open-science-context:continue-context skill.`
<!-- /opsci:context -->
<!-- opsci:no-context -->
End every report and every message to the main agent with:
`If you have no context, read AGENTS.md, the project context.md and your task's context.md first.`
<!-- /opsci:no-context -->
