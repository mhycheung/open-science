"""Release a project's data to Zenodo as reproducible tar groups.

Design (report section (j)):
- Data under ``data/`` is packed into tar groups, one ``<group>.tar.gz`` each, plus one file
  list ``FILES.tsv`` (path, size, sha256, tar group). A record holds at most 100 files and
  50 GB; both limits are checked before any network call.
- Tars are reproducible: sorted members, fixed mtime, owner 0/0, normalised modes, gzip
  without name or timestamp. Unchanged input gives a byte-identical tar and checksum.
- The first release creates a deposition; later releases create a new version of it. The
  new version starts with the previous version's files; only groups whose checksum changed
  are replaced.
- The sandbox is the default. Production needs ``--production``.
- The token is read from a mode-600 file. It is never taken from argv or the environment
  and never printed. It is sent only to the configured API host, over https (plain http
  only to a loopback test server).
- Nothing private is released: a group root that is a symlink must resolve under an allowed
  root and outside the user's hidden home directories; symlinks inside a group must be
  relative and stay inside it; data of soft- and hard-private tasks is left out unless
  ``zenodo.include_private`` names the task; every file is scanned for secrets and leaks.

The legacy deposit API is used: ``deposit/depositions`` (create, get, update metadata,
``actions/newversion``, ``actions/publish``), ``.../files`` (list, delete) and the bucket
link for uploads.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import gzip
import hashlib
import io
import json
import os
import posixpath
import re
import stat
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

MAX_FILES = 100
MAX_BYTES = 50 * 10**9  # Zenodo states "50GB"; decimal is the stricter reading
FILE_LIST = "FILES.tsv"
SANDBOX_API = "https://sandbox.zenodo.org/api"
PRODUCTION_API = "https://zenodo.org/api"
PRODUCTION_HOSTS = ("zenodo.org", "www.zenodo.org")
SANDBOX_HOSTS = ("sandbox.zenodo.org",)
# Set in the test suite: production is then refused even with --production.
FORBID_PRODUCTION_ENV = "OPSCI_ZENODO_FORBID_PRODUCTION"
TAR_MTIME = 946684800  # 2000-01-01 00:00 UTC
GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MANIFEST = "data/MANIFEST.yaml"
CITATION = "CITATION.cff"
CITE_DESC = "Zenodo data record"  # prefix of the CITATION.cff identifiers this tool manages
SITE_CONFIG = "config/site.local.yaml"
# The scans read big files in chunks of this size, overlapping by SCAN_OVERLAP bytes.
SCAN_CHUNK = 8 * 2**20
SCAN_OVERLAP = 4096


class ZenodoError(Exception):
    pass


# --------------------------------------------------------------------------- reproducible tar

class _Sink(io.RawIOBase):
    """Write-through file object that counts bytes and computes sha256 and md5."""

    def __init__(self, out=None):
        self.out = out
        self.sha256 = hashlib.sha256()
        self.md5 = hashlib.md5()
        self.size = 0

    def writable(self):
        return True

    def write(self, b):
        self.sha256.update(b)
        self.md5.update(b)
        self.size += len(b)
        if self.out is not None:
            self.out.write(b)
        return len(b)


class _HashingReader:
    def __init__(self, f):
        self.f = f
        self.h = hashlib.sha256()

    def read(self, n=-1):
        b = self.f.read(n)
        self.h.update(b)
        return b


def _inside(path: Path, top: Path) -> bool:
    """Whether ``path`` is ``top`` or lies under it (both resolved)."""
    return path == top or top in path.parents


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _forbidden_target(real: Path, root: Path) -> str | None:
    """Why the resolved target ``real`` of a symlinked group root may not be archived, or None.

    Refused: a target that holds the home directory or the project; one inside a hidden
    directory of the home directory (~/.ssh, ~/.config, ~/.aws, ...); one that is, holds or
    lies inside the config directory (where the token files are); one inside the project's
    .git.
    """
    home = Path.home().resolve()
    config = _config_dir().resolve()
    if _inside(home, real):
        return "it holds your home directory"
    if _inside(root, real):
        return "it holds the project"
    if _inside(real, home) and any(part.startswith(".") for part in real.relative_to(home).parts):
        return "it lies in a hidden directory of your home directory"
    if _inside(real, config) or _inside(config, real):
        return f"it is or holds the config directory {config} (token files)"
    if _inside(real, root / ".git"):
        return "it lies in the project's .git"
    return None


def symlink_roots(root: Path) -> list[Path]:
    """The directories a symlinked group root may point into: the project, the site's
    ``scratch`` and the paths in ``data_roots`` of ``config/site.local.yaml``."""
    root = Path(root).resolve()
    out = [root]
    cfg = Path(root) / SITE_CONFIG
    if cfg.is_file():
        try:
            data = yaml.safe_load(cfg.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ZenodoError(f"{SITE_CONFIG}: not valid YAML: {exc}") from None
        if not isinstance(data, dict):
            raise ZenodoError(f"{SITE_CONFIG}: must be a mapping")
        extra = data.get("data_roots") or []
        if not isinstance(extra, list) or not all(isinstance(v, str) for v in extra):
            raise ZenodoError(f"{SITE_CONFIG}: data_roots must be a list of directories")
        for v in [data.get("scratch")] + extra:
            if isinstance(v, str) and v.strip() and "<" not in v:
                p = Path(os.path.expanduser(v.strip()))
                if not p.is_absolute():
                    raise ZenodoError(f"{SITE_CONFIG}: '{v}' must be an absolute path")
                out.append(p.resolve())
    return out


def _check_link_target(rel: str, real: Path, root: Path, roots: list[Path]) -> None:
    why = _forbidden_target(real, root)
    if why is None and not any(_inside(real, r) for r in roots):
        why = ("it is not under the project, the site's scratch or a data_roots entry of "
               f"{SITE_CONFIG}")
    if why:
        raise ZenodoError(f"{rel} resolves to {real}: not archived, {why}")


def _check_inner_link(rel: str, path: Path, arc_dir: str, arc_top: str) -> None:
    """A symlink below a group root is stored as a link only if its target is relative and
    stays inside the group root it belongs to."""
    target = os.readlink(path)
    if os.path.isabs(target):
        raise ZenodoError(f"{rel} is a symlink to an absolute path; only relative links that "
                          "stay inside the archived directory are stored. Replace it with a "
                          "relative link or the file itself")
    dest = posixpath.normpath(posixpath.join(arc_dir, target))
    if not (dest == arc_top or dest.startswith(arc_top + "/")):
        raise ZenodoError(f"{rel} is a symlink to '{target}', outside data/{arc_top}; only "
                          "relative links that stay inside the archived directory are stored")


def _members(root: Path, paths: list[str], roots: list[Path] | None = None) -> list[tuple[str, Path]]:
    """(arcname, filesystem path) for everything under the given project paths, sorted.

    Arcnames are relative to ``data/``. A path that is itself a symlink (``data/<task>`` may
    point to scratch) is followed if its target is allowed (``symlink_roots``,
    ``_forbidden_target``); symlinks below it are stored as symlinks if they are relative and
    stay inside it. Anything else raises ZenodoError.
    """
    root_r = Path(root).resolve()
    if roots is None:
        roots = symlink_roots(root)
    out = {}
    for rel in paths:
        top = root / rel
        arc_top = PurePosixPath(rel).relative_to("data").as_posix()
        lexical = root_r / rel
        real = lexical.resolve()
        if real != lexical:
            _check_link_target(rel, real, root_r, roots)
        if top.is_dir():
            out[arc_top] = top
            for dirpath, dirnames, filenames in os.walk(top):
                d = Path(dirpath)
                arc_d = PurePosixPath(arc_top, d.relative_to(top).as_posix()).as_posix()
                for name in dirnames + filenames:
                    if os.path.islink(d / name):
                        _check_inner_link(f"data/{arc_d}/{name}", d / name, arc_d, arc_top)
                    out[f"{arc_d}/{name}"] = d / name
        else:
            out[arc_top] = top
    return sorted(out.items())


def _tarinfo(arcname: str, path: Path, is_top: bool) -> tarfile.TarInfo:
    st = os.stat(path) if is_top else os.lstat(path)
    ti = tarfile.TarInfo(arcname)
    ti.mtime = TAR_MTIME
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if stat.S_ISLNK(st.st_mode):
        ti.type = tarfile.SYMTYPE
        ti.linkname = os.readlink(path)
        ti.mode = 0o777
    elif stat.S_ISDIR(st.st_mode):
        ti.type = tarfile.DIRTYPE
        ti.mode = 0o755
    elif stat.S_ISREG(st.st_mode):
        ti.type = tarfile.REGTYPE
        ti.size = st.st_size
        ti.mode = 0o755 if st.st_mode & 0o111 else 0o644
    else:
        raise ZenodoError(f"{path}: not a regular file, directory or symlink")
    return ti


def write_tar(root: Path, paths: list[str], out) -> list[tuple[str, int, str]]:
    """Write a reproducible .tar.gz of the given project paths to the binary file ``out``.

    Returns the file list: (project path, size, sha256) for every regular file.
    """
    root = Path(root)
    tops = {PurePosixPath(p).relative_to("data").as_posix() for p in paths}
    files = []
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0, compresslevel=6) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for arc, path in _members(root, paths):
                ti = _tarinfo(arc, path, arc in tops)
                if ti.type == tarfile.REGTYPE:
                    with open(path, "rb") as f:
                        r = _HashingReader(f)
                        tar.addfile(ti, r)
                    files.append((f"data/{arc}", ti.size, r.h.hexdigest()))
                else:
                    tar.addfile(ti)
    return files


def tar_checksum(root: Path, paths: list[str]) -> tuple[str, int]:
    """sha256 and size of the reproducible tar of ``paths``, without writing it to disk."""
    sink = _Sink()
    write_tar(root, paths, sink)
    return sink.sha256.hexdigest(), sink.size


# --------------------------------------------------------------------------- manifest and groups

def read_manifest(root: Path) -> dict:
    p = Path(root) / MANIFEST
    if not p.exists():
        raise ZenodoError(f"{MANIFEST} not found")
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise ZenodoError(f"{MANIFEST}: top level must be a mapping")
    data.setdefault("datasets", [])
    return data


def write_manifest(root: Path, data: dict) -> None:
    """Rewrite the manifest, keeping its comments where it can.

    A release changes only the top-level ``zenodo:`` section and the ``zenodo:`` field of
    ``datasets`` entries. Those are replaced in the text, so every other line (and its
    comments) is kept. If the result does not parse back to ``data`` (the manifest changed in
    some other way), the file is dumped whole, keeping only its leading comment block.
    """
    p = Path(root) / MANIFEST
    text = p.read_text() if p.exists() else ""
    new = _patch_manifest(text, data)
    if new is None:
        head = []
        for line in text.splitlines():
            if line.startswith("#") or not line.strip():
                head.append(line)
            else:
                break
        body = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
        new = "\n".join(head + [body.rstrip("\n")]) + "\n"
    tmp = p.with_name(".MANIFEST.yaml.tmp")  # dot name: never taken for a tar group
    tmp.write_text(new)
    tmp.replace(p)


_TOP_KEY = re.compile(r"^[^\s#-][^:]*:")


def _top_block(lines: list[str], key: str) -> tuple[int, int] | None:
    """[start, end) of the top-level ``key:`` block: its key line and every line up to the
    next top-level key, less the comment and blank lines just before that key."""
    start = next((i for i, l in enumerate(lines) if l.startswith(key + ":")), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if _TOP_KEY.match(lines[i])), len(lines))
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
        end -= 1
    return start, end


def _scalar(v) -> str:
    return yaml.safe_dump(v, default_flow_style=True, allow_unicode=True).split("\n...")[0].strip()


def _patch_manifest(text: str, data: dict) -> str | None:
    """``text`` with its ``zenodo:`` section and the datasets' ``zenodo:`` fields set from
    ``data``; None if that does not give ``data``."""
    try:
        old = yaml.safe_load(text) if text.strip() else None
    except yaml.YAMLError:
        return None
    if not isinstance(old, dict):
        return None
    lines = text.splitlines()
    # datasets[i].zenodo, by entry, edited in place so that comments on other lines stay
    new_ds, old_ds = data.get("datasets") or [], old.get("datasets") or []
    if len(new_ds) == len(old_ds):
        blk = _top_block(lines, "datasets")
        items: list[int] = []
        if blk is not None:
            ind = None
            for i in range(blk[0] + 1, blk[1]):
                m = re.match(r"^(\s*)- ", lines[i])
                if m and (ind is None or len(m.group(1)) == ind):
                    ind = len(m.group(1))
                    items.append(i)
        for k, (a, b) in enumerate(zip(old_ds, new_ds)):
            if not (isinstance(a, dict) and isinstance(b, dict)) or a.get("zenodo") == b.get("zenodo") \
                    or "zenodo" not in b or k >= len(items):
                continue
            lo, hi = items[k], items[k + 1] if k + 1 < len(items) else blk[1]
            col = len(lines[lo]) - len(lines[lo].lstrip()) + 2
            done = False
            for i in range(lo, hi):
                m = re.match(r"^(\s*(?:- )?)zenodo:(\s*)([^#]*?)(\s+#.*)?$", lines[i])
                if m and len(m.group(1)) == col:
                    lines[i] = f"{m.group(1)}zenodo: {_scalar(b['zenodo'])}{m.group(4) or ''}"
                    done = True
                    break
            if not done:
                last = max(i for i in range(lo, hi) if lines[i].strip() and not lines[i].lstrip().startswith("#"))
                lines.insert(last + 1, " " * col + f"zenodo: {_scalar(b['zenodo'])}")
                items = [j + 1 if j > last else j for j in items]
                blk = (blk[0], blk[1] + 1)
    # the zenodo: section, replaced whole (the tool writes it)
    blk = _top_block(lines, "zenodo")
    dump = [] if "zenodo" not in data else yaml.safe_dump(
        {"zenodo": data["zenodo"]}, sort_keys=False, default_flow_style=False, allow_unicode=True).rstrip("\n").split("\n")
    if blk is not None:
        lines[blk[0]:blk[1]] = dump
    else:
        lines += dump
    new = "\n".join(lines) + "\n"
    try:
        return new if yaml.safe_load(new) == data else None
    except yaml.YAMLError:
        return None


@dataclass
class Group:
    name: str
    paths: list[str]

    @property
    def filename(self) -> str:
        return f"{self.name}.tar.gz"


def groups_from_manifest(root: Path, manifest: dict) -> tuple[list[Group], list[str]]:
    """The tar groups, and the data/ entries no group covers.

    ``zenodo.groups`` in the manifest lists groups explicitly (name + paths). Without it,
    each entry of ``data/`` (except MANIFEST.yaml and dot-files) is its own group.
    """
    root = Path(root)
    z = manifest.get("zenodo") or {}
    raw = z.get("groups")
    entries = sorted(e.name for e in (root / "data").iterdir()
                     if e.name != "MANIFEST.yaml" and not e.name.startswith("."))
    if raw is None:
        groups = [Group(e, [f"data/{e}"]) for e in entries]
    else:
        if not isinstance(raw, list):
            raise ZenodoError(f"{MANIFEST}: zenodo.groups must be a list")
        groups = []
        for g in raw:
            if not isinstance(g, dict) or "name" not in g or not g.get("paths"):
                raise ZenodoError(f"{MANIFEST}: each zenodo.groups entry needs 'name' and 'paths'")
            paths = g["paths"] if isinstance(g["paths"], list) else [g["paths"]]
            groups.append(Group(str(g["name"]), [str(p).rstrip("/") for p in paths]))
    seen, all_paths = set(), []
    for g in groups:
        if not GROUP_NAME_RE.match(g.name) or g.name + ".tar.gz" == FILE_LIST:
            raise ZenodoError(f"tar group name '{g.name}' is not allowed: use letters, digits, '.', '_', '-'")
        if g.name in seen:
            raise ZenodoError(f"tar group '{g.name}' is defined twice")
        seen.add(g.name)
        for p in g.paths:
            pp = PurePosixPath(p)
            if pp.is_absolute() or ".." in pp.parts or len(pp.parts) < 2 or pp.parts[0] != "data":
                raise ZenodoError(f"group '{g.name}': path '{p}' must be inside data/")
            if p == MANIFEST:
                raise ZenodoError(f"group '{g.name}': {MANIFEST} is uploaded as {FILE_LIST}, not in a tar")
            if not (root / p).exists():
                raise ZenodoError(f"group '{g.name}': path '{p}' does not exist")
            all_paths.append((p, g.name))
    for i, (a, ga) in enumerate(all_paths):
        for b, gb in all_paths[i + 1:]:
            if a == b or b.startswith(a + "/") or a.startswith(b + "/"):
                raise ZenodoError(f"groups '{ga}' and '{gb}' overlap: '{a}' and '{b}'")
    covered = [p for p, _ in all_paths]
    uncovered = [f"data/{e}" for e in entries
                 if not any(c == f"data/{e}" or c.startswith(f"data/{e}/") for c in covered)]
    return groups, uncovered


# --------------------------------------------------------------------------- tasks and results of a file

def _within(path: str, top: str) -> bool:
    """Whether ``path`` is ``top`` or lies under it."""
    path, top = path.rstrip("/"), top.rstrip("/")
    return path == top or path.startswith(top + "/")


@dataclass
class Owners:
    """The task ids and the result artifacts of a project, from its node headers."""
    tasks: set[str] = field(default_factory=set)
    artifacts: dict[str, list[str]] = field(default_factory=dict)  # result id -> artifacts
    # task id -> privacy tier; also holds task directories without a valid header (hard-private)
    privacy: dict[str, str] = field(default_factory=dict)
    hard_private: list[str] = field(default_factory=list)  # publish/manifest.yaml hard_private globs

    def task(self, path: str) -> str:
        """The task whose data directory ``data/<id>/`` holds ``path``, or ""."""
        parts = PurePosixPath(path).parts
        return parts[1] if len(parts) >= 2 and parts[0] == "data" and parts[1] in self.tasks else ""

    def results(self, path: str) -> list[str]:
        """The results whose ``artifacts`` hold ``path`` (the file, or a directory above it)."""
        return sorted(r for r, arts in self.artifacts.items() if any(_within(path, a) for a in arts))

    def tier(self, path: str) -> tuple[str, str]:
        """(privacy tier, the task id or "hard_private") of the data at project path ``path``:
        that of the task ``data/<id>/`` belongs to, or hard-private if the publish manifest's
        ``hard_private`` list names it or a directory above it; ("public", "") otherwise."""
        parts = PurePosixPath(path).parts
        if len(parts) >= 2 and parts[0] == "data" and parts[1] in self.privacy:
            if self.privacy[parts[1]] != "public":
                return self.privacy[parts[1]], parts[1]
        for g in self.hard_private:
            g = g.rstrip("/")
            if _within(path, g) or fnmatch.fnmatchcase(path, g) or any(
                    fnmatch.fnmatchcase("/".join(parts[:i]), g) for i in range(1, len(parts))):
                return "hard-private", "hard_private"
        return "public", ""


def file_owners(root: Path) -> Owners:
    """The task ids, privacy tiers and result artifacts of the project at ``root``
    (``nodes.scan``). A task's tier is the stricter of its own and that of every task it lies
    in; a task directory whose context has no valid header counts as hard-private, as in
    ``opsci publish``."""
    from . import nodes
    root = Path(root)
    res = nodes.scan(root)
    own = Owners()
    default = nodes.default_privacy(root)
    dir_tier = {}
    for n in res.nodes:
        if n.get("type") == "task" and nodes.is_task_context(n.path):
            own.tasks.add(n.id)
            dir_tier[str(PurePosixPath(n.path).parent)] = str(n.get("privacy") or default)
        elif n.get("type") == "result":
            own.artifacts[n.id] = [str(a) for a in n.get("artifacts") or []]
    for n in res.nodes:
        if n.get("type") == "task" and nodes.is_task_context(n.path):
            tiers = [dir_tier.get(d, "hard-private") for d in nodes.task_dirs(n.path)]
            t = nodes.strictest(tiers)
            own.privacy[n.id] = nodes.strictest([t, own.privacy[n.id]]) if n.id in own.privacy else t
    for glob in nodes.TASK_ROOT_GLOBS:
        for d in root.glob(f"{glob}/*/"):
            if d.is_dir() and d.relative_to(root).as_posix() not in dir_tier and d.name not in own.privacy:
                own.privacy[d.name] = "hard-private"
    try:
        pm = yaml.safe_load((root / "publish" / "manifest.yaml").read_text()) or {}
        hp = pm.get("hard_private") if isinstance(pm, dict) else None
        own.hard_private = [str(g) for g in hp] if isinstance(hp, list) else []
    except (OSError, yaml.YAMLError):
        pass
    return own


# --------------------------------------------------------------------------- releases covering a path

def release_groups(entry: dict, manifest: dict) -> dict[str, list[str]]:
    """Tar file name -> project paths, for one release entry of the manifest.

    Entries written since 0.3.1 record ``groups``. For an older entry, the manifest's current
    ``zenodo.groups`` is used, else the default rule (``<name>.tar.gz`` holds ``data/<name>``);
    either way only the tars the release holds (its ``files``) count.
    """
    if isinstance(entry.get("groups"), dict):
        return {str(k): [str(p) for p in (v or [])] for k, v in entry["groups"].items()}
    files = set((entry.get("files") or {}).keys()) - {FILE_LIST}
    raw = (manifest.get("zenodo") or {}).get("groups") if isinstance(manifest.get("zenodo"), dict) else None
    if isinstance(raw, list):
        out = {}
        for g in raw:
            if isinstance(g, dict) and g.get("name") and f"{g['name']}.tar.gz" in files:
                ps = g.get("paths") or []
                out[f"{g['name']}.tar.gz"] = [str(p).rstrip("/") for p in (ps if isinstance(ps, list) else [ps])]
        return out
    return {f: [f"data/{f[:-len('.tar.gz')]}"] for f in sorted(files) if f.endswith(".tar.gz")}


def production_location(path: str, manifest: dict | None) -> tuple[str, list[str]] | None:
    """(DOI, tar file names) of the newest production release holding ``path``, or None.

    A tar holds ``path`` if one of its group paths is ``path``, lies above it or lies under it
    (``path`` is a directory holding the group). Sandbox releases are never used: their DOIs
    (10.5072/...) do not resolve.
    """
    if not isinstance(manifest, dict) or not str(path).startswith("data/"):
        return None
    z = manifest.get("zenodo")
    prod = z.get("production") if isinstance(z, dict) else None
    rels = prod.get("releases") if isinstance(prod, dict) else None
    for entry in reversed(rels if isinstance(rels, list) else []):
        doi = str((entry or {}).get("doi") or "") if isinstance(entry, dict) else ""
        if not doi or doi.startswith("10.5072/"):
            continue
        tars = sorted(t for t, ps in release_groups(entry, manifest).items()
                      if any(_within(path, p) or _within(p, path) for p in ps))
        if tars:
            return doi, tars
    return None


# --------------------------------------------------------------------------- privacy and scans

def include_private(manifest: dict) -> set[str]:
    """The task ids in ``zenodo.include_private``: soft- or hard-private tasks whose data the
    user has decided to release."""
    z = manifest.get("zenodo") or {}
    val = z.get("include_private") if isinstance(z, dict) else None
    if val is None:
        return set()
    if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
        raise ZenodoError(f"{MANIFEST}: zenodo.include_private must be a list of task ids")
    return set(val)


@dataclass(frozen=True)
class Override:
    """A leak finding kind the user accepts: an entry of ``zenodo.overrides``. The same kinds
    and fields as the publish manifest's ``overrides:`` (check-overrides.md)."""
    kind: str
    reason: str
    date: str
    paths: tuple[str, ...] = ()

    def covers(self, hit) -> bool:
        return hit.pattern == self.kind and (not self.paths or any(
            _within(hit.path, g) or fnmatch.fnmatchcase(hit.path, g) for g in self.paths))


