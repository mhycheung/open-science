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
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from . import graphdraw, leakscan, mapbuild, nodes, pdf, results, secretscan

MANIFEST = "publish/manifest.yaml"
LAST_PUBLISHED = "publish/LAST_PUBLISHED"
REPORT_DIR = "publish/reports"
CHECKOUT = ".opsci/public"  # the private repo's checkout of the public repo (git-ignored)
# Never exported, whatever the manifest says. The list of works consulted but not used is
# soft-private: it may be mentioned, not published. Environment files (`.env`, `.env.local`,
# `prod.env`) hold tokens and passwords, in any directory.
ALWAYS_NEVER = ("publish", "lit_cache", "data", "config/site.local.yaml", ".opsci",
                "messages", "citations/consulted.md",
                "**/.env", "**/.env/*", "**/.env.*", "**/*.env")
# Public-repo infrastructure (site workflow, issue templates). Not part of the export, kept
# on the public side, ignored by the consistency check and by pull-public.
PUBLIC_ONLY = (".github",)
# Markdown files that need no `status` header. The manifest's `status_exempt` adds to this list.
STATUS_EXEMPT = ("README.md", "**/README.md", "**/*.caption.md", "AGENTS.md", "CLAUDE.md", "PROJECT.md", "context.md",
                 "ABSTRACT.md", "WRITEUP.md",
                 "log/**", "map/**", "citations/**", "rules/**", "tasks/*/log.md", "tasks/*/map.md",
                 "tasks/*/subcontext/**", "docs/**", "brainstorm/context.md", "brainstorm/log/**",
                 "brainstorm/map/**", "brainstorm/tasks/*/log.md",
                 "brainstorm/tasks/*/map.md", "brainstorm/tasks/*/subcontext/**",
                 "verifications/*/log.md", "verifications/*/map.md", "verifications/*/subcontext/**",
                 "brainstorm/verifications/*/log.md", "brainstorm/verifications/*/map.md",
                 "brainstorm/verifications/*/subcontext/**")
MANIFEST_KEYS = {"policy", "include", "never", "hard_private", "status_exempt", "public_repo", "site_banner", "site_url",
                 "overrides"}
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
# Omission marker, for private housekeeping in a published file (a context file's "Waiting on
# the user" item such as "commit the plots?"): the export drops the span without a trace, and
# drops the lines it covered when they hold nothing else.
OMIT_RE = re.compile(r"[ \t]*<!--\s*omit\s*-->(.*?)<!--\s*/omit\s*-->[ \t]*(\n)?", re.S)
OMIT_LEFT_RE = re.compile(r"<!--\s*/?omit\b")
# Copyright check.
PUBLISHER_SUFFIXES = (".pdf", ".epub", ".djvu")
MAX_QUOTE_WORDS = 150
SHARED_RUN_WORDS = 40
# Findings the user may override in the manifest's `overrides:`: check -> finding kind -> why
# the kind is flagged, in plain words for the user who decides (the report and the publish
# skill show it). Only findings that are often legitimate and name no person, machine, path,
# secret or private material are here. A `leak` found in the built site (check `site`) is
# covered by the override of its pattern. Everything else is fixed at its source.
OVERRIDABLE: dict[str, dict[str, str]] = {
    "leak": dict(leakscan.OVERRIDABLE),
    "copyright": {
        "long-quote": f"A quotation of more than {MAX_QUOTE_WORDS} words may copy more of another "
                      "author's text than fair use allows. A quote that is marked as a quote and "
                      "cited next to it is usually fine.",
        "lit-cache-text": f"The file shares a run of {SHARED_RUN_WORDS} or more words with a source "
                          "in lit_cache/, so it may copy another author's text without saying so. "
                          "Text that is marked as a quote and cited next to it is usually fine.",
    },
}
OVERRIDE_KEYS = {"check", "kind", "paths", "reason", "date"}
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
    r"^(?:Claude-Session:|Codex-Session:|Agent:|Co-Authored-By:.*(?:Claude|anthropic\.com|Codex|openai\.com)|.*Generated with \[(?:Claude Code|Codex)\])",
    re.IGNORECASE | re.MULTILINE)


class PublishError(Exception):
    pass


@dataclass
class Problem:
    check: str
    path: str
    message: str
    kind: str = ""  # the finding's kind, for the overrides: a leak pattern, a copyright kind
    match: str = ""  # the matched text of a leak finding

    @property
    def overridable(self) -> bool:
        check = "leak" if self.check == "site" else self.check
        return self.kind in OVERRIDABLE.get(check, {})

    def __str__(self) -> str:
        return f"[{self.check}] {self.path}: {self.message}"


