"""The pages of the project site, assembled from the files of a public repo (used by `site.py`).

The site shows what a reader needs, not every file: the home page, the results, one map page
(the logic of the project, the claims graph and the project graph), the dead ends, one page
per task, the citations, the project context and the project log. A task page holds all of
the task: its context, results, figures with their captions, plan, map, working notes and
log. The other markdown files are left out; a link to one becomes plain text, and a link to a
file merged into a page points to its section there.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import bibfmt, nodes

FENCE_RE = re.compile(r"^(```|~~~)")
# A markdown link or image: [label](target "title"). The label may hold one level of brackets.
FULL_LINK_RE = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)")
CITE_RE = re.compile(r"\[@([^\]\s;,]+)\]")
LOG_ENTRY_RE = re.compile(r"^\s*(?:[-*]\s+)?(\d{4}-\d{2}-\d{2})\b\s*[:—–-]*\s*(.*)$")
UNVERIFIED = '<span class="opsci-unverified">{}</span>'
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")
TASK_SECTIONS = ("results/README.md", "plan.md", "map.md", "log.md")


@dataclass
class Site:
    """What the staged site holds, for rewriting links."""
    docs: Path
    pages: set[str] = field(default_factory=set)  # markdown pages in the site
    moved: dict[str, tuple[str, str]] = field(default_factory=dict)  # merged file -> (page, anchor)
    dropped: set[str] = field(default_factory=set)  # markdown files left out of the site
    task_ids: dict[str, str] = field(default_factory=dict)  # task id -> its page


# ------------------------------------------------------------------ text helpers

def outside_fences(text: str, fn) -> str:
    """``text`` with ``fn`` applied to each run of lines outside fenced code blocks."""
    out, run, fence = [], [], None
    for line in text.splitlines(keepends=True):
        m = FENCE_RE.match(line.lstrip())
        if fence is None and m:
            out.append(fn("".join(run)))
            run, fence = [], m.group(1)
            out.append(line)
        elif fence is not None:
            out.append(line)
            if line.strip().startswith(fence):
                fence = None
        else:
            run.append(line)
    out.append(fn("".join(run)))
    return "".join(out)


def demote(text: str, by: int) -> str:
    """Headings moved ``by`` levels down (at most h6), outside code blocks."""
    return outside_fences(text, lambda t: re.sub(
        r"(?m)^(#{1,6})(?= )", lambda m: "#" * min(6, len(m.group(1)) + by), t))


def strip_front_matter(text: str) -> str:
    raw, present = nodes.read_front_matter(text)
    if not present or raw is None:
        return text
    return "\n".join(text.splitlines()[raw.count("\n") + 3:]) + "\n"


def split_title(text: str) -> tuple[str | None, str]:
    """(the first `# ` heading, the text without it)."""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("# "):
            return l[2:].strip(), "\n".join(lines[:i] + lines[i + 1:]) + "\n"
    return None, text


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _rel(target: str, from_page: str) -> str:
    return os.path.relpath(target, PurePosixPath(from_page).parent.as_posix() or ".").replace(os.sep, "/")


# ------------------------------------------------------------------ links

def fix_links(text: str, orig: str, page: str, site: Site) -> str:
    """The links of ``text``, written for file ``orig``, rewritten for its place in ``page``:
    to merged files as links to their section, to left-out pages as plain text, to
    directories as links to their page (or plain text), and relative to ``page``."""
    base = PurePosixPath(orig).parent

    def sub(m):
        bang, label, target, title = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("/"):
            return m.group(0)
        path, _, frag = target.partition("#")
        if not path:  # an anchor in the same file
            return m.group(0)
        path = path.split("?")[0]
        norm = os.path.normpath((base / path).as_posix()).replace(os.sep, "/")
        if norm.startswith(".."):
            return m.group(0)
        if (site.docs / norm).is_dir() or path.endswith("/"):
            norm = next((f"{norm}/{n}" for n in ("README.md", "index.md")
                         if f"{norm}/{n}" in site.pages or f"{norm}/{n}" in site.moved), None)
            if norm is None:
                return label if not bang else m.group(0)
        if norm in site.moved:
            dest, anchor = site.moved[norm]
            href = ("" if dest == page else _rel(dest, page)) + "#" + (frag or anchor)
            return f"{bang}[{label}]({href}{title})"
        if norm in site.dropped:
            return label if not bang else ""
        href = _rel(norm, page) + (f"#{frag}" if frag else "")
        return f"{bang}[{label}]({href}{title})"
    return outside_fences(text, lambda t: FULL_LINK_RE.sub(sub, t))


def cite_links(text: str, page: str, keys: set[str]) -> str:
    """`[@key]` -> a link to the key's row in the citations page."""
    target = _rel("citations/README.md", page)

    def sub(m):
        k = m.group(1)
        return f"[\\[{k}\\]]({target}#cite-{slug(k)})" if k in keys else m.group(0)
    return outside_fences(text, lambda t: CITE_RE.sub(sub, t))