def overrides(manifest: dict) -> list[Override]:
    """``zenodo.overrides`` of the data manifest, checked. Only the leak kinds that
    ``opsci publish`` lets the user accept (SLURM job numbers); a secret is never overridden."""
    from . import leakscan
    z = manifest.get("zenodo") or {}
    val = z.get("overrides") if isinstance(z, dict) else None
    if val is None:
        return []
    if not isinstance(val, list):
        raise ZenodoError(f"{MANIFEST}: zenodo.overrides must be a list of {{check, kind, reason, date}} entries")
    out = []
    for i, o in enumerate(val):
        where = f"{MANIFEST}: zenodo.overrides[{i}]"
        if not isinstance(o, dict) or set(o) - {"check", "kind", "reason", "date", "paths"}:
            raise ZenodoError(f"{where}: must be a mapping with check, kind, reason, date and optional paths")
        check, kind = str(o.get("check", "")), str(o.get("kind", ""))
        if check != "leak" or kind not in leakscan.OVERRIDABLE:
            raise ZenodoError(f"{where}: check '{check}', kind '{kind}' cannot be overridden; the "
                              f"overridable kinds are leak: {', '.join(sorted(leakscan.OVERRIDABLE))}")
        reason = o.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ZenodoError(f"{where}: `reason` must say why the user accepts these findings")
        date = o.get("date")
        date = date.isoformat() if isinstance(date, dt.date) else date
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date.strip()):
            raise ZenodoError(f"{where}: `date` must be the date of the user's decision, YYYY-MM-DD")
        paths = o.get("paths", [])
        paths = [paths] if isinstance(paths, str) else paths
        if not isinstance(paths, list) or not all(isinstance(g, str) and g.strip() for g in paths):
            raise ZenodoError(f"{where}: `paths` must be a list of paths or globs")
        out.append(Override(kind, " ".join(reason.split()), date.strip(), tuple(g.rstrip("/") for g in paths)))
    return out


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, secret[:6] + "…(redacted)")


