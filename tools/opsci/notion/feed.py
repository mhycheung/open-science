"""The project's Feed page: messages to the user, newest first, removed after a few days.

Each message is one callout block, inserted just below the Feed's header block. Its title and
text are markdown with $LaTeX$; attached plots appear inline with their caption files
(<stem>.caption.md). With `mention`, the user (notify.notion.user) is @mentioned, so Notion
notifies them: the messages are written by the integration (a bot), not by the user.

Every message is also kept in messages/notion-feed.jsonl. Pruning deletes only the callout
block in Notion; results and plots stay in the project files and so in the task pages.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path

from . import blocks as nb
from .client import NotionError
from .mirror import caption_paragraphs
from .project import Project

KINDS = {"status": ("🔵", "blue_background"), "result": ("🟢", "green_background"),
         "question": ("🟠", "orange_background"), "blocker": ("🔴", "red_background"),
         "note": ("⚪", "gray_background")}
DEFAULT_DAYS = 3


def _attachment_blocks(proj: Project, st: dict, path: Path) -> list:
    """The attached file, then (for a plot) its caption file as paragraphs."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    fid = proj.upload(st, path, digest)
    try:
        rel = str(path.resolve().relative_to(proj.root))
    except ValueError:
        rel = path.name
    suffix = path.suffix.lower()
    kind = "pdf" if suffix == ".pdf" else "image" if suffix in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp") else "file"
    out = [nb.media(fid, nb._t(rel, {"code": True, "color": "gray"}), kind=kind)]
    if kind != "file":
        out += [nb.blk("paragraph", p) for p in caption_paragraphs(path)]
    return out


def post(proj: Project, text: str, author: str = "agent", kind: str = "note", task: str = "",
         title: str | None = None, mention: bool = False, files=(), days: int = DEFAULT_DAYS,
         prune_old: bool = True) -> str:
    if kind not in KINDS:
        raise NotionError(f"unknown kind '{kind}'; one of {', '.join(KINDS)}")
    st = proj.require_state()
    feed, header = st.get("feed_page"), st.get("feed_header")
    if not feed or not header:
        raise NotionError("this project has no Feed page; run: opsci notion init")
    user = proj.section.get("user")
    if mention and not user:
        raise NotionError("no user to mention: set notify.notion.user (opsci notion check prints it)")
    now = dt.datetime.now(dt.timezone.utc)
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if title is None:            # the first paragraph, unless it is one long paragraph
        if not paras:
            title = "(no text)"
        elif len(paras) > 1 or len(paras[0]) <= 120:
            title = paras.pop(0)
        else:
            title = paras[0][:80] + "…"
    head = []
    if mention:
        head += [{"type": "mention", "mention": {"type": "user", "user": {"id": user}}}] + nb._t(" ")
    from .mirror import task_finder, task_links
    find = task_finder(task_links(st))         # task ids in a message link to the task pages
    head += nb.fit100(nb.link_rich(nb.rich(title, {"bold": True}), find))[:100 - len(head)]
    meta = f"{kind} · {author}" + (f" · {task}" if task else "") + f" · {now:%Y-%m-%d %H:%M} UTC"
    children = [nb.blk("paragraph", nb._t(meta, {"italic": True, "color": "gray"}))]
    for para in paras:
        children += [nb.blk("paragraph", rt)
                     for rt in nb.chunks100(nb.link_rich(nb.rich(" ".join(para.split())), find))]
    attached = []
    for f in files or ():
        f = Path(f)
        if not f.is_file():
            raise NotionError(f"attachment not found: {f}")
        children += _attachment_blocks(proj, st, f)
        attached.append(str(f))
    icon, color = KINDS[kind]
    block = nb.callout(head, emoji=icon, color=color, children=children)
    bid = proj.client.append(feed, [block], after=header)[0]
    proj.save_state(st)          # the upload cache
    msgs = proj.ledger()
    msgs.append({"posted": now.isoformat(timespec="seconds"), "author": author, "kind": kind, "task": task,
                 "title": title, "text": text, "files": attached, "mention": bool(mention),
                 "block_id": bid, "pruned": False})
    proj.save_ledger(msgs)
    if prune_old:
        prune(proj, days)
    return bid


def prune(proj: Project, days: int = DEFAULT_DAYS) -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    msgs = proj.ledger()
    n = 0
    for m in msgs:
        if not m.get("pruned") and dt.datetime.fromisoformat(m["posted"]) < cutoff:
            proj.client.delete(m["block_id"])
            m["pruned"] = True
            n += 1
    if n:
        proj.save_ledger(msgs)
    return n


def create(proj: Project, st: dict, days: int = DEFAULT_DAYS) -> None:
    """Create the Feed page under the project's root page (init)."""
    page = proj.client.call("POST", "/pages", {
        "parent": {"page_id": st["root_page"]}, "icon": {"type": "emoji", "emoji": "📣"},
        "properties": {"title": {"title": nb._t("Feed")}}})
    header = proj.client.append(page["id"], [nb.callout(nb.rich(
        f"Messages from the agents, newest first. Messages older than {days} days are removed; "
        "results and plots stay in the task pages."), emoji="ℹ️")])[0]
    st["feed_page"], st["feed_header"] = page["id"], header