def mark_unverified(text: str) -> str:
    """`unverified` in red and bold where it is a label: in the `*type · status · level*`
    lines of the results pages and in table cells."""
    def lines(t):
        out = []
        for l in t.splitlines(keepends=True):
            s = l.strip()
            if s.startswith("*") and s.endswith("*") and " · " in s:
                l = re.sub(r"\bunverified\b", UNVERIFIED.format("unverified"), l)
            elif s.startswith("|"):
                l = re.sub(r"(?<=\|)(\s*)(unverified|not verified)(\s*)(?=\|)",
                           lambda m: m.group(1) + UNVERIFIED.format(m.group(2)) + m.group(3), l)
            out.append(l)
        return "".join(out)
    return outside_fences(text, lines)


# ------------------------------------------------------------------ task pages

def task_dirs(docs: Path, scan) -> dict[str, "nodes.Node"]:
    """Task directory -> its task node, for every task whose context.md is in the site."""
    out = {}
    for n in scan.nodes:
        if n.get("type") == "task" and n.path.endswith("/context.md") and nodes.task_file(n.path) \
                and (docs / n.path).is_file():
            out[PurePosixPath(n.path).parent.as_posix()] = n
    return out


def owner(rel: str, tdirs) -> str | None:
    """The task directory whose page holds ``rel`` (the innermost task holding it)."""
    for d in reversed(nodes.task_dirs(rel)):
        if d in tdirs:
            return d
    return None


def figures(docs: Path, tdir: str, tdirs) -> list[tuple[str, str | None]]:
    """(image, caption file or None) of every image of the task, and of every caption
    whose image is only a PDF."""
    out, seen = [], set()
    for p in sorted((docs / tdir).rglob("*")):
        rel = p.relative_to(docs).as_posix()
        if owner(rel, tdirs) != tdir or not p.is_file():
            continue
        if rel.endswith(".caption.md"):
            stem = rel[:-len(".caption.md")]
            img = next((stem + s for s in IMAGE_SUFFIXES if (docs / (stem + s)).is_file()), None)
            if img is None:
                img = stem + ".pdf" if (docs / (stem + ".pdf")).is_file() else None
            if img and img not in seen:
                out.append((img, rel))
                seen.add(img)
        elif rel.endswith(IMAGE_SUFFIXES) and "/results/" not in rel:
            cap = next((c for c in (rel.rsplit(".", 1)[0] + ".caption.md",) if (docs / c).is_file()), None)
            if rel not in seen and cap is None:
                out.append((rel, None))
                seen.add(rel)
    return out


def figure_block(img: str, caption: str | None, page: str, site: Site, heading: str | None) -> str:
    parts = [f"{heading} {{#{fig_anchor(img)}}}", ""] if heading else []
    name = PurePosixPath(img).name
    if img.endswith(".pdf"):
        parts += [f"[`{name}`]({_rel(img, page)}) (PDF)", ""]
    else:
        parts += [f"![{name}]({_rel(img, page)})", ""]
        pdf = img.rsplit(".", 1)[0] + ".pdf"
        if (site.docs / pdf).is_file():
            parts += [f"[PDF]({_rel(pdf, page)})", ""]
    if caption:
        body = fix_links(strip_front_matter((site.docs / caption).read_text(encoding="utf-8")),
                         caption, page, site)
        parts += ['<div class="opsci-caption" markdown>', "", body.strip(), "", "</div>", ""]
    return "\n".join(parts)