@dataclass(frozen=True)
class Override:
    """A finding kind the user accepts: an entry of the manifest's `overrides:`."""
    check: str
    kind: str
    reason: str
    date: str
    paths: tuple[str, ...] = ()  # globs; empty: every file

    def covers(self, p: Problem) -> bool:
        return (p.check == self.check and p.kind == self.kind
                and (not self.paths or any(_matches(p.path, g) for g in self.paths)))

    def label(self) -> str:
        where = f", paths {', '.join(self.paths)}" if self.paths else ""
        return f"{self.check} `{self.kind}`{where}: \"{self.reason}\" ({self.date})"


@dataclass
class Manifest:
    include: list[dict]
    never: list[str]
    status_exempt: tuple[str, ...]
    default_privacy: str
    collaborators_agreed: bool
    public_repo: str | None
    hard_private: list[str] = field(default_factory=list)
    site_banner: str = ""  # the project site's banner; "" for none
    site_url: str | None = None  # the project site's URL, when it is not the GitHub Pages URL of public_repo
    overrides: list[Override] = field(default_factory=list)  # finding kinds the user accepts


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
    omitted: dict = field(default_factory=dict)  # exported path -> number of omitted spans
    site_link: str = ""  # the site URL whose line the export added to README.md; "" if none was added


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
    """Write the tree of ``commit`` into ``dest``: the committed bytes of every file, as git
    stores them. Not `git archive`, which applies the `export-subst` attribute (a committed
    `.gitattributes` would expand `$Format:...$` into branch names and commit messages of the
    private history) and `export-ignore`; this reads the tree and its blobs directly, so no
    attribute or filter changes the content."""
    listing = _git(root, "ls-tree", "-r", "-z", "--full-tree", commit, text=False).stdout
    entries = []
    for rec in listing.split(b"\0"):
        if not rec:
            continue
        meta, raw_path = rec.split(b"\t", 1)
        mode, kind, sha = meta.split()
        path = PurePosixPath(os.fsdecode(raw_path))
        if path.is_absolute() or any(part in ("", ".", "..", ".git") for part in path.parts):
            raise PublishError(f"commit {commit[:12]} holds an unsafe path {str(path)!r}")
        entries.append((mode, kind, sha, path))
    blobs = [e for e in entries if e[1] == b"blob"]
    out = _git(root, "cat-file", "--batch", input=b"".join(e[2] + b"\n" for e in blobs), text=False).stdout
    pos = 0
    for mode, _kind, sha, path in blobs:
        nl = out.index(b"\n", pos)
        head = out[pos:nl].split()
        if len(head) != 3 or head[0] != sha:
            raise PublishError(f"git cat-file: unexpected output for {path}")
        size = int(head[2])
        body = out[nl + 1:nl + 1 + size]
        pos = nl + 2 + size
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == b"120000":
            os.symlink(os.fsdecode(body), target)
        else:
            target.write_bytes(body)
            target.chmod(0o755 if mode == b"100755" else 0o644)
    for mode, kind, _sha, path in entries:
        if kind == b"commit":  # a submodule: an empty directory, as `git archive` writes it
            (dest / path).mkdir(parents=True, exist_ok=True)


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
    from .site import DEFAULT_BANNER
    banner = m.get("site_banner", DEFAULT_BANNER)
    if banner is False or banner is None:
        banner = ""
    if not isinstance(banner, str):
        raise PublishError(f"{MANIFEST}: site_banner must be a text, or \"\" (or false) for no banner")
    site_url = m.get("site_url")
    if site_url is not None and not isinstance(site_url, str):
        raise PublishError(f"{MANIFEST}: site_url must be a URL, or \"\" for no site")
    return Manifest(
        overrides=_overrides(m.get("overrides")),
        site_banner=banner.strip(),
        site_url=site_url.strip() if site_url is not None else None,
        include=include,
        never=lists["never"] or [],
        status_exempt=STATUS_EXEMPT + tuple(lists["status_exempt"] or ()),
        default_privacy=default,
        collaborators_agreed=policy.get("collaborators_agreed") is True,
        public_repo=m.get("public_repo"),
        hard_private=lists["hard_private"] or [],
    )


def _overridable_list() -> str:
    return "; ".join(f"{c}: {', '.join(sorted(k))}" for c, k in OVERRIDABLE.items())


