"""`opsci publish`: export the public part of a project, check it, push it, keep both sides equal.

The export is a snapshot of one private commit, filtered by ``publish/manifest.yaml``. It is
copied into a checkout of the public repo and committed there, so the private history never
reaches the public repo. ``publish/LAST_PUBLISHED`` records the private and public commits of
the last publish; the review diff, the consistency check and ``pull-public`` start from it.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import io
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from . import leakscan, mapbuild, nodes, results, secretscan

MANIFEST = "publish/manifest.yaml"
LAST_PUBLISHED = "publish/LAST_PUBLISHED"
REPORT_DIR = "publish/reports"
CHECKOUT = ".opsci/public"  # the private repo's checkout of the public repo (git-ignored)
# Never exported, whatever the manifest says.
ALWAYS_NEVER = ("publish", "lit_cache", "data", "config/site.local.yaml", ".opsci", ".env",
                "messages")
# Public-repo infrastructure (site workflow, issue templates). Not part of the export, kept
# on the public side, ignored by the consistency check and by pull-public.
PUBLIC_ONLY = (".github",)
# Markdown files that need no `status` header by default. The manifest's `status_exempt`
# replaces this list.
STATUS_EXEMPT = ("README.md", "**/README.md", "AGENTS.md", "CLAUDE.md", "PROJECT.md", "context.md",
                 "log/**", "map/**", "citations/**", "rules/**", "tasks/*/log.md", "tasks/*/map.md",
                 "tasks/*/subcontext/**", "docs/**", "brainstorm/context.md", "brainstorm/log/**",
                 "brainstorm/map/**", "brainstorm/tasks/*/log.md",
                 "brainstorm/tasks/*/map.md", "brainstorm/tasks/*/subcontext/**",
                 "verifications/*/log.md", "verifications/*/map.md", "verifications/*/subcontext/**",
                 "brainstorm/verifications/*/log.md", "brainstorm/verifications/*/map.md",
                 "brainstorm/verifications/*/subcontext/**")
MANIFEST_KEYS = {"policy", "include", "never", "hard_private", "status_exempt", "public_repo"}
POLICY_KEYS = {"default_privacy", "collaborators_agreed"}
PRIVACY_TIERS = nodes.PRIVACY_TIERS
# How unpublished nodes appear in the published map: groups that replace several nodes with
# one less specific node, and new titles and summaries for single nodes. Written by the
# publish skill, approved by the user; never exported (it is under publish/).
MAP_OVERRIDES = "publish/map_overrides.yaml"
OVERRIDE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
# Redaction marker, written in the private file; the export replaces the span with
# "[redacted (<reason>)]".
REDACT_RE = re.compile(r"<!--\s*redact:(.*?)-->(.*?)<!--\s*/redact\s*-->", re.S)
REDACT_LEFT_RE = re.compile(r"<!--\s*/?redact\b")
# Copyright check.
PUBLISHER_SUFFIXES = (".pdf", ".epub", ".djvu")
MAX_QUOTE_WORDS = 150
SHARED_RUN_WORDS = 40
# Private-content check: a run of this many words shared with a non-exported file refuses.
PRIVATE_RUN_WORDS = 12
# Template and task-skeleton text is recognised in runs of this many words.
BOILERPLATE_WORDS = 5
# A non-exported node title of at least this many words refuses when it appears in the export.
PRIVATE_TITLE_WORDS = 3
# Soft-private mentions listed by name in the report notes (the rest are counted).
SOFT_NOTES_SHOWN = 10
# Directories kept private as a whole unless the manifest exports something inside them.
PRIVATE_DIRS = ("brainstorm", "private-docs")
# Files the template puts in those directories; naming them discloses nothing.
PRIVATE_SKELETON = ("README.md", "context.md")
# Prose compared for shared runs (code and bibliographies repeat legitimately).
PROSE_SUFFIXES = (".md", ".tex", ".txt", ".html", ".htm", ".rst")
# A commit carrying one of these lines was made by an agent (it cannot set human-verified).
AGENT_TRAILER_RE = re.compile(
    r"^(?:Claude-Session:|Agent:|Co-Authored-By:.*(?:Claude|anthropic\.com)|.*Generated with \[Claude Code\])",
    re.IGNORECASE | re.MULTILINE)


class PublishError(Exception):
    pass


@dataclass
class Problem:
    check: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.path}: {self.message}"


@dataclass
class Manifest:
    include: list[dict]
    never: list[str]
    status_exempt: tuple[str, ...]
    default_privacy: str
    collaborators_agreed: bool
    public_repo: str | None
    hard_private: list[str] = field(default_factory=list)


@dataclass
class Export:
    commit: str
    files: list[str]  # exported paths, sorted
    excluded: dict[str, str]  # path -> reason, for the report
    export_id: str
    tree: Path  # directory holding the exported files
    snapshot: Path  # directory holding the whole commit
    nodes: list = field(default_factory=list)  # node headers of the exported files
    rebuilt_map: list = field(default_factory=list)  # map files rebuilt for the export
    map_nodes: list = field(default_factory=list)  # nodes in the rebuilt map: all but hard-private
    map_private: list = field(default_factory=list)  # how each unpublished node appears in it
    hard: set = field(default_factory=set)  # snapshot paths that are hard-private
    redacted: dict = field(default_factory=dict)  # exported path -> number of redacted spans
    redaction_problems: list = field(default_factory=list)


# --------------------------------------------------------------------------- git helpers

def _git(cwd: Path, *args: str, check: bool = True, input: bytes | None = None,
         text: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=text and input is None,
                       input=input)
    if check and r.returncode != 0:
        err = r.stderr if isinstance(r.stderr, str) else r.stderr.decode(errors="replace")
        raise PublishError(f"git {' '.join(args)} failed: {err.strip()}")
    return r


def resolve(root: Path, rev: str) -> str:
    r = _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
    if r.returncode != 0:
        raise PublishError(f"'{rev}' is not a commit in {root.name}")
    return r.stdout.strip()


def snapshot(root: Path, commit: str, dest: Path) -> None:
    """Write the tree of ``commit`` into ``dest`` (committed content only)."""
    data = _git(root, "archive", "--format=tar", commit, text=False).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="tar")


# --------------------------------------------------------------------------- manifest

def load_manifest(root: Path) -> Manifest:
    path = Path(root) / MANIFEST
    if not path.is_file():
        raise PublishError(f"{MANIFEST} not found")
    try:
        m = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PublishError(f"{MANIFEST}: not valid YAML: {exc}") from exc
    if not isinstance(m, dict):
        raise PublishError(f"{MANIFEST}: must be a mapping")
    unknown = set(m) - MANIFEST_KEYS
    if unknown:
        raise PublishError(f"{MANIFEST}: unknown key(s) {sorted(unknown)}")
    policy = m.get("policy") or {}
    if "embargo_default" in policy:
        raise PublishError(f"{MANIFEST}: policy.embargo_default was replaced by policy.default_privacy "
                           f"({' | '.join(PRIVACY_TIERS)}); see the 0.3.0 project migration in CHANGELOG.md")
    if set(policy) - POLICY_KEYS:
        raise PublishError(f"{MANIFEST}: unknown policy key(s) {sorted(set(policy) - POLICY_KEYS)}")
    default = str(policy.get("default_privacy", "public"))
    if default not in PRIVACY_TIERS:
        raise PublishError(f"{MANIFEST}: policy.default_privacy must be one of {', '.join(PRIVACY_TIERS)}")
    include = m.get("include") or []
    if not isinstance(include, list) or not all(isinstance(e, dict) and "path" in e for e in include):
        raise PublishError(f"{MANIFEST}: include must be a list of {{path: ..., type: ...}} entries")
    lists = {}
    for key in ("never", "hard_private", "status_exempt"):
        val = m.get(key)
        if val is not None and not (isinstance(val, list) and all(isinstance(v, str) for v in val)):
            raise PublishError(f"{MANIFEST}: {key} must be a list of paths or globs")
        lists[key] = val
    return Manifest(
        include=include,
        never=lists["never"] or [],
        status_exempt=tuple(lists["status_exempt"]) if lists["status_exempt"] is not None else STATUS_EXEMPT,
        default_privacy=default,
        collaborators_agreed=policy.get("collaborators_agreed") is True,
        public_repo=m.get("public_repo"),
        hard_private=lists["hard_private"] or [],
    )


def _matches(path: str, pattern: str) -> bool:
    pattern = pattern.rstrip("/")
    return (path == pattern or path.startswith(pattern + "/") or fnmatch.fnmatchcase(path, pattern)
            or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])))


# --------------------------------------------------------------------------- export

def _privacy(header: dict, man: Manifest) -> str:
    return str(header.get("privacy", man.default_privacy))




def select(snap: Path, man: Manifest) -> tuple[list[str], dict[str, str], list, set]:
    """(exported paths, excluded path -> reason, exported nodes, hard-private paths) for a
    commit snapshot. A file is hard-private if its task, its node, a `node.yaml` above it, or
    the manifest's `hard_private` list says so; every other excluded file is soft-private."""
    by_path = {n.path: n for n in all_nodes(snap)}
    # plan.md repeats its task's header; the task's context.md decides for the directory.
    task_tier = {}
    for glob in nodes.TASK_ROOT_GLOBS:
        for d in sorted(snap.glob(f"{glob}/*/")):
            if d.is_dir():
                rel = d.relative_to(snap).as_posix()
                ctx = by_path.get(f"{rel}/context.md")
                # A task with no valid header is treated as hard-private: nothing is known of it.
                task_tier[rel] = _privacy(ctx.header, man) if ctx else "hard-private"
    dir_nodes = {str(PurePosixPath(p).parent): n for p, n in by_path.items() if p.endswith("node.yaml")}

    def tier(f: str) -> tuple[str, str]:
        """(tier, what decided it) of snapshot path f."""
        # A verification task inside another task: the stricter of the two decides.
        tdirs = nodes.task_dirs(f)
        tdir = tdirs[-1] if tdirs else None
        tiers = [(task_tier.get(d, "public"), d) for d in tdirs]
        strict = min(tiers, key=lambda t: nodes.PRIVACY_ORDER[t[0]], default=("public", None))
        if strict[0] != "public":
            return strict[0], f"task {strict[1].rsplit('/', 1)[1]}"
        node = by_path.get(f)
        if node is not None and _privacy(node.header, man) != "public" and not (
                tdir is not None and f in (f"{tdir}/context.md", f"{tdir}/plan.md")):
            return _privacy(node.header, man), f"node {node.id}"
        parent = PurePosixPath(f).parent
        while True:
            dn = dir_nodes.get(str(parent))
            if dn is not None and _privacy(dn.header, man) != "public":
                return _privacy(dn.header, man), f"node {dn.id} (node.yaml)"
            if str(parent) in (".", ""):
                break
            parent = parent.parent
        if any(_matches(f, h) for h in man.hard_private):
            return "hard-private", "listed under hard_private"
        return "public", ""

    files = sorted(p.relative_to(snap).as_posix() for p in snap.rglob("*")
                   if p.is_file() or p.is_symlink())
    out, excluded, hard = [], {}, set()
    for f in files:
        if any(_matches(f, n) for n in ALWAYS_NEVER):
            continue  # never listed in the report either: these are private by construction
        t, why = tier(f)
        if t == "hard-private":
            hard.add(f)
        if not any(_matches(f, str(e["path"])) for e in man.include):
            excluded[f] = "not in the manifest"
        elif any(_matches(f, n) for n in man.never):
            excluded[f] = "listed under never"
        elif t != "public":
            excluded[f] = f"{why}: {t}"
        else:
            out.append(f)
    exported = set(out)
    return out, excluded, [n for n in by_path.values() if n.path in exported], hard