def fig_anchor(img: str) -> str:
    return "fig-" + slug(PurePosixPath(img).with_suffix("").name)


def note_anchor(rel: str, tdir: str) -> str:
    return "note-" + slug(PurePosixPath(rel).relative_to(tdir).with_suffix("").as_posix())


def plan_moves(docs: Path, tdirs, site: Site) -> None:
    """Record where each file of a task goes in its task page."""
    for tdir in tdirs:
        page = f"{tdir}/context.md"
        site.pages.add(page)
        for p in (docs / tdir).rglob("*.md"):
            rel = p.relative_to(docs).as_posix()
            if owner(rel, tdirs) != tdir or rel == page:
                continue
            sub = PurePosixPath(rel).relative_to(tdir).as_posix()
            if sub in TASK_SECTIONS:
                site.moved[rel] = (page, sub.split("/")[0].removesuffix(".md"))
            elif rel.endswith(".caption.md"):
                site.moved[rel] = (page, fig_anchor(rel[:-len(".caption.md")]))
            elif sub.startswith("results/") and sub.count("/") == 1:
                continue  # a result page: a page of its own
            else:
                site.moved[rel] = (page, note_anchor(rel, tdir))


def task_page(tdir: str, context_text: str, tdirs, site: Site) -> str:
    """The whole task on one page: context, results, figures, plan, map, notes, log."""
    docs, page = site.docs, f"{tdir}/context.md"
    out = [fix_links(context_text, page, page, site).rstrip(), ""]

    def section(sub: str, heading: str, anchor: str, transform=None) -> None:
        p = docs / tdir / sub
        if not p.is_file():
            return
        text = strip_front_matter(p.read_text(encoding="utf-8"))
        _, body = split_title(text)
        body = transform(body) if transform else body
        body = demote(fix_links(body, f"{tdir}/{sub}", page, site), 1).strip()
        if body:
            out.extend([f"## {heading} {{#{anchor}}}", "", body, ""])

    shown = set()

    def results_body(body: str) -> str:
        # drop the generated introduction; put each figure's caption under it
        at = body.find("\n## ")
        body = body[at + 1:] if at >= 0 else body

        def cap(m):
            img = os.path.normpath((PurePosixPath(tdir) / "results" / m.group(2)).as_posix())
            c = img.rsplit(".", 1)[0] + ".caption.md"
            if not (docs / c).is_file():
                return m.group(0)
            shown.add(img)
            text = fix_links(strip_front_matter((docs / c).read_text(encoding="utf-8")), c,
                             f"{tdir}/results/README.md", site)
            return (m.group(0) + "\n\n" + f'<div class="opsci-caption" markdown>\n\n{text.strip()}\n\n</div>'
                    + f'\n<span id="{fig_anchor(img)}"></span>\n')
        return re.sub(r"(?m)^!\[([^\]]*)\]\(([^)\s]+)\)\s*$", cap, body)

    section("results/README.md", "Results", "results", results_body)
    figs = [(i, c) for i, c in figures(docs, tdir, tdirs) if i not in shown]
    if figs:
        out += ["## Figures {#figures}", ""]
        for img, cap in figs:
            out.append(figure_block(img, cap, page, site, "### " + PurePosixPath(img).stem.replace("_", " ")))
    section("plan.md", "Plan", "plan")
    section("map.md", "Map", "map")
    notes = sorted(rel for rel, (dest, anchor) in site.moved.items()
                   if dest == page and anchor.startswith("note-"))
    notes.sort(key=lambda r: (PurePosixPath(r).relative_to(tdir).parts[0] == "subcontext", r))
    notes = [r for r in notes if PurePosixPath(r).name != "README.md"
             or PurePosixPath(r).parent.as_posix() != f"{tdir}/subcontext"]
    if notes:
        out += ["## Working notes {#notes}", ""]
        for rel in notes:
            text = strip_front_matter((docs / rel).read_text(encoding="utf-8"))
            title, body = split_title(text)
            sub = PurePosixPath(rel).relative_to(tdir).as_posix()
            out += [f"### {title or sub} {{#{site.moved[rel][1]}}}", "", f"*`{sub}`*", "",
                    demote(fix_links(body, rel, page, site), 2).strip(), ""]
    log = docs / tdir / "log.md"
    if log.is_file():
        entries = log_entries(log.read_text(encoding="utf-8"))
        if entries:
            out += ["## Log {#log}", "", render_log(entries, page, site, level=3, base=tdir), ""]
    return "\n".join(out).rstrip() + "\n"


