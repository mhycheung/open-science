"""`opsci template`: copy the project template and fill its placeholders."""

from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# The path pattern and its exemptions are defined once, in the leak scan.
from .leakscan import ABS_PATH_ALLOWED, ABS_PATH_EXEMPT_FILES, ABS_PATH_RE, PLANTED_MARKER  # noqa: F401

# Every placeholder the template may use. A {{NAME}} not in this list is an error.
PLACEHOLDERS = (
    "PROJECT_NAME",      # short slug, e.g. ringdown-tests
    "PROJECT_TITLE",     # one-line human title
    "AUTHOR",            # the user's name
    "YEAR",              # YYYY, for licences
    "DATE",              # YYYY-MM-DD, the day the project was created
    "MONTH",             # YYYY-MM, names the first log file
    "FRAMEWORK_REPO",    # where the framework came from (URL), or "local copy"
    "FRAMEWORK_URL",     # the framework's web page (https), for links in the README
    "FRAMEWORK_COMMIT",  # framework commit the template was copied at
    "CONTEXT_MANAGEMENT",  # "true" if the project uses the open-science-context plugin
    "NOTION",            # "true" if the project is mirrored to Notion (opsci notion)
)

# Template text that depends on the optional components. A block runs from its opening marker
# line to its closing marker line and is kept only when its condition holds: `context` with
# the context-management component (plugin open-science-context), `no-context` without it,
# `notion` when the project is mirrored to Notion, `no-notion` when not, `framework-line` when
# the user agreed to the README line naming the framework. The marker lines themselves are
# always removed.
COMPONENTS = ("no-context", "context", "no-notion", "notion", "framework-line")
BLOCK_RE = re.compile(r"^[ \t]*<!-- opsci:(no-context|context|no-notion|notion|framework-line) -->[ \t]*\n(.*?)^[ \t]*<!-- /opsci:\1 -->[ \t]*\n",
                      re.M | re.S)
MARKER_RE = re.compile(r"<!-- /?opsci:(?:no-context|context|no-notion|notion|framework-line) -->")

# The framework's home, linked when the recorded repo has no web address (a local copy).
FRAMEWORK_HOME = "https://github.com/mhycheung/open-science"


def apply_components(text: str, context_management: bool, notion: bool = False,
                     framework_line: bool = False) -> str:
    keep = {"context" if context_management else "no-context", "notion" if notion else "no-notion"}
    if framework_line:
        keep.add("framework-line")
    return BLOCK_RE.sub(lambda m: m.group(2) if m.group(1) in keep else "", text)