def redact(text: str) -> tuple[str, int, list[str]]:
    """(text with every redaction span replaced, number of spans, problems)."""
    probs = []

    def sub(m):
        reason = " ".join(m.group(1).split())
        if not reason:
            probs.append(f"line {text.count(chr(10), 0, m.start()) + 1}: redaction marker without a reason")
        return f"[redacted ({reason})]"

    out, n = REDACT_RE.subn(sub, text)
    left = REDACT_LEFT_RE.search(out)
    if left:
        probs.append(f"line {out.count(chr(10), 0, left.start()) + 1}: redaction marker that is not "
                     "closed (`<!-- redact: <reason> -->text<!-- /redact -->`)")
    return out, n, probs


def _reparse(node, text: str):
    """The node with its header read from redacted text, or None if the header broke."""
    raw = text if PurePosixPath(node.path).name == "node.yaml" else nodes.read_front_matter(text)[0]
    try:
        header = yaml.safe_load(raw or "")
    except yaml.YAMLError:
        return None
    return nodes.Node(node.path, header) if isinstance(header, dict) and "id" in header else None


def export_id(tree: Path, files: list[str]) -> str:
    h = hashlib.sha256()
    for f in files:
        p = tree / f
        h.update(f.encode() + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode() + b"\n")
    return h.hexdigest()[:16]


def export(root: Path, dest: Path, commit: str = "HEAD") -> Export:
    """Export ``commit`` of the project at ``root`` into ``dest``/tree (dest must be empty)."""
    root = Path(root).resolve()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise PublishError(f"export directory {dest} is not empty")
    sha = resolve(root, commit)
    snap, tree = dest / "snapshot", dest / "tree"
    snap.mkdir()
    tree.mkdir()
    snapshot(root, sha, snap)
    man = load_manifest(snap) if (snap / MANIFEST).is_file() else load_manifest(root)
    files, excluded, exported_nodes, hard = select(snap, man)
    redacted, rprobs = {}, []
    by_path = {n.path: i for i, n in enumerate(exported_nodes)}
    for f in files:
        src = snap / f
        if src.is_symlink():
            raise PublishError(f"{f} is a symbolic link; the export copies files only")
        (tree / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tree / f)
        text = _read(src)
        if text is None or "redact" not in text:
            continue
        new, n, probs = redact(text)
        rprobs += [Problem("redaction", f, p) for p in probs]
        if n:
            (tree / f).write_text(new, encoding="utf-8")
            redacted[f] = n
            if f in by_path:
                node = _reparse(exported_nodes[by_path[f]], new)
                if node is None:
                    rprobs.append(Problem("redaction", f, "the node header is not valid YAML after "
                                          "redaction: put the redacted value in quotes"))
                else:
                    exported_nodes[by_path[f]] = node
    rebuilt, map_nodes = [], []
    if is_template_project(snap):
        # The committed map links every node. The exported copy is rebuilt: it leaves out the
        # hard-private nodes, names the other unexported ones without a link, and shows every
        # header as redacted.
        map_nodes, mprobs = _map_nodes(snap, hard, exported_nodes)
        map_nodes, oprobs, map_private = apply_map_overrides(map_nodes, set(files), snap / MAP_OVERRIDES)
        rprobs += mprobs + oprobs
        texts = mapbuild.outputs(map_nodes, lambda sub: f"{sub}/map/graph.md" in files, set(files),
                                 results.read_bib(snap))
        for rel, text in texts.items():
            if rel in files:
                (tree / rel).write_text(text, encoding="utf-8")
                rebuilt.append(rel)
    return Export(sha, files, excluded, export_id(tree, files), tree, snap, exported_nodes, rebuilt,
                  map_nodes, map_private if rebuilt else [], hard, redacted, rprobs)