def _overrides(val) -> list[Override]:
    """The manifest's `overrides:` entries, checked: each names an overridable check and kind,
    a reason and the date of the user's decision, and optionally the paths it covers."""
    if val is None:
        return []
    if not isinstance(val, list):
        raise PublishError(f"{MANIFEST}: overrides must be a list of {{check, kind, reason, date}} entries")
    out = []
    for i, o in enumerate(val):
        where = f"{MANIFEST}: overrides[{i}]"
        if not isinstance(o, dict):
            raise PublishError(f"{where}: must be a mapping with check, kind, reason and date")
        if set(o) - OVERRIDE_KEYS:
            raise PublishError(f"{where}: unknown key(s) {sorted(set(o) - OVERRIDE_KEYS)} "
                               f"(allowed: {', '.join(sorted(OVERRIDE_KEYS))})")
        check, kind = str(o.get("check", "")), str(o.get("kind", ""))
        if kind not in OVERRIDABLE.get(check, {}):
            raise PublishError(f"{where}: check '{check}', kind '{kind}' cannot be overridden; "
                               f"the overridable kinds are {_overridable_list()}")
        reason = o.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise PublishError(f"{where}: `reason` must say why the user accepts these findings")
        date = o.get("date")
        if isinstance(date, dt.date):
            date = date.isoformat()
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date.strip()):
            raise PublishError(f"{where}: `date` must be the date of the user's decision, YYYY-MM-DD")
        paths = o.get("paths", [])
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not all(isinstance(g, str) and g.strip() for g in paths):
            raise PublishError(f"{where}: `paths` must be a list of paths or globs")
        out.append(Override(check, kind, " ".join(reason.split()), date.strip(), tuple(paths)))
    return out


def overridden_leak_patterns(man: Manifest) -> list[str]:
    """The leak patterns the manifest overrides (for any path)."""
    return sorted({o.kind for o in man.overrides if o.check == "leak"})


def apply_overrides(probs: list[Problem], overrides: list[Override]) -> tuple[list[Problem], list]:
    """(the problems no override covers, [(problem, override)] for the ones the user overrode)."""
    failed, overridden = [], []
    for p in probs:
        o = next((o for o in overrides if o.covers(p)), None)
        if o is None:
            failed.append(p)
        else:
            overridden.append((p, o))
    return failed, overridden


def _matches(path: str, pattern: str) -> bool:
    pattern = pattern.rstrip("/")
    return (path == pattern or path.startswith(pattern + "/") or fnmatch.fnmatchcase(path, pattern)
            or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])))


# --------------------------------------------------------------------------- export

def _privacy(header: dict, man: Manifest) -> str:
    return str(header.get("privacy", man.default_privacy))




def _tier_of(snap: Path, man: Manifest, by_path: dict):
    """A function: snapshot path -> (tier, what decided it). A file is hard-private if its
    task, its node, a `node.yaml` above it, or the manifest's `hard_private` list says so."""
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
    return tier


def _exclusion(f: str, man: Manifest, tier) -> str | None:
    """Why the export leaves out path f (not in the manifest, listed under never, or its
    tier), or None if it is exported."""
    t, why = tier(f)
    if not any(_matches(f, str(e["path"])) for e in man.include):
        return "not in the manifest"
    if any(_matches(f, n) for n in man.never):
        return "listed under never"
    if t != "public":
        return f"{why}: {t}"
    return None


def privacy_badges(root: Path, node_list: list) -> dict[str, str]:
    """Node id -> ``public``, ``soft private`` or ``hard private`` for the nodes in
    ``node_list``, the labels of the committed graph images. Hard private: the export's map
    leaves the node out (``_map_nodes``); soft private: the map names it but the export
    leaves out its files (as ``select`` decides); public: its files are exported. Empty
    without a manifest."""
    try:
        man = load_manifest(root)
    except PublishError:
        return {}
    tier = _tier_of(Path(root), man, {n.path: n for n in all_nodes(Path(root))})

    def badge(n):
        if tier(n.path)[0] == "hard-private" or n.get("privacy") == "hard-private":
            return "hard private"
        if any(_matches(n.path, a) for a in ALWAYS_NEVER) or _exclusion(n.path, man, tier) is not None:
            return "soft private"
        return "public"
    return {n.id: badge(n) for n in node_list}


