"""`opsci guide check`: the user guide stays short, and everything it names exists.

Checks a markdown file (default `USER_GUIDE.md` of the framework repo):
- at most `max_words` words (readable in about 3 minutes);
- every path in backticks exists in the framework repo or in its `template/`; for a path
  with a placeholder (`tasks/<id>/context.md`) only the part before the first `<...>`
  segment is checked (`tasks/`), since a template has no instances; paths under `~` or
  `$VAR` name the reader's own machine and are skipped;
- every `opsci <group> [<command>]` it names is a real command;
- every `<plugin>:<skill>` it names, for a plugin under `plugins/` or `extras/`, is a real skill.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CODE_RE = re.compile(r"`([^`\n]+)`")
OPSCI_RE = re.compile(r"\bopsci ([a-z][\w-]*)(?: ([a-z][\w-]*))?")
SKILL_RE = re.compile(r"(?<![\w/.-])([a-z][\w-]*):([a-z][\w-]*)")
PATHY_RE = re.compile(r"^[\w.<>*-]+(/[\w.<>*-]*)*$")
FILE_EXT = (".md", ".yaml", ".yml", ".json", ".py", ".sh", ".toml", ".bib", ".cff", ".html")


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][\w'’.-]*", text))


def _is_path(tok: str) -> bool:
    if tok.startswith(("~", "$", "http", "-")) or " " in tok or not PATHY_RE.match(tok):
        return False
    return "/" in tok or tok.endswith(FILE_EXT)


def _exists(tok: str, roots: list[Path]) -> bool:
    parts = tok.rstrip("/").split("/")
    if any("<" in p for p in parts):
        parts = parts[:next(i for i, p in enumerate(parts) if "<" in p)]
        if not parts:
            return True
    return any((r / "/".join(parts)).exists() for r in roots)


def _opsci_ok(group: str, cmd: str | None) -> bool:
    args = [group] + ([cmd] if cmd else []) + ["--help"]
    r = subprocess.run([sys.executable, "-m", "opsci.cli", *args], capture_output=True)
    return r.returncode == 0


def check(guide: Path, repo: Path, max_words: int = 600) -> list[str]:
    guide, repo = Path(guide), Path(repo)
    if not guide.is_file():
        return [f"{guide}: not found"]
    text = guide.read_text(encoding="utf-8")
    problems = []
    n = word_count(text)
    if n > max_words:
        problems.append(f"{n} words, over the limit of {max_words}")
    roots = [repo, repo / "template"]
    seen = set()
    for tok in CODE_RE.findall(text):
        tok = tok.strip()
        if tok in seen:
            continue
        seen.add(tok)
        if _is_path(tok) and not _exists(tok, roots):
            problems.append(f"names a path that does not exist: {tok}")
    for g, c in sorted(set(OPSCI_RE.findall(text))):
        if not _opsci_ok(g, c or None):
            problems.append(f"names an unknown command: opsci {g} {c}".rstrip())
    plugins = {p.name: p for d in ("plugins", "extras") if (repo / d).is_dir()
               for p in (repo / d).iterdir() if (p / ".claude-plugin" / "plugin.json").is_file()}
    for plugin, skill in sorted(set(SKILL_RE.findall(text))):
        if plugin in plugins and not (plugins[plugin] / "skills" / skill / "SKILL.md").is_file():
            problems.append(f"names an unknown skill: {plugin}:{skill}")
    return problems