def apply_map_overrides(map_nodes: list, exported: set, path: Path) -> tuple[list, list[Problem], list[str]]:
    """The map nodes with ``publish/map_overrides.yaml`` applied, its problems, and one line
    per unpublished node saying how it appears. Only nodes whose files are not exported can
    be grouped or rewritten. A group takes the place of its members: edges to a member point
    to the group, and edges between members disappear."""
    rel = MAP_OVERRIDES
    probs: list[Problem] = []
    ov = {}
    if path.is_file():
        try:
            ov = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            probs.append(Problem("map-overrides", rel, f"not valid YAML: {exc}".splitlines()[0]))
        if not isinstance(ov, dict):
            probs.append(Problem("map-overrides", rel, "must be a mapping with `groups` and `nodes`"))
            ov = {}
    for k in sorted(set(ov) - {"groups", "nodes"}):
        probs.append(Problem("map-overrides", rel, f"unknown key '{k}' (allowed: groups, nodes)"))
    by_id = {n.id: n for n in map_nodes}
    private = {n.id for n in map_nodes if n.path not in exported}

    def text_ok(where, d, key):
        v = d.get(key)
        if not isinstance(v, str) or not v.strip() or "\n" in v.strip():
            probs.append(Problem("map-overrides", rel, f"{where}: `{key}` must be one non-empty line"))
            return False
        return True

    member_of: dict[str, str] = {}
    groups = []
    for i, g in enumerate(ov.get("groups") or []):
        where = f"groups[{i}]"
        if not isinstance(g, dict):
            probs.append(Problem("map-overrides", rel, f"{where}: must be a mapping"))
            continue
        gid = g.get("id")
        bad = [k for k in g if k not in ("id", "title", "summary", "members", "status", "type")]
        if bad:
            probs.append(Problem("map-overrides", rel, f"{where}: unknown key(s) {', '.join(map(str, bad))}"))
        if not isinstance(gid, str) or not OVERRIDE_ID_RE.fullmatch(gid):
            probs.append(Problem("map-overrides", rel, f"{where}: `id` must be lower case letters, digits and hyphens"))
            continue
        if gid in by_id or any(gid == x["id"] for x in groups):
            probs.append(Problem("map-overrides", rel, f"group '{gid}': the id is already used"))
            continue
        ok = text_ok(f"group '{gid}'", g, "title") & text_ok(f"group '{gid}'", g, "summary")
        members = g.get("members")
        if not isinstance(members, list) or len(members) < 2:
            probs.append(Problem("map-overrides", rel, f"group '{gid}': `members` must list at least two "
                                 "node ids (rewrite a single node under `nodes`)"))
            continue
        for m in members:
            if m not in private:
                why = "is published" if m in by_id else "is not a node of the map (hard-private or unknown)"
                probs.append(Problem("map-overrides", rel, f"group '{gid}': member '{m}' {why}"))
                ok = False
            elif m in member_of:
                probs.append(Problem("map-overrides", rel, f"group '{gid}': member '{m}' is already in "
                                     f"group '{member_of[m]}'"))
                ok = False
        if ok:
            for m in members:
                member_of[m] = gid
            groups.append(g)
    rewrites = {}
    nodes_ov = ov.get("nodes") or {}
    if not isinstance(nodes_ov, dict):
        probs.append(Problem("map-overrides", rel, "`nodes` must map node ids to a title and summary"))
        nodes_ov = {}
    for nid, d in nodes_ov.items():
        where = f"nodes.{nid}"
        if nid not in private:
            why = "is published" if nid in by_id else "is not a node of the map (hard-private or unknown)"
            probs.append(Problem("map-overrides", rel, f"{where}: '{nid}' {why}"))
        elif nid in member_of:
            probs.append(Problem("map-overrides", rel, f"{where}: '{nid}' is in group '{member_of[nid]}'"))
        elif not isinstance(d, dict) or set(d) - {"title", "summary"}:
            probs.append(Problem("map-overrides", rel, f"{where}: must have only `title` and `summary`"))
        elif all(text_ok(where, d, k) for k in d):
            rewrites[nid] = d

    def remap(h: dict, self_id: str) -> dict:
        for f in nodes.EDGE_FIELDS:
            if h.get(f):
                h[f] = list(dict.fromkeys(member_of.get(t, t) for t in h[f]))
                h[f] = [t for t in h[f] if t != self_id]
        return h

    out = []
    for n in map_nodes:
        if n.id in member_of:
            continue
        h = remap({**n.header, **rewrites.get(n.id, {})}, n.id)
        out.append(nodes.Node(n.path, h))
    for g in groups:
        ms = [by_id[m] for m in g["members"]]
        statuses = {m.get("status") for m in ms}
        status = g.get("status") or (statuses.pop() if len(statuses) == 1 else
                                     "active" if "active" in statuses else "done")
        h = {"id": g["id"], "title": g["title"], "type": g.get("type") or "task", "status": status,
             "summary": g["summary"], "verification": "unverified"}
        for f in nodes.EDGE_FIELDS:
            h[f] = [t for m in ms for t in m.get(f, []) or []]
        h = remap(h, g["id"])
        subs = {m.path.split("/", 1)[0] for m in ms}
        box = subs.pop() if len(subs) == 1 and next(iter(subs), None) in nodes.SUBROOTS else None
        out.append(nodes.Node(f"{box}/(group {g['id']})" if box else f"(group {g['id']})", h))
    shown = []
    for nid in sorted(private):
        if nid in member_of:
            shown.append(f"`{nid}`: in group `{member_of[nid]}`")
        else:
            n = next(x for x in out if x.id == nid)
            note = " (rewritten)" if nid in rewrites else " (as in its header)"
            shown.append(f"`{nid}`{note}: {n.get('title')} — {n.get('summary')}")
    for g in groups:
        shown.append(f"group `{g['id']}` ({', '.join(g['members'])}): {g['title']} — {g['summary']}")
    return out, probs, shown


def _map_nodes(snap: Path, hard: set, exported_nodes: list) -> tuple[list, list[Problem]]:
    """The nodes of the project graph that are not hard-private, each with its header as the
    export shows it: redacted, whether or not its file is exported."""
    exported = {n.path: n for n in exported_nodes}
    out, probs = [], []
    for n in nodes.scan(snap).nodes:
        if n.path in hard or n.get("privacy") == "hard-private":
            continue
        if n.path in exported:
            out.append(exported[n.path])
            continue
        text = _read(snap / n.path)
        new, k, rp = redact(text) if text and "redact" in text else (text, 0, [])
        probs += [Problem("redaction", n.path, p) for p in rp]
        node = _reparse(n, new) if k else n
        if node is None:
            probs.append(Problem("redaction", n.path, "the node header is not valid YAML after "
                                 "redaction: put the redacted value in quotes"))
            continue
        out.append(node)
    return out, probs


