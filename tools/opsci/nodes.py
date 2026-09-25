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

# Directories never scanned for nodes: bulk data, copyrighted full texts and private notes.
SKIP_DIRS = ("data", "lit_cache", ".git", ".pixi", "private-docs")
# Sub-roots: directories with the project's layout on a smaller scale. Their nodes are part
# of the project graph, and `opsci map build` also writes a map of each sub-root alone.
SUBROOTS = ("brainstorm",)
# Verification tasks (audits, checks, adverse reviews of finished work) live in a
# `verifications/` directory: inside the task whose work they check, or at the top of the
# project (or sub-root) when they check the work of several tasks.
VERIFICATIONS = "verifications"
# Directories whose subdirectories are tasks, as globs relative to the project root.
TASK_ROOT_GLOBS = tuple(f"{pre}{d}" for pre in ("", *(f"{s}/" for s in SUBROOTS))
                        for d in ("tasks", VERIFICATIONS, f"tasks/*/{VERIFICATIONS}"))
# Files map build writes itself.
GENERATED = ("map/graph.md", "map/dead_ends.md", "map/claims.md", "results/README.md") + tuple(
    f"{s}/map/{f}" for s in SUBROOTS for f in ("graph.md", "dead_ends.md", "claims.md"))
DEAD_STATUSES = ("failed", "superseded", "abandoned")
PRIVACY_TIERS = ("public", "soft-private", "hard-private")
EDGE_FIELDS = ("depends_on", "supersedes", "related", "verifies")
# The strictest privacy tier first.
PRIVACY_ORDER = {"hard-private": 0, "soft-private": 1, "public": 2}


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


def graph_root(root: Path) -> tuple[Path, str]:
    """(project root, prefix of root inside it). A sub-root such as ``brainstorm/`` belongs to
    the graph of the project around it; any other directory is its own project root."""
    root = Path(root)
    here = root.resolve()
    if here.name in SUBROOTS and (here.parent / "context.md").is_file() and (here.parent / "tasks").is_dir():
        return here.parent, here.name + "/"
    return root, ""


def is_task_root(d: str) -> bool:
    """Whether the directory ``d`` (relative to the project root) holds tasks: ``tasks``,
    ``verifications``, ``tasks/<id>/verifications``, and the same under a sub-root."""
    parts = PurePosixPath(d).parts
    if parts and parts[0] in SUBROOTS:
        parts = parts[1:]
    return parts in (("tasks",), (VERIFICATIONS,)) or (
        len(parts) == 3 and parts[0] == "tasks" and parts[2] == VERIFICATIONS)


def task_dirs(path: str) -> list[str]:
    """The task directories holding ``path``, outermost first: ``[tasks/t07]`` for a file of
    task t07, ``[tasks/t07, tasks/t07/verifications/v01]`` for a file of a verification task
    inside it."""
    p = PurePosixPath(path)
    return ["/".join(p.parts[:i + 1]) for i in range(1, len(p.parts) - 1)
            if is_task_root("/".join(p.parts[:i]))]


def task_file(path: str) -> tuple[str, str] | None:
    """(task directory, file name) of a file directly in a task directory (``tasks/<id>/``,
    ``brainstorm/tasks/<id>/``, ``tasks/<id>/verifications/<vid>/``, ...), else None."""
    p = PurePosixPath(path)
    if len(p.parts) >= 3 and is_task_root("/".join(p.parts[:-2])):
        return p.parent.as_posix(), p.name
    return None


def is_verification(n: "Node") -> bool:
    """A verification task: a task that names the nodes it checks in ``verifies``."""
    return n.get("type") == "task" and bool(n.get("verifies"))


def display_type(n: "Node") -> str:
    """The node's type as the graphs show it: ``verification`` for a verification task."""
    return "verification" if is_verification(n) else str(n.get("type"))


