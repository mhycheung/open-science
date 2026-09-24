"""Discover and validate the node headers of a project.

A node is a task, result, paper, site page or dataset. Its header is YAML front matter at
the top of a markdown file, or a whole ``node.yaml`` file beside a non-markdown artifact.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path, PurePosixPath

import jsonschema
import yaml

# Directories never scanned for nodes: bulk data, copyrighted full texts, the brainstorm
# sub-root (it has its own map: `opsci map build brainstorm`) and private notes.
SKIP_DIRS = ("data", "lit_cache", ".git", ".pixi", "brainstorm", "private-docs")
# Files map build writes itself.
GENERATED = ("map/graph.md", "map/dead_ends.md")
DEAD_STATUSES = ("failed", "superseded", "abandoned")
PRIVACY_TIERS = ("public", "soft-private", "hard-private")
EDGE_FIELDS = ("depends_on", "supersedes", "related")


@dataclass
class Node:
    path: str  # relative to the project root, POSIX separators
    header: dict

    @property
    def id(self) -> str:
        return self.header["id"]

    def get(self, key, default=None):
        return self.header.get(key, default)


@dataclass
class Problem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class ScanResult:
    nodes: list[Node] = field(default_factory=list)
    errors: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    # ids of headers that failed validation: edges to them are not reported as dangling.
    bad_ids: set[str] = field(default_factory=set)

    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}


def load_schema() -> dict:
    text = resources.files("opsci").joinpath("schemas/node_header.schema.json").read_text()
    return json.loads(text)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True)


def list_files(root: Path) -> list[str]:
    """Files to scan, relative to root.

    Inside a git work tree this is tracked plus untracked-but-not-ignored files, so
    gitignored material is never scanned. Outside git (or when root itself is ignored)
    it is a plain walk.
    """
    files = None
    try:
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
        if inside.returncode == 0 and _git(root, "check-ignore", "-q", ".").returncode != 0:
            out = _git(root, "ls-files", "-co", "--exclude-standard", "-z")
            if out.returncode == 0:
                files = [f for f in out.stdout.decode().split("\0") if f]
    except FileNotFoundError:  # no git executable
        pass
    if files is None:
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", ".pixi")]
            for fn in filenames:
                files.append((Path(dirpath) / fn).relative_to(root).as_posix())
    keep = []
    for f in files:
        parts = PurePosixPath(f).parts
        if not parts or parts[0] in SKIP_DIRS or ".git" in parts or f in GENERATED:
            continue
        keep.append(f)
    return sorted(keep)


def read_front_matter(text: str):
    """Return (yaml_text, has_front_matter). Front matter opens with '---' on line 1."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, False
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[1:i]), True
    return None, True  # opened but never closed


def is_task_context(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) == 3 and parts[0] == "tasks" and parts[2] == "context.md"


def is_task_plan(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) == 3 and parts[0] == "tasks" and parts[2] == "plan.md"


