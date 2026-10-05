"""The leak scan: refuse to publish anything that carries internal information.

Generalised from a working project's site leak gate. It scans every file of a tree: the
file's path, its bytes (UTF-16 text decoded), the text chunks of PNG images (not their
compressed pixels), the EXIF and XMP metadata of JPEG images, the dictionaries, strings and
page text of PDFs, and the members of zip and tar archives and of .gz, .bz2 and .xz files.
An archive it cannot read refuses the publish. Any hit the user has not overridden refuses the
publish, names the file and prints the matching line.

There is no override flag here. A few patterns flag information that is often harmless
(SLURM job numbers, in ``OVERRIDABLE``); the user may accept their hits in the publish
manifest (``overrides:``, see publish.py), and the site build may be told to accept them
(``opsci site build --allow-leak``). Every other hit is fixed by changing the string, not the scan.

Pattern sources:
- fixed patterns (absolute paths, emails, IPv4 addresses, SLURM job identifiers);
- site identifiers, as literals: the current user name, this host's name and domain (neither
  on a GitHub Actions runner), and the values in ``config/site.local.yaml`` (scratch path,
  account, partition, and the list ``identifiers``). A partition named by a plain word
  (``shared``, ``gpu``) matches only where it names the partition (``--partition=shared``,
  ``-p shared``, ``partition: shared``); as a bare word it is ordinary English;
- the project's private patterns, the fenced block in ``publish/PRIVATE_POLICY.md``.
"""

from __future__ import annotations

import bz2
import getpass
import gzip
import html
import io
import lzma
import os
import re
import socket
import struct
import tarfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import pdf

# An absolute POSIX path: '/' + at least two path segments, not part of a URL or a relative
# path. '~/...' is allowed (it names a per-user location without naming the user).
ABS_PATH_RE = re.compile(r"(?<![\w.~:/)\]}>-])/[A-Za-z][\w.-]*(?:/[\w.-]+)+")
ABS_PATH_ALLOWED = ("/dev/null", "/usr/bin/env", "/bin/bash", "/bin/sh")
# Files whose leading '/' anchors a pattern at the repo root; not a filesystem path.
ABS_PATH_EXEMPT_FILES = (".gitignore", ".gitattributes")
# A file containing this marker line holds deliberately planted leaks (test inputs). Only
# the framework's self-scan honours it, and only inside tests/. A project export never does.
PLANTED_MARKER = "opsci: planted-leaks"

POLICY_FILE = "publish/PRIVATE_POLICY.md"
SITE_CONFIG = "config/site.local.yaml"
# Literal identifiers shorter than this are not scanned (too many chance matches).
MIN_IDENTIFIER = 3
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class LeakScanError(Exception):
    """A pattern source is unusable (bad regex in the policy file, unreadable config)."""


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: re.Pattern
    why: str
    # False for a pattern short and generic enough to match by chance inside binary data.
    # Such a pattern still scans file names, text files and PNG text chunks.
    binary: bool = True
    # Matches of this regex are not leaks (reserved example domains, loopback addresses).
    allowed: re.Pattern | None = None


@dataclass(frozen=True)
class Hit:
    path: str  # relative to the scanned root, POSIX separators
    where: str  # filename | content | image-metadata
    pattern: str
    line_number: int | None
    line: str
    match: str

    def render(self) -> str:
        loc = f"line {self.line_number}" if self.line_number is not None else "-"
        return (f"{self.path} [{self.where}, {loc}] {self.pattern}: {self.match!r}\n"
                f"    {self.line.strip()[:300]}")


def _p(name, pattern, why, flags=0, binary=True, allowed=None) -> Pattern:
    return Pattern(name, re.compile(pattern, flags), why, binary,
                   re.compile(allowed) if allowed else None)


