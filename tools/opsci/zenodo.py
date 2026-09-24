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
  and never printed.

The legacy deposit API is used: ``deposit/depositions`` (create, get, update metadata,
``actions/newversion``, ``actions/publish``), ``.../files`` (list, delete) and the bucket
link for uploads.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import os
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


def _members(root: Path, paths: list[str]) -> list[tuple[str, Path]]:
    """(arcname, filesystem path) for everything under the given project paths, sorted.

    Arcnames are relative to ``data/``. A path that is itself a symlink (``data/<task>`` may
    point to scratch) is followed; symlinks below it are stored as symlinks.
    """
    out = {}
    for rel in paths:
        top = root / rel
        arc_top = PurePosixPath(rel).relative_to("data").as_posix()
        if top.is_dir():
            out[arc_top] = top
            for dirpath, dirnames, filenames in os.walk(top):
                d = Path(dirpath)
                arc_d = PurePosixPath(arc_top, d.relative_to(top).as_posix()).as_posix()
                for name in dirnames + filenames:
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
    """Rewrite the manifest, keeping its leading comment block."""
    p = Path(root) / MANIFEST
    head = []
    for line in p.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            head.append(line)
        else:
            break
    body = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
    tmp = p.with_name(".MANIFEST.yaml.tmp")  # dot name: never taken for a tar group
    tmp.write_text("\n".join(head + [body.rstrip("\n")]) + "\n")
    tmp.replace(p)


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
    plan = Plan(server, api_url, groups, uncovered=uncovered)
    sec = section(manifest, server)
    plan.previous = sec["releases"][-1] if sec["releases"] else None
    prev_files = (plan.previous or {}).get("files") or {}
    if not groups:
        plan.errors.append("no tar groups: data/ is empty and zenodo.groups is not set")
        return plan
    # The file-count limit needs no tar: refuse before building anything.
    plan.errors += check_limits({g.filename: 0 for g in groups} | {FILE_LIST: 0})
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
    text = "# path\tsize\tsha256\ttar\n" + "".join(f"{p}\t{s}\t{h}\t{t}\n" for p, s, h, t in sorted(listing))
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
    if plan.files and not plan.changed and plan.previous:
        out.append("note: nothing changed since the previous release")
    for e in plan.errors:
        out.append(f"REFUSED: {e}")
    return "\n".join(out)


# --------------------------------------------------------------------------- server and token

def resolve_server(production: bool, api_url: str | None) -> tuple[str, str]:
    """(server kind, API base URL). Kinds: sandbox, production, custom (a local test server)."""
    if api_url:
        host = (urllib.parse.urlparse(api_url).hostname or "").lower()
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

    def _call(self, method, url, body=None, json_body=None, length=None):
        if not url.startswith(("http://", "https://")):
            url = self.base + "/" + url.lstrip("/")
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


def build_metadata(root: Path, manifest: dict, version: str, date: dt.date) -> dict:
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
    md = {
        "upload_type": "dataset",
        "title": f"{title}: data",
        "creators": creators,
        "description": (f"Data for {title}. Each .tar.gz holds one group of datasets; "
                        f"{FILE_LIST} lists every file with its size, sha256 checksum and tar."),
        "access_right": "open",
        "license": "cc-by-4.0",
    }
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
    metadata = build_metadata(root, manifest, version, date)
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
