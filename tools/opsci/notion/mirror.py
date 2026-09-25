"""The Notion mirror of a project: render the project files into pages, and write what changed.

Pages under the project's root page: Project (PROJECT.md), Context, Map (the project graph and
the claims graph as Mermaid code blocks, and the dead ends), Milestone results (results/README.md), Log,
Rules, Brainstorm context, Private docs, the Feed (feed.py), and two databases:
- Tasks: one row per task in tasks/, brainstorm/tasks/ and the verifications/ directories,
  its properties from the node header, its body the task's context.md, then Results (the
  task's results/README.md), Plan, Task map, Task log and subcontext files as toggles, then
  its plots.
- Results: one row per result node (`type: result`, tasks/<id>/results/*.md or results/*.md),
  its properties from the node header, its body the result's file.
Figures in these files (a line `![alt](path)`) are uploaded and shown as images. Every mention
of a task or result id links to its page.

Plots: every image or PDF under tasks/<id>/ (not data/). Files that differ only by an ISO date
in the name (bands_2026-09-25.png, bands_2026-09-28.png) are versions of one plot: only the
newest is shown, in the slot of the older one. Each plot's caption is the file beside it with
the same stem and `.caption.md` (bands_2026-09-25.caption.md): a self-contained description,
markdown with $LaTeX$. It is shown in a box under the image (a Notion caption field holds
too little for a caption with many equations).

A page whose text changed is rewritten. A task page whose text did not change but whose plots
did is updated in place: a changed plot's image block gets the new file and its caption box the
new caption (same blocks, same position), new plots are inserted in order, removed ones
deleted.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from ..nodes import TASK_ROOT_GLOBS, scan
from . import blocks as nb
from .client import MAX_UPLOAD, NotionError
from .project import Project

PLOT_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf")
SKIP_PARTS = {"data", "lit_cache", ".git", ".pixi", "__pycache__", ".ipynb_checkpoints",
              "verifications"}  # a verification task inside a task has its own page
DATE_IN_NAME = re.compile(r"[_-](\d{4}-\d{2}-\d{2})(?=\.[^.]+$)")
KEEP_TYPES = ("child_page", "child_database")
CAPTION_SUFFIX = ".caption.md"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _sha_json(obj) -> str:
    return _sha(json.dumps(obj, sort_keys=True).encode())


# ---------------------------------------------------------------- plots

def caption_file(plot: Path) -> Path:
    return plot.with_name(plot.stem + CAPTION_SUFFIX)


def caption_paragraphs(plot: Path) -> list[list]:
    """The caption file as a list of paragraphs, each a Notion rich text list (<= 100 items)."""
    cf = caption_file(plot)
    if not cf.exists():
        return []
    text = nb.strip_generated(_read(cf)).strip()
    out = []
    for para in re.split(r"\n\s*\n", text):
        if para.strip():
            out += nb.chunks100(nb.rich(" ".join(para.split())))
    return out


def caption_blocks(paras: list[list], rel: str) -> dict:
    """The caption box shown under a plot: a callout holding the whole caption.

    A caption field holds at most 100 rich text items, and a caption with many equations has
    more, so the caption goes in its own block, not in the image's caption field."""
    if not paras:
        paras = [nb._t(f"No caption yet: write {rel.rsplit('.', 1)[0]}{CAPTION_SUFFIX}", {"italic": True})]
    kids = [nb.blk("paragraph", p) for p in paras[1:]]
    return nb.callout(paras[0], emoji="📊", color="gray_background", children=kids or None)


def caption_rich(plot: Path, rel: str) -> list:
    """The caption as one rich text list (for places with no room for blocks)."""
    rt = []
    for i, p in enumerate(caption_paragraphs(plot)):
        rt += (nb._t("\n") if i else []) + p
    return nb.fit100(rt + (nb._t("\n") if rt else []) + nb._t(rel, {"code": True, "color": "gray"}))