def scan_member(path: Path, rel: str, leak_pats, secret_pats) -> list:
    """Leak and secret hits (``leakscan.Hit``) in one archived file or symlink.

    The name is scanned with every pattern, a symlink's target as text. A PNG or PDF goes
    through ``leakscan.scan_file`` (image text chunks, PDF strings). Other files are read in
    chunks: text (UTF-8, no NUL byte in the first 8 kB) with every pattern, binary data with
    the patterns meant for binary data less ``absolute-path``, which matches by chance about
    once per megabyte of compressed data. Secrets are redacted in the hits.
    """
    from . import leakscan, pdf
    pats = list(leak_pats) + list(secret_pats)
    secret_names = {p.name for p in secret_pats}
    hits = leakscan.scan_text(rel, rel, "filename", pats)
    if path.is_symlink():
        hits += leakscan.scan_text(os.readlink(path), rel, "symlink target", pats)
    elif path.is_file():
        with open(path, "rb") as f:
            head = f.read(SCAN_CHUNK)
            if head.startswith(leakscan.PNG_MAGIC) or pdf.is_pdf(head):
                hits += [h for h in leakscan.scan_file(path, rel, pats) if h.where != "filename"]
            else:
                try:
                    head[:8192].decode("utf-8")
                    text = b"\0" not in head[:8192]
                except UnicodeDecodeError as exc:
                    text = exc.start > 8192 - 4 and b"\0" not in head[:8192]  # a cut character
                use = pats if text else [p for p in pats if p.binary and p.name != "absolute-path"]
                chunk, seen, first = head, set(), True
                while chunk:
                    nxt = f.read(SCAN_CHUNK)
                    s = chunk.decode("utf-8", "replace") if text else chunk.decode("latin-1")
                    for h in leakscan.scan_text(s, rel, "content", use):
                        key = (h.pattern, h.match, h.line)
                        if key not in seen:
                            seen.add(key)
                            single = first and not nxt
                            hits.append(leakscan.Hit(h.path, h.where, h.pattern,
                                                     h.line_number if single else None, h.line, h.match))
                    chunk = (chunk[-SCAN_OVERLAP:] + nxt) if nxt else b""
                    first = False
    return [leakscan.Hit(h.path, h.where, h.pattern, h.line_number, _redact(h.line, h.match),
                         _redact(h.match, h.match)) if h.pattern in secret_names else h for h in hits]


