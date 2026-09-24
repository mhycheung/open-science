"""`opsci context check`: line caps on the files agents resume from."""

from __future__ import annotations

from pathlib import Path

# (glob relative to the project root, cap in lines). The same caps are in the plugin's
# PostToolUse hook (plugins/open-science-project/scripts/context_hook.sh); keep the two in step.
CAPS = (
    ("context.md", 200),
    ("tasks/*/context.md", 200),
    ("map/README.md", 150),
    # The brainstorm sub-root has the same layout and the same caps.
    ("brainstorm/context.md", 200),
    ("brainstorm/tasks/*/context.md", 200),
    ("brainstorm/map/README.md", 150),
)


def line_count(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def check(root: Path) -> tuple[list[str], list[str]]:
    """Return (problems, report lines). A problem is a file over its cap."""
    root = Path(root)
    problems, report = [], []
    for pattern, cap in CAPS:
        for p in sorted(root.glob(pattern)):
            n = line_count(p)
            rel = p.relative_to(root).as_posix()
            report.append(f"{rel}: {n}/{cap}")
            if n > cap:
                problems.append(f"{rel}: {n} lines, over its cap of {cap}; prune it "
                                "(move finished material to subcontext/ or the log)")
    return problems, report
