"""The project layout version, and the warning for a project made from an older template.

`LAYOUT_VERSION` is the layout of the current `template/`. A project records the layout it
was made with (or last migrated to) as `layout_version:` in `config/framework.yaml`; a
project with no such key has layout 1. `CHANGELOG.md` has a "Project migration" section for
every layout bump.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LAYOUT_VERSION = 2

FRAMEWORK_YAML = "config/framework.yaml"
# A top-level integer key. Read with a regular expression, not YAML, so that the template's
# own copy (which still holds `{{...}}` placeholders) can be read too.
_KEY_RE = re.compile(r"^layout_version:\s*(\d+)\s*(?:#.*)?$", re.M)


def project_root(start: Path) -> Path | None:
    """The nearest directory at or above `start` holding AGENTS.md and config/framework.yaml
    (the test the project plugin's hooks use), or None. `start` may be a sub-root such as
    `brainstorm/`."""
    d = Path(start).resolve()
    for cand in (d, *d.parents):
        if (cand / "AGENTS.md").is_file() and (cand / FRAMEWORK_YAML).is_file():
            return cand
    return None


def read_layout_version(text: str) -> int:
    m = _KEY_RE.search(text)
    return int(m.group(1)) if m else 1


def project_layout(start: Path) -> int | None:
    """The layout version of the project containing `start`, or None outside a project."""
    root = project_root(start)
    if root is None:
        return None
    try:
        return read_layout_version((root / FRAMEWORK_YAML).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def outdated_message(start: Path) -> str | None:
    n = project_layout(start)
    if n is None or n >= LAYOUT_VERSION:
        return None
    return (f"this project's layout ({n}) is older than the framework's ({LAYOUT_VERSION}): "
            "run open-science-project:update-from-template")


def warn_if_outdated(start: Path) -> None:
    """Print the warning to stderr if the project is on an older layout. Never raises."""
    try:
        msg = outdated_message(start)
    except Exception:  # a warning must not break the command it rides on
        return
    if msg:
        print(f"warning: {msg}", file=sys.stderr)


def migration_heading(n: int) -> str:
    """The CHANGELOG.md heading of the steps that take a project from layout n-1 to n."""
    return f"### Project migration (layout {n - 1} -> {n})"


def missing_migrations(changelog: str) -> list[int]:
    """Layout versions 2..LAYOUT_VERSION whose migration heading is not in the changelog."""
    lines = {line.strip() for line in changelog.splitlines()}
    return [n for n in range(2, LAYOUT_VERSION + 1) if migration_heading(n) not in lines]