def plots_in(root: Path, task_dir: Path) -> list[dict]:
    """The newest version of each plot in a task directory."""
    best = {}
    for p in sorted(task_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in PLOT_EXT:
            continue
        if SKIP_PARTS & set(p.relative_to(task_dir).parts):
            continue
        if p.suffix.lower() == ".pdf" and any(p.with_suffix(e).exists() for e in (".png", ".jpg", ".svg")):
            continue                                   # the PDF twin of a PNG is the same figure
        rel = str(p.relative_to(root))
        key = DATE_IN_NAME.sub("", rel)
        m = DATE_IN_NAME.search(rel)
        rank = (m.group(1) if m else "", p.stat().st_mtime)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, p)
    out = []
    for key, (_, p) in sorted(best.items()):
        rel = str(p.relative_to(root))
        big = p.stat().st_size > MAX_UPLOAD
        paras = caption_paragraphs(p)
        out.append({"key": key, "path": rel, "too_big": big,
                    "sha": "" if big else _sha(p.read_bytes()),
                    "caption": paras, "cap_sha": _sha_json(paras), "has_caption": bool(paras),
                    "kind": "pdf" if p.suffix.lower() == ".pdf" else "image"})
    return out


def _plot_id(x: dict) -> str:
    return f"{x['path']}@{x['sha']}@{x['cap_sha']}"


# ---------------------------------------------------------------- links to task pages

def page_url(page_id: str) -> str:
    return "https://www.notion.so/" + page_id.replace("-", "")


def task_links(st: dict) -> dict:
    """{task or result id: URL of its Notion page} for every such page the state knows."""
    return {k.split(":", 1)[1]: page_url(v["page_id"]) for k, v in (st.get("pages") or {}).items()
            if k.startswith(("task:", "result:")) and v.get("page_id")}


def task_finder(links: dict):
    """A function text -> [(start, end, url)] finding references to tasks and results: a full
    id (t02-posterior-inclination, r-t02-near-edge-on-at-merger, also inside a path), or a
    task's short form (t02) when exactly one task has it."""
    if not links:
        return lambda text, self_url=None: []
    names = dict(links)
    shorts = {}
    for tid in links:
        m = re.match(r"([a-z]+\d+)-", tid)
        if m:
            shorts.setdefault(m.group(1), []).append(tid)
    for short, tids in shorts.items():
        if len(tids) == 1 and short not in names:
            names[short] = links[tids[0]]
    rx = re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                    + r")(?![\w-])")

    def find(text, self_url=None):
        return [(m.start(), m.end(), names[m.group(1)]) for m in rx.finditer(text)
                if names[m.group(1)] != self_url]
    return find


# ---------------------------------------------------------------- render

def image_resolver(root: Path, base: Path):
    """For md_to_blocks: an image line whose file exists in the project becomes an image
    placeholder with the file's path and hash (the sync uploads it and fills the block)."""
    def resolve(target: str, alt: str):
        if re.match(r"[a-z]+://", target):
            return None
        p = (base / target.split("#")[0]).resolve()
        if not p.is_file() or p.suffix.lower() not in PLOT_EXT or not p.is_relative_to(root.resolve()) \
                or p.stat().st_size > MAX_UPLOAD:
            return None
        rel = str(p.relative_to(root.resolve()))
        kind = "pdf" if p.suffix.lower() == ".pdf" else "image"
        return {"object": "block", "type": kind, kind: {},
                "_local": {"path": rel, "sha": _sha(p.read_bytes()), "alt": alt}}
    return resolve


def _md(root: Path, path: Path, text: str | None = None) -> list:
    """md_to_blocks for a project file, with its figures resolved relative to the file."""
    return nb.md_to_blocks(text if text is not None else _read(path), images=image_resolver(root, path.parent))