FIXED_PATTERNS: tuple[Pattern, ...] = (
    # ASCII: in binary data decoded as latin-1, a byte such as 0xfe is a letter to a Unicode
    # regex, and the look-behind would then hide a path that follows it.
    _p("absolute-path", ABS_PATH_RE.pattern, "an absolute path names one machine's layout",
       re.ASCII),
    _p("email",
       # The final label must be 2+ letters: every real top-level domain is, and a digit or
       # one-letter tail is almost always a chance match in data.
       r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}",
       "an email address", binary=False,
       # RFC 2606 / 6761 reserved names never reach a real mailbox; git@github.com is
       # GitHub's SSH login, the same for every user.
       allowed=r"(?:.*@(?:[\w.-]+\.)?(?:example\.(?:com|org|net)|[\w-]+\.(?:invalid|test|example))"
               r"|git@github\.com)$"),
    # Not inside a longer dotted run and not after '-' or a version operator: package
    # versions (alsa-lib-1.2.16.1, >=0.2026.6.22.1.23.34, ==2.0.1.3) are not addresses.
    _p("ipv4", r"(?<![\w.-])(?<![<>=!~]=)(?:25[0-5]|2[0-4]\d|1?\d?\d)"
       r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w-]|\.\d)",
       "an IP address names a machine", binary=False,
       # loopback, the unspecified address, and the RFC 5737 documentation ranges
       allowed=r"(?:127\.\d+\.\d+|192\.0\.2|198\.51\.100|203\.0\.113)\.\d+$|0\.0\.0\.0$"),
    _p("slurm-job-id", r"(?:slurm|sbatch|squeue|jobid|job[ _\-]?id)[ _\-:=]*\d{3,}",
       "a SLURM job identifier", re.IGNORECASE),
    _p("slurm-array-id", r"\b\d{6,9}_\d{1,5}\b", "a SLURM array job/task identifier",
       binary=False),
    _p("slurm-out-file", r"\bslurm-\d+\.out\b", "a SLURM output file name", re.IGNORECASE),
)


# Patterns whose hits the user may override (publish/manifest.yaml `overrides:`), and why each
# is flagged, in plain words for the user who decides. A SLURM job number names no person,
# machine or path by itself. Every other pattern names a person, a machine, a path, an
# address or a private term, and is never overridden.
OVERRIDABLE: dict[str, str] = {
    "slurm-job-id": "A SLURM job number (`SLURM` or `jobid=` followed by a number) records that work "
                    "ran on a batch cluster. With other details it could tie the text to an account "
                    "there; by itself it names no person, machine or path.",
    "slurm-array-id": "A number such as `<job>_<task>` (six to nine digits, an underscore, a short "
                      "number) looks like a SLURM array job and task number, with the same small risk "
                      "as a job number. It may also be an ordinary number with an underscore.",
    "slurm-out-file": "A file name such as `slurm-<job>.out` is a SLURM log name; it carries a job "
                      "number, with the same small risk.",
}
assert set(OVERRIDABLE) <= {p.name for p in FIXED_PATTERNS}