def default_privacy(root: Path) -> str:
    """The manifest's `policy.default_privacy`: the tier of a node with no `privacy`."""
    try:
        manifest = yaml.safe_load((Path(root) / "publish" / "manifest.yaml").read_text()) or {}
        tier = (manifest.get("policy") or {}).get("default_privacy", "public")
    except (OSError, yaml.YAMLError, AttributeError):
        return "public"
    return tier if tier in PRIVACY_TIERS else "public"


def strictest(tiers) -> str:
    """The strictest of the privacy tiers ``tiers`` (``public`` if there are none)."""
    return min(tiers, key=PRIVACY_ORDER.__getitem__, default="public")


def is_task_context(path: str) -> bool:
    t = task_file(path)
    return t is not None and t[1] == "context.md"


def is_task_plan(path: str) -> bool:
    t = task_file(path)
    return t is not None and t[1] == "plan.md"


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
                                      "hard-private, the user decides)"))
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
            task_dir = PurePosixPath(rel).parts[-2]
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

    _check_verifications(res, ids)

    superseded_by = {t for n in res.nodes for t in n.get("supersedes", []) or []}
    for n in res.nodes:
        if n.get("status") == "superseded" and n.id not in superseded_by:
            res.warnings.append(Problem(n.path, f"status is superseded but no node supersedes '{n.id}'"))

    for p in plans:
        ctx = ids.get(p.id)
        task_dir = PurePosixPath(p.path).parent.as_posix()
        if ctx is None or PurePosixPath(ctx.path).parent.as_posix() != task_dir:
            res.errors.append(Problem(p.path, f"plan header id '{p.id}' does not match a task context in {task_dir}/"))
            continue
        for k in ("status",) + EDGE_FIELDS:
            if (p.get(k) or None) != (ctx.get(k) or None):
                res.warnings.append(Problem(p.path, f"{k} differs from {ctx.path}; context.md is the one the map uses"))


def verification_home(targets: list[Node]) -> str:
    """Where a verification task of ``targets`` goes: ``<task>/verifications`` when every
    target lies in one task (a verification task inside it counts as that task), else the
    ``verifications`` directory of the sub-root holding every target, else the project's."""
    owners = {(task_dirs(t.path) or [None])[0] for t in targets}
    if len(owners) == 1 and None not in owners:
        return f"{owners.pop()}/{VERIFICATIONS}"
    subs = {t.path.split("/", 1)[0] for t in targets}
    sub = subs.pop() if len(subs) == 1 else None
    return f"{sub}/{VERIFICATIONS}" if sub in SUBROOTS else VERIFICATIONS


def _check_verifications(res: ScanResult, ids: dict[str, Node]) -> None:
    """A verification task is a task context with `verifies`, in a `verifications/`
    directory; every task in such a directory is one. Warn when it is not where
    `verification_home` puts it."""
    for n in res.nodes:
        if not n.get("verifies"):
            if is_task_context(n.path) and PurePosixPath(n.path).parts[-3] == VERIFICATIONS:
                res.errors.append(Problem(n.path, f"a task in a {VERIFICATIONS}/ directory is a "
                                          "verification task: name what it checks in `verifies`"))
            continue
        if n.get("type") != "task" or not is_task_context(n.path):
            res.errors.append(Problem(n.path, "verifies: only a task's context.md has it"))
            continue
        if PurePosixPath(n.path).parts[-3] != VERIFICATIONS:
            res.errors.append(Problem(n.path, f"a verification task (a task with `verifies`) goes in a "
                                      f"{VERIFICATIONS}/ directory"))
            continue
        if n.id in n.get("verifies"):
            res.errors.append(Problem(n.path, "verifies: a task cannot verify itself"))
            continue
        targets = [ids[t] for t in n.get("verifies") if t in ids]
        if len(targets) != len(n.get("verifies")):
            continue  # _check_graph reports the ids that are not nodes
        home = verification_home(targets)
        here = PurePosixPath(n.path).parent.parent.as_posix()
        if here != home:
            res.warnings.append(Problem(n.path, f"a verification of {', '.join(t.id for t in targets)} "
                                        f"belongs in {home}/, not {here}/"))