def render(root: Path, links: dict | None = None) -> list[dict]:
    """The project as Notion pages. `links` ({task or result id: URL}) turns references to
    tasks and results into links to their pages."""
    pages = []
    finder = task_finder(links or {})

    def page(key, title, blocks, kind="page", props=None, plots=(), icon=None):
        self_url = (links or {}).get(key.split(":", 1)[1]) if key.startswith(("task:", "result:")) else None
        blocks = nb.link_blocks(blocks, lambda text: finder(text, self_url))
        pages.append({"key": key, "kind": kind, "title": title, "icon": icon, "properties": props or {},
                      "blocks": blocks, "plots": list(plots),
                      "text_sha": _sha_json([title, icon, props or {}, blocks])})

    banner = [nb.callout(nb.rich(
        f"Mirror of the open-science project `{root.name}`, written by `opsci notion sync`. "
        "Pages here are overwritten on each sync: edit the project files, not these pages. "
        "**Feed** holds the agents' messages (newest first, removed after a few days)."))]
    page("home", root.name, banner, kind="home")
    if (root / "PROJECT.md").exists():
        page("project", "Project", nb.md_to_blocks(_read(root / "PROJECT.md")), icon="🎯")
    if (root / "context.md").exists():
        page("context", "Context", nb.md_to_blocks(_read(root / "context.md")), icon="📍")

    if (root / "map" / "README.md").exists():
        blocks = nb.md_to_blocks(_read(root / "map" / "README.md"))
        for f in ("graph.md", "claims.md", "dead_ends.md"):
            if (root / "map" / f).exists():
                blocks += [nb.blk("divider")] + nb.demote(nb.md_to_blocks(_read(root / "map" / f)))
        page("map", "Map", blocks, icon="🗺️")

    if (root / "results" / "README.md").exists():
        page("results", "Milestone results", _md(root, root / "results" / "README.md"), icon="🏆")

    logs = sorted((root / "log").glob("[0-9]*.md"), reverse=True)
    blocks = [b for p in logs for b in nb.demote(nb.md_to_blocks(_read(p)))]
    page("log", "Log", blocks or nb.md_to_blocks("No log yet."), icon="📜")

    if (root / "rules" / "README.md").exists():
        page("rules", "Rules", nb.md_to_blocks(_read(root / "rules" / "README.md")), icon="📏")
    if (root / "brainstorm" / "context.md").exists():
        page("brainstorm", "Brainstorm context", nb.md_to_blocks(_read(root / "brainstorm" / "context.md")), icon="💡")
    priv = sorted((root / "private-docs").rglob("*.md")) if (root / "private-docs").exists() else []
    if priv:
        page("private", "Private docs",
             [nb.toggle(f"`{p.relative_to(root)}`", nb.demote(nb.md_to_blocks(_read(p)), 2)) for p in priv],
             icon="🔒")

    for tr in TASK_ROOT_GLOBS:
        for td in sorted(root.glob(f"{tr}/*/")):
            ctx = td / "context.md"
            if not ctx.exists():
                continue
            fm, body = nb.split_front_matter(_read(ctx))
            tid = fm.get("id", td.name)
            body = re.sub(r"\A\s*# .*\n", "", nb.strip_generated(body))   # the row title has it
            blocks = _md(root, ctx, body)
            res = td / "results" / "README.md"
            if res.exists():
                inner = _md(root, res, re.sub(r"\A\s*# .*\n", "", nb.strip_generated(_read(res))))
                blocks.append(nb.toggle("Results", nb.demote(inner, 2)))
            for name, label in (("plan.md", "Plan"), ("map.md", "Task map"), ("log.md", "Task log")):
                if (td / name).exists():
                    inner = nb.md_to_blocks(nb.split_front_matter(_read(td / name))[1])
                    blocks.append(nb.toggle(label, nb.demote(inner, 2)))
            for sc in sorted((td / "subcontext").glob("*.md")) if (td / "subcontext").exists() else []:
                if sc.name != "README.md":
                    blocks.append(nb.toggle(f"`subcontext/{sc.name}`", nb.demote(nb.md_to_blocks(_read(sc)), 2)))
            props = {"Name": f"{tid}: {fm.get('title', '')}".rstrip(": "), "ID": tid,
                     "Status": fm.get("status"),
                     "Type": "verification" if fm.get("verifies") else fm.get("type", "task"),
                     "Area": "brainstorm" if tr.startswith("brainstorm") else "project",
                     "Privacy": fm.get("privacy"), "Verification": fm.get("verification"),
                     "Summary": str(fm.get("summary", ""))}
            page(f"task:{tid}", props["Name"], blocks, kind="task", props=props, plots=plots_in(root, td))

    for n in sorted(scan(root).nodes, key=lambda n: n.path):
        if n.get("type") != "result" or Path(n.path).name == "README.md":
            continue
        f = root / n.path
        body = re.sub(r"\A\s*# .*\n", "", nb.strip_generated(nb.split_front_matter(_read(f))[1]))
        parts = Path(n.path).parts          # tasks/<id>/results/<rid>.md, or results/<rid>.md
        task = parts[-3] if len(parts) >= 3 and parts[-2] == "results" else ""
        props = {"Name": f"{n.id}: {n.get('title', '')}".rstrip(": "), "ID": n.id,
                 "Kind": n.get("kind"), "Status": n.get("status"),
                 "Milestone": bool(n.get("milestone")), "Verification": n.get("verification"),
                 "Task": task, "Summary": str(n.get("summary", ""))}
        page(f"result:{n.id}", props["Name"], _md(root, f, body), kind="result", props=props)
    return pages