def select(snap: Path, man: Manifest) -> tuple[list[str], dict[str, str], list, set]:
    """(exported paths, excluded path -> reason, exported nodes, hard-private paths) for a
    commit snapshot. A file is hard-private if its task, its node, a `node.yaml` above it, or
    the manifest's `hard_private` list says so; every other excluded file is soft-private."""
    by_path = {n.path: n for n in all_nodes(snap)}
    tier = _tier_of(snap, man, by_path)

    files = sorted(p.relative_to(snap).as_posix() for p in snap.rglob("*")
                   if p.is_file() or p.is_symlink())
    out, excluded, hard = [], {}, set()
    for f in files:
        if any(_matches(f, n) for n in ALWAYS_NEVER):
            continue  # never listed in the report either: these are private by construction
        if tier(f)[0] == "hard-private":
            hard.add(f)
        why = _exclusion(f, man, tier)
        if why:
            excluded[f] = why
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


def _empty_sections(text: str) -> set[str]:
    """The `## ` headings of the sections of ``text`` that hold no text."""
    parts = re.split(r"(?m)^(## .*)$", text)
    return {parts[i] for i in range(1, len(parts), 2) if not parts[i + 1].strip()}


def omit(text: str) -> tuple[str, int, list[str]]:
    """(text with every omission span removed, number of spans, problems). A span that fills
    its lines removes them; a `## ` section that the omission leaves empty loses its heading."""
    out, pos = [], 0
    spans = list(OMIT_RE.finditer(text))
    for m in spans:
        line_start = text.rfind("\n", 0, m.start()) + 1
        if not text[line_start:m.start()].strip() and m.group(2) is not None:
            out.append(text[pos:line_start])  # whole lines
        else:
            out.append(text[pos:m.start()] + (m.group(2) or ""))
        pos = m.end()
    new = "".join(out) + text[pos:]
    probs = []
    left = OMIT_LEFT_RE.search(new)
    if left:
        probs.append(f"line {new.count(chr(10), 0, left.start()) + 1}: omission marker that is not "
                     "closed (`<!-- omit -->text<!-- /omit -->`)")
    if spans:
        emptied = _empty_sections(new) - _empty_sections(text)
        parts = re.split(r"(?m)^(## .*)$", new)
        new = parts[0] + "".join(parts[i] + parts[i + 1] for i in range(1, len(parts), 2)
                                 if parts[i] not in emptied)
    return new, len(spans), probs