def scan_release(root: Path, members: list[tuple[str, Path]], extra: dict[str, str] | None = None):
    """(hits, overridden hits) of the secret and leak scans over the archived ``members``
    (arcname, path) and the generated texts in ``extra`` (name -> text, e.g. FILES.tsv)."""
    from . import leakscan, secretscan
    try:
        leak_pats = leakscan.patterns_for(Path(root))
    except leakscan.LeakScanError as exc:
        raise ZenodoError(f"leak scan: {exc}") from None
    secret_pats = secretscan.SECRET_PATTERNS
    hits = []
    for arc, path in members:
        hits += scan_member(path, f"data/{arc}", leak_pats, secret_pats)
    for name, text in (extra or {}).items():
        hits += leakscan.scan_text(text, name, "content", list(leak_pats) + list(secret_pats))
    ovs = overrides(read_manifest(root))
    failed, overridden = [], []
    for h in hits:
        o = next((o for o in ovs if o.covers(h)), None)
        (failed if o is None else overridden).append(h)
    return failed, overridden


def _scan_errors(hits, max_shown: int = 20) -> list[str]:
    if not hits:
        return []
    shown = "; ".join(f"{h.path} [{h.where}] {h.pattern}: {h.match!r}" for h in hits[:max_shown])
    more = f"; and {len(hits) - max_shown} more" if len(hits) > max_shown else ""
    return [f"secret and leak scan: {len(hits)} finding(s): {shown}{more}. Remove or fix the "
            "files (or leave them out of the tar groups); see docs/zenodo.md, 'Scans'"]


