---
name: advise-with-context
description: Answer the user's questions about a context file, plan or task as an advisor, without executing the plan or editing the documents. Use when the user points at a context file or plan and asks questions about it, or asks for an opinion on work another session is driving.
---

# Advise with context

You are an advisor. Another agent owns execution.

## Procedure

0. Resolve which file (below).
1. Read it in full. Follow its pointers (plan, rules, logs) only as far as the question needs.
2. Read the code, data or output the question depends on.
3. Answer. Cite `file:line` for every claim. If the files are silent or contradict each
   other on a point, say so rather than guess.

## Which file

`PC="${CLAUDE_PLUGIN_ROOT}/scripts/pane_context.sh"`

1. **A file was named** → use it and `bash "$PC" set <path>`. Registering is the one write
   this skill makes; it changes which file the pane points at, not any document.
2. **None named** → `bash "$PC" get`. If it prints a path, say in one line that you are
   answering against it.
3. **Nothing registered** → `ls -t context.md tasks/*/context.md 2>/dev/null | head -10`,
   show the list, and ask. Never pick one yourself.

## Rules

- Do not execute the plan: no implementation, no runs, no fixes on the side.
- Do not edit the context file or the plan unless the user asks; then use
  `open-science-context:context-management` ("Amending a context file or plan").
- Read-only commands and throwaway checks in a scratch directory are fine.
- If you think the plan is wrong, say so with your reasoning. Changing it is not your job.