def _literal(name: str, value: str, why: str) -> Pattern:
    # Word-ish boundaries so that a short user name does not match inside a longer word.
    return _p(name, r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", why)


def host_identifiers() -> list[str]:
    """This host's fully qualified name and its domain (the name minus its first label)."""
    out = []
    for name in {socket.gethostname(), socket.getfqdn()}:
        if not name or name in ("localhost", "localhost.localdomain") or len(name) < MIN_IDENTIFIER:
            continue
        out.append(name)
        labels = name.split(".")
        if len(labels) >= 3:
            out.append(".".join(labels[1:]))
    return sorted(set(out))


def site_identifiers(root: Path | None) -> list[tuple[str, str]]:
    """(source, literal) pairs that name this site: user, host, and site.local.yaml values."""
    pairs: list[tuple[str, str]] = []
    # On a GitHub Actions runner the account ("runner") and host belong to GitHub, not to the
    # project's site; the site build there must not refuse the English word "runner".
    if os.environ.get("GITHUB_ACTIONS") != "true":
        try:
            user = getpass.getuser()
        except Exception:  # no user database entry; nothing to add
            user = os.environ.get("USER", "")
        if user:
            pairs.append(("user name", user))
        pairs += [("host name", h) for h in host_identifiers()]
    if root is not None:
        cfg_path = Path(root) / SITE_CONFIG
        if cfg_path.is_file():
            try:
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise LeakScanError(f"{SITE_CONFIG}: not valid YAML: {exc}") from exc
            batch = cfg.get("batch") or {}
            for key, val in (("scratch", cfg.get("scratch")), ("batch.account", batch.get("account")),
                             ("batch.partition", batch.get("partition"))):
                if isinstance(val, str):
                    pairs.append((f"{SITE_CONFIG} {key}", val))
            for val in cfg.get("identifiers") or []:
                pairs.append((f"{SITE_CONFIG} identifiers", str(val)))
    seen, out = set(), []
    for src, val in pairs:
        val = val.strip().rstrip("/")
        if len(val) >= MIN_IDENTIFIER and "<" not in val and val not in seen:
            seen.add(val)
            out.append((src, val))
    return out


def policy_patterns(root: Path) -> list[Pattern]:
    """The regexes in the first fenced block after the 'Patterns' heading of the policy file."""
    path = Path(root) / POLICY_FILE
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out, in_section, in_fence = [], False, False
    for i, line in enumerate(lines, 1):
        if line.startswith("#") and not in_fence:
            in_section = line.lstrip("#").strip().lower().startswith("patterns")
            continue
        if not in_section:
            continue
        if line.strip().startswith("```"):
            if in_fence:
                break
            in_fence = True
            continue
        if in_fence and line.strip() and not line.strip().startswith("#"):
            try:
                out.append(Pattern(f"private-policy:{i}", re.compile(line.strip()),
                                   f"{POLICY_FILE} line {i}"))
            except re.error as exc:
                raise LeakScanError(f"{POLICY_FILE} line {i}: bad regular expression: {exc}") from exc
    return out


def _partition(name: str, value: str, why: str) -> Pattern:
    return _p(name, r"(?:partition\b|(?<![\w-])-p)[\s\"'=:]*" + re.escape(value) + r"(?![A-Za-z0-9])",
              why, re.IGNORECASE)


def patterns_for(root: Path | None) -> list[Pattern]:
    """Every pattern that applies to a project (or, with root None, to any tree on this site)."""
    pats = list(FIXED_PATTERNS)
    for src, val in site_identifiers(root):
        make = _partition if src.endswith("batch.partition") and val.isalpha() else _literal
        pats.append(make(f"site-identifier ({src})", val, f"names this site ({src})"))
    if root is not None:
        pats += policy_patterns(root)
    return pats


def _png_text(data: bytes) -> str:
    """Text of a PNG's tEXt, zTXt, iTXt and eXIf chunks (where plotting tools and cameras put
    metadata)."""
    out, pos = [], len(PNG_MAGIC)
    while pos + 8 <= len(data):
        (length,), ctype = struct.unpack(">I", data[pos:pos + 4]), data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        try:
            if ctype == b"tEXt":
                out.append(body.replace(b"\0", b"=").decode("latin-1"))
            elif ctype == b"zTXt":
                key, _, rest = body.partition(b"\0")
                out.append(key.decode("latin-1") + "=" + zlib.decompress(rest[1:]).decode("latin-1"))
            elif ctype == b"iTXt":
                key, _, rest = body.partition(b"\0")
                flag, rest = rest[0], rest[2:]
                _lang, _, rest = rest.partition(b"\0")
                _tkey, _, text = rest.partition(b"\0")
                text = zlib.decompress(text) if flag else text
                out.append(key.decode("latin-1") + "=" + text.decode("utf-8", "replace"))
            elif ctype == b"eXIf":
                out += [m.group().decode("latin-1") for m in META_STRINGS_RE.finditer(body)]
        except (zlib.error, IndexError, UnicodeDecodeError):
            out.append("<unreadable PNG text chunk>")
        if ctype == b"IEND":
            break
    return "\n".join(out)


def scan_text(text: str, rel: str, where: str, patterns, line_numbers: bool = True) -> list[Hit]:
    hits = []
    lines = text.splitlines() or [text]
    exempt_paths = Path(rel).name in ABS_PATH_EXEMPT_FILES
    for pat in patterns:
        if exempt_paths and pat.name == "absolute-path":
            continue
        for n, line in enumerate(lines, 1):
            for m in pat.regex.finditer(line):
                if pat.name == "absolute-path" and m.group(0).startswith(ABS_PATH_ALLOWED):
                    continue
                if pat.allowed is not None and pat.allowed.match(m.group(0)):
                    continue
                number = n if line_numbers and not where.startswith("filename") else None
                hits.append(Hit(rel, where, pat.name, number, line, m.group(0)))
    return hits


def _utf16(data: bytes) -> str | None:
    """The text of UTF-16 data (with a byte order mark, or ASCII text with every other byte
    NUL), or None. Decoded as latin-1, its NUL bytes would split every word."""
    if len(data) < 4:
        return None
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        codec = "utf-16"
    else:
        sample = data[:4096]
        even, odd = sample[0::2], sample[1::2]
        if odd.count(0) >= 0.9 * len(odd) and even.count(0) <= 0.1 * len(even):
            codec = "utf-16-le"
        elif even.count(0) >= 0.9 * len(even) and odd.count(0) <= 0.1 * len(odd):
            codec = "utf-16-be"
        else:
            return None
    try:
        text = data.decode(codec)
    except UnicodeDecodeError:
        return None
    # Random bytes after a chance byte order mark decode to unassigned and private-use characters.
    shown = sum(1 for c in text if c.isprintable() or c in "\t\n\r")
    return text if shown >= 0.95 * len(text) else None


# Runs of printable ASCII in binary data, like `strings`. Long enough that random bytes (a
# compressed stream, pixels) almost never produce one (about once per 10^7 bytes), so the
# patterns kept off binary data (emails, IP addresses) can run on these runs.
STRINGS_RE = re.compile(rb"[\x20-\x7e]{16,}")
# Shorter runs in image metadata (EXIF and XMP), which holds text by design.
META_STRINGS_RE = re.compile(rb"[\x20-\x7e\t\n\r]{4,}")
JPEG_MAGIC = b"\xff\xd8\xff"


def _jpeg_meta(data: bytes) -> str:
    """Printable text of a JPEG's APPn (EXIF, XMP, ICC) and comment segments."""
    out, pos = [], 2
    while pos + 4 <= len(data) and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xDA, 0xD9):  # start of scan: the compressed pixels follow
            break
        (length,) = struct.unpack(">H", data[pos + 2:pos + 4])
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:
            out += [m.group().decode("latin-1") for m in META_STRINGS_RE.finditer(data[pos + 4:pos + 2 + length])]
        pos += 2 + length
    return "\n".join(out)