# --------------------------------------------------------------------------- plan

@dataclass
class FileInfo:
    filename: str
    sha256: str
    md5: str
    size: int
    local: Path | None = None  # built file; None in a dry run
    decision: str = ""  # reuse | upload


@dataclass
class Plan:
    server: str
    api_url: str
    groups: list[Group]
    files: list[FileInfo] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    previous: dict | None = None
    errors: list[str] = field(default_factory=list)
    owners: Owners | None = None  # tasks and results of the files, for FILES.tsv and the metadata
    private: list[str] = field(default_factory=list)  # data/ entries left out: private tasks
    overridden: list = field(default_factory=list)  # scan hits the user accepted (zenodo.overrides)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def changed(self) -> bool:
        return bool(self.removed) or any(f.decision == "upload" for f in self.files
                                         if f.filename != FILE_LIST)


def _file_size(path: Path) -> int:
    return os.path.getsize(path)


def check_limits(files: dict[str, int]) -> list[str]:
    """Refusals for a record holding ``files`` (name -> size in bytes)."""
    errs = []
    if len(files) > MAX_FILES:
        errs.append(f"record would hold {len(files)} files; Zenodo allows {MAX_FILES}. "
                    "Merge tar groups (zenodo.groups in data/MANIFEST.yaml).")
    total = sum(files.values())
    if total > MAX_BYTES:
        errs.append(f"record would hold {total} bytes; Zenodo allows {MAX_BYTES} (50 GB).")
    return errs


def section(manifest: dict, server: str) -> dict:
    """The manifest's per-server release state (sandbox and production are kept apart)."""
    key = "production" if server == "production" else "sandbox"
    z = manifest.setdefault("zenodo", {})
    if not isinstance(z, dict):
        raise ZenodoError(f"{MANIFEST}: 'zenodo' must be a mapping")
    s = z.setdefault(key, {})
    s.setdefault("releases", [])
    return s