# --------------------------------------------------------------------------- checks

def check_scans(root: Path, ex: Export) -> list[Problem]:
    probs = []
    try:
        pats = leakscan.patterns_for(root)
    except leakscan.LeakScanError as exc:
        return [Problem("leak", "publish/PRIVATE_POLICY.md", str(exc))]
    for h in leakscan.scan_tree(ex.tree, pats, ex.files):
        probs.append(Problem("leak", h.path, f"{h.pattern} [{h.where}, line {h.line_number}]: {h.match!r}"))
    hits, _ = secretscan.scan(ex.tree, ex.files)
    for h in hits:
        probs.append(Problem("secret", h.path, f"{h.pattern} line {h.line_number}: {h.match}"))
    return probs


BIB_KEY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
MD_BRACKET_RE = re.compile(r"\[[^\[\]]*@[^\[\]]*\]")
MD_KEY_RE = re.compile(r"(?<![\w.@])-?@([A-Za-z0-9_][\w:.#$%&+?<>~/-]*)")
TEX_CITE_RE = re.compile(r"\\(?:[a-zA-Z]*cite[a-zA-Z]*|nocite)\*?(?:\[[^\]]*\])*\{([^}]*)\}")
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def cited_keys(text: str, suffix: str) -> list[tuple[int, str]]:
    """(line number, key) for every citation key in a markdown or LaTeX text."""
    out = []
    if suffix == ".tex":
        for m in TEX_CITE_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out += [(line, k.strip()) for k in m.group(1).split(",") if k.strip()]
        return out
    blanked = FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    blanked = INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), blanked)
    for b in MD_BRACKET_RE.finditer(blanked):
        line = blanked.count("\n", 0, b.start()) + 1
        for k in MD_KEY_RE.finditer(b.group(0)):
            out.append((line, k.group(1).rstrip(".:,;")))
    return out


def check_citations(ex: Export) -> list[Problem]:
    keys = set()
    for f in ex.files:
        if f.endswith(".bib"):
            keys |= set(BIB_KEY_RE.findall((ex.tree / f).read_text(encoding="utf-8", errors="replace")))
    probs = []
    for f in ex.files:
        suffix = PurePosixPath(f).suffix
        if suffix not in (".md", ".tex"):
            continue
        text = (ex.tree / f).read_text(encoding="utf-8", errors="replace")
        for line, key in cited_keys(text, suffix):
            if key not in keys:
                probs.append(Problem("citation", f, f"line {line}: key '{key}' is not in any exported .bib file"))
    return probs


def check_status(ex: Export, man: Manifest) -> list[Problem]:
    allowed = nodes.load_schema()["properties"]["status"]["enum"]
    probs = []
    for f in ex.files:
        if not f.endswith(".md") or any(_matches(f, g) for g in man.status_exempt):
            continue
        raw, present = nodes.read_front_matter((ex.tree / f).read_text(encoding="utf-8", errors="replace"))
        try:
            header = yaml.safe_load(raw) if raw else None
        except yaml.YAMLError:
            header = None
        status = header.get("status") if isinstance(header, dict) else None
        if status is None:
            probs.append(Problem("status", f, "no `status:` in a front-matter header"))
        elif status not in allowed:
            probs.append(Problem("status", f, f"status '{status}' is not one of {allowed}"))
    return probs


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _shingles(words: list[str], n: int) -> set[int]:
    return {hash(" ".join(words[i:i + n])) for i in range(len(words) - n + 1)}


def _source_text(path: Path) -> str | None:
    if path.suffix.lower() == ".pdf":
        if not shutil.which("pdftotext"):
            return None
        r = subprocess.run(["pdftotext", "-q", str(path), "-"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None
    if path.suffix.lower() in (".txt", ".md", ".tex", ".html", ".htm", ".xml"):
        return path.read_text(encoding="utf-8", errors="replace")
    return None


def check_copyright(root: Path, ex: Export) -> tuple[list[Problem], list[str]]:
    """(problems, notes). Refuses publisher formats, long quotes, text shared with lit_cache."""
    probs, notes = [], []
    papers = {n.path: n for n in ex.nodes if n.get("type") == "paper"}
    for f in ex.files:
        if f.lower().endswith(PUBLISHER_SUFFIXES) and not mapbuild._covered(f, "paper", papers):
            probs.append(Problem("copyright", f, "a PDF/ebook that is not covered by a `type: paper` node; "
                                 "the project's own papers need a paper node, other people's texts stay in lit_cache/"))
    for f in ex.files:
        if not f.endswith(".md"):
            continue
        lines = (ex.tree / f).read_text(encoding="utf-8", errors="replace").splitlines()
        start, words = None, 0
        for i, line in enumerate(lines + [""], 1):
            if line.lstrip().startswith(">"):
                if start is None:
                    start, words = i, 0
                words += len(line.lstrip().lstrip(">").split())
            else:
                if start is not None and words > MAX_QUOTE_WORDS:
                    probs.append(Problem("copyright", f, f"lines {start}-{i - 1}: a quotation of {words} words "
                                         f"(limit {MAX_QUOTE_WORDS}); quote less and cite"))
                start = None
    lit = Path(root) / "lit_cache"
    sources = {}
    if lit.is_dir():
        for p in sorted(lit.rglob("*")):
            if p.is_file() and p.name != "README.md":
                text = _source_text(p)
                if text is None:
                    notes.append(f"lit_cache/{p.relative_to(lit).as_posix()}: not compared (no text extractor)")
                else:
                    sources[p.relative_to(lit).as_posix()] = _shingles(_words(text), SHARED_RUN_WORDS)
    if sources:
        for f in ex.files:
            if PurePosixPath(f).suffix not in (".md", ".tex", ".txt", ".html"):
                continue
            mine = _shingles(_words((ex.tree / f).read_text(encoding="utf-8", errors="replace")), SHARED_RUN_WORDS)
            for src, sh in sources.items():
                if mine & sh:
                    probs.append(Problem("copyright", f, f"shares a run of {SHARED_RUN_WORDS}+ words with "
                                         f"lit_cache/{src}; quote less and cite"))
    return probs, notes


def agent_commit(root: Path, commit: str, path: str, line: int) -> str | None:
    """The commit that last set ``path`` line ``line`` at ``commit``, if an agent made it."""
    r = _git(root, "blame", "--porcelain", "-L", f"{line},{line}", commit, "--", path, check=False)
    if r.returncode != 0 or not r.stdout:
        return None
    sha = r.stdout.split()[0]
    msg = _git(root, "show", "-s", "--format=%B", sha).stdout
    return sha[:12] if AGENT_TRAILER_RE.search(msg) else None


def check_verification(root: Path, ex: Export) -> tuple[list[Problem], list[str]]:
    """(problems, one report line per exported node with its verification level)."""
    probs, levels = [], []
    exported = set(ex.files)
    for n in sorted(ex.nodes, key=lambda n: n.path):
        level = n.get("verification", "unverified")
        levels.append(f"{n.id} ({n.get('type')}, {n.get('status')}): {level}")
        if level != "unverified" and n.get("evidence") and n.get("evidence") not in exported:
            probs.append(Problem("evidence", n.path, f"{level}, but its evidence '{n.get('evidence')}' is not exported"))
        if level == "human-verified":
            text = (ex.snapshot / n.path).read_text(encoding="utf-8")
            line = next((i for i, l in enumerate(text.splitlines(), 1)
                         if re.match(r"\s*verification\s*:", l)), None)
            sha = agent_commit(root, ex.commit, n.path, line) if line else None
            if sha:
                probs.append(Problem("human-verified", n.path,
                                     f"`verification: human-verified` was set in agent commit {sha}; "
                                     "only the user sets it, in a commit of their own"))
    return probs, levels


def check_map(ex: Export) -> list[Problem]:
    res, stale = mapbuild.build(ex.snapshot, check=True)
    probs = [Problem("map", e.path, e.message) for e in res.errors]
    probs += [Problem("map", s, "out of date; run `opsci map build` and commit") for s in stale]
    return probs


def all_nodes(snap: Path) -> list:
    """Every node of the snapshot: the project scan plus the directories in PRIVATE_DIRS that
    the project scan skips."""
    found = {n.path: n for n in nodes.scan(snap).nodes}
    for sub in PRIVATE_DIRS:
        if sub in nodes.SKIP_DIRS and (snap / sub).is_dir():
            for n in nodes.scan(snap / sub).nodes:
                found.setdefault(f"{sub}/{n.path}", nodes.Node(f"{sub}/{n.path}", n.header))
    return list(found.values())


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _blank_code(text: str) -> str:
    """Text with fenced and inline code replaced by spaces (line numbers kept)."""
    text = FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), text)


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _under(path: str, files) -> bool:
    return any(f == path or f.startswith(path + "/") for f in files)


LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")
REFDEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?", re.M)
HREF_RE = re.compile(r"""\b(?:href|src)\s*=\s*["']([^"']+)["']""", re.I)
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def link_targets(text: str) -> list[tuple[int, str]]:
    """(line, target) for every Markdown inline link, reference definition and HTML href/src
    outside code."""
    blanked = _blank_code(text)
    return sorted((_line(blanked, m.start()), m.group(1))
                  for rx in (LINK_RE, REFDEF_RE, HREF_RE) for m in rx.finditer(blanked))


def _resolve_link(src: str, target: str) -> str | None:
    """The snapshot path a relative link in file ``src`` points to, or None (external,
    anchor-only, or outside the project)."""
    import posixpath
    from urllib.parse import unquote
    if SCHEME_RE.match(target) or target.startswith(("#", "//")):
        return None
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not target:
        return None
    base = "" if target.startswith("/") else posixpath.dirname(src)
    path = posixpath.normpath(posixpath.join(base, target.lstrip("/")))
    return None if path == ".." or path.startswith("../") else path


def _hard_node(n, ex: Export) -> bool:
    return n.path in ex.hard or n.get("privacy") == "hard-private"


def _hard_path(path: str, ex: Export) -> bool:
    """A hard-private file, or a directory whose excluded files are all hard-private."""
    under = [x for x in ex.excluded if x == path or x.startswith(path + "/")]
    return bool(under) and all(x in ex.hard for x in under)


def check_references(ex: Export, every: list) -> list[Problem]:
    """Exported files must not point at what the export leaves out: node headers naming a
    hard-private node (a soft-private one may be named; the public map shows it unlinked), and
    links to any non-exported file or directory of the commit (they would be broken)."""
    exported = set(ex.files)
    public_ids = {n.id for n in every if n.path in exported}
    hard_ids = {n.id for n in every if n.path not in exported and _hard_node(n, ex)} - public_ids
    probs = []
    for n in sorted(ex.nodes, key=lambda n: n.path):
        for fld in nodes.EDGE_FIELDS:
            for ref in n.get(fld, []) or []:
                if ref in hard_ids:
                    probs.append(Problem("references", n.path, f"`{fld}` names node '{ref}', which is "
                                         "hard-private: remove the reference"))
    for f in ex.files:
        if PurePosixPath(f).suffix.lower() not in (".md", ".html", ".htm"):
            continue
        text = _read(ex.tree / f)
        if text is None:
            continue
        for line, target in link_targets(text):
            path = _resolve_link(f, target)
            if path is None or path == "." or _under(path, exported):
                continue
            if (ex.snapshot / path).exists():
                if _hard_path(path, ex):
                    msg = "which is hard-private: remove the link and the mention"
                else:
                    msg = ("which is not exported (soft-private): make it a plain mention in backticks, "
                           "or publish it")
                probs.append(Problem("references", f, f"line {line}: links to '{path}', {msg}"))
    return probs


def _boilerplate(n: int) -> tuple[set[str], str]:
    """Word runs that template text and the task skeleton put in many files, and a note on
    where they came from."""
    from . import tasks, template
    texts = tasks.skeleton_texts()
    for status in ("active", "failed"):  # the fixed text of the generated map files
        blank = [nodes.Node(f"{d}x", {"id": "x", "title": "", "type": "task", "status": status, "summary": ""})
                 for d in ("", *(s + "/" for s in nodes.SUBROOTS))]
        for linked in (None, set()):
            texts += [mapbuild.render_graph(blank, linked), mapbuild.render_dead_ends(blank, linked)]
        texts.append(mapbuild.render_graph([]))
        texts.append(results.render_claims([]))
        with_result = [nodes.Node(f"{d}tasks/x/context.md", {"id": "x", "title": "", "type": "task",
                                                             "status": status, "summary": ""})
                       for d in ("", "brainstorm/")] + [
            nodes.Node("tasks/x/results/y.md", {"id": "y", "title": "", "type": "result", "status": status,
                                                "summary": "", "depends_on": ["x"]})]
        for g in ([], with_result):
            texts += list(results.outputs(g, lambda sub: True, None).values())
    tdir = template.default_template_dir()
    if tdir is not None:
        for p in sorted(tdir.rglob("*")):
            if p.is_file() and p.suffix.lower() in PROSE_SUFFIXES:
                texts.append(re.sub(r"\{\{[A-Z_]+\}\}", " ", _read(p) or ""))
    runs = set()
    for t in texts:
        runs |= _runs(_prose_words(t), n)
    where = f"the task skeleton and {tdir.name}/" if tdir is not None else \
        "the task skeleton only (no framework template/ found beside opsci)"
    return runs, where


def _prose_words(text: str) -> list[str]:
    """Words of a text for the private-content comparison: the front-matter header is left
    out (its ids and titles are checked on their own) and so are numbers (dates)."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")), 0)
        text = "\n".join(lines[end + 1:])
    return [w for w in _words(text) if not w.isdigit()]


def _runs(words: list[str], n: int) -> set[str]:
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _private_paths(ex: Export) -> tuple[set[str], set[str]]:
    """(hard, soft) paths whose mention the private-content check looks for: every
    non-exported task directory, and every non-exported file of a private directory or of
    the hard-private material, apart from the template's own skeleton files."""
    exported = set(ex.files)
    snap_files = sorted(p.relative_to(ex.snapshot).as_posix() for p in ex.snapshot.rglob("*") if p.is_file())
    hard, soft = set(), set()
    for f in snap_files:
        parts = PurePosixPath(f).parts
        if f in exported:
            continue
        name = None
        if parts[0] == "tasks" and len(parts) > 2 and not _under(f"tasks/{parts[1]}", exported):
            name = f"tasks/{parts[1]}"
        elif parts[0] in PRIVATE_DIRS and len(parts) > 1:
            if len(parts) > 3 and parts[1] == "tasks":
                name = "/".join(parts[:3])
            elif parts[-1] not in PRIVATE_SKELETON and not (len(parts) == 3 and parts[1] in ("map", "log")):
                name = f
        elif f in ex.hard:
            name = f
        if name is not None:
            (hard if f in ex.hard else soft).add(name)
    return hard, soft - hard