# Archives are opened and their members scanned like files (a .zip, and .npz, .docx, .xlsx,
# which are zip files; a .tar, compressed or not; a .gz, .bz2 or .xz file). Formats the
# standard library cannot read are refused. Limits keep a decompression bomb out of memory.
ARCHIVE_MAX_BYTES = 1 << 30  # uncompressed bytes read from one archive, members included
ARCHIVE_MAX_DEPTH = 3  # archives inside archives
UNREADABLE_MAGIC = {b"7z\xbc\xaf\x27\x1c": "a 7z archive", b"Rar!\x1a\x07": "a RAR archive"}
UNSCANNED = "unscanned-archive"


def _unscanned(rel: str, member: str, why: str) -> Hit:
    return Hit(rel, _where("content", member), UNSCANNED, None,
               f"{why}: its content cannot be scanned; publish the files themselves, or a zip or "
               "tar archive", why)


def _where(kind: str, member: str) -> str:
    return f"{kind} of member {member}" if member else kind


def _decompress(data: bytes, kind: str, limit: int) -> bytes | None:
    """The decompressed bytes, or None if they exceed ``limit``."""
    if kind == "gz":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            out = f.read(limit + 1)
    else:
        d = bz2.BZ2Decompressor() if kind == "bz2" else lzma.LZMADecompressor()
        out = d.decompress(data, max_length=limit + 1)
    return None if len(out) > limit else out


def _is_tar(data: bytes) -> bool:
    return len(data) > 262 and data[257:262] == b"ustar"


def _members(data: bytes, rel: str, member: str):
    """[(member name, bytes or None, problem Hit or None)] for the files of an archive, or None
    if ``data`` is not an archive. A compressed single file is one member with an empty name.
    What cannot be read gives a problem Hit, which refuses the publish."""
    for magic, what in UNREADABLE_MAGIC.items():
        if data.startswith(magic):
            return [("", None, _unscanned(rel, member, what))]
    too_big = f"more than {ARCHIVE_MAX_BYTES} bytes uncompressed"
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        out, budget = [], ARCHIVE_MAX_BYTES
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    if info.flag_bits & 0x1:
                        out.append((info.filename, None, _unscanned(rel, member, f"{info.filename}: encrypted")))
                        continue
                    with z.open(info) as f:
                        body = f.read(budget + 1)
                    if len(body) > budget:
                        out.append((info.filename, None, _unscanned(rel, member, too_big)))
                        break
                    budget -= len(body)
                    out.append((info.filename, body, None))
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError, zlib.error) as exc:
            out.append(("", None, _unscanned(rel, member, f"an unreadable zip file ({exc})")))
        return out
    kind = ("gz" if data.startswith(b"\x1f\x8b") else "bz2" if data.startswith(b"BZh")
            else "xz" if data.startswith(b"\xfd7zXZ\x00") else None)
    if kind is None and not _is_tar(data):
        return None
    raw = data
    if kind is not None:
        try:
            raw = _decompress(data, kind, ARCHIVE_MAX_BYTES)
        except (OSError, EOFError, ValueError, lzma.LZMAError) as exc:
            return [("", None, _unscanned(rel, member, f"unreadable {kind} data ({exc})"))]
        if raw is None:
            return [("", None, _unscanned(rel, member, too_big))]
        if not _is_tar(raw):
            return [("", raw, None)]
    out = []
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            for m in tar:
                if m.isfile():
                    out.append((m.name, tar.extractfile(m).read(), None))
    except (tarfile.TarError, OSError, EOFError) as exc:
        out.append(("", None, _unscanned(rel, member, f"an unreadable tar file ({exc})")))
    return out


