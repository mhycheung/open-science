"""`opsci task new`: create a task directory with its node header, map, results page, log and
(optional) plan."""

from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

import jsonschema
import yaml

from . import nodes, nodetable, results

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
AUTONOMY = ("autonomous", "checkpoints", "collaborative")

CONTEXT_BODY = """\
# {title}

Last updated: {date}

<!-- The state of this task now, edited in place. At most 200 lines. A fresh agent given
AGENTS.md, the project context.md and this file must be able to take the correct next
action. Detail and history go in subcontext/ and log.md. -->

## Goal

{goal}

## Current state

Nothing done yet.

## In flight

Nothing.

## Next step

{next_step}

## Open questions

None.

## Pointers

{pointers}
"""

# The plan template: report "Plans in new-task". Headings and their order are part of the
# format; the skill fills the sections.
PLAN_BODY = """\
# {title}: <one line on what gets built or measured>

## Goal

<2-5 sentences. What "done" means, as a falsifiable criterion.>

## Design

<The chosen approach and why, in a few paragraphs. Rejected alternatives, one line each
(they become dead-end entries if they are later tried and fail).>

## Constraints

<Conventions (units, normalizations), environment, compute (read from the site config,
never written into the plan), frozen files, and the rule ids that apply (R03, R07).>

## Delegation

<Which subtasks run in parallel and are dispatched; everything else is hands-on.>

## Subtask S1: <name> — <tier> — ~<cost> (±2×)

- Output directory: tasks/{id}/S1/
- Delivers: <artifact paths>
- Steps: <outcome + artifact + acceptance criterion each, all numbers and paths inline>
- Gate (only if expensive or irreversible): <the check, the command, the pass bar>
- Fatal if: <conditions specific to this subtask that make continuing wasteful or harmful>

## Escalate only if fatal

<The assumptions that would invalidate the plan if false, and what to do instead.>

## Budget

<Estimate per subtask and a ceiling; measured values are recorded in the task context.>
"""

# The task map: every task has one, even a task with a single step.
MAP_BODY = """\
# Map: {title}

<!-- The internal structure of this task, edited in place by the main agent after every
finished subtask: one node per subtask or line of attack, arrows for what depends on what,
and the status of each (active, done, failed, abandoned). A task with no internal structure
keeps a single node. The project graph is map/graph.md; this map shows what is inside the
task. -->

```mermaid
flowchart LR
  task["{id}"]
```
"""

LOG_README = "# Log: {title}\n\nAppend only, one line per entry, newest last.\n\n{date} — task created.\n"
SUBCONTEXT_README = ("# Subcontext\n\nPer-subtask and per-subagent context documents for this task, "
                     "and old context documents moved here by a migration.\n")

# Default fillers of CONTEXT_BODY.
GOAL_NONE = "<two lines>"
NEXT_STEP_NONE = "<the first concrete action>"
NEXT_STEP_PLAN = "Fill plan.md with the user, then start subtask S1."
POINTERS_NONE = "None."
POINTERS_PLAN = "- Plan: plan.md — the authority for this task; read the current subtask section."


def skeleton_texts() -> list[str]:
    """The text `new_task` writes into every task, with the title, date and id left empty:
    the boilerplate the publish check's private-content comparison ignores."""
    blank = {"title": "", "date": "", "id": ""}
    out = [CONTEXT_BODY.format(**blank, goal=GOAL_NONE, next_step=n, pointers=p)
           for n, p in ((NEXT_STEP_NONE, POINTERS_NONE), (NEXT_STEP_PLAN, POINTERS_PLAN))]
    table = nodetable.render({"id": "", "type": "task"})  # the table's marker comments
    return out + [PLAN_BODY.format(**blank), MAP_BODY.format(**blank), LOG_README.format(**blank),
                  SUBCONTEXT_README, table]


class TaskError(Exception):
    pass


def _header(task_id, title, summary, depends_on, related, supersedes, privacy="public",
            plan_extra=None, short_name=None, verifies=()) -> dict:
    h = {"id": task_id, "title": title}
    if short_name:
        h["short_name"] = short_name
    h.update({"type": "task", "status": "active"})
    for k, v in (("depends_on", depends_on), ("supersedes", supersedes), ("related", related),
                 ("verifies", verifies)):
        if v:
            h[k] = list(v)
    h["privacy"] = privacy
    h["summary"] = summary
    h["verification"] = "unverified"
    if plan_extra:
        h.update(plan_extra)
    return h


def _front(header: dict) -> str:
    return "---\n" + yaml.safe_dump(header, sort_keys=False, allow_unicode=True, width=100) + "---\n"


