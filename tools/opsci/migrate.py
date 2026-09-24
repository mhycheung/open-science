"""`opsci migrate`: prove that moving a project into the framework lost no file.

`inventory` records every file of the project (tracked, plus untracked files git does not
ignore) with a content hash, before anything moves. `compare` runs after the migration: a
file counts as kept if its path still exists or its content now lives at another path (a
`git mv`). A file whose content is found nowhere is lost, and `compare` fails.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


class MigrateError(Exception):
    pass


def _blob_sha(path: Path) -> str:
    """The git blob id of a file, so tracked and untracked files hash the same way."""
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def inventory(root: Path) -> dict[str, str]:
    """{relative path: git blob sha} for the project's files."""
    root = Path(root)
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                        "--exclude-standard"], capture_output=True)
    if r.returncode != 0:
        raise MigrateError(f"'{root}' is not a git work tree (git init and commit it first)")
    out = {}
    for rel in sorted(set(filter(None, r.stdout.decode().split("\0")))):
        p = root / rel
        if p.is_symlink():
            out[rel] = "symlink:" + str(p.readlink())
        elif p.is_file():
            out[rel] = _blob_sha(p)
    return out


def write_inventory(root: Path, dest: Path) -> int:
    inv = inventory(root)
    Path(dest).write_text(json.dumps({"files": inv}, indent=1, sort_keys=True) + "\n")
    return len(inv)


def compare(before: dict[str, str], after: dict[str, str]) -> dict[str, list]:
    """Classify each file of `before`: kept, modified (same path, new content), moved, lost."""
    where = {}
    for rel, sha in after.items():
        where.setdefault(sha, []).append(rel)
    out = {"kept": [], "modified": [], "moved": [], "lost": []}
    for rel, sha in sorted(before.items()):
        if after.get(rel) == sha:
            out["kept"].append(rel)
        elif sha in where:
            out["moved"].append((rel, where[sha][0]))
        elif rel in after:
            out["modified"].append(rel)
        else:
            out["lost"].append(rel)
    return out


def load_inventory(path: Path) -> dict[str, str]:
    try:
        return json.loads(Path(path).read_text())["files"]
    except (OSError, ValueError, KeyError) as exc:
        raise MigrateError(f"cannot read inventory '{path}': {exc}")
