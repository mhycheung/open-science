"""`opsci site`: build the project site from a public repo with MkDocs (Material theme).

Tabs: Home, Results, Map, Dead ends, Tasks, Citations, Context, Log; `sitepages` assembles
the pages (one per task, one map page, the citations table, the log). Markdown files that
are not pages of the site are left out. Pages with a node header get a status banner
(active, failed, superseded, ...) and a verification line. The build runs in
strict mode, so a broken link fails it, and the built site must pass the leak scan.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from . import leakscan, nodes, sitepages

SKIP = (".git", ".github", ".opsci", "site", "_site")
# Files never copied into the site: the list of works consulted but not used is private.
DROP = {"citations/consulted.md"}
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
    "human-verified": "Human-verified: the user checked this result.",
}
MKDOCS_PINS = "mkdocs==1.6.1 mkdocs-material==9.7.7"
# Shown at the top of every page unless `site_banner:` in publish/manifest.yaml replaces it
# (or turns it off with an empty string).
DEFAULT_BANNER = ("Warning: this is an ongoing, unpublished project. Many results are very "
                  "preliminary and unverified.")
MATHJAX = "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-mml-chtml.js"
# The theme override of every page. MathJax typesets the \( \) and \[ \] spans that
# pymdownx.arithmatex writes for $...$ and $$...$$ (also after Material's instant navigation), and
# the $...$ left in page titles where the theme shows them (navigation, table of contents,
# header, previous/next links: its md-ellipsis elements). The browser tab's <title> gets a
# plain-text title (config.extra.opsci_titles), as no renderer runs there.
# Material's announcement bar holds the banner, kept in view: it sticks to the top of the window,
# and the header, the sidebars and link targets move down by its height (set by the script, as
# the text can wrap).
PAGE_TEMPLATE = """{% extends "base.html" %}
{% block announce %}{% if config.extra.opsci_banner %}<strong class="opsci-banner">\
{{ config.extra.opsci_banner | e }}</strong>{% endif %}{% endblock %}
{% block htmltitle %}{% set t = config.extra.opsci_titles.get(page.file.src_uri) if page and page.file %}\
{% if t and ((page.meta and page.meta.title) or not page.is_homepage) %}<title>{{ t }} - {{ config.site_name }}</title>\
{% else %}<title>{{ config.site_name }}</title>{% endif %}{% endblock %}
{% block styles %}{{ super() }}
<style>
[data-md-component=announce]{position:sticky;top:0;z-index:5}
.md-banner{background-color:#b3261e;color:#fff}
.opsci-unverified{color:#b3261e;font-weight:700}
.opsci-caption{font-size:.7rem;color:var(--md-default-fg-color--light);margin:0 0 1.5em}
.md-header{top:var(--opsci-banner-h,0px)}
.md-typeset :target{scroll-margin-top:calc(var(--md-scroll-margin) - var(--md-scroll-offset) + var(--opsci-banner-h,0px)) !important}
@media screen and (min-width:60em){.md-sidebar--secondary{top:calc(2.4rem + var(--opsci-banner-h,0px)) !important}}
@media screen and (min-width:76.25em){.md-sidebar--primary{top:calc(2.4rem + var(--opsci-banner-h,0px)) !important}}
</style>{% endblock %}
{% block scripts %}{{ super() }}
<script>(function(){var b=document.querySelector("[data-md-component=announce]");
function h(){document.documentElement.style.setProperty("--opsci-banner-h",b.offsetHeight+"px")}
h();window.addEventListener("resize",h)})();
window.MathJax={tex:{inlineMath:[["\\\\(","\\\\)"],["$","$"]],displayMath:[["\\\\[","\\\\]"]],processEscapes:true,
processEnvironments:true},options:{ignoreHtmlClass:".*|",processHtmlClass:"arithmatex|md-ellipsis"}};
document$.subscribe(function(){if(window.MathJax.typesetPromise){MathJax.startup.output.clearCache();
MathJax.typesetClear();MathJax.texReset();MathJax.typesetPromise()}});</script>
<script src="MATHJAX_URL" async></script>{% endblock %}
""".replace("MATHJAX_URL", MATHJAX)


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
        label = VERIFICATION.get(level, level)
        label = (sitepages.UNVERIFIED.format(label) if level == "unverified" else f"**{label}**")
        lines += [f"{label}{ev_txt}", ""]
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


@dataclass
class Staged:
    pages: list[str]  # the markdown pages of the site
    nav: list  # the navigation (tabs)


def stage(src: Path, docs: Path) -> Staged:
    """Copy the public repo into a MkDocs docs directory and assemble the site's pages (see
    `sitepages`): the files that are not pages of the site stay as files that pages link to."""
    src = Path(src)
    ids: dict[str, str] = {}
    superseded_by: dict[str, list[str]] = {}
    scan = nodes.scan(src)
    for n in scan.nodes:
        if n.path.endswith(".md"):
            ids[n.id] = n.path
        for old in n.get("supersedes") or []:
            superseded_by.setdefault(old, []).append(n.id)
    md = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src).as_posix()
        if any(rel == s or rel.startswith(s + "/") for s in SKIP) or not p.is_file():
            continue
        if rel in DROP:
            continue
        dest = docs / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        if rel.endswith(".md"):
            md.append(rel)
    site = sitepages.Site(docs)
    tdirs = sitepages.task_dirs(docs, scan)
    site.task_ids = {n.id: f"{d}/context.md" for d, n in tdirs.items()}
    result_pages = {n.path for n in scan.nodes if n.get("type") in RESULT_TYPES and n.path.endswith(".md")}
    home = next((p for p in ("index.md", "README.md") if p in md), None)

    def is_result_page(p: str) -> bool:
        return p in result_pages or p.startswith(("results/", "paper/"))
    sitepages.plan_moves(docs, tdirs, site)
    for p in md:
        if p in site.moved and not is_result_page(p):
            continue
        if (p == home or p == "context.md" or is_result_page(p) or p == "map/dead_ends.md"
                or p in ("map/README.md",)):
            site.pages.add(p)
    for p in ("map/claims.md", "map/graph.md"):
        if p in md:
            site.moved[p] = ("map/README.md", "claims-graph" if "claims" in p else "project-graph")
    generated = {}
    if any(p.startswith("map/") for p in site.moved) or "map/README.md" in md:
        site.pages.add("map/README.md")
    keys = sitepages.bib_keys(docs)
    if keys:
        site.pages.add("citations/README.md")
    if any(p.startswith("log/") for p in md):
        site.pages.add("log/README.md")
    if tdirs:
        site.pages.add("tasks/README.md")
    site.dropped = {p for p in md if p not in site.pages and p not in site.moved}
    # the pages that are assembled from several files
    for tdir in tdirs:
        page = f"{tdir}/context.md"
        text = _decorate((docs / page).read_text(encoding="utf-8"), page, ids, superseded_by)
        generated[page] = sitepages.task_page(tdir, text, tdirs, site)
    m = sitepages.map_page(docs, site)
    if m:
        generated["map/README.md"] = m
    if tdirs:
        generated["tasks/README.md"] = sitepages.tasks_index(tdirs, site)
    c = sitepages.citations_page(docs, scan, site) if keys else None
    if c:
        generated["citations/README.md"] = c
    lg = sitepages.project_log(docs, site)
    if lg:
        generated["log/README.md"] = lg
    site.pages = {p for p in site.pages if p in generated or (docs / p).is_file()}
    for p in sorted(site.pages):
        if p in generated:
            text = generated[p]
        else:
            text = _decorate((docs / p).read_text(encoding="utf-8"), p, ids, superseded_by)
            text = sitepages.fix_links(text, p, p, site)
            if p == "results/README.md":  # the milestone results
                text = re.sub(r"(?m)^# Results\s*$", "# Main results", text, count=1)
        text = sitepages.mark_unverified(sitepages.cite_links(text, p, keys))
        (docs / p).write_text(text, encoding="utf-8")
    for p in md:
        if p not in site.pages:
            (docs / p).unlink()
    pages = sorted(site.pages)
    return Staged(pages, navigation(docs, pages, home, tdirs, is_result_page))


def html_path(page: str) -> str:
    """Where MkDocs writes a page (use_directory_urls off; README.md is its directory's index)."""
    pp = PurePosixPath(page)
    if pp.name in ("README.md", "index.md"):
        return (pp.parent / "index.html").as_posix()
    return pp.with_suffix(".html").as_posix()


TEX_TEXT = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "circ": "°", "langle": "⟨", "rangle": "⟩", "lvert": "|",
    "rvert": "|", "vert": "|", "times": "×", "pm": "±", "mp": "∓", "leq": "≤", "le": "≤",
    "geq": "≥", "ge": "≥", "neq": "≠", "approx": "≈", "sim": "~", "simeq": "≃", "propto": "∝",
    "infty": "∞", "to": "→", "rightarrow": "→", "odot": "⊙", "cdot": "·", "partial": "∂",
    "nabla": "∇", "sum": "Σ", "int": "∫", "ell": "ℓ", "hbar": "ħ", "prime": "′", "ldots": "…",
    "dots": "…", "log": "log", "ln": "ln", "exp": "exp", "sin": "sin", "cos": "cos", "tan": "tan",
}
MATH_RE = re.compile(r"\$([^$]+)\$|\\\((.+?)\\\)")


def _tex_text(tex: str) -> str:
    """Readable plain text for a short TeX expression (`\\iota_Q(t)` -> `ι_Q(t)`)."""
    t = re.sub(r"\^\{?\\circ\}?", "°", tex)
    t = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", t)
    t = re.sub(r"\\[,;:!> ]", lambda m: "" if m.group() == "\\!" else " ", t)
    t = re.sub(r"\\(?:rm|mathrm|text|textrm|mathbf|mathit|mathcal|operatorname|left|right|big|Big)"
               r"(?![A-Za-z])\s*", "", t)
    def word(m):  # a function name (\\cos\\iota) keeps a space before what follows it
        out = TEX_TEXT.get(m.group(1), m.group(1))
        nxt = m.string[m.end():m.end() + 1]
        return out + " " if out.isalpha() and len(out) > 1 and (nxt.isalpha() or nxt == "\\") else out
    t = re.sub(r"\\([A-Za-z]+)", word, t)
    return re.sub(r"\s+", " ", t.replace("{", "").replace("}", "")).strip()


def plain_title(title: str) -> str:
    """A title with its $...$ or \\(...\\) math turned into plain text, for where no renderer runs."""
    return MATH_RE.sub(lambda m: _tex_text(m.group(1) or m.group(2)), title)


def _page_title(docs: Path, rel: str) -> str:
    for line in (docs / rel).read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return PurePosixPath(rel).stem.replace("-", " ").replace("_", " ")


def navigation(docs: Path, pages: list[str], home: str | None, tdirs: dict, is_result_page) -> list:
    """Tabs: Home, Results, Map, Dead ends, Tasks, Citations, Context, Log."""
    def entry(p: str, title: str | None = None) -> dict:
        return {title or _page_title(docs, p): p}
    nav: list = [{"Home": home}] if home else []
    results = [p for p in pages if is_result_page(p)]
    if results:
        top = [entry(p, "Main results" if p == "results/README.md" else None)
               for p in results if not nodes.task_dirs(p)]
        by_task: dict[str, list] = {}
        for p in results:
            if nodes.task_dirs(p):
                by_task.setdefault(sitepages.owner(p, tdirs) or nodes.task_dirs(p)[-1], []).append(p)
        for tdir, ps in sorted(by_task.items()):
            n = tdirs.get(tdir)
            top.append({(n.get("title") if n else None) or tdir: [entry(p) for p in ps]})
        nav.append({"Results": top})
    if "map/README.md" in pages:
        nav.append({"Map": "map/README.md"})
    if "map/dead_ends.md" in pages:
        nav.append({"Dead ends": "map/dead_ends.md"})
    if "tasks/README.md" in pages:
        nav.append({"Tasks": [{"Overview": "tasks/README.md"}]
                    + [entry(f"{d}/context.md", str(n.get("title") or n.id)) for d, n in sorted(tdirs.items())]})
    if "citations/README.md" in pages:
        nav.append({"Citations": "citations/README.md"})
    if "context.md" in pages:
        nav.append({"Context": "context.md"})
    if "log/README.md" in pages:
        nav.append({"Log": "log/README.md"})
    return nav


def config(title: str, nav: list, docs: Path, banner: str | None = None) -> dict:
    """The mkdocs.yml. The theme's custom_dir (PAGE_TEMPLATE) is ``docs``' sibling ``overrides``."""
    theme = {"name": "material", "features": ["navigation.tabs", "navigation.sections",
                                              "navigation.indexes", "search.highlight"]}
    theme["custom_dir"] = str(Path(docs).parent / "overrides")
    extra = {"opsci_banner": banner} if banner else {}
    return {
        "site_name": title,
        "docs_dir": str(docs),
        "use_directory_urls": False,
        "theme": theme,
        **({"extra": extra} if extra else {}),
        "markdown_extensions": [
            "admonition", "tables", "toc", "attr_list", "md_in_html",
            {"pymdownx.arithmatex": {"generic": True}},
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


def build(src: Path, out: Path, keep_config: Path | None = None,
          banner: str | None = DEFAULT_BANNER) -> list[str]:
    """Build the site of public repo ``src`` into ``out``, with ``banner`` at the top of every
    page (None or "" for none). Returns problems (empty = success)."""
    src, out = Path(src).resolve(), Path(out).resolve()
    work = Path(tempfile.mkdtemp(prefix="opsci-site-"))
    docs = work / "docs"
    docs.mkdir()
    staged = stage(src, docs)
    pages = staged.pages
    if not pages:
        return ["no markdown files to build"]
    banner = (banner or "").strip()
    (work / "overrides").mkdir()
    (work / "overrides/main.html").write_text(PAGE_TEMPLATE, encoding="utf-8")
    cfg = config(_title(src), staged.nav, docs, banner)
    titles = {}
    for p in pages:
        h, _ = _header((docs / p).read_text(encoding="utf-8"))
        titles[p] = plain_title(str(h["title"]) if h and h.get("title") else _page_title(docs, p))
    cfg["extra"] = {**cfg.get("extra", {}), "opsci_titles": titles}
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
    index = out / "search/search_index.json"
    if index.is_file():  # search results show titles as text
        data = json.loads(index.read_text(encoding="utf-8"))
        for d in data.get("docs", []):
            d["title"] = plain_title(d.get("title", ""))
        index.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
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


def _https(repo: str) -> str | None:
    """The https clone URL of a git remote (`git@host:a/b.git` and `ssh://git@host/a/b.git` ->
    `https://host/a/b.git`), or None. The framework repo is public, so CI needs no key."""
    m = (re.fullmatch(r"git@([^:/]+):(.+?)/?", repo)
         or re.fullmatch(r"ssh://(?:[^@/]+@)?([^:/]+)(?::\d+)?/(.+?)/?", repo)
         or re.fullmatch(r"https://(?:[^@/]+@)?([^/]+)/(.+?)/?", repo))
    return f"https://{m.group(1)}/{m.group(2)}" if m else None


def running_commit() -> str | None:
    """The framework commit of the opsci that is running: the commit pip recorded for a git
    install, or HEAD of the framework checkout it runs from when tools/ is clean. None if unknown."""
    try:
        from importlib.metadata import distribution
        commit = json.loads(distribution("opsci").read_text("direct_url.json") or "{}") \
            .get("vcs_info", {}).get("commit_id")
        if commit:
            return commit
    except Exception:
        pass
    tools = Path(__file__).resolve().parents[1]

    def git(*args: str) -> str | None:
        r = subprocess.run(["git", "-C", str(tools), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    if (tools / "pyproject.toml").is_file() and git("status", "--porcelain", "--untracked-files=no", "--", ".") == "":
        return git("rev-parse", "HEAD")
    return None


def workflow(root: Path, banner: str | None = DEFAULT_BANNER) -> str:
    """The GitHub Actions workflow that builds the site on the public repo and deploys it to Pages.
    It installs opsci over https from the framework repo in config/framework.yaml, at the commit of
    the opsci running this publish (else at the recorded copied_at_commit). The banner is written
    into the workflow, as the manifest that sets it is not published."""
    repo, copied = _framework(root)
    url = _https(str(repo)) if repo else None
    commit = running_commit() or copied
    if url and commit and re.fullmatch(r"[0-9a-f]{7,40}", str(commit)):
        install = f'pip install "opsci @ git+{url}@{commit}#subdirectory=tools" {MKDOCS_PINS}'
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
      - run: opsci site build . --out _site --banner "$OPSCI_SITE_BANNER"
        env:
          OPSCI_SITE_BANNER: {json.dumps((banner or "").strip())}
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
