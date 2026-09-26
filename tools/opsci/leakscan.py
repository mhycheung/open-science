"""The leak scan: refuse to publish anything that carries internal information.

Generalised from a working project's site leak gate. It scans every file of a tree: the
file's path, its bytes, the text chunks of PNG images (not their compressed pixels), and
the dictionaries and strings of PDFs. Any hit refuses the publish, names the file and prints
the matching line.

There is no override flag. If a legitimate string matches, change the string, not the scan.

Pattern sources:
- fixed patterns (absolute paths, emails, IPv4 addresses, SLURM job identifiers);
- site identifiers, as literals: the current user name, this host's name and domain, and
  the values in ``config/site.local.yaml`` (scratch path, account, partition, and the list
  ``identifiers``). A partition named by a plain word (``shared``, ``gpu``) matches only
  where it names the partition (``--partition=shared``, ``-p shared``, ``partition: shared``);
  as a bare word it is ordinary English;
- the project's private patterns, the fenced block in ``publish/PRIVATE_POLICY.md``.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import struct
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
    """Text of a PNG's tEXt, zTXt and iTXt chunks (where plotting tools put metadata)."""
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
        except (zlib.error, IndexError, UnicodeDecodeError):
            out.append("<unreadable PNG text chunk>")
        if ctype == b"IEND":
            break
    return "\n".join(out)


def scan_text(text: str, rel: str, where: str, patterns) -> list[Hit]:
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
                hits.append(Hit(rel, where, pat.name, n if where != "filename" else None, line,
                                m.group(0)))
    return hits


def scan_file(path: Path, rel: str, patterns) -> list[Hit]:
    hits = scan_text(rel, rel, "filename", patterns)
    if path.is_symlink() or not path.is_file():
        return hits
    data = path.read_bytes()
    if data.startswith(PNG_MAGIC):
        # Text chunks only: the pixel data is compressed, and random bytes match short patterns.
        meta = _png_text(data)
        return hits + (scan_text(meta, rel, "image-metadata", patterns) if meta else [])
    if pdf.is_pdf(data):
        # Dictionaries and strings only: compressed streams are random bytes, and runs of PDF
        # names look like paths. With those gone, every pattern applies.
        return hits + scan_text(pdf.scan_text(data), rel, "content", patterns)
    try:
        text = data.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        text = data.decode("latin-1")
        binary = True
    return hits + scan_text(text, rel, "content", [p for p in patterns if p.binary] if binary else patterns)


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