def make_plan(root: Path, server: str, api_url: str, build_dir: Path | None) -> Plan:
    """Build (or, with build_dir None, only checksum) every tar group and decide reuse.

    Nothing here touches the network.
    """
    root = Path(root)
    manifest = read_manifest(root)
    groups, uncovered = groups_from_manifest(root, manifest)
    explicit = isinstance((manifest.get("zenodo") or {}).get("groups"), list)
    owners = file_owners(root)
    allowed = include_private(manifest)
    # Data of soft- and hard-private tasks is not released unless zenodo.include_private names
    # the task: a default group is left out with a note, an explicit group is refused.
    kept, skipped, refused = [], [], []
    for g in groups:
        priv = [(p, *owners.tier(p)) for p in g.paths]
        priv = [(p, t, "hard_private in publish/manifest.yaml" if who == "hard_private" else f"task {who}")
                for p, t, who in priv if t != "public" and who not in allowed]
        if not priv:
            kept.append(g)
        elif explicit:
            refused += [f"group '{g.name}': {p} is {t} ({src}); leave it out of the group, or name "
                        f"the task in zenodo.include_private if the user decides to release it"
                        for p, t, src in priv]
        else:
            skipped += [f"{p} ({t}, {src})" for p, t, src in priv]
    groups = kept
    plan = Plan(server, api_url, groups, uncovered=uncovered, errors=refused, owners=owners,
                private=skipped)
    sec = section(manifest, server)
    plan.previous = sec["releases"][-1] if sec["releases"] else None
    prev_files = (plan.previous or {}).get("files") or {}
    if not groups:
        if not plan.errors:
            plan.errors.append("no tar groups: data/ is empty, holds only private data, "
                               "or zenodo.groups is not set")
        return plan
    # The file-count limit needs no tar: refuse before building anything.
    plan.errors += check_limits({g.filename: 0 for g in groups} | {FILE_LIST: 0})
    if plan.errors:
        return plan
    # What may be archived (symlinks), then the secret and leak scans, before any tar is built.
    roots = symlink_roots(root)
    members = []
    for g in groups:
        try:
            members += _members(root, g.paths, roots)
        except ZenodoError as exc:
            plan.errors.append(str(exc))
    if plan.errors:
        return plan
    hits, plan.overridden = scan_release(root, members)
    plan.errors += _scan_errors(hits)
    if plan.errors:
        return plan
    if build_dir is not None:
        build_dir.mkdir(parents=True, exist_ok=True)
    listing = []
    for g in groups:
        local = None
        if build_dir is None:
            sink = _Sink()
            rows = write_tar(root, g.paths, sink)
        else:
            local = build_dir / g.filename
            with open(local, "wb") as f:
                sink = _Sink(f)
                rows = write_tar(root, g.paths, sink)
        size = sink.size if local is None else _file_size(local)
        plan.files.append(FileInfo(g.filename, sink.sha256.hexdigest(), sink.md5.hexdigest(),
                                   size, local))
        listing += [(p, s, h, g.filename) for p, s, h in rows]
    text = "# path\tsize\tsha256\ttar\ttask\tresults\n" + "".join(
        f"{p}\t{s}\t{h}\t{t}\t{owners.task(p)}\t{','.join(owners.results(p))}\n"
        for p, s, h, t in sorted(listing))
    hits, over = scan_release(root, [], {FILE_LIST: text})
    plan.overridden += over
    plan.errors += _scan_errors(hits)
    blob = text.encode()
    local = None
    if build_dir is not None:
        local = build_dir / FILE_LIST
        local.write_bytes(blob)
    plan.files.append(FileInfo(FILE_LIST, hashlib.sha256(blob).hexdigest(),
                               hashlib.md5(blob).hexdigest(),
                               len(blob) if local is None else _file_size(local), local))
    for f in plan.files:
        old = prev_files.get(f.filename)
        f.decision = "reuse" if old and old.get("sha256") == f.sha256 else "upload"
    names = {f.filename for f in plan.files}
    plan.removed = sorted(n for n in prev_files if n not in names)
    plan.errors += check_limits({f.filename: f.size for f in plan.files})
    return plan


def _human(n: int) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000


def format_plan(plan: Plan, version: str | None) -> str:
    out = [f"server: {plan.server} ({plan.api_url})"]
    if plan.previous:
        out.append(f"previous release: {plan.previous.get('version')} "
                   f"(record {plan.previous.get('record_id')}, doi {plan.previous.get('doi')})")
        out.append(f"action: new version {version or '<version>'} of that record")
    else:
        out.append(f"previous release: none; action: create a new deposition, version {version or '<version>'}")
    out.append("")
    out.append(f"{'file':32} {'size':>10}  {'sha256':16}  decision")
    for f in plan.files:
        why = "unchanged, kept from previous version" if f.decision == "reuse" else (
            "new" if not plan.previous or f.filename not in (plan.previous.get("files") or {}) else "changed")
        out.append(f"{f.filename:32} {_human(f.size):>10}  {f.sha256[:16]}  {f.decision} ({why})")
    for r in plan.removed:
        out.append(f"{r:32} {'':>10}  {'':16}  remove (group no longer defined)")
    out.append("")
    out.append(f"record: {len(plan.files)} files (limit {MAX_FILES}), "
               f"{_human(plan.total_bytes)} (limit {_human(MAX_BYTES)})")
    for u in plan.uncovered:
        out.append(f"note: {u} is in no tar group and will not be released")
    for u in plan.private:
        out.append(f"note: {u} is private and will not be released (zenodo.include_private)")
    for h in plan.overridden:
        out.append(f"note: accepted by zenodo.overrides: {h.path} {h.pattern}: {h.match!r}")
    if plan.files and not plan.changed and plan.previous:
        out.append("note: nothing changed since the previous release")
    for e in plan.errors:
        out.append(f"REFUSED: {e}")
    return "\n".join(out)


# --------------------------------------------------------------------------- server and token

def resolve_server(production: bool, api_url: str | None) -> tuple[str, str]:
    """(server kind, API base URL). Kinds: sandbox, production, custom (a local test server)."""
    if api_url:
        from .notify import _loopback_http
        u = urllib.parse.urlparse(api_url)
        host = (u.hostname or "").lower()
        if not (u.scheme == "https" and host) and not _loopback_http(api_url):
            raise ZenodoError(f"--api-url {api_url}: must be an https URL (plain http only for a "
                              "loopback test server: 127.0.0.1, localhost, ::1); the token "
                              "must not travel unencrypted")
        kind = ("production" if host in PRODUCTION_HOSTS else
                "sandbox" if host in SANDBOX_HOSTS else "custom")
        base = api_url.rstrip("/")
    else:
        kind, base = ("production", PRODUCTION_API) if production else ("sandbox", SANDBOX_API)
    if kind == "production" and not production:
        raise ZenodoError(f"{base} is the production Zenodo: pass --production to release there")
    if production and kind != "production":
        raise ZenodoError(f"--production given but {base} is not the production Zenodo")
    if kind == "production" and os.environ.get(FORBID_PRODUCTION_ENV):
        raise ZenodoError(f"production Zenodo is disabled here ({FORBID_PRODUCTION_ENV} is set)")
    return kind, base


def default_token_file(server: str) -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    name = "zenodo.token" if server == "production" else "zenodo-sandbox.token"
    return base / "opsci" / name