def result_properties(props: dict, links: dict | None = None) -> dict:
    find = task_finder(links or {})
    out = {"Name": {"title": nb._t(props["Name"])},
           "ID": {"rich_text": nb._t(props["ID"])},
           "Milestone": {"checkbox": bool(props.get("Milestone"))},
           "Task": {"rich_text": nb.link_rich(nb._t(props["Task"]), find) if props.get("Task") else []},
           "Summary": {"rich_text": nb.fit100(nb.rich(props["Summary"]))},
           "Last synced": {"date": {"start": dt.date.today().isoformat()}}}
    for k in ("Kind", "Status", "Verification"):
        out[k] = {"select": {"name": str(props[k])} if props.get(k) else None}
    return out


RESULTS_DB_PROPERTIES = {
    "Name": {"title": {}}, "ID": {"rich_text": {}}, "Task": {"rich_text": {}},
    "Summary": {"rich_text": {}}, "Last synced": {"date": {}}, "Milestone": {"checkbox": {}},
    "Kind": {"select": {}},
    "Status": {"select": {"options": [{"name": n, "color": c} for n, c in (
        ("active", "blue"), ("paused", "yellow"), ("done", "green"), ("failed", "red"),
        ("superseded", "gray"), ("abandoned", "brown"))]}},
    "Verification": {"select": {}},
}


def row_properties(page: dict, links: dict | None = None) -> dict:
    return result_properties(page["properties"], links) if page["kind"] == "result" \
        else task_properties(page["properties"])


def task_properties(props: dict) -> dict:
    out = {"Name": {"title": nb._t(props["Name"])},
           "ID": {"rich_text": nb._t(props["ID"])},
           "Summary": {"rich_text": nb.fit100(nb.rich(props["Summary"]))},
           "Last synced": {"date": {"start": dt.date.today().isoformat()}}}
    for k in ("Status", "Type", "Area", "Privacy", "Verification"):
        out[k] = {"select": {"name": str(props[k])} if props.get(k) else None}
    return out


TASKS_DB_PROPERTIES = {
    "Name": {"title": {}}, "ID": {"rich_text": {}}, "Summary": {"rich_text": {}},
    "Last synced": {"date": {}},
    "Status": {"select": {"options": [{"name": n, "color": c} for n, c in (
        ("active", "blue"), ("paused", "yellow"), ("done", "green"), ("failed", "red"),
        ("superseded", "gray"), ("abandoned", "brown"))]}},
    "Area": {"select": {"options": [{"name": "project", "color": "purple"}, {"name": "brainstorm", "color": "pink"}]}},
    "Type": {"select": {}}, "Privacy": {"select": {"options": [
        {"name": "public", "color": "green"}, {"name": "soft-private", "color": "yellow"},
        {"name": "hard-private", "color": "red"}]}},
    "Verification": {"select": {}},
}


# ---------------------------------------------------------------- change detection

def changes(pages: list[dict], st: dict) -> list[tuple[str, dict]]:
    out = []
    known = st.get("pages", {})
    for p in pages:
        ent = known.get(p["key"], {})
        if not ent.get("page_id"):
            out.append(("new", p))
        elif ent.get("text_sha") != p["text_sha"]:
            out.append(("text", p))
        elif {k: v["file"] for k, v in ent.get("plots", {}).items()} != \
                {x["key"]: _plot_id(x) for x in p["plots"] if not x["too_big"]}:
            out.append(("plots", p))
    return out


def missing_captions(pages: list[dict]) -> list[str]:
    return [x["path"] for p in pages for x in p["plots"] if not x["has_caption"]]


# ---------------------------------------------------------------- writing