def check_private_content(ex: Export, every: list) -> tuple[list[Problem], list[str]]:
    """(problems, notes). Exported text must not carry hard-private material: ids and titles of
    hard-private nodes, their paths, and runs of PRIVATE_RUN_WORDS words shared with a
    hard-private file. The same matches against soft-private material are allowed; they are
    listed in the notes for the review. Paraphrase is not caught; that is the review's job."""
    exported = set(ex.files)
    public = [n for n in every if n.path in exported]
    private = [n for n in every if n.path not in exported]
    public_ids, public_titles = {n.id for n in public}, {" ".join(_words(str(n.get("title") or ""))) for n in public}

    def names(group):
        ids = sorted({n.id for n in group} - public_ids)
        word_ids = [i for i in ids if re.search(r"[-_0-9]", i)]  # single words are too common to match
        titles = sorted({t for t in (" ".join(_words(str(n.get("title") or ""))) for n in group)
                         if len(t.split()) >= PRIVATE_TITLE_WORDS} - public_titles)
        return ids, word_ids, titles

    hard_nodes = [n for n in private if _hard_node(n, ex)]
    h_ids, h_word_ids, h_titles = names(hard_nodes)
    s_ids, s_word_ids, s_titles = names([n for n in private if not _hard_node(n, ex)])
    s_word_ids = [i for i in s_word_ids if i not in h_ids]
    s_titles = [t for t in s_titles if t not in h_titles]
    h_paths, s_paths = _private_paths(ex)

    def rx(items):
        return [(i, re.compile(rf"(?<![\w-]){re.escape(i)}(?![\w-])")) for i in sorted(items)]

    probs, soft_hits = [], []
    for f in ex.files:
        text = _read(ex.tree / f)
        if text is None:
            continue
        flat = " " + " ".join(_words(text)) + " "
        for hard, id_items, path_items, titles in ((True, h_word_ids, h_paths, h_titles),
                                                   (False, s_word_ids, s_paths, s_titles)):
            for what, pairs in (("node id", rx(id_items)), ("path", rx(path_items))):
                for name, r in pairs:
                    m = r.search(text)
                    if not m:
                        continue
                    if hard:
                        probs.append(Problem("private-content", f, f"line {_line(text, m.start())}: names the "
                                             f"hard-private {what} '{name}': change or remove the mention, "
                                             "or redact it"))
                    else:
                        soft_hits.append(f"{f}:{_line(text, m.start())} {what} '{name}'")
            for t in titles:
                if f" {t} " in flat:
                    if hard:
                        probs.append(Problem("private-content", f, "contains the title of a hard-private node: "
                                             f"'{t}': change or remove the mention, or redact it"))
                    else:
                        soft_hits.append(f"{f} title '{t}'")

    boiler, where = _boilerplate(BOILERPLATE_WORDS)
    sources = {}
    for f in sorted(ex.excluded):
        if PurePosixPath(f).suffix.lower() in PROSE_SUFFIXES:
            text = _read(ex.snapshot / f)
            if text is not None:
                sources[f] = _runs(_prose_words(text), PRIVATE_RUN_WORDS)
    n_hard_src = sum(1 for f in sources if f in ex.hard)
    for f in ex.files:
        if PurePosixPath(f).suffix.lower() not in PROSE_SUFFIXES or not sources:
            continue
        words = _prose_words(_read(ex.tree / f) or "")
        # A word inside a boilerplate run is template text. A shared run counts only if at
        # least half its words are not: a title or date next to template text is not a leak.
        covered = [False] * len(words)
        for i in range(len(words) - BOILERPLATE_WORDS + 1):
            if " ".join(words[i:i + BOILERPLATE_WORDS]) in boiler:
                covered[i:i + BOILERPLATE_WORDS] = [True] * BOILERPLATE_WORDS
        mine = {" ".join(words[i:i + PRIVATE_RUN_WORDS]) for i in range(len(words) - PRIVATE_RUN_WORDS + 1)
                if covered[i:i + PRIVATE_RUN_WORDS].count(False) * 2 >= PRIVATE_RUN_WORDS}
        for src, runs in sources.items():
            shared = mine & runs
            if not shared:
                continue
            if src in ex.hard:
                probs.append(Problem("private-content", f, f"shares {len(shared)} run(s) of {PRIVATE_RUN_WORDS} "
                                     f"words with hard-private {src}, e.g. \"{min(shared)}\": rewrite it "
                                     "without the private material, or redact it"))
            else:
                soft_hits.append(f"{f} shares {len(shared)} run(s) of {PRIVATE_RUN_WORDS} words with {src}")
    notes = [f"private-content: compared the export against the hard-private material: {len(h_word_ids)} "
             f"node ids ({len(h_ids) - len(h_word_ids)} one-word ids not matched), {len(h_titles)} titles of "
             f"{PRIVATE_TITLE_WORDS}+ words, {len(h_paths)} paths, and {n_hard_src} prose files (runs of "
             f"{PRIVATE_RUN_WORDS} words; boilerplate taken from {where}). "
             "Paraphrase is not detected: the review compares the export with the excluded files."]
    if soft_hits:
        shown = "; ".join(soft_hits[:SOFT_NOTES_SHOWN])
        more = f"; and {len(soft_hits) - SOFT_NOTES_SHOWN} more" if len(soft_hits) > SOFT_NOTES_SHOWN else ""
        notes.append(f"soft-private material is mentioned {len(soft_hits)} time(s) (allowed; the review "
                     f"checks that each is in passing): {shown}{more}")
    return probs, notes


def check_redaction(ex: Export) -> tuple[list[Problem], list[str]]:
    notes = []
    if ex.redacted:
        notes.append(f"redaction: {sum(ex.redacted.values())} span(s) redacted in "
                     + ", ".join(f"{f} ({n})" for f, n in sorted(ex.redacted.items())))
    return list(ex.redaction_problems), notes


def check_policy(man: Manifest) -> list[Problem]:
    if not man.collaborators_agreed:
        return [Problem("policy", MANIFEST, "policy.collaborators_agreed is not true: confirm that co-authors "
                        "agree to publishing shared work (or that there are none), then set it")]
    return []


def is_template_project(snap: Path) -> bool:
    """A project made from the template (component 2, open-science-project). The same test
    as the project plugin's hooks: AGENTS.md and config/framework.yaml at the root."""
    return (snap / "AGENTS.md").is_file() and (snap / "config" / "framework.yaml").is_file()