def tasks_index(tdirs, site: Site) -> str:
    """The Tasks overview: every task with its status and summary."""
    groups: dict[str, list] = {"Tasks": [], "Verification tasks": [], "Brainstorm": []}
    for tdir, n in sorted(tdirs.items()):
        g = ("Brainstorm" if tdir.startswith("brainstorm/")
             else "Verification tasks" if nodes.is_verification(n) else "Tasks")
        groups[g].append((tdir, n))
    out = ["# Tasks", "", "One page per task, with everything the task produced: its current "
           "state, results, figures, plan, map, working notes and log.", ""]
    for g, items in groups.items():
        if not items:
            continue
        if g != "Tasks" or len([1 for v in groups.values() if v]) > 1:
            out += [f"## {g}", ""]
        out += ["| task | status | summary |", "|---|---|---|"]
        for tdir, n in items:
            summary = " ".join(str(n.get("summary") or "").split()).replace("|", "\\|")
            out.append(f"| [{n.get('title') or n.id}]({_rel(tdir + '/context.md', 'tasks/README.md')}) "
                       f"| {n.get('status', '')} | {summary} |")
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ log

def log_entries(text: str) -> list[tuple[str, str]]:
    """(date, text) of each entry of a log file; a line that does not start with a date
    continues the entry before it."""
    entries: list[list[str]] = []
    fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line.lstrip()):
            fence = not fence
            continue
        if fence or line.startswith("#") or line.lstrip().startswith("<!--"):
            continue
        m = LOG_ENTRY_RE.match(line)
        if m:
            entries.append([m.group(1), m.group(2).strip()])
        elif line.strip() and entries and (line[:1].isspace() or not line.lstrip().startswith(("-", "*"))):
            entries[-1][1] += " " + line.strip()
    return [(d, t) for d, t in entries if t]


def _log_text(t: str, page: str, site: Site, base: str = "") -> str:
    """One entry, with its task id linked and bold, and paths (relative to ``base`` or to the
    repo) linked or in code."""
    t = t.replace(" → ", " — ").replace("→", "—").replace(" -> ", " → ")
    m = (re.match(r"^([a-z]\d+-[\w-]+)(?=[\s:])\s*[:—–-]?\s*", t)
         or re.match(r"^(project|publish)\s*:\s*", t))
    head = ""
    if m:
        tid = m.group(1)
        if tid in site.task_ids:
            head = f"**[{tid}]({_rel(site.task_ids[tid], page)})**: "
        else:
            head = f"**{tid}**: "
        t = t[m.end():]

    def path(pm):
        p = pm.group(0).rstrip(".,;:)")
        rest = pm.group(0)[len(p):]
        norm = os.path.normpath(p)
        if base:
            local = os.path.normpath(f"{base}/{p}")
            if local in site.moved or (site.docs / local).is_file():
                norm = local
        if norm in site.moved:
            dest, anchor = site.moved[norm]
            return f"[`{p}`]({_rel(dest, page)}#{anchor}){rest}"
        if norm in site.pages or (site.docs / norm).is_file() and not norm.endswith(".md"):
            return f"[`{p}`]({_rel(norm, page)}){rest}"
        return f"`{p}`{rest}"
    t = re.sub(r"(?<![`\w/$])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z]{1,5}\b[.,;:)]*", path, t)
    return head + t[:1].upper() + t[1:] if not head else head + t


