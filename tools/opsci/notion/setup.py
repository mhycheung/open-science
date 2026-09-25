"""Set up Notion for the user (check) and for a project (init, enable), and the sync hook.

- check: the token file, the integration, the parent page, and the user's Notion account.
- init: create the project's pages under the parent page (root page, Tasks database, Feed),
  then write everything once.
- enable: for an existing project, add the Notion section to AGENTS.md, the auto-sync hook
  to .claude/settings.json, `notion: true` to config/framework.yaml, and the state file to
  .gitignore. New projects get
  the same from `opsci template instantiate --notion`.
- hook: what the hook runs when a Claude Code turn ends: a sync in the background, one at a
  time, output in messages/notion-sync.log. It never blocks or fails the turn.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import blocks as nb
from . import feed, mirror
from .client import Client, NotionError, credentials_path, page_id_from, read_token
from .project import STATE_FILE, Project

# `|| true`: a Stop hook that exits 2 stops Claude Code from ending the turn, and an old opsci
# without `notion` exits 2 on the unknown command. The hook must never block a session.
HOOK_COMMAND = "opsci notion sync --hook || true"
LOG_FILE = "messages/notion-sync.log"
LOCK_FILE = "messages/.notion-sync.lock"

# The AGENTS.md section a project gets when it uses Notion. The template holds the same text
# between <!-- opsci:notion --> markers; a test checks that the two agree.
AGENTS_SECTION = """## 10. Notion

This project is mirrored to Notion, where the user reads it (skill
`open-science-project:notion`). `opsci notify` posts to the project's Feed in Notion.

- **Sync after every change.** After you change project files, run `opsci notion sync`. A
  hook also runs it when a turn ends. `opsci notion diff` then prints `in sync` and names
  no plot without a caption.
- **Plots.** Only plots under `tasks/<id>/` appear in the task's page, each with its caption
  file (section 2). A remade plot gets a new dated name; it replaces the old one in place.
- **Feed.** Post with `opsci notion post --kind KIND [--task ID] [--mention] [--file PLOT]
  "text"`: a finished subtask as `result` with its key plot and `--mention`; a question or
  blocker for the user as `question` or `blocker` with `--mention`; a long job submitted
  or finished as `status`. While work runs, post a `status` at least once per session. Do
  not post routine steps.
- **Anything that waits on the user goes to the Feed**, unasked: a new plan to approve, a
  hold point, a decision, a question. Sync first, so the task page shows what the message
  is about, then post it as `question` with `--task` and `--mention` before ending the turn.
  A message only in the chat is one the user may never see.
- **Messages expire.** Feed messages are removed after a few days. Anything that must last
  goes in the project files, which the task pages show.