def scan(root: Path) -> ScanResult:
    root = Path(root)
    res = ScanResult()
    validator = jsonschema.Draft202012Validator(load_schema())
    plans: list[Node] = []

    for rel in list_files(root):
        name = PurePosixPath(rel).name
        if not (rel.endswith(".md") or name == "node.yaml"):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            if name == "node.yaml" or is_task_context(rel):
                res.errors.append(Problem(rel, f"cannot read: {exc}"))
            continue
        if name == "node.yaml":
            raw, present = text, True
        else:
            raw, present = read_front_matter(text)
            if present and raw is None:
                res.errors.append(Problem(rel, "front matter opened with '---' but never closed"))
                continue
        if not present:
            if is_task_context(rel):
                res.errors.append(Problem(rel, "task context has no node header"))
            continue
        try:
            header = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            msg = f"header is not valid YAML: {exc}".splitlines()[0]
            # Without an `id:` line the front matter was never meant as a node header
            # (e.g. an archived agent definition), so it is reported but not fatal.
            meant_as_node = name == "node.yaml" or is_task_context(rel) or any(
                line.startswith("id:") for line in raw.splitlines())
            if meant_as_node:
                res.errors.append(Problem(rel, msg))
            else:
                res.warnings.append(Problem(rel, f"{msg} (not a node header: no 'id' line)"))
            continue
        if not isinstance(header, dict) or ("id" not in header and name != "node.yaml"):
            if is_task_context(rel):
                res.errors.append(Problem(rel, "task context has no node header (no 'id' key)"))
            continue
        if "publish" in header:
            # Layout 1 used `publish: yes|no|embargo` (CHANGELOG 0.3.0, Project migration).
            res.errors.append(Problem(rel, "`publish` was replaced by `privacy: public | soft-private | "
                                      "hard-private` (yes -> public; no or embargo -> soft-private or "
                                      "hard-private, the owner decides)"))
            if isinstance(header.get("id"), str):
                res.bad_ids.add(header["id"])
            continue
        bad = sorted(validator.iter_errors(header), key=lambda e: list(e.path))
        for err in bad:
            where = ".".join(str(p) for p in err.path) or "header"
            res.errors.append(Problem(rel, f"{where}: {err.message}"))
        if bad:
            if isinstance(header.get("id"), str):
                res.bad_ids.add(header["id"])
            continue
        node = Node(rel, header)
        if is_task_context(rel):
            task_dir = PurePosixPath(rel).parts[1]
            if node.id != task_dir:
                res.errors.append(Problem(rel, f"id '{node.id}' does not match task directory '{task_dir}'"))
                res.bad_ids.add(node.id)
                continue
        if is_task_plan(rel):
            plans.append(node)  # a plan repeats its task's header; not a separate node
            continue
        ev = header.get("evidence")
        if ev is not None and not (root / ev).exists():
            res.errors.append(Problem(rel, f"evidence: file '{ev}' does not exist"))
            res.bad_ids.add(node.id)
            continue
        res.nodes.append(node)

    _check_graph(res, plans)
    return res


def _check_graph(res: ScanResult, plans: list[Node]) -> None:
    seen: dict[str, str] = {}
    unique = []
    for n in res.nodes:
        if n.id in seen:
            res.errors.append(Problem(n.path, f"duplicate id '{n.id}' (also in {seen[n.id]})"))
            continue
        seen[n.id] = n.path
        unique.append(n)
    res.nodes = unique
    ids = res.by_id()

    for n in res.nodes:
        for f in EDGE_FIELDS:
            for target in n.get(f, []) or []:
                if target not in ids and target not in res.bad_ids:
                    res.errors.append(Problem(n.path, f"{f}: '{target}' is not a node in this project"))

    # depends_on must be acyclic.
    state: dict[str, int] = {}

    def visit(i: str, stack: list[str]) -> None:
        state[i] = 1
        for d in ids[i].get("depends_on", []) or []:
            if d not in ids:
                continue
            if state.get(d) == 1:
                cycle = stack[stack.index(d):] + [d] if d in stack else [i, d]
                res.errors.append(Problem(ids[i].path, "depends_on cycle: " + " -> ".join(cycle)))
            elif state.get(d) is None:
                visit(d, stack + [d])
        state[i] = 2

    for i in sorted(ids):
        if state.get(i) is None:
            visit(i, [i])

    superseded_by = {t for n in res.nodes for t in n.get("supersedes", []) or []}
    for n in res.nodes:
        if n.get("status") == "superseded" and n.id not in superseded_by:
            res.warnings.append(Problem(n.path, f"status is superseded but no node supersedes '{n.id}'"))

    for p in plans:
        ctx = ids.get(p.id)
        task_dir = PurePosixPath(p.path).parts[1]
        if ctx is None or PurePosixPath(ctx.path).parts[1] != task_dir:
            res.errors.append(Problem(p.path, f"plan header id '{p.id}' does not match a task context in tasks/{task_dir}/"))
            continue
        for k in ("status",) + EDGE_FIELDS:
            if (p.get(k) or None) != (ctx.get(k) or None):
                res.warnings.append(Problem(p.path, f"{k} differs from {ctx.path}; context.md is the one the map uses"))