def scan_bytes(data: bytes, rel: str, patterns, member: str = "", depth: int = 0) -> list[Hit]:
    """Scan the content of file ``rel``, or of its archive member ``member`` (a path such as
    ``inner.tar!a/b.txt``, members of nested archives joined by '!')."""
    members = _members(data, rel, member)
    if members is not None:
        if depth >= ARCHIVE_MAX_DEPTH:
            return [_unscanned(rel, member, f"archives nested more than {ARCHIVE_MAX_DEPTH} deep")]
        hits = []
        for name, body, problem in members:
            if problem is not None:
                hits.append(problem)
                continue
            inner = f"{member}!{name}" if member and name else (name or member or "(decompressed)")
            if name:
                hits += scan_text(name, rel, _where("filename", inner), patterns)
            hits += scan_bytes(body, rel, patterns, inner, depth + 1)
        return hits
    name = member.rsplit("!", 1)[-1] if member else rel
    content, meta_where = _where("content", member), _where("image-metadata", member)
    if data.startswith(PNG_MAGIC):
        # Text chunks only: the pixel data is compressed, and random bytes match short patterns.
        meta = _png_text(data)
        return scan_text(meta, rel, meta_where, patterns) if meta else []
    if pdf.is_pdf(data):
        # Dictionaries, strings and page text only: compressed streams are random bytes, and runs
        # of PDF names look like paths. With those gone, every pattern applies.
        return scan_text(pdf.scan_text(data), rel, content, patterns)
    binary_pats = [p for p in patterns if p.binary]
    text16 = _utf16(data)
    text = text16
    if text is None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    if text is not None:
        if name.endswith((".html", ".htm", ".xml", ".json")):
            # Markup escapes '<' and '>' as entities, whose ';' would otherwise start an absolute path.
            text = html.unescape(text)
        hits = scan_text(text, rel, content, patterns)
        if text16 is not None:  # the byte order mark may be chance: scan the bytes as binary data too
            hits += scan_text(data.decode("latin-1"), rel, content, binary_pats)
        return hits
    # Binary data: the patterns that do not match by chance run on all of it; the others run on
    # its long printable runs, and on all the text of a JPEG's metadata.
    hits = scan_text(data.decode("latin-1"), rel, content, binary_pats)
    chance = [p for p in patterns if not p.binary]
    if chance:
        if data.startswith(JPEG_MAGIC):
            meta = _jpeg_meta(data)
            if meta:
                hits += scan_text(meta, rel, meta_where, chance)
        runs = "\n".join(m.group().decode("ascii") for m in STRINGS_RE.finditer(data))
        if runs:
            hits += scan_text(runs, rel, content + " (printable runs)", chance, line_numbers=False)
    return hits


def scan_file(path: Path, rel: str, patterns) -> list[Hit]:
    hits = scan_text(rel, rel, "filename", patterns)
    if path.is_symlink() or not path.is_file():
        return hits
    return hits + scan_bytes(path.read_bytes(), rel, patterns)


def scan_tree(root: Path, patterns, files=None, honour_planted_marker: bool = False) -> list[Hit]:
    """Scan ``files`` (paths relative to root; default: every file under root).

    ``honour_planted_marker`` is for the framework's self-scan only: a file inside a
    ``tests`` directory that carries PLANTED_MARKER is skipped.
    """
    root = Path(root)
    if files is None:
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                       if (p.is_file() or p.is_symlink()) and ".git" not in p.relative_to(root).parts)
    hits = []
    for rel in files:
        path = root / rel
        if honour_planted_marker and "tests" in Path(rel).parts[:-1] and path.is_file():
            try:
                if PLANTED_MARKER in path.read_text(encoding="utf-8"):
                    continue
            except UnicodeDecodeError:
                pass
        hits += scan_file(path, rel, patterns)
    return hits


def format_hits(hits) -> str:
    return "\n".join(h.render() for h in hits)