def run_checks(root: Path, ex: Export) -> tuple[list[Problem], dict]:
    man = load_manifest(ex.snapshot) if (ex.snapshot / MANIFEST).is_file() else load_manifest(root)
    structured = is_template_project(ex.snapshot)
    probs = check_policy(man) + check_scans(root, ex) + check_citations(ex)
    if structured:  # the map and `status:` headers exist only in a template project
        probs += check_map(ex) + check_status(ex, man)
    cp, notes = check_copyright(root, ex)
    if not structured:
        notes = ["not a template project (no AGENTS.md + config/framework.yaml): the map and "
                 "`status:` header checks were skipped"] + notes
    if ex.rebuilt_map:
        notes.append(f"{', '.join(ex.rebuilt_map)} rebuilt from the {len(ex.map_nodes)} nodes that are "
                     f"not hard-private, {len(ex.nodes)} of them published and linked (the committed map "
                     "links every node)")
    from . import layout
    old_layout = layout.outdated_message(root)
    if old_layout:
        notes.insert(0, old_layout)
    vp, levels = check_verification(root, ex)
    every = all_nodes(ex.snapshot)
    pp, pnotes = check_private_content(ex, every)
    rp, rnotes = check_redaction(ex)
    probs += cp + vp + check_references(ex, every) + pp + rp
    notes += rnotes + pnotes
    _, ran = secretscan.scan(ex.tree, [])  # names of the scanners only
    return probs, {"notes": notes, "levels": levels, "secret_scanners": ran}


# --------------------------------------------------------------------------- LAST_PUBLISHED

def read_last(root: Path, commit: str | None = None) -> dict | None:
    """The last publish record ({private, public, date, export_id}) or None if never published."""
    if commit:
        r = _git(root, "show", f"{commit}:{LAST_PUBLISHED}", check=False)
        text = r.stdout if r.returncode == 0 else ""
    else:
        p = Path(root) / LAST_PUBLISHED
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not lines or lines[0].strip() == "none":
        return None
    try:
        rec = yaml.safe_load("\n".join(lines))
    except yaml.YAMLError as exc:
        raise PublishError(f"{LAST_PUBLISHED}: not valid YAML: {exc}") from exc
    if not isinstance(rec, dict) or not rec.get("private") or not rec.get("public"):
        raise PublishError(f"{LAST_PUBLISHED}: needs `private:` and `public:` commits")
    return {k: str(v) for k, v in rec.items()}


LAST_HEADER = ("# Written by `opsci publish push`. The private and public commits of the last publish;\n"
               "# the review diff, the consistency check and pull-public start here.\n")


def write_last(root: Path, private: str, public: str, eid: str) -> None:
    (Path(root) / LAST_PUBLISHED).write_text(
        LAST_HEADER + f"private: {private}\npublic: {public}\ndate: {dt.date.today().isoformat()}\n"
        f"export_id: {eid}\n", encoding="utf-8")


# --------------------------------------------------------------------------- review packet

def review_diff(root: Path, ex: Export, work: Path) -> str:
    """Unified diff of the export since the last publish (the whole export if none)."""
    last = read_last(root, ex.commit)
    old = work / "last-export"
    if last:
        prev = export(root, old, last["private"])
        old_tree = prev.tree
    else:
        old_tree = old / "tree"
        old_tree.mkdir(parents=True)
    pair = work / "pair"
    shutil.copytree(old_tree, pair / "a")
    shutil.copytree(ex.tree, pair / "b")
    r = subprocess.run(["git", "diff", "--no-index", "--no-color", "--stat", "--patch", "--", "a", "b"],
                       capture_output=True, text=True, cwd=pair)
    return r.stdout


def write_report(root: Path, ex: Export, probs: list[Problem], info: dict, diff: str) -> Path:
    """Write the review report and diff under publish/reports/ (never exported). Returns its path."""
    rdir = Path(root) / REPORT_DIR
    rdir.mkdir(parents=True, exist_ok=True)
    stem = f"{dt.date.today().isoformat()}-{ex.commit[:8]}"
    (rdir / f"{stem}.diff").write_text(diff, encoding="utf-8")
    last = read_last(root, ex.commit)
    lines = [
        f"# Publish report {stem}",
        "",
        f"- private commit: `{ex.commit}`",
        f"- export id: `{ex.export_id}` (pass it to `opsci publish push --export-id`)",
        f"- last publish: {('`' + last['private'][:12] + '` on ' + last.get('date', '?')) if last else 'never'}",
        f"- files exported: {len(ex.files)}; excluded by the manifest or headers: {len(ex.excluded)}",
        f"- secrets scanners: {', '.join(info['secret_scanners'])}",
        f"- diff since last publish: `{REPORT_DIR}/{stem}.diff` ({len(diff.splitlines())} lines)",
        "",
        "## Deterministic checks",
        "",
        "PASSED: no problems." if not probs else f"**FAILED: {len(probs)} problem(s).** Fix them in the private repo, commit, and check again.",
        "",
        *[f"- {p}" for p in probs],
        "",
        "## Verification level of each published node",
        "",
        *([f"- {l}" for l in info["levels"]] or ["- (no nodes exported)"]),
        "",
    ]
    if info["notes"]:
        lines += ["## Notes", "", *[f"- {n}" for n in info["notes"]], ""]
    if ex.map_private:
        lines += ["## Unpublished nodes in the public map", "",
                  f"Named without a link. Group or rewrite a node that is too specific in `{MAP_OVERRIDES}`.", "",
                  *[f"- {l}" for l in ex.map_private], ""]
    lines += ["## Excluded files", "", *[f"- `{f}`: {r}" for f, r in sorted(ex.excluded.items())], "",
              "## Review (tone, claims)", "",
              "<!-- Written by the publish skill from the diff, with the rubric in the skill's "
              "reference/review-rubric.md. -->", ""]
    path = rdir / f"{stem}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def check(root: Path, commit: str = "HEAD") -> tuple[int, Path, Export]:
    """Export, run every check, write the report. Returns (problem count, report path, export)."""
    root = Path(root).resolve()
    work = Path(tempfile.mkdtemp(prefix="opsci-publish-"))
    ex = export(root, work / "new", commit)
    probs, info = run_checks(root, ex)
    diff = review_diff(root, ex, work)
    return len(probs), write_report(root, ex, probs, info, diff), ex


# --------------------------------------------------------------------------- public side

def _public_repo(root: Path, override: str | None) -> str:
    repo = override or load_manifest(root).public_repo
    if not repo:
        raise PublishError(f"no public repo: set `public_repo:` in {MANIFEST} or pass --public-repo")
    return repo


def public_checkout(root: Path, repo: str) -> tuple[Path, str | None]:
    """Clone or refresh the private repo's checkout of the public repo. (path, HEAD or None)."""
    co = Path(root) / CHECKOUT
    if not (co / ".git").is_dir():
        co.parent.mkdir(parents=True, exist_ok=True)
        _git(Path(root), "clone", "--quiet", repo, str(co))
    else:
        _git(co, "remote", "set-url", "origin", repo)
        _git(co, "fetch", "--quiet", "origin")
    heads = _git(co, "ls-remote", "--heads", "origin", "main").stdout.strip()
    if heads:
        _git(co, "checkout", "--quiet", "-B", "main", "origin/main")
        return co, heads.split()[0]
    _git(co, "checkout", "--quiet", "--orphan", "main", check=False)
    return co, None


