"""`opsci site`: build the project site from a public repo with MkDocs (Material theme).

Every markdown file becomes a page and appears in the navigation. Tabs: Results, Map, Dead
ends, Tasks, Citations, Context, Log, and Other for the rest. Pages with a node header get a
status banner (active, failed, superseded, ...) and a verification line. The build runs in
strict mode, so a broken link fails it, and the built site must pass the leak scan.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

import yaml

from . import leakscan, nodes

SKIP = (".git", ".github", ".opsci", "site", "_site")
TABS = (  # (tab title, predicate on the page path)
    ("Results", lambda p: p.startswith(("results/", "paper/"))),
    ("Map", lambda p: p in ("map/README.md", "map/graph.md") or (p.startswith("map/") and p != "map/dead_ends.md")),
    ("Dead ends", lambda p: p == "map/dead_ends.md"),
    ("Tasks", lambda p: p.startswith("tasks/")),
    ("Citations", lambda p: p.startswith("citations/")),
    ("Context", lambda p: p == "context.md"),
    ("Log", lambda p: p.startswith("log/")),
)
RESULT_TYPES = ("result", "page", "paper", "dataset")
BANNERS = {
    "active": ("warning", "Work in progress", "This is active work. Its results may change."),
    "paused": ("warning", "Paused", "Work on this has stopped for now. Its results may be incomplete."),
    "failed": ("failure", "Failed", "This route did not work. It is kept as a record of what was tried."),
    "superseded": ("note", "Superseded", "A later node replaces this one."),
    "abandoned": ("note", "Abandoned", "Work on this stopped before a result."),
}
VERIFICATION = {
    "unverified": "Not verified.",
    "verified": "Verified: a stated check was run against a provenance record.",
    "human-verified": "Human-verified: the project owner checked this result.",
}
MKDOCS_PINS = "mkdocs==1.6.1 mkdocs-material==9.7.7"


class SiteError(Exception):
    pass


def _title(src: Path) -> str:
    cff = src / "CITATION.cff"
    if cff.is_file():
        try:
            t = (yaml.safe_load(cff.read_text(encoding="utf-8")) or {}).get("title")
            if t:
                return str(t)
        except yaml.YAMLError:
            pass
    readme = src / "README.md"
    if readme.is_file():
        for line in readme.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return src.resolve().name


def _header(text: str) -> tuple[dict | None, str]:
    raw, present = nodes.read_front_matter(text)
    if not present or raw is None:
        return None, text
    try:
        h = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, text
    # the header is the opening ---, the raw lines and the closing ---
    body = "\n".join(text.splitlines()[raw.count("\n") + 3:])
    return (h if isinstance(h, dict) else None), body


def _decorate(text: str, rel: str, ids: dict[str, str], superseded_by: dict[str, list[str]]) -> str:
    """Put the status banner and verification line under the page's first heading."""
    h, body = _header(text)
    if not h or "status" not in h:
        return text
    lines = []
    kind, label, sentence = BANNERS.get(h["status"], (None, None, None))
    if kind:
        extra = ""
        if h["status"] == "superseded":
            by = superseded_by.get(h.get("id"), [])
            extra = (" See " + ", ".join(f"[{i}]({_rel_link(rel, ids[i])})" if i in ids else i
                                         for i in sorted(by)) + ".") if by else ""
        lines += [f'!!! {kind} "{label}"', f"    {sentence}{extra}", ""]
    level = h.get("verification", "unverified")
    if h.get("type") in RESULT_TYPES or level != "unverified":
        ev = h.get("evidence")
        ev_txt = f" Evidence: [`{ev}`]({_rel_link(rel, ev)})." if ev else ""
        lines += [f"**{VERIFICATION.get(level, level)}**{ev_txt}", ""]
    if not lines:
        return text
    body_lines = body.splitlines()
    at = next((i + 1 for i, l in enumerate(body_lines) if l.startswith("# ")), 0)
    meta = {k: h[k] for k in ("title",) if k in h}
    front = ("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n") if meta else ""
    return front + "\n".join(body_lines[:at] + [""] + lines + body_lines[at:]) + "\n"


def _rel_link(from_page: str, target: str) -> str:
    base = PurePosixPath(from_page).parent
    up = "../" * len(base.parts)
    return up + target


LINK_RE = re.compile(r"(\]\()([^)\s#?]+)((?:[#?][^)\s]*)?(?:\s+\"[^\"]*\")?\))")