class Mirror:
    def __init__(self, proj: Project):
        self.p = proj
        self.c = proj.client
        self.st = proj.require_state()
        self.st.setdefault("pages", {})

    def save(self):
        self.p.save_state(self.st)

    def _image(self, x: dict) -> dict:
        fid = self.p.upload(self.st, self.p.root / x["path"], x["sha"])
        return nb.media(fid, nb._t(x["path"], {"code": True, "color": "gray"}), kind=x["kind"])

    def _pair(self, x: dict) -> list:
        """A plot as two blocks: the image, then its caption box."""
        img, cap = self._image(x), caption_blocks(x["caption"], x["path"])
        img["_plot"], cap["_cap"] = x["key"], x["key"]
        return [img, cap]

    def _plot_blocks(self, page: dict) -> list:
        out = [nb.blk("heading_2", nb.rich("Plots"))] if page["plots"] else []
        for x in page["plots"]:
            if x["too_big"]:
                out += nb.md_to_blocks(f"`{x['path']}` (over 20 MiB, not uploaded)")
            else:
                out += self._pair(x)
        return out

    def _clear(self, page_id: str, keep=()):
        for b in self.c.children(page_id):
            if b["type"] not in KEEP_TYPES and b["id"] not in keep:
                self.c.delete(b["id"])

    def _resolve_images(self, blocks: list) -> list:
        """Upload the figures of image placeholders (image_resolver) and fill their blocks."""
        out = []
        for b in blocks:
            if "_local" in b:
                loc = b["_local"]
                fid = self.p.upload(self.st, self.p.root / loc["path"], loc["sha"])
                cap = nb._t(loc["path"], {"code": True, "color": "gray"})
                b = nb.media(fid, cap, kind=b["type"])
            else:
                body = b[b["type"]]
                if body.get("children"):
                    b = {**b, b["type"]: {**body, "children": self._resolve_images(body["children"])}}
            out.append(b)
        return out

    def _write_body(self, page: dict, page_id: str) -> dict:
        self._clear(page_id)
        blocks = self._resolve_images(page["blocks"]) + self._plot_blocks(page)
        ids = self.c.append(page_id, blocks)
        by_key = {x["key"]: x for x in page["plots"]}
        out = {}
        for b, i in zip(blocks, ids):
            if "_plot" in b:
                x = by_key[b["_plot"]]
                out[x["key"]] = {"block": i, "file": _plot_id(x)}
            elif "_cap" in b:
                out[b["_cap"]]["caption_block"] = i
        return out

    def _replace_caption(self, block_id: str, x: dict) -> None:
        cap = caption_blocks(x["caption"], x["path"])["callout"]
        self.c.call("PATCH", f"/blocks/{block_id}", {"callout": {"rich_text": cap["rich_text"]}})
        for b in self.c.children(block_id):
            self.c.delete(b["id"])
        if cap.get("children"):
            self.c.append(block_id, cap["children"])

    def _update_plots(self, page: dict, ent: dict, log) -> dict:
        old = ent.get("plots", {})
        new = {x["key"]: x for x in page["plots"] if not x["too_big"]}
        out, last = {}, None
        for key, x in new.items():
            ident = _plot_id(x)
            o = old.get(key)
            if o and o["file"] == ident and o.get("caption_block"):
                out[key] = o
                last = o["caption_block"]
                continue
            if o and o.get("caption_block"):
                cur = self.c.call("GET", f"/blocks/{o['block']}", ok404=True)
                if cur and not cur.get("archived") and cur["type"] == x["kind"]:
                    old_path_sha, new_path_sha = o["file"].rsplit("@", 1)[0], ident.rsplit("@", 1)[0]
                    if old_path_sha != new_path_sha:
                        img = self._image(x)[x["kind"]]
                        patch = {k: v for k, v in img.items() if k != "type"}   # PATCH rejects "type"
                        self.c.call("PATCH", f"/blocks/{o['block']}", {x["kind"]: patch})
                    if o["file"].rsplit("@", 1)[1] != x["cap_sha"]:
                        self._replace_caption(o["caption_block"], x)
                    out[key] = {"block": o["block"], "caption_block": o["caption_block"], "file": ident}
                    last = o["caption_block"]
                    log(f"  updated {x['path']}")
                    continue
            if o:                            # an older layout, or a block of another kind
                self.c.delete(o["block"])
                if o.get("caption_block"):
                    self.c.delete(o["caption_block"])
            if last is None:     # no earlier plot: after the Plots heading, or a new heading
                heads = [b for b in self.c.children(ent["page_id"]) if b["type"] == "heading_2"
                         and "".join(t["plain_text"] for t in b["heading_2"]["rich_text"]) == "Plots"]
                last = heads[-1]["id"] if heads else \
                    self.c.append(ent["page_id"], [nb.blk("heading_2", nb.rich("Plots"))])[0]
            img_id, cap_id = self.c.append(ent["page_id"], self._pair(x), after=last)
            out[key] = {"block": img_id, "caption_block": cap_id, "file": ident}
            last = cap_id
            log(f"  {'replaced' if o else 'added'} {x['path']}")
        for key in set(old) - set(new):
            self.c.delete(old[key]["block"])
            if old[key].get("caption_block"):
                self.c.delete(old[key]["caption_block"])
            log(f"  removed {key}")
        return out

    def database(self, kind: str) -> str:
        """The id of the Tasks or Results database; Results is made when first needed."""
        if kind == "task":
            return self.st["tasks_db"]
        if not self.st.get("results_db"):
            db = self.c.call("POST", "/databases", {
                "parent": {"type": "page_id", "page_id": self.st["root_page"]},
                "title": nb._t("Results"), "properties": RESULTS_DB_PROPERTIES})
            self.st["results_db"] = db["id"]
            self.save()
        return self.st["results_db"]

    def sync_page(self, how: str, page: dict, log=print):
        pages = self.st["pages"]
        home = self.st["root_page"]
        ent = pages.setdefault(page["key"], {})
        if page["kind"] == "home":
            rt = page["blocks"][0]["callout"]["rich_text"]
            banner = self.st.get("banner")
            if banner and self.c.call("GET", f"/blocks/{banner}", ok404=True):
                self.c.call("PATCH", f"/blocks/{banner}", {"callout": {"rich_text": rt}})
            else:
                banner = self.c.append(home, page["blocks"])[0]
                self.st["banner"] = banner
            self._clear(home, keep=(banner,))
            ent.update(page_id=home, text_sha=page["text_sha"], plots={})
            return
        if how == "new":
            body = {"parent": {"page_id": home}, "properties": {"title": {"title": nb._t(page["title"])}}}
            if page["kind"] in ("task", "result"):
                body = {"parent": {"database_id": self.database(page["kind"])},
                        "properties": row_properties(page, task_links(self.st))}
            if page.get("icon"):
                body["icon"] = {"type": "emoji", "emoji": page["icon"]}
            ent["page_id"] = self.c.call("POST", "/pages", body)["id"]
        elif page["kind"] in ("task", "result"):
            self.c.call("PATCH", f"/pages/{ent['page_id']}",
                        {"properties": row_properties(page, task_links(self.st))})
        elif how == "text":
            body = {"properties": {"title": {"title": nb._t(page["title"])}}}
            if page.get("icon"):
                body["icon"] = {"type": "emoji", "emoji": page["icon"]}
            self.c.call("PATCH", f"/pages/{ent['page_id']}", body)
        if how in ("new", "text"):
            ent["plots"] = self._write_body(page, ent["page_id"])
        else:
            ent["plots"] = self._update_plots(page, ent, log)
        ent["text_sha"] = page["text_sha"]