def add_site_link(text: str, url: str) -> str | None:
    """``text`` (a README) with the line `The project site: <url>` under its title, the first
    `# ` heading outside the front matter and code blocks; at the top of the body when it has
    no title. None when the text already links to ``url``."""
    if url.rstrip("/") in text:
        return None
    lines = text.splitlines(keepends=True)
    start = 0
    _, present = nodes.read_front_matter(text)
    if present and lines and lines[0].strip() == "---":
        start = next((i + 1 for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
    at, fence = None, False
    for i in range(start, len(lines)):
        if lines[i].lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence and lines[i].startswith("# "):
            at = i + 1
            break
    line = f"The project site: <{url}>\n"
    if at is None:
        rest = "".join(lines[start:])
        return "".join(lines[:start]) + line + ("\n" + rest if rest.strip() else "")
    head, rest = "".join(lines[:at]), "".join(lines[at:])
    if not head.endswith("\n"):
        head += "\n"
    return head + "\n" + line + ("\n" + rest.lstrip("\n") if rest.strip() else "")


def _reparse(node, text: str):
    """The node with its header read from redacted text, or None if the header broke."""
    raw = text if PurePosixPath(node.path).name == "node.yaml" else nodes.read_front_matter(text)[0]
    try:
        header = yaml.safe_load(raw or "")
    except yaml.YAMLError:
        return None
    return nodes.Node(node.path, header) if isinstance(header, dict) and "id" in header else None


def export_id(tree: Path, files: list[str], overrides: list[Override] = ()) -> str:
    """A hash of every exported path and its content, and of the user's overrides (if any): a
    new override needs a new check and a new approval."""
    h = hashlib.sha256()
    for f in files:
        p = tree / f
        h.update(f.encode() + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode() + b"\n")
    for o in overrides:
        h.update(repr((o.check, o.kind, o.paths, o.reason, o.date)).encode() + b"\n")
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
    redacted, omitted, rprobs = {}, {}, []
    by_path = {n.path: i for i, n in enumerate(exported_nodes)}
    for f in files:
        src = snap / f
        if src.is_symlink():
            raise PublishError(f"{f} is a symbolic link; the export copies files only")
        (tree / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tree / f)
        text = _read(src)
        if text is None or ("redact" not in text and "omit" not in text):
            continue
        new, k, probs = omit(text) if f.endswith(".md") else (text, 0, [])
        rprobs += [Problem("omission", f, p) for p in probs]
        if k:
            omitted[f] = k
        new, n, probs = redact(new)
        rprobs += [Problem("redaction", f, p) for p in probs]
        if n or k:
            (tree / f).write_text(new, encoding="utf-8")
        if n:
            redacted[f] = n
        if n or k:
            if f in by_path:
                node = _reparse(exported_nodes[by_path[f]], new)
                if node is None:
                    rprobs.append(Problem("redaction", f, "the node header is not valid YAML after "
                                          "redaction: put the redacted value in quotes"))
                else:
                    exported_nodes[by_path[f]] = node
    # The public README links to the project site. A README without the link gets it at the
    # export, so that the first publish needs no edit before the site exists.
    url, linked = site_url(man), ""
    if url and "README.md" in files:
        text = _read(tree / "README.md")
        new = add_site_link(text, url) if text is not None else None
        if new is not None:
            (tree / "README.md").write_text(new, encoding="utf-8")
            linked = url
    rebuilt, map_nodes = [], []
    if is_template_project(snap):
        # The committed map links every node. The exported copy is rebuilt: it leaves out the
        # hard-private nodes, names the other unexported ones without a link, and shows every
        # header as redacted.
        map_nodes, mprobs = _map_nodes(snap, hard, exported_nodes)
        map_nodes, oprobs, map_private = apply_map_overrides(map_nodes, set(files), snap / MAP_OVERRIDES)
        rprobs += mprobs + oprobs
        texts = mapbuild.outputs(map_nodes, lambda sub: f"{sub}/map/graph.md" in files, set(files),
                                 results.read_bib(snap), results.read_data_manifest(snap))
        for rel, text in texts.items():
            if rel in files:
                (tree / rel).write_text(text, encoding="utf-8")
                rebuilt.append(rel)
        # The committed graph images show every node, labelled by privacy: each exported one
        # is redrawn without the hard-private nodes, the unexported ones labelled "not
        # published", and one that cannot be redrawn stops the publish.
        unexported = {n.id: "not published" for n in map_nodes if n.path not in set(files)}
        for base, d in mapbuild.drawings(map_nodes, lambda sub: f"{sub}/map/graph.md" in files,
                                         results.read_bib(snap), unexported).items():
            if not any(f"{base}.{x}" in files for x in ("svg", "png", "html")):
                continue
            try:
                graphdraw.render(d, tree / base)
                rebuilt += [f for f in (f"{base}.svg", f"{base}.png", f"{base}.html") if f in files]
            except (graphdraw.DrawError, OSError, subprocess.SubprocessError) as exc:
                rprobs.append(Problem("map", f"{base}.svg", f"cannot redraw the graph image for the export: {exc}"))
    return Export(sha, files, excluded, export_id(tree, files, man.overrides), tree, snap, exported_nodes, rebuilt,
                  map_nodes, map_private if rebuilt else [], hard, redacted, rprobs, omitted, linked)


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
        probs.append(Problem("leak", h.path, f"{h.pattern} [{h.where}, line {h.line_number}]: {h.match!r}",
                             kind=h.pattern, match=h.match))
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
    figures = 0
    for f in ex.files:
        if f.lower().endswith(PUBLISHER_SUFFIXES) and not mapbuild._covered(f, "paper", papers):
            # A one-page PDF is a figure, not someone else's text.
            if f.lower().endswith(".pdf") and pdf.page_count((ex.tree / f).read_bytes()) == 1:
                figures += 1
                continue
            probs.append(Problem("copyright", f, "a PDF/ebook that is not covered by a `type: paper` node; "
                                 "the project's own papers need a paper node, other people's texts stay in lit_cache/"))
    if figures:
        notes.append(f"{figures} one-page PDF(s) outside paper nodes treated as figures")
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
                                         f"(limit {MAX_QUOTE_WORDS}); quote less and cite",
                                         kind="long-quote"))
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
                                         f"lit_cache/{src}; quote less and cite", kind="lit-cache-text"))
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
            lines = verification_lines(text, n.path)
            if not lines:
                probs.append(Problem("human-verified", n.path, "`verification: human-verified`, but the "
                                     "line that sets it was not found, so who set it is unknown"))
                continue
            sha = next((s for s in (agent_commit(root, ex.commit, n.path, i) for i in lines) if s), None)
            if sha:
                probs.append(Problem("human-verified", n.path,
                                     f"`verification: human-verified` was set in agent commit {sha}; "
                                     "only the user sets it, in a commit of their own"))
    return probs, levels