def new_task(root: Path, task_id: str, title: str, summary: str | None = None,
             depends_on=(), related=(), supersedes=(), plan: bool = False,
             autonomy: str = "autonomous", hold_at=(), goal: str | None = None, privacy: str | None = None,
             date: dt.date | None = None, short_name: str | None = None, verifies=()) -> Path:
    """Create tasks/<id>/ in the project at root. Returns the task directory.

    ``root`` may be a sub-root (``brainstorm/``): the task goes in its ``tasks/``, its edges
    may name any node of the project graph, and its privacy defaults to soft-private
    instead of public.

    With ``verifies`` (the ids of the nodes it checks) the task is a verification task: an
    audit, check or adverse review of finished work. It goes in ``<task>/verifications/<id>/``
    when every node it verifies lies in that one task, else in the ``verifications/``
    directory of the project (of ``brainstorm/`` when every node lies there), and its privacy
    defaults to the strictest privacy of those nodes.

    Refuses: a bad id, an id already used in the project graph, an existing task, a root that
    is not a project, edges to ids that are not nodes, an unknown autonomy level or privacy
    tier, hold points without `checkpoints`.
    """
    root = Path(root)
    graph, prefix = nodes.graph_root(root)
    if not (root / "tasks").is_dir() or not (root / "context.md").is_file():
        raise TaskError(f"'{root}' is not a project root (needs context.md and tasks/)")
    if not ID_RE.fullmatch(task_id):
        raise TaskError(f"task id '{task_id}' must be lower case letters, digits and hyphens")
    if not title.strip() or "\n" in title:
        raise TaskError("title must be one non-empty line")
    if autonomy not in AUTONOMY:
        raise TaskError(f"autonomy must be one of {', '.join(AUTONOMY)}")
    if privacy is not None and privacy not in nodes.PRIVACY_TIERS:
        raise TaskError(f"privacy must be one of {', '.join(nodes.PRIVACY_TIERS)}")
    if hold_at and autonomy != "checkpoints":
        raise TaskError("hold_at is only used with autonomy 'checkpoints'")
    if not verifies and (graph / f"{prefix}tasks/{task_id}").exists():
        raise TaskError(f"tasks/{task_id} already exists")

    res = nodes.scan(graph)
    known = res.by_id()
    if task_id in known:
        raise TaskError(f"id '{task_id}' is already used by {known[task_id].path}")
    missing = [t for t in (*depends_on, *related, *supersedes, *verifies) if t not in known]
    if missing:
        raise TaskError("not nodes in this project: " + ", ".join(missing))
    if verifies:
        targets = [known[t] for t in verifies]
        rel = f"{nodes.verification_home(targets)}/{task_id}"
        if privacy is None:
            default = nodes.default_privacy(graph)
            privacy = nodes.strictest(t.get("privacy", default) for t in targets)
    else:
        rel = f"{prefix}tasks/{task_id}"
    if privacy is None:
        privacy = "soft-private" if prefix else "public"
    tdir = graph / rel
    if tdir.exists():
        raise TaskError(f"{rel} already exists")

    date = date or dt.date.today()
    summary = summary or f"TODO: one sentence on what {task_id} established, or why it failed."
    header = _header(task_id, title, summary, depends_on, related, supersedes, privacy,
                     short_name=short_name, verifies=verifies)
    validator = jsonschema.Draft202012Validator(nodes.load_schema())
    errs = [e.message for e in validator.iter_errors(header)]
    if errs:
        raise TaskError("header invalid: " + "; ".join(errs))

    pointers = POINTERS_PLAN if plan else POINTERS_NONE
    next_step = NEXT_STEP_PLAN if plan else NEXT_STEP_NONE
    made = next(p for p in (tdir, *tdir.parents) if p.parent.exists())  # removed on failure
    tdir.mkdir(parents=True)
    try:
        (tdir / "context.md").write_text(nodetable.refresh(_front(header) + "\n" + CONTEXT_BODY.format(
            title=title, date=date.isoformat(), goal=goal or GOAL_NONE,
            next_step=next_step, pointers=pointers)), encoding="utf-8")
        (tdir / "map.md").write_text(MAP_BODY.format(title=title, id=task_id), encoding="utf-8")
        (tdir / "results").mkdir()
        node = nodes.Node(f"{rel}/context.md", header)
        (tdir / "results" / "README.md").write_text(results.render_task_results(node, [node]), encoding="utf-8")
        (tdir / "log.md").write_text(LOG_README.format(title=title, date=date.isoformat()), encoding="utf-8")
        (tdir / "subcontext").mkdir()
        (tdir / "subcontext" / "README.md").write_text(SUBCONTEXT_README, encoding="utf-8")
        if plan:
            ph = _header(task_id, title, summary, depends_on, related, supersedes, privacy,
                         {"autonomy": autonomy, "hold_at": list(hold_at)}, short_name=short_name,
                         verifies=verifies)
            (tdir / "plan.md").write_text(
                nodetable.refresh(_front(ph) + "\n" + PLAN_BODY.format(title=title, id=task_id)),
                encoding="utf-8")

        after = nodes.scan(graph)
        mine = [str(p) for p in after.errors if p.path.startswith(f"{rel}/")]
        if mine:
            raise TaskError("the new task fails the map scan: " + "; ".join(mine))
    except BaseException:
        shutil.rmtree(made, ignore_errors=True)  # leave nothing half-made
        raise
    return tdir