def sync(proj: Project, only=None, plots_only=False, dry_run=False, log=print) -> list[tuple[str, str]]:
    """Write every changed page; return [(how, key)]. The state is saved after each page.

    New tasks and results get their (empty) rows first, so that every page can link to them."""
    m = Mirror(proj) if not dry_run else None
    st = m.st if m else proj.require_state()
    if m:
        for page in render(proj.root, task_links(st)):
            ent = st["pages"].get(page["key"], {})
            if page["kind"] in ("task", "result") and not ent.get("page_id"):
                log(f"new    {page['key']}")
                ent = st["pages"].setdefault(page["key"], {})
                ent["page_id"] = m.c.call("POST", "/pages", {
                    "parent": {"database_id": m.database(page["kind"])},
                    "properties": row_properties(page)})["id"]
                m.save()
    todo = changes(render(proj.root, task_links(st)), st)
    if only:
        todo = [(h, p) for h, p in todo if p["key"] in only]
    if plots_only:
        todo = [(h, p) for h, p in todo if h == "plots"]
    todo.sort(key=lambda hp: hp[1]["kind"] != "home")      # the banner first, pages below it
    for how, page in todo:
        log(f"{how:6s} {page['key']}")
        if m:
            m.sync_page(how, page, log)
            m.save()
    return [(h, p["key"]) for h, p in todo]


def diff(proj: Project) -> dict:
    st = proj.require_state()
    pages = render(proj.root, task_links(st))
    gone = {k for k in st.get("pages", {})} - {p["key"] for p in pages}
    return {"changes": [(h, p["key"]) for h, p in changes(pages, st)],
            "gone": sorted(gone), "missing_captions": missing_captions(pages)}