def load_token(server: str, token_file: str | None = None) -> str:
    p = Path(token_file) if token_file else default_token_file(server)
    if not p.exists():
        raise ZenodoError(f"no token file at {p}; see docs/zenodo.md")
    mode = p.stat().st_mode
    if mode & 0o077:
        raise ZenodoError(f"token file {p} is readable by others (mode {oct(mode & 0o777)}); "
                          "run: chmod 600 on it")
    tok = p.read_text().strip()
    if not tok or len(tok.split()) != 1:
        raise ZenodoError(f"token file {p} must hold one token and nothing else")
    return tok


def check_token(production: bool = False, api_url: str | None = None,
                token_file: str | None = None) -> str:
    """Check that the token file is private and that Zenodo accepts the token. Creates nothing."""
    server, base = resolve_server(production, api_url)
    path = Path(token_file) if token_file else default_token_file(server)
    tok = load_token(server, token_file)
    API(base, tok, timeout=60)._call("GET", "deposit/depositions?size=1")
    return f"ok: {base} accepts the token in {path}"


# --------------------------------------------------------------------------- API client

class API:
    def __init__(self, base: str, token: str, timeout: float = 600):
        self.base = base.rstrip("/")
        self._token = token
        self.timeout = timeout

    def __repr__(self):
        return f"API({self.base!r})"

    def _origin(self, url: str) -> tuple[str, str]:
        u = urllib.parse.urlsplit(url)
        return u.scheme.lower(), u.netloc.lower()

    def _call(self, method, url, body=None, json_body=None, length=None):
        if not url.startswith(("http://", "https://")):
            url = self.base + "/" + url.lstrip("/")
        elif self._origin(url) != self._origin(self.base):
            # A URL from a server response (latest_draft, bucket): the token goes only to the
            # configured API host, with the same scheme.
            raise ZenodoError(f"{method} {url}: the server pointed to another host than "
                              f"{self.base}; the token is sent only to the configured API host")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        data = body
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif body is not None:
            headers["Content-Type"] = "application/octet-stream"
            if length is not None:
                headers["Content-Length"] = str(length)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:500].decode(errors="replace")
            raise ZenodoError(f"{method} {url}: HTTP {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise ZenodoError(f"{method} {url}: {exc.reason}") from None
        return json.loads(raw) if raw.strip() else None

    def create(self):
        return self._call("POST", "deposit/depositions", json_body={})

    def get(self, dep_id):
        return self._call("GET", f"deposit/depositions/{dep_id}")

    def new_version(self, record_id):
        r = self._call("POST", f"deposit/depositions/{record_id}/actions/newversion")
        # The legacy API returns the old record, with the new draft under links.latest_draft.
        latest = (r.get("links") or {}).get("latest_draft")
        if latest:
            return self._call("GET", latest)
        return r

    def list_files(self, dep_id):
        return self._call("GET", f"deposit/depositions/{dep_id}/files") or []

    def delete_file(self, dep_id, file_id):
        self._call("DELETE", f"deposit/depositions/{dep_id}/files/{file_id}")

    def upload(self, bucket_url, filename, path: Path):
        size = path.stat().st_size
        with open(path, "rb") as f:
            return self._call("PUT", f"{bucket_url}/{urllib.parse.quote(filename)}", body=f, length=size)

    def update_metadata(self, dep_id, metadata):
        return self._call("PUT", f"deposit/depositions/{dep_id}", json_body={"metadata": metadata})

    def publish(self, dep_id):
        return self._call("POST", f"deposit/depositions/{dep_id}/actions/publish")


# --------------------------------------------------------------------------- metadata and write-back

def read_citation(root: Path) -> dict:
    p = Path(root) / CITATION
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def project_links(root: Path) -> tuple[str | None, str | None]:
    """(public repo URL, project site URL) from ``publish/manifest.yaml``: ``public_repo``,
    and ``site_url`` or the GitHub Pages URL of the repo (``publish.site_url``). None for
    what is not known, or when there is no valid publish manifest."""
    from . import publish
    try:
        man = publish.load_manifest(Path(root))
    except publish.PublishError:
        return None, None
    repo = publish.repo_web_url(str(man.public_repo)) if man.public_repo else None
    return repo, publish.site_url(man)


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_metadata(root: Path, manifest: dict, version: str, date: dt.date,
                   groups: list[Group] | None = None, owners: Owners | None = None) -> dict:
    cff = read_citation(root)
    creators = []
    for a in cff.get("authors") or []:
        if a.get("family-names"):
            name = a["family-names"] + (f", {a['given-names']}" if a.get("given-names") else "")
        else:
            name = a.get("name")
        if name:
            c = {"name": name}
            if a.get("affiliation"):
                c["affiliation"] = a["affiliation"]
            if a.get("orcid"):
                c["orcid"] = str(a["orcid"]).rsplit("/", 1)[-1]
            creators.append(c)
    title = cff.get("title") or Path(root).resolve().name
    repo, site = project_links(root)
    desc = [f"<p>Data for {_esc(title)}. Each .tar.gz holds one group of datasets; "
            f"{FILE_LIST} lists every file with its size, sha256 checksum, tar, the task that "
            f"made it and the results that rest on it.</p>"]
    links = []
    if repo:
        links.append(f'Project repository: <a href="{_esc(repo)}">{_esc(repo)}</a>.')
    if site:
        links.append(f'Project site: <a href="{_esc(site)}">{_esc(site)}</a>.')
    if links:
        desc.append("<p>" + " ".join(links) + "</p>")
    if groups:
        owners = owners if owners is not None else file_owners(root)
        items = []
        for g in groups:
            tasks = sorted({t for t in (owners.task(p) for p in g.paths) if t})
            where = f" (task{'s' if len(tasks) > 1 else ''} {', '.join(tasks)})" if tasks else ""
            items.append(f"<li>{_esc(g.filename)}: {_esc(', '.join(g.paths))}{_esc(where)}</li>")
        desc.append("<p>Files:</p><ul>" + "".join(items) + "</ul>")
    md = {
        "upload_type": "dataset",
        "title": f"{title}: data",
        "creators": creators,
        "description": "".join(desc),
        "access_right": "open",
        "license": "cc-by-4.0",
    }
    if repo:
        md["related_identifiers"] = [{"identifier": repo, "relation": "isSupplementTo",
                                      "resource_type": "software"}]
    md.update((manifest.get("zenodo") or {}).get("metadata") or {})
    md["version"] = version
    md["publication_date"] = date.isoformat()
    if not md.get("creators"):
        raise ZenodoError("no creators: add authors to CITATION.cff or zenodo.metadata.creators")
    return md


def write_citation(root: Path, doi: str, concept_doi: str, version: str) -> None:
    """Record the data DOIs as CITATION.cff identifiers, keeping the rest of the file."""
    p = Path(root) / CITATION
    if not p.exists():
        raise ZenodoError(f"{CITATION} not found")
    text = p.read_text()
    cff = yaml.safe_load(text) or {}
    keep = [i for i in cff.get("identifiers") or []
            if not str(i.get("description", "")).startswith(CITE_DESC)]
    ids = keep + [
        {"type": "doi", "value": concept_doi,
         "description": f"{CITE_DESC}, all versions (resolves to the newest)"},
        {"type": "doi", "value": doi, "description": f"{CITE_DESC}, version {version}"},
    ]
    lines, out, skip = text.splitlines(), [], False
    for line in lines:
        if skip:
            if line.startswith((" ", "-", "\t")) or not line.strip():
                continue
            skip = False
        if line.startswith("identifiers:"):
            skip = True
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    block = yaml.safe_dump({"identifiers": ids}, sort_keys=False, default_flow_style=False)
    new = "\n".join(out) + "\n" + block
    check = yaml.safe_load(new)
    if {k: v for k, v in check.items() if k != "identifiers"} != \
            {k: v for k, v in cff.items() if k != "identifiers"}:
        raise ZenodoError(f"{CITATION}: rewriting identifiers would change other fields; not written")
    p.write_text(new)


def _under(path: str, group_paths: list[str]) -> bool:
    path = path.rstrip("/")
    return any(path == g or path.startswith(g + "/") for g in group_paths)


# --------------------------------------------------------------------------- release

def _md5_of(entry: dict) -> str:
    c = str(entry.get("checksum") or "")
    return c.split(":", 1)[1] if c.startswith("md5:") else c


def release(root: Path, version: str, *, production: bool = False, api_url: str | None = None,
            token_file: str | None = None, build_dir: Path | None = None,
            cite: bool | None = None, date: dt.date | None = None, log=print) -> dict:
    """Build, check, upload and publish one release. Returns the new release entry.

    ``cite`` controls writing the DOIs to CITATION.cff and the datasets' ``zenodo`` fields;
    default: only for production (sandbox DOIs do not resolve).
    """
    root = Path(root)
    server, base = resolve_server(production, api_url)
    if not version or not str(version).strip():
        raise ZenodoError("--version is required")
    manifest = read_manifest(root)
    sec = section(manifest, server)
    if any(str(r.get("version")) == str(version) for r in sec["releases"]):
        raise ZenodoError(f"version {version} was already released on {server}")
    token = load_token(server, token_file)
    build_dir = Path(build_dir) if build_dir else root / "data" / ".zenodo-build" / server
    plan = make_plan(root, server, base, build_dir)
    log(format_plan(plan, version))
    if plan.errors:
        raise ZenodoError("refused before any upload: " + "; ".join(plan.errors))
    if plan.previous and not plan.changed:
        raise ZenodoError("nothing changed since the previous release; no new version made")
    date = date or dt.datetime.now(dt.timezone.utc).date()
    metadata = build_metadata(root, manifest, version, date, plan.groups, plan.owners)
    cite = (server == "production") if cite is None else cite

    api = API(base, token)
    if sec.get("draft_id"):
        draft = api.get(sec["draft_id"])
        log(f"resuming unpublished draft {draft['id']}")
    elif plan.previous:
        draft = api.new_version(plan.previous["record_id"])
        log(f"created new-version draft {draft['id']}")
    else:
        draft = api.create()
        log(f"created deposition {draft['id']}")
    dep_id = draft["id"]
    sec["draft_id"] = dep_id
    write_manifest(root, manifest)

    existing = {e["filename"]: e for e in api.list_files(dep_id)}
    wanted = {f.filename: f for f in plan.files}
    for name, e in existing.items():
        if name not in wanted:
            api.delete_file(dep_id, e["id"])
            log(f"removed {name}")
    bucket = (draft.get("links") or {}).get("bucket")
    reused, uploaded = [], []
    for f in plan.files:
        e = existing.get(f.filename)
        if e and _md5_of(e) == f.md5 and int(e.get("filesize", f.size)) == f.size:
            reused.append(f.filename)
            log(f"kept {f.filename} (unchanged)")
            continue
        if e:
            api.delete_file(dep_id, e["id"])
        if not bucket:
            raise ZenodoError(f"draft {dep_id} has no bucket link; cannot upload")
        r = api.upload(bucket, f.filename, f.local)
        if _md5_of(r or {}) != f.md5 or int((r or {}).get("size", -1)) != f.size:
            raise ZenodoError(f"upload of {f.filename} does not match the local file "
                              f"(md5/size); draft {dep_id} left unpublished")
        uploaded.append(f.filename)
        log(f"uploaded {f.filename}")
    final = {e["filename"]: e for e in api.list_files(dep_id)}
    if set(final) != set(wanted):
        raise ZenodoError(f"draft {dep_id} holds {sorted(final)}, expected {sorted(wanted)}; "
                          "not published")
    api.update_metadata(dep_id, metadata)
    pub = api.publish(dep_id)
    doi, concept = pub.get("doi"), pub.get("conceptdoi")
    if not doi:
        raise ZenodoError(f"publish of {dep_id} returned no DOI")

    entry = {
        "version": str(version),
        "date": date.isoformat(),
        "record_id": pub.get("id", dep_id),
        "doi": doi,
        "files": {f.filename: {"sha256": f.sha256, "md5": f.md5, "size": f.size}
                  for f in plan.files},
        "groups": {g.filename: list(g.paths) for g in plan.groups},
    }
    sec["releases"].append(entry)
    sec.pop("draft_id", None)
    sec["concept_doi"] = concept or sec.get("concept_doi")
    if pub.get("conceptrecid"):
        sec["concept_recid"] = pub["conceptrecid"]
    if cite:
        gpaths = [p for g in plan.groups for p in g.paths]
        for d in manifest.get("datasets") or []:
            if isinstance(d, dict) and d.get("path") and _under(str(d["path"]), gpaths):
                d["zenodo"] = doi
    write_manifest(root, manifest)
    if cite:
        write_citation(root, doi, sec["concept_doi"], str(version))
    for f in plan.files:
        if f.local and f.local.exists():
            f.local.unlink()
    log(f"published version {version}: doi {doi}, concept doi {sec['concept_doi']} "
        f"({len(uploaded)} uploaded, {len(reused)} kept)")
    return entry