def _fix_dir_links(text: str, rel: str, src: Path, listings: set[str]) -> str:
    """Point links at directories to the directory's README, or to a generated listing page."""
    def sub(m):
        target = m.group(2)
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("/"):
            return m.group(0)
        path = (src / PurePosixPath(rel).parent / target).resolve()
        if not path.is_dir() or not path.is_relative_to(src.resolve()):
            return m.group(0)
        for name in ("README.md", "index.md"):
            if (path / name).is_file():
                return m.group(1) + target.rstrip("/") + "/" + name + m.group(3)
        listings.add(path.relative_to(src.resolve()).as_posix())
        return m.group(1) + target.rstrip("/") + "/index.md" + m.group(3)
    return LINK_RE.sub(sub, text)


def _listing(src: Path, d: str) -> str:
    rows = [f"# `{d}/`", "", "Files in this directory:", ""]
    for p in sorted((src / d).iterdir()):
        if p.name.startswith("."):
            continue
        rows.append(f"- [`{p.name}{'/' if p.is_dir() else ''}`]({p.name}{'/index.md' if p.is_dir() else ''})"
                    if not p.is_dir() else f"- `{p.name}/`")
    return "\n".join(rows) + "\n"


def stage(src: Path, docs: Path) -> list[str]:
    """Copy the public repo into a MkDocs docs directory. Returns the markdown pages."""
    src = Path(src)
    pages = []
    ids: dict[str, str] = {}
    superseded_by: dict[str, list[str]] = {}
    listings: set[str] = set()
    for n in nodes.scan(src).nodes:
        if n.path.endswith(".md"):
            ids[n.id] = n.path
        for old in n.get("supersedes") or []:
            superseded_by.setdefault(old, []).append(n.id)
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src).as_posix()
        if any(rel == s or rel.startswith(s + "/") for s in SKIP) or not p.is_file():
            continue
        dest = docs / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".md"):
            text = _fix_dir_links(p.read_text(encoding="utf-8"), rel, src, listings)
            dest.write_text(_decorate(text, rel, ids, superseded_by), encoding="utf-8")
            pages.append(rel)
        else:
            shutil.copy2(p, dest)
    for d in sorted(listings):
        (docs / d / "index.md").write_text(_listing(src, d), encoding="utf-8")
        pages.append(f"{d}/index.md")
    bibs = sorted(p for p in docs.glob("citations/*.bib"))
    if bibs:
        out = ["# Bibliography", ""]
        for b in bibs:
            out += [f"## `{b.name}`", "", "```bibtex", b.read_text(encoding="utf-8").rstrip(), "```", ""]
        (docs / "citations/bibliography.md").write_text("\n".join(out), encoding="utf-8")
        pages.append("citations/bibliography.md")
    return sorted(pages)


def html_path(page: str) -> str:
    """Where MkDocs writes a page (use_directory_urls off; README.md is its directory's index)."""
    pp = PurePosixPath(page)
    if pp.name in ("README.md", "index.md"):
        return (pp.parent / "index.html").as_posix()
    return pp.with_suffix(".html").as_posix()


def _page_title(docs: Path, rel: str) -> str:
    for line in (docs / rel).read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return PurePosixPath(rel).stem.replace("-", " ").replace("_", " ")


def navigation(docs: Path, pages: list[str], result_pages: set[str]) -> list:
    nav: list = []
    home = next((p for p in ("index.md", "README.md") if p in pages), None)
    left = [p for p in pages if p != home]
    if home:
        nav.append({"Home": home})
    for tab, pred in TABS:
        members = [p for p in left if pred(p) or (tab == "Results" and p in result_pages)]
        if members:
            nav.append({tab: [{_page_title(docs, p): p} for p in members]})
            left = [p for p in left if p not in members]
    if left:
        nav.append({"Other": [{_page_title(docs, p): p} for p in left]})
    return nav


def config(title: str, nav: list, docs: Path) -> dict:
    return {
        "site_name": title,
        "docs_dir": str(docs),
        "use_directory_urls": False,
        "theme": {"name": "material", "features": ["navigation.tabs", "navigation.sections",
                                                   "navigation.indexes", "search.highlight"]},
        "markdown_extensions": [
            "admonition", "tables", "toc",
            {"pymdownx.superfences": {"custom_fences": [
                {"name": "mermaid", "class": "mermaid",
                 "format": "!!python/name:pymdownx.superfences.fence_code_format"}]}},
        ],
        "validation": {"nav": {"omitted_files": "warn", "not_found": "warn"},
                       "links": {"not_found": "warn", "absolute_links": "warn",
                                 "unrecognized_links": "warn"}},
        "nav": nav,
    }