# Files a fresh project must have.
REQUIRED = (
    "AGENTS.md", "CLAUDE.md", "README.md", "PROJECT.md", "context.md", "CITATION.cff",
    "LICENSE", "LICENSE-docs", ".gitignore", ".gitattributes",
    "log/README.md", "map/README.md",
    "citations/used.bib", "citations/consulted.md",
    "rules/README.md", "contracts/main.md", "contracts/subagent.md",
    "tasks/README.md", "src/README.md", "data/MANIFEST.yaml",
    "lit_cache/README.md", "archive/README.md", "messages/README.md",
    "config/site.example.yaml", "config/framework.yaml",
    "publish/manifest.yaml", "publish/PRIVATE_POLICY.md", "publish/LAST_PUBLISHED",
    ".claude/settings.json",
    ".claude/agents/med-effort.md", ".claude/agents/high-effort.md",
    ".claude/agents/low-effort.md", ".claude/agents/literature.md", ".claude/agents/text.md",
    "docs/README.md", "private-docs/README.md",
    "brainstorm/README.md", "brainstorm/context.md", "brainstorm/tasks/README.md",
    "brainstorm/map/README.md", "brainstorm/log/README.md",
    "verifications/README.md",
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")


class TemplateError(Exception):
    pass


def default_template_dir() -> Path | None:
    """The template/ directory of the framework checkout this package runs from, if any."""
    cand = Path(__file__).resolve().parents[2] / "template"
    return cand if (cand / "AGENTS.md").exists() else None


def _git(cwd: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def framework_origin(template_dir: Path) -> tuple[str, str]:
    """(repo, commit) of the framework checkout. Never returns a local filesystem path."""
    commit = _git(template_dir, "rev-parse", "HEAD") or "unknown"
    if commit != "unknown" and _git(template_dir, "status", "--porcelain", "--", "."):
        commit += "-dirty"
    url = _git(template_dir, "remote", "get-url", "origin") or ""
    if not url or url.startswith(("/", "file:", "~", ".")):
        url = "local copy"
    return url, commit


def web_url(repo: str) -> str:
    """The https page of a git remote (`git@host:a/b.git` -> `https://host/a/b`), or FRAMEWORK_HOME."""
    m = (re.fullmatch(r"git@([^:/]+):(.+?)(?:\.git)?/?", repo)
         or re.fullmatch(r"ssh://(?:[^@/]+@)?([^:/]+)(?::\d+)?/(.+?)(?:\.git)?/?", repo)
         or re.fullmatch(r"https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?", repo))
    return f"https://{m.group(1)}/{m.group(2)}" if m else FRAMEWORK_HOME


def text_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.relative_to(root).parts:
            try:
                yield p, p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue


def instantiate(dest: Path, name: str, title: str, author: str, template: Path | None = None,
                date: dt.date | None = None, framework_repo: str | None = None,
                context_management: bool = True, notion: bool = False,
                framework_line: bool = False) -> Path:
    template = Path(template) if template else default_template_dir()
    if template is None or not (template / "AGENTS.md").exists():
        raise TemplateError("template directory not found; pass --template <framework>/template")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise TemplateError(f"project name '{name}' must be lower case letters, digits and hyphens")
    for label, v in (("title", title), ("author", author)):
        if not v.strip() or any(c in v for c in '"\\\n'):
            raise TemplateError(f"{label} must be non-empty and contain no quote, backslash or newline")
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise TemplateError(f"destination '{dest}' exists and is not empty")
    created = not dest.exists()
    try:
        return _fill(dest, template, name, title, author, date, framework_repo, context_management, notion,
                     framework_line)
    except BaseException:
        # Leave nothing half-made behind.
        if created:
            shutil.rmtree(dest, ignore_errors=True)
        else:
            for child in dest.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        raise


def _fill(dest, template, name, title, author, date, framework_repo, context_management=True,
          notion=False, framework_line=False) -> Path:
    date = date or dt.date.today()
    repo, commit = framework_origin(template)
    values = {
        "PROJECT_NAME": name, "PROJECT_TITLE": title, "AUTHOR": author,
        "YEAR": f"{date.year:04d}", "DATE": date.isoformat(), "MONTH": date.strftime("%Y-%m"),
        "FRAMEWORK_REPO": framework_repo or repo, "FRAMEWORK_COMMIT": commit,
        "FRAMEWORK_URL": web_url(framework_repo or repo),
        "CONTEXT_MANAGEMENT": "true" if context_management else "false",
        "NOTION": "true" if notion else "false",
    }
    shutil.copytree(template, dest, dirs_exist_ok=True)

    unknown = []
    for p, text in text_files(dest):
        def sub(m):
            key = m.group(1)
            if key not in values:
                unknown.append(f"{p.relative_to(dest)}: {{{{{key}}}}}")
                return m.group(0)
            return values[key]
        new = PLACEHOLDER_RE.sub(sub, apply_components(text, context_management, notion, framework_line))
        if new != text:
            p.write_text(new, encoding="utf-8")
    if unknown:
        raise TemplateError("unknown placeholders in template: " + "; ".join(unknown))

    if notion:  # the auto-sync hook; JSON has no comments, so it is added here, not by markers
        from .notion.setup import add_hook
        add_hook(dest / ".claude" / "settings.json")

    log = dest / "log" / f"{values['MONTH']}.md"
    log.write_text(
        f"# Project log, {values['MONTH']}\n\n"
        f"{values['DATE']} — project created from the open-science template "
        f"(framework commit {commit}). → config/framework.yaml\n",
        encoding="utf-8",
    )
    from .mapbuild import build  # local import: mapbuild does not depend on this module
    for sub in (".", "brainstorm"):  # the project graph, and the brainstorm sub-root's own
        res, _ = build(dest / sub)
        if res.errors:
            raise TemplateError(f"map build failed on the new project ({sub}): "
                                + "; ".join(map(str, res.errors)))
    problems = verify_instance(dest)
    if problems:
        raise TemplateError("the new project fails its own check:\n  " + "\n  ".join(problems))
    return dest


def verify_instance(root: Path) -> list[str]:
    """Problems in an instantiated project: missing files, leftover placeholders, absolute paths."""
    root = Path(root)
    problems = [f"missing: {r}" for r in REQUIRED if not (root / r).exists()]
    for p, text in text_files(root):
        rel = p.relative_to(root).as_posix()
        for m in PLACEHOLDER_RE.finditer(text):
            problems.append(f"{rel}: placeholder {m.group(0)} left unfilled")
        for m in MARKER_RE.finditer(text):
            problems.append(f"{rel}: component marker {m.group(0)} left in place")
        if p.name in ABS_PATH_EXEMPT_FILES:
            continue
        for m in ABS_PATH_RE.finditer(text):
            if not m.group(0).startswith(ABS_PATH_ALLOWED):
                problems.append(f"{rel}: absolute path '{m.group(0)}'")
    problems += gitignore_problems(root)
    return problems


# (path, must it be ignored?) — checked against the project's .gitignore.
GITIGNORE_EXPECT = (
    ("data/some-task/output.h5", True),
    ("data/MANIFEST.yaml", False),
    ("config/site.local.yaml", True),
    ("config/site.example.yaml", False),
    ("config/notion.local.yaml", True),
    (".env", True),
    ("messages/2026-01-01_120000_run-finished.md", True),
    ("messages/README.md", False),
)


def gitignore_problems(root: Path) -> list[str]:
    """Check .gitignore behaviour with git itself, in a scratch repo holding only that file."""
    gi = Path(root) / ".gitignore"
    if not gi.exists():
        return []  # reported as missing already
    with tempfile.TemporaryDirectory() as tmp:
        if subprocess.run(["git", "init", "-q", tmp], capture_output=True).returncode != 0:
            return ["cannot run git to check .gitignore"]
        shutil.copy(gi, Path(tmp) / ".gitignore")
        problems = []
        for path, want in GITIGNORE_EXPECT:
            r = subprocess.run(["git", "-C", tmp, "check-ignore", "-q", "--no-index", path])
            if (r.returncode == 0) != want:
                problems.append(f".gitignore: '{path}' should {'' if want else 'not '}be ignored")
        return problems