def verification_lines(text: str, path: str) -> list[int]:
    """The line numbers (1-based) of the `verification` key and its value in a node header: in a
    markdown file's front matter or in a `node.yaml`. Found from the parsed YAML, so a quoted key
    (`"verification":`) or a value on the next line is found too."""
    if PurePosixPath(path).name == "node.yaml":
        raw, offset = text, 0
    else:
        raw, present = nodes.read_front_matter(text)
        if not present or raw is None:
            return []
        offset = 1  # the opening '---'
    try:
        doc = yaml.compose(raw)
    except yaml.YAMLError:
        return []
    found = set()
    if isinstance(doc, yaml.MappingNode):
        for key, value in doc.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "verification":
                end = value.end_mark.line - (1 if value.end_mark.column == 0 else 0)
                found.update(range(key.start_mark.line, max(end, key.start_mark.line) + 1))
    return sorted(offset + i + 1 for i in found)


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
    if ex.omitted:
        notes.append(f"omission: {sum(ex.omitted.values())} span(s) left out of "
                     + ", ".join(f"{f} ({n})" for f, n in sorted(ex.omitted.items())))
    return list(ex.redaction_problems), notes


def check_policy(man: Manifest) -> list[Problem]:
    if not man.collaborators_agreed:
        return [Problem("policy", MANIFEST, "policy.collaborators_agreed is not true: confirm that co-authors "
                        "agree to publishing shared work (or that there are none), then set it")]
    return []


def pages_url(repo: str) -> str | None:
    """The GitHub Pages URL of a github.com repo (an https or ssh remote), or None."""
    m = re.fullmatch(r"(?:https://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|git@)github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?",
                     repo.strip())
    if not m:
        return None
    owner, name = m.group(1).lower(), m.group(2)
    return f"https://{name.lower()}/" if name.lower() == f"{owner}.github.io" else f"https://{owner}.github.io/{name}/"


def repo_web_url(repo: str) -> str | None:
    """The web URL of a public repo: ``https://github.com/<owner>/<repo>`` for a github.com
    remote (https or ssh), the URL itself (less ``.git``) for another http(s) remote, None for
    a local path."""
    repo = repo.strip()
    m = re.fullmatch(r"(?:https://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|git@)github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?",
                     repo)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    if re.match(r"https?://", repo):
        return re.sub(r"(?:\.git)?/?$", "", repo)
    return None


def site_url(man: Manifest) -> str | None:
    """The URL of the project site: `site_url` in the manifest ("" for none), else the GitHub
    Pages URL of `public_repo`, else None."""
    if man.site_url is not None:
        return man.site_url or None
    return pages_url(str(man.public_repo)) if man.public_repo else None


def check_site_link(ex: Export, man: Manifest) -> list[Problem]:
    """The published README links to the project site, so that a reader of the public repo finds
    it. The export adds the link when the README lacks it (`add_site_link`); this check catches
    a README the export could not change (not UTF-8 text)."""
    url = site_url(man)
    if not url or "README.md" not in ex.files:
        return []
    if url.rstrip("/") in (_read(ex.tree / "README.md") or ""):
        return []
    return [Problem("site-link", "README.md", f"the README does not link to the project site: add "
                    f"`The project site: <{url}>` under the title")]


def is_template_project(snap: Path) -> bool:
    """A project made from the template (component 2, open-science-project). The same test
    as the project plugin's hooks: AGENTS.md and config/framework.yaml at the root."""
    return (snap / "AGENTS.md").is_file() and (snap / "config" / "framework.yaml").is_file()


def run_checks(root: Path, ex: Export) -> tuple[list[Problem], dict]:
    """(the problems that fail the publish, info). The findings the user overrode are not among
    the problems; info["overridden"] lists each with its override."""
    man = load_manifest(ex.snapshot) if (ex.snapshot / MANIFEST).is_file() else load_manifest(root)
    structured = is_template_project(ex.snapshot)
    probs = check_policy(man) + check_site_link(ex, man) + check_scans(root, ex) + check_citations(ex)
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
    if ex.site_link:
        rnotes.append(f"site link: the export added `The project site: <{ex.site_link}>` under the title "
                      "of README.md (the private README.md does not link to the site; add the line there "
                      "to keep it in the private repo too)")
    probs += cp + vp + check_references(ex, every) + pp + rp
    notes += rnotes + pnotes
    _, ran = secretscan.scan(ex.tree, [])  # names of the scanners only
    # The findings the user overrode leave the failures; the report lists each of them.
    probs, overridden = apply_overrides(probs, man.overrides)
    return probs, {"notes": notes, "levels": levels, "secret_scanners": ran, "overridden": overridden,
                   "overrides": man.overrides}


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
                       capture_output=True, cwd=pair)
    # An exported file need not be UTF-8 text; git marks real binaries, the rest is shown as is.
    return r.stdout.decode("utf-8", errors="replace")