"""


# ---------------------------------------------------------------- check

def send_test(project_root: str | None = None, out=print) -> int:
    """Make a page under the parent page that @mentions the user (onboarding's test)."""
    proj = Project(project_root)
    parent, user = proj.section.get("parent_page"), proj.section.get("user")
    if not parent or not user:
        raise NotionError("set notify.notion.parent_page and notify.notion.user first (opsci notion check)")
    page = proj.client.call("POST", "/pages", {
        "parent": {"page_id": page_id_from(str(parent))},
        "properties": {"title": {"title": nb._t("opsci: notification test")}},
        "children": [nb.blk("paragraph", [{"type": "mention", "mention": {"type": "user", "user": {"id": user}}}]
                            + nb._t(" this is a test message from opsci. If Notion notified you, the setup works."))]})
    out(f"test_page={page['id']}")
    return 0


def remove_test(page_id: str, project_root: str | None = None, out=print) -> int:
    proj = Project(project_root)
    proj.client.call("PATCH", f"/pages/{page_id_from(page_id)}", {"archived": True})
    out("test_page=removed")
    return 0


def check(project_root: str | None = None, out=print) -> int:
    """Check the user's Notion setup; print key=value lines, never the token."""
    proj = Project(project_root)
    ok = True
    out(f"backend={proj.cfg.get('backend') or 'file'}")
    path = credentials_path(proj.section)
    try:
        token = read_token(path, (proj.root, proj.main))
        out("token=ok")
    except NotionError as exc:
        out(f"token=problem: {exc}")
        return 1
    c = Client(token)
    try:
        me = c.call("GET", "users/me")
        out(f"integration=ok:{me.get('name')}")
    except NotionError as exc:
        out(f"integration=problem: {exc}")
        return 1
    parent = proj.section.get("parent_page")
    if not parent:
        out("parent_page=missing")
        ok = False
    else:
        try:
            pg = c.call("GET", f"/pages/{page_id_from(str(parent))}")
            t = "".join(x.get("plain_text", "") for v in pg.get("properties", {}).values()
                        if v.get("type") == "title" for x in v["title"])
            out(f"parent_page=ok:{t}")
        except NotionError as exc:
            out(f"parent_page=problem: {exc} (share the page with the integration: ••• → Connections)")
            ok = False
    user = proj.section.get("user")
    if user:
        try:
            u = c.call("GET", f"/users/{user}")
            out(f"user=ok:{u.get('name')}")
        except NotionError as exc:
            out(f"user=problem: {exc}")
            ok = False
    else:
        people = [u for u in c.call("GET", "/users?page_size=100").get("results", []) if u.get("type") == "person"]
        out("user=missing")
        for u in people:
            email = (u.get("person") or {}).get("email", "")
            out(f"user_candidate={u['id']} {u.get('name', '')} {email}".rstrip())
        ok = False
    return 0 if ok else 1


# ---------------------------------------------------------------- init

def _project_title(root: Path) -> str:
    p = root / "PROJECT.md"
    if p.exists():
        m = re.search(r"^# (.+?)(?::\s*what this project is about)?\s*$", p.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1).strip()
    return root.name


def init(project_root: str | None = None, days: int = feed.DEFAULT_DAYS, log=print) -> str:
    proj = Project(project_root)
    if proj.load_state().get("root_page"):
        raise NotionError(f"this project already has Notion pages ({proj.state_path}); use opsci notion sync")
    parent = proj.section.get("parent_page")
    if not parent:
        raise NotionError("no parent page: set notify.notion.parent_page in ~/.config/opsci/config.yaml "
                          "(opsci notion check)")
    c = proj.client
    title = f"{proj.root.name} — {_project_title(proj.root)}"
    root = c.call("POST", "/pages", {"parent": {"page_id": page_id_from(str(parent))},
                                     "icon": {"type": "emoji", "emoji": "🔭"},
                                     "properties": {"title": {"title": nb._t(title)}}})
    st = {"root_page": root["id"], "url": root.get("url", ""), "pages": {}}
    proj.save_state(st)          # from here on a failure can be resumed with sync
    db = c.call("POST", "/databases", {"parent": {"type": "page_id", "page_id": root["id"]},
                                       "title": nb._t("Tasks"), "properties": mirror.TASKS_DB_PROPERTIES})
    st["tasks_db"] = db["id"]
    feed.create(proj, st, days)
    proj.save_state(st)
    mirror.sync(proj, log=log)
    return st["url"]


# ---------------------------------------------------------------- enable (existing project)

def add_hook(settings: Path) -> bool:
    """Add the Stop hook to a Claude Code settings file; keep every other key. True if added."""
    data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    stop = data.setdefault("hooks", {}).setdefault("Stop", [])
    for entry in stop:
        for h in entry.get("hooks", []):
            if str(h.get("command", "")).startswith("opsci notion sync --hook"):
                if h["command"] == HOOK_COMMAND:
                    return False
                h["command"] = HOOK_COMMAND          # upgrade an older form in place
                settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                return True
    stop.append({"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def add_agents_section(agents: Path) -> bool:
    text = agents.read_text(encoding="utf-8")
    if "\n## 10. Notion\n" in text or text.startswith("## 10. Notion\n"):
        return False
    agents.write_text(text.rstrip("\n") + "\n\n" + AGENTS_SECTION, encoding="utf-8")
    return True


CLAUDE_LINE = """- Notion (this project is mirrored there, `AGENTS.md` section 10):
  `open-science-project:notion`. Main agents load it at session start.
"""


def add_claude_skill_line(claude_md: Path) -> bool:
    """Name the notion skill in CLAUDE.md's skill list (template: the opsci:notion block)."""
    if not claude_md.exists():
        return False
    text = claude_md.read_text(encoding="utf-8")
    if "open-science-project:notion" in text:
        return False
    m = re.search(r"^## Skills\n.*?(?=^## |\Z)", text, re.M | re.S)
    if m:
        block = m.group(0).rstrip("\n") + "\n" + CLAUDE_LINE
        text = text[:m.start()] + block + ("\n" if text[m.end():] else "") + text[m.end():]
    else:
        text = text.rstrip("\n") + "\n\n## Skills\n\n" + CLAUDE_LINE
    claude_md.write_text(text, encoding="utf-8")
    return True


def set_framework_flag(framework: Path) -> bool:
    if not framework.exists():
        return False
    text = framework.read_text(encoding="utf-8")
    if re.search(r"^notion:\s*true\s*$", text, re.M):
        return False
    if re.search(r"^notion:", text, re.M):
        text = re.sub(r"^notion:.*$", "notion: true", text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n# Mirrored to Notion (opsci notion; AGENTS.md section 10).\nnotion: true\n"
    framework.write_text(text, encoding="utf-8")
    return True


def add_gitignore(gitignore: Path) -> bool:
    line = "/" + STATE_FILE          # anchored at the project root
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if line in text.split("\n"):
        return False
    gitignore.write_text(text.rstrip("\n") + "\n# Notion page ids and sync state of this checkout (opsci notion)\n"
                         + line + "\n", encoding="utf-8")
    return True


def enable(project_root: str | None = None, log=print) -> None:
    proj = Project(project_root)
    root = proj.root
    for label, done in ((f".gitignore: {STATE_FILE}", add_gitignore(root / ".gitignore")),
                        ("AGENTS.md: section 10 (Notion)", add_agents_section(root / "AGENTS.md")),
                        ("CLAUDE.md: the notion skill", add_claude_skill_line(root / "CLAUDE.md")),
                        (".claude/settings.json: Stop hook", add_hook(root / ".claude" / "settings.json")),
                        ("config/framework.yaml: notion: true", set_framework_flag(root / "config" / "framework.yaml"))):
        log(f"{'added' if done else 'already there'}: {label}")


# ---------------------------------------------------------------- the hook

def run_hook(project_root: str | None = None) -> int:
    """Start a background sync and return at once. Never fails the Claude Code turn."""
    try:
        proj = Project(project_root)
        if not proj.load_state().get("root_page"):
            return 0
        logf = proj.main / LOG_FILE
        logf.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "opsci.cli", "notion", "sync", "--locked",
               "--project-root", str(proj.root)]
        with open(logf, "a", encoding="utf-8") as fh:
            subprocess.Popen(cmd, stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
                             start_new_session=True, cwd=str(proj.root))
    except Exception:  # noqa: BLE001 - a hook must never break the session
        pass
    return 0


def locked_sync(project_root: str | None = None) -> int:
    """Sync unless another sync of this checkout is running (then that one will catch up)."""
    import datetime as dt
    proj = Project(project_root)
    lock = proj.main / LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        print(f"--- {dt.datetime.now():%Y-%m-%d %H:%M:%S} sync", flush=True)
        try:
            done = mirror.sync(proj)
            print("in sync" if not done else f"wrote {len(done)} page(s)", flush=True)
        except NotionError as exc:
            print(f"error: {exc}", flush=True)
            return 1
    return 0
