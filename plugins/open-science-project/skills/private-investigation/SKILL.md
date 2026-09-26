---
name: private-investigation
description: Record a side investigation in its own private context file under private-docs/investigations/, soft-private by default, so that a public task context can name it without holding the question or the findings. Use when the user invokes this skill, or when they ask a clarifying or side question whose answer needs recorded work (reading code or papers, a computation, a test run) but does not drive the project. A question that can be answered quickly is answered directly, without this skill.
---

# Private investigation

An investigation is a question the user asks on the side: why a plot looks the way it
does, how a library handles a case, whether an old result still holds. Answering it takes
work that has to survive compaction, but the work does not advance the project, and the
user may not want the question or the answer released. It gets its own context file in
`private-docs/`, which is never exported (`publish/manifest.yaml` lists it under `never`).

## When to use it

- **The user invokes this skill:** always make an investigation.
- **A side question that needs recorded work:** make one without asking. "Recorded work"
  means reading several files or sources, running code, or anything that will not fit in one
  short answer and would be lost at a session jump.
- **A quick question** (answerable from what is already in context, or from one short
  lookup): answer it. Do not make a file.
- **Not an investigation:** work the plan calls for belongs in the task
  (`open-science-project:context-files`); a new idea that may become project work is a
  brainstorm task (`open-science-project:new-task`); a planned piece of work is a task.

## Privacy tier

`soft-private` (default): the file is not released, and a public document may name it in
passing, in backticks. Use `hard-private` when the question or the answer concerns what
`AGENTS.md` §6 lists as hard-private (proprietary data, collaborators' unpublished work,
unpublished ideas, private information about people), or when the user asks. Ask only when
the question obviously looks hard-private and the user did not say, with both definitions:

> This investigation looks like it may be hard-private. Which tier should it have?
> - `soft-private` (default): not released, but the task's context may name the file.
> - `hard-private`: must not appear anywhere in the release, not even by name. For example
>   proprietary data, collaborators' unpublished work, private information about people.

For `hard-private`, add the directory under `hard_private:` in `publish/manifest.yaml`, so
that the publish check refuses its path and copies of its text.

## Files

```
private-docs/investigations/
  README.md                      one line per investigation: date, directory, status, task
  <YYYY-MM-DD>-<slug>/
    context.md                   question, state, findings; at most 200 lines
    <working files>              scripts, plots, small outputs
```

Large outputs go in `data/investigations/<YYYY-MM-DD>-<slug>/` (git-ignored). The slug is a
few lower-case words joined by hyphens. For a hard-private investigation it is neutral
(`2026-09-25-side-question`), since the manifest names it.

`context.md` starts with this block and keeps it current:

```markdown
# Investigation: <the question, one line>

- privacy: soft-private | hard-private
- status: open | answered | abandoned
- started: <YYYY-MM-DD>
- asked during: <task id, or "no task">
- pointer in: <path of the public file that names this investigation, or "none">

## Question
<what the user asked, in their words where possible, and what would count as an answer>

## State
<what has been done, what was found (MEASURED / ESTIMATED), what was ruled out, next step>

## Answer
<filled when answered: the answer, the evidence, and what it means for the task if anything>
```

## Procedure

1. **Create the directory and `context.md`** as above, with the question written down before
   any work starts. Add a line to `private-docs/investigations/README.md` (create it if
   missing, with a one-line heading saying what the directory holds).
2. **Point to it from the task, soft-private only.** If the question came up while a task is
   being driven, add one line under the task context's pointers:

   ```markdown
   - Side investigation, private: `private-docs/investigations/<dir>/context.md`. Read only
     if the user asks about it.
   ```

   Keep the pointer neutral. It names the file and when to read it; it does not state the
   question or the findings, and it copies no text from the investigation (the publish
   check `private-content` flags any run of 12 words shared with a private file). Never
   write a markdown link to it: the `references` check refuses a link to a file that is not
   exported. A hard-private investigation gets no pointer in any exported file; record
   "none" under "pointer in", and the `README.md` index is the way back to it.
3. **Do the work.** Save scripts and plots in the investigation directory, not in the task
   directory, since a public task's directory is exported. Update `context.md` after each
   step that produced a result or ruled something out, and before a session jump. The
   context-file hook caps it at 200 lines; move old detail to a `notes.md` beside it.
4. **Answer the user** in the chat, with the evidence. Then write the answer into
   `## Answer`, set `status: answered` (or `abandoned`, with the reason) and update the
   `README.md` line.
5. **If the answer changes the project** (a bug in shared code, a result that no longer
   holds), say so to the user. What the task needs is restated in public form in the task's
   own files; it does not quote or link the investigation. For a hard-private investigation,
   the restatement must not reveal the private material.
6. **Commit** the investigation directory, the index and the task pointer line.

Sources read in full still go in `lit_cache/` and the citation files (`AGENTS.md` §2).
`citations/consulted.md` is soft-private (never exported), but it names what was read: for
a hard-private investigation, list them in the investigation's `context.md` instead.