def _kind_key(p: Problem) -> tuple[str, str]:
    return ("leak" if p.check == "site" else p.check), p.kind


def _verdict(probs: list[Problem], overridden: list) -> str:
    over = (f" {len(overridden)} finding(s) overridden by the user: see \"Overridden by the user\"."
            if overridden else "")
    if not probs:
        return "PASSED: no problems." + over
    can = (" The user may override some of them instead: see \"Findings the user may override\"."
           if any(p.overridable for p in probs) else "")
    return (f"**FAILED: {len(probs)} problem(s).** Fix them in the private repo, commit, and check again."
            + can + over)


def _overridable_section(probs: list[Problem]) -> list[str]:
    """The failed findings of a kind the user may override, grouped by kind, with why each kind
    is flagged."""
    groups: dict[tuple[str, str], list[Problem]] = {}
    for p in probs:
        if p.overridable:
            groups.setdefault(_kind_key(p), []).append(p)
    if not groups:
        return []
    n = sum(len(g) for g in groups.values())
    out = ["### Findings the user may override", "",
           f"{n} of the problems above are of a kind the user may accept instead of fixing. Ask the "
           f"user about each kind; only the user's answer adds an entry to `overrides:` in {MANIFEST} "
           "(see the publish skill).", ""]
    for (check, kind), g in sorted(groups.items()):
        files = len({p.path for p in g if p.check != "site"})
        site_n = sum(1 for p in g if p.check == "site")
        where = f"{files} file(s)" + (f" and {site_n} in the built site" if site_n else "")
        out.append(f"- {check} `{kind}`: {len(g)} finding(s), in {where}. Why it is flagged: "
                   f"{OVERRIDABLE[check][kind]}")
    return out + [""]


def _overridden_section(overridden: list) -> list[str]:
    """Every finding the user overrode, under the override that covers it."""
    if not overridden:
        return []
    out = ["## Overridden by the user", "",
           f"{len(overridden)} finding(s) that the user accepted in `overrides:` of {MANIFEST}. They do "
           "not fail the publish, and they are published as they are.", ""]
    by: dict = {}
    for p, o in overridden:
        by.setdefault(o, []).append(p)
    for o, ps in by.items():
        out.append(f"- {o.label()}: {len(ps)} finding(s)")
        out += [f"  - {p}" for p in ps]
    return out + [""]


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
        _verdict(probs, info.get("overridden", [])),
        "",
        *[f"- {p}" for p in probs],
        "",
        *_overridable_section(probs),
        *_overridden_section(info.get("overridden", [])),
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


def check_site(root: Path, ex: Export, work: Path) -> tuple[list[Problem], list[str]]:
    """Build the site of the export as the public repo's workflow will, with its leak scan.
    (problems, notes). Without mkdocs the build is skipped with a note."""
    from . import site
    if importlib.util.find_spec("mkdocs") is None:
        return [], [f"the site was not built: mkdocs is not installed (pip install {site.MKDOCS_PINS})"]
    man = load_manifest(ex.snapshot) if (ex.snapshot / MANIFEST).is_file() else load_manifest(root)
    leaks = []
    probs = [Problem("site", "site", m) for m in site.build(ex.tree, work / "site", banner=man.site_banner,
                                                             leaks=leaks)]
    probs += [Problem("site", "site", site.leak_message(h), kind=h.pattern, match=h.match) for h in leaks]
    return probs, []


def override_site_leaks(probs: list[Problem], overrides: list[Override], overridden: list,
                        failed: list[Problem]) -> tuple[list[Problem], list]:
    """(site problems still failing, [(problem, override)] overridden). A leak in the built site
    is text of an exported file. It is overridden when an override of its pattern covers every
    file (no `paths`), or when the same text was overridden in the export and fails nowhere in it."""
    done = {(p.kind, p.match): o for p, o in overridden if p.check == "leak"}
    bad = {(p.kind, p.match) for p in failed if p.check == "leak"}
    still, over = [], []
    for p in probs:
        o = None
        if p.check == "site" and p.kind:
            o = next((o for o in overrides if o.check == "leak" and o.kind == p.kind and not o.paths), None)
            if o is None and (p.kind, p.match) not in bad:
                o = done.get((p.kind, p.match))
        if o is None:
            still.append(p)
        else:
            over.append((p, o))
    return still, over