def render_log(entries: list[tuple[str, str]], page: str, site: Site, level: int = 2,
               newest_first: bool = True, base: str = "") -> str:
    """Entries grouped by date under a heading each, newest first."""
    by_date: dict[str, list[str]] = {}
    for d, t in entries:
        by_date.setdefault(d, []).append(t)
    out = []
    for d in sorted(by_date, reverse=newest_first):
        out += [f"{'#' * level} {d}", ""] + [f"- {_log_text(t, page, site, base)}" for t in by_date[d]] + [""]
    return "\n".join(out).strip()


def project_log(docs: Path, site: Site) -> str | None:
    entries = []
    for p in sorted((docs / "log").glob("*.md")):
        if p.name != "README.md":
            entries += log_entries(p.read_text(encoding="utf-8"))
    if not entries:
        return None
    return ("# Project log\n\nWhat was done, one entry per finished piece of work, newest first. "
            "Each task page has the task's own log.\n\n" + render_log(entries, "log/README.md", site) + "\n")


# ------------------------------------------------------------------ map

def map_page(docs: Path, site: Site) -> str | None:
    """The logic of the project, the claims graph and the project graph, on one page."""
    parts = []
    readme = docs / "map/README.md"
    if readme.is_file():
        title, body = split_title(readme.read_text(encoding="utf-8"))
        # the list of the generated files is for agents; the page shows the graphs themselves
        lines, skip = [], False
        for l in body.splitlines():
            if l.startswith("Generated from the node headers"):
                skip = True
            elif skip and l.strip() and not l.startswith(("- ", "  ")):
                skip = False
            if not skip:
                lines.append(l)
        body = "\n".join(lines)
        parts += [f"# {title or 'Map'}", "", fix_links(body, "map/README.md", "map/README.md", site).strip(), ""]
    else:
        parts += ["# Map", ""]
    for name, heading, anchor in (("claims.md", "Claims graph", "claims-graph"),
                                  ("graph.md", "Project graph", "project-graph")):
        p = docs / "map" / name
        if not p.is_file():
            continue
        _, body = split_title(strip_front_matter(p.read_text(encoding="utf-8")))
        body = body.replace("<!-- GENERATED by `opsci map build` from the node headers. Do not edit "
                            "this file; edit the headers. -->", "")
        parts += [f"## {heading} {{#{anchor}}}", "",
                  demote(fix_links(body, f"map/{name}", "map/README.md", site), 1).strip(), ""]
    return "\n".join(parts).rstrip() + "\n" if len(parts) > 2 else None


# ------------------------------------------------------------------ citations

def citations_page(docs: Path, scan, site: Site) -> str | None:
    """The works and software the project uses, as a table: the reference, with links to the
    journal and arXiv, and how the project uses it."""
    bibs = sorted((docs / "citations").glob("*.bib"))
    entries = [e for b in bibs for e in bibfmt.parse(b.read_text(encoding="utf-8"))]
    if not entries:
        return None
    used_by: dict[str, list] = {}
    for n in scan.nodes:
        for k in n.get("uses") or []:
            used_by.setdefault(str(k).lstrip("@"), []).append(n)
    out = ["# Citations", "", "The works and software this project uses.", "",
           "| | reference | how it is used |", "|---|---|---|"]
    for i, e in enumerate(entries, 1):
        use = " ".join(bibfmt.detex(e.get("usage")).split())
        if not use and e.key in used_by:
            links = []
            for n in used_by[e.key]:
                dest = n.path if n.path in site.pages else site.moved.get(n.path, (None,))[0]
                links.append(f"[{n.id}]({_rel(dest, 'citations/README.md')})" if dest else n.id)
            use = "Used in " + ", ".join(links) + "."
        cell = bibfmt.reference(e).replace("|", "\\|")
        out.append(f'| <span id="cite-{slug(e.key)}"></span>[{i}] | {cell} | {use.replace("|", chr(92) + "|")} |')
    out += ["", "BibTeX: " + ", ".join(f"[`{b.name}`]({b.name})" for b in bibs) + ".", ""]
    return "\n".join(out)


def bib_keys(docs: Path) -> set[str]:
    return {e.key for b in (docs / "citations").glob("*.bib")
            for e in bibfmt.parse(b.read_text(encoding="utf-8"))}