def _tree_files(d: Path) -> dict[str, str]:
    """path -> sha256 for every file under d, outside .git and the public-only paths."""
    out = {}
    for p in d.rglob("*"):
        rel = p.relative_to(d).as_posix()
        if not p.is_file() or rel.startswith(".git/") or any(_matches(rel, x) for x in PUBLIC_ONLY):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _compare(a: dict[str, str], b: dict[str, str]) -> list[str]:
    diffs = [f"only in public: {f}" for f in sorted(set(a) - set(b))]
    diffs += [f"only in export: {f}" for f in sorted(set(b) - set(a))]
    diffs += [f"differs: {f}" for f in sorted(set(a) & set(b)) if a[f] != b[f]]
    return diffs


def _pulled(root: Path, commit: str, public_sha: str) -> bool:
    """Was public commit ``public_sha`` brought into the history of ``commit`` by pull-public?"""
    r = _git(root, "log", "--format=%H", f"--grep=^Public-Commit: {public_sha}", commit, check=False)
    return bool(r.stdout.strip())


def status(root: Path, public_repo: str | None = None, commit: str = "HEAD") -> tuple[list[str], list[str]]:
    """The consistency check. (drift: public changes not in the private repo; pending: private
    changes not yet published). Drift is a failure; pending is information."""
    root = Path(root).resolve()
    repo = _public_repo(root, public_repo)
    co, head = public_checkout(root, repo)
    last = read_last(root, resolve(root, commit))
    work = Path(tempfile.mkdtemp(prefix="opsci-status-"))
    public = _tree_files(co) if head else {}
    drift = []
    if last is None:
        if public:
            drift.append("never published, but the public repo is not empty: " + ", ".join(sorted(public)[:5]))
    elif head is None:
        drift.append(f"{LAST_PUBLISHED} records public commit {last['public'][:12]}, but the public repo has no main branch")
    elif head == last["public"]:
        published = export(root, work / "last", last["private"])
        drift += [f"{d} (public repo differs from the export of {last['private'][:12]})"
                  for d in _compare(public, _tree_files(published.tree))]
    elif not _pulled(root, resolve(root, commit), head):
        drift.append(f"public main is at {head[:12]}, the last publish was {last['public'][:12]}: "
                     "public-side changes are not in the private repo; run `opsci publish pull-public`")
    current = export(root, work / "now", commit)
    pending = _compare(public, _tree_files(current.tree))
    return drift, pending


SITE_WORKFLOW = ".github/workflows/site.yml"


def push(root: Path, export_id_expected: str, public_repo: str | None = None, commit: str = "HEAD",
         message: str | None = None) -> tuple[str, str]:
    """Publish ``commit``: refuses unless its export has the reviewed id and passes every check,
    and the public repo holds nothing the private repo lacks. Returns (private, public) commits."""
    root = Path(root).resolve()
    sha = resolve(root, commit)
    work = Path(tempfile.mkdtemp(prefix="opsci-push-"))
    ex = export(root, work / "new", sha)
    if ex.export_id != export_id_expected:
        raise PublishError(f"export id is {ex.export_id}, not the reviewed {export_id_expected}: the "
                           "export changed since the report; run `opsci publish check` and review again")
    probs, _ = run_checks(root, ex)
    if probs:
        raise PublishError(f"{len(probs)} check(s) fail; run `opsci publish check`")
    drift, _ = status(root, public_repo, sha)
    if drift:
        raise PublishError("the public repo has changes the private repo lacks:\n  " + "\n  ".join(drift))
    co = Path(root) / CHECKOUT
    for p in sorted(co.rglob("*"), reverse=True):
        rel = p.relative_to(co).as_posix()
        if rel == ".git" or rel.startswith(".git/") or any(_matches(rel, x) for x in PUBLIC_ONLY):
            continue
        if p.is_file() or p.is_symlink():
            p.unlink()
        elif p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    for f in ex.files:
        (co / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ex.tree / f, co / f)
    from . import site  # the site workflow is generated at every publish
    wf = co / SITE_WORKFLOW
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(site.workflow(root), encoding="utf-8")
    _git(co, "add", "-A")
    ident = _identity(root)
    msg = message or f"Publish {sha[:12]}"
    if _git(co, "diff", "--cached", "--quiet", check=False).returncode == 0:
        raise PublishError("nothing to publish: the public repo already equals this export")
    _git(co, *ident, "commit", "--quiet", "-m", msg)
    _git(co, "push", "--quiet", "origin", "main")
    public_sha = _git(co, "rev-parse", "HEAD").stdout.strip()
    write_last(root, sha, public_sha, ex.export_id)
    _git(root, *ident, "commit", "--quiet", "-m", f"Record publish of {sha[:12]} (public {public_sha[:12]})",
         "--", LAST_PUBLISHED)
    return sha, public_sha


def _identity(root: Path) -> list[str]:
    """-c options repeating the private repo's commit identity (the public commit is the user's)."""
    out = []
    for key in ("user.name", "user.email"):
        v = _git(root, "config", key, check=False).stdout.strip()
        if v:
            out += ["-c", f"{key}={v}"]
    return out


def pull_public(root: Path, public_repo: str | None = None) -> tuple[str, list[str]]:
    """Bring public-side changes since the last publish into a new private branch.
    Returns (branch name, changed paths). Refuses paths the manifest would not export."""
    root = Path(root).resolve()
    last = read_last(root)
    if last is None:
        raise PublishError("never published: there is nothing to pull")
    co, head = public_checkout(root, _public_repo(root, public_repo))
    if head is None or head == last["public"]:
        raise PublishError("the public repo has no changes since the last publish")
    excl = [f":(exclude){x}" for x in PUBLIC_ONLY]
    names = _git(co, "diff", "--name-only", last["public"], head, "--", ".", *excl).stdout.split()
    if not names:
        raise PublishError("public changes since the last publish touch only public-only files ("
                           + ", ".join(PUBLIC_ONLY) + ")")
    man = load_manifest(root)
    bad = [f for f in names if any(_matches(f, n) for n in ALWAYS_NEVER + tuple(man.never))
           or not any(_matches(f, str(e["path"])) for e in man.include)]
    if bad:
        raise PublishError("public changes touch paths the manifest does not export: " + ", ".join(bad))
    patch = _git(co, "diff", "--binary", last["public"], head, "--", ".", *excl, text=False).stdout
    branch = f"pull-public/{dt.date.today().isoformat()}-{head[:8]}"
    wt = Path(tempfile.mkdtemp(prefix="opsci-pull-")) / "wt"
    _git(root, "worktree", "add", "--quiet", "-b", branch, str(wt), last["private"])
    try:
        r = _git(wt, "apply", "--index", "--3way", "-", input=patch, check=False)
        if r.returncode != 0:
            raise PublishError("the public changes do not apply to the published private commit: "
                               + r.stderr.decode(errors="replace").strip())
        log = _git(co, "log", "--format=- %h %s (%an)", f"{last['public']}..{head}").stdout
        msg = (f"Bring public changes {last['public'][:12]}..{head[:12]} into the private repo\n\n{log}\n"
               f"Public-Commit: {head}\n")
        _git(wt, *_identity(root), "commit", "--quiet", "-m", msg)
    except Exception:
        _git(root, "worktree", "remove", "--force", str(wt), check=False)
        _git(root, "branch", "-D", branch, check=False)
        raise
    _git(root, "worktree", "remove", "--force", str(wt))
    return branch, names