def _dump(cfg: dict) -> str:
    text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
    # MkDocs needs a python tag for the mermaid formatter; yaml.safe_dump writes it quoted.
    return text.replace("'!!python/name:pymdownx.superfences.fence_code_format'",
                        "!!python/name:pymdownx.superfences.fence_code_format")


def build(src: Path, out: Path, keep_config: Path | None = None) -> list[str]:
    """Build the site of public repo ``src`` into ``out``. Returns problems (empty = success)."""
    src, out = Path(src).resolve(), Path(out).resolve()
    work = Path(tempfile.mkdtemp(prefix="opsci-site-"))
    docs = work / "docs"
    docs.mkdir()
    pages = stage(src, docs)
    if not pages:
        return ["no markdown files to build"]
    scan = nodes.scan(src)
    result_pages = {n.path for n in scan.nodes if n.get("type") in RESULT_TYPES and n.path.endswith(".md")}
    cfg = config(_title(src), navigation(docs, pages, result_pages), docs)
    cfg_path = work / "mkdocs.yml"
    cfg_path.write_text(_dump(cfg), encoding="utf-8")
    if keep_config:
        shutil.copy2(cfg_path, keep_config)
    if out.exists():
        shutil.rmtree(out)
    r = subprocess.run([sys.executable, "-m", "mkdocs", "build", "--strict", "-f", str(cfg_path),
                        "-d", str(out)], capture_output=True, text=True)
    problems = []
    if r.returncode != 0:
        out_lines = [l for l in (r.stderr + r.stdout).splitlines() if l.strip()]
        msgs = [l for l in out_lines if l.startswith(("WARNING", "ERROR", "Aborted"))] or out_lines
        problems += [f"mkdocs: {m}" for m in msgs] or [f"mkdocs failed with exit code {r.returncode}"]
        return problems
    for p in pages:
        if not (out / html_path(p)).is_file():
            problems.append(f"page {p} was not built ({html_path(p)} missing)")
    # Scan what the project put in the site: its pages, search index and copied files. The
    # theme's own static files (minified CSS/JS, source maps) are third-party and unchanged.
    theme = _theme_files()
    staged = {p.relative_to(docs).as_posix() for p in docs.rglob("*") if p.is_file()}
    files = sorted(r for r in (p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
                   if r in staged or r not in theme)
    hits = leakscan.scan_tree(out, leakscan.patterns_for(None), files)
    problems += [f"leak in built site: {h.path} {h.pattern}: {h.match!r}" for h in hits]
    return problems


def _theme_files() -> set[str]:
    import material  # the Material for MkDocs package
    d = Path(material.__file__).parent / "templates"
    return {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()}


def _framework(root: Path) -> tuple[str | None, str | None]:
    p = Path(root) / "config/framework.yaml"
    if not p.is_file():
        return None, None
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return cfg.get("framework_repo"), cfg.get("copied_at_commit")


def workflow(root: Path) -> str:
    """The GitHub Actions workflow that builds the site on the public repo and deploys it to Pages.
    It installs opsci from the framework repo at the commit in config/framework.yaml."""
    repo, commit = _framework(root)
    if repo and re.match(r"^(https://|git@|ssh://)", str(repo)) and commit and re.fullmatch(r"[0-9a-f]{7,40}", str(commit)):
        install = f'pip install "opsci @ git+{repo}@{commit}#subdirectory=tools" {MKDOCS_PINS}'
    else:
        install = ('echo "config/framework.yaml names no public framework repo and commit; '
                   'opsci cannot be installed" && exit 1')
    return f"""# Generated by `opsci publish push`; edits are overwritten at the next publish.
# Builds the project site from this repository and deploys it to GitHub Pages
# (Settings -> Pages -> Source: GitHub Actions).
name: site
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: {install}
      - run: opsci site build . --out _site
      - uses: actions/upload-pages-artifact@v3
        with:
          path: _site
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{{{ steps.deployment.outputs.page_url }}}}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
"""