def check(root: Path, commit: str = "HEAD", build_site: bool = True) -> tuple[int, Path, Export]:
    """Export, run every check, write the report. Returns (problem count, report path, export)."""
    root = Path(root).resolve()
    work = Path(tempfile.mkdtemp(prefix="opsci-publish-"))
    ex = export(root, work / "new", commit)
    probs, info = run_checks(root, ex)
    if build_site:
        sp, snotes = check_site(root, ex, work)
        sp, sover = override_site_leaks(sp, info["overrides"], info["overridden"], probs)
        probs += sp
        info["overridden"] += sover
        info["notes"] += snotes
    used = {o for _, o in info["overridden"]}
    info["notes"] += [f"the override {o.label()} covers no finding of this export: ask the user whether "
                      f"to remove it from {MANIFEST}" for o in info["overrides"] if o not in used]
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
    # No main on the remote (a new repo, or a first push that failed): start main unborn, dropping
    # any local commit that never reached the remote, so the export is compared against nothing.
    _git(co, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(co, "update-ref", "-d", "refs/heads/main", check=False)
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
        # Public main is the commit the last publish pushed, so it holds nothing the private repo
        # lacks. Its tree is not compared with a fresh export of the last published commit: a
        # newer opsci exports that commit differently (a file no longer exported, a map drawn
        # another way), which is not a public-side change. Such differences show as pending.
        pass
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
    bad = check_message(root, message)
    if bad:
        raise PublishError("the commit message carries internal information or a secret; rewrite it:\n  "
                           + "\n  ".join(bad))
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
    man = load_manifest(ex.snapshot) if (ex.snapshot / MANIFEST).is_file() else load_manifest(root)
    # The public repo's site build accepts the leak patterns the user overrode. The check above
    # already applied each override's `paths` to this export, which the public repo now equals.
    wf.write_text(site.workflow(root, load_manifest(root).site_banner, overridden_leak_patterns(man)),
                  encoding="utf-8")
    _git(co, "add", "-A")
    ident = _identity(root)
    if _git(co, "diff", "--cached", "--quiet", check=False).returncode == 0:
        raise PublishError("nothing to publish: the public repo already equals this export")
    msg = commit_message(message, _git(co, "diff", "--cached", "--name-status").stdout, sha)
    _git(co, *ident, "commit", "--quiet", "-m", msg)
    _git(co, "push", "--quiet", "origin", "main")
    public_sha = _git(co, "rev-parse", "HEAD").stdout.strip()
    write_last(root, sha, public_sha, ex.export_id)
    _git(root, *ident, "commit", "--quiet", "-m", f"Record publish of {sha[:12]} (public {public_sha[:12]})",
         "--", LAST_PUBLISHED)
    return sha, public_sha


def check_message(root: Path, message: str | None) -> list[str]:
    """The leak and secret findings in a public commit message's summary (the rest of the message
    is the exported file names, which the checks scan, and the private commit). No override
    applies: a message is rewritten, not excused."""
    if not message:
        return []
    try:
        pats = leakscan.patterns_for(root)
    except leakscan.LeakScanError as exc:
        return [str(exc)]
    leaks = leakscan.scan_text(message, "commit message", "content", pats)
    keys = leakscan.scan_text(message, "commit message", "content", secretscan.SECRET_PATTERNS)
    return ([f"{h.pattern} (line {h.line_number}): {h.match!r}" for h in leaks]
            + [f"{h.pattern} (line {h.line_number}): {h.match[:6]}…(redacted)" for h in keys])


CHANGE_WORDS = {"A": "added", "M": "changed", "D": "removed"}
MAX_LISTED = 40  # changed files listed in a public commit message


def commit_message(summary: str | None, name_status: str, sha: str) -> str:
    """The public commit message: the summary (a subject line, then an optional paragraph, written
    for readers of the public repo), the files the publish adds, changes and removes, and the
    private commit it exports."""
    rows = [line.split("\t") for line in name_status.splitlines() if line.strip()]
    files = [f"- {CHANGE_WORDS.get(r[0][0], 'changed')}: {r[-1]}" for r in rows]
    if len(files) > MAX_LISTED:
        files = files[:MAX_LISTED] + [f"- and {len(files) - MAX_LISTED} more files"]
    head = (summary or "").strip() or f"Publish {len(rows)} changed file{'s' if len(rows) != 1 else ''}"
    return f"{head}\n\n" + "\n".join(files) + f"\n\nExported from private commit {sha[:12]}.\n"


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
