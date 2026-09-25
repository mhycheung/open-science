"""`opsci notion` tests: markdown conversion, plot discovery, the mirror, the Feed, the notify
back end, setup (init, enable, template component) and the token's safety.

Everything runs against tests/notion_mock.py, a local stand-in for the Notion REST API, reached
through $OPSCI_NOTION_TEST_API_BASE (honoured only for loopback URLs). The user config comes
from $OPSCI_CONFIG and HOME is an empty temporary directory, so no real config or token is
read. The one test against the real API is manual (--run-manual) and only runs `check`.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import REPO, TEMPLATE, run_opsci
from notion_mock import MockNotion
from opsci import template as T
from opsci.notion import blocks as nb
from opsci.notion import client as NC
from opsci.notion import mirror, setup as NS
from opsci.notion.project import STATE_FILE

# Fake, for the mock server only; built in pieces so secret scanners do not flag this file.
TOKEN = "ntn_" + "000000000000" + "MockOnlyToken" + "q7Zp" * 4
TASK = "t01-demo"
# The mock records paths relative to /v1. Split in two so the repository's absolute-path
# check (tests/test_repo.py) does not read it as a filesystem path.
ME_PATH = "/users" "/me"


# ---------------------------------------------------------------- helpers

def write_creds(path: Path, mode=0o600, token=TOKEN) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"NOTION_TOKEN={token}\n")
    os.chmod(path, mode)
    return path


def write_config(path: Path, mock: MockNotion, creds: Path, backend="notion", user=True):
    section = {"credentials_file": str(creds), "parent_page": mock.parent_id}
    if user:
        section["user"] = mock.person["id"]
    path.write_text(yaml.safe_dump({"notify": {"backend": backend, "notion": section}}))


@pytest.fixture
def mock():
    with MockNotion(TOKEN) as m:
        yield m


@pytest.fixture
def S(tmp_path, mock, monkeypatch):
    """Test setting: mock server, private token file, user config, and the child environment.
    The same variables are set in this process too, for the in-process calls."""
    home = tmp_path / "home"
    home.mkdir()
    creds = write_creds(tmp_path / "secrets" / "notion.env")
    cfg = tmp_path / "config.yaml"
    write_config(cfg, mock, creds)
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPSCI_CONFIG", "XDG_CONFIG_HOME", NC.TEST_API_ENV, "OPSCI_AUTHOR")}
    env.update({"HOME": str(home), "OPSCI_CONFIG": str(cfg), NC.TEST_API_ENV: mock.base})
    for k in ("HOME", "OPSCI_CONFIG", NC.TEST_API_ENV):
        monkeypatch.setenv(k, env[k])
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return SimpleNamespace(tmp=tmp_path, mock=mock, creds=creds, cfg=cfg, env=env)


def run(S, *args, cwd=None, rc=0, env=None):
    """Run `opsci` as a user would. The token must never appear in its output."""
    r = subprocess.run([sys.executable, "-m", "opsci.cli", *map(str, args)], capture_output=True,
                       text=True, cwd=cwd, env=env or S.env)
    assert TOKEN not in r.stdout + r.stderr
    if rc is not None:
        assert r.returncode == rc, f"rc={r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    return r


def make_project(S, notion=True, name="proj") -> Path:
    dest = S.tmp / name
    args = ["template", "instantiate", dest, "--name", name, "--title", "Demo project", "--author", "A",
            "--template", TEMPLATE, "--framework-repo", "https://example.org/fw"]
    run(S, *args, *(["--notion"] if notion else []))
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    return dest


def add_task(proj: Path, tid=TASK) -> Path:
    td = proj / "tasks" / tid
    td.mkdir(parents=True)
    (td / "context.md").write_text(
        f"---\nid: {tid}\ntitle: Demo task\ntype: task\nstatus: active\nprivacy: public\n"
        f"summary: A demo with $x^2$.\n---\n# {tid}: Demo task\n\nThe fit gives $\\hat{{Q}}(t)$.\n")
    return td


def plot(path: Path, data: bytes, caption: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if caption is not None:
        mirror.caption_file(path).write_text(caption)
    return path


def state(proj: Path) -> dict:
    return yaml.safe_load((proj / "config" / "notion.local.yaml").read_text())


def task_page(proj: Path) -> str:
    return state(proj)["pages"][f"task:{TASK}"]["page_id"]


def live_ids(mock, page_id) -> list[str]:
    return [n["id"] for n in mock.kids(page_id)]


def text_of(n: dict) -> str:
    return MockNotion.plain(n["body"].get("rich_text", []))


def plot_entries(mock, page_id) -> list[dict]:
    """The plots of a task page, in order: {image, cap, text, rich} where cap is the caption
    callout after the image, text its whole text (first paragraph + child paragraphs)."""
    kids = mock.kids(page_id)
    heads = [i for i, n in enumerate(kids) if n["type"] == "heading_2" and text_of(n) == "Plots"]
    assert len(heads) <= 1
    out = []
    if not heads:
        return out
    rest = kids[heads[0] + 1:]
    for i, n in enumerate(rest):
        if n["type"] in ("image", "pdf"):
            cap = rest[i + 1] if i + 1 < len(rest) and rest[i + 1]["type"] == "callout" else None
            rich = list(cap["body"]["rich_text"]) if cap else []
            for c in (mock.kids(cap["id"]) if cap else []):
                rich += c["body"].get("rich_text", [])
            out.append({"image": n, "cap": cap, "rich": rich, "text": MockNotion.plain(rich)})
    return out


# ---------------------------------------------------------------- blocks: inline

def plain(rt):
    return "".join(x["text"]["content"] if x["type"] == "text" else x["equation"]["expression"] for x in rt)


def test_rich_keeps_asterisk_in_t_star_literal():
    rt = nb.rich("peak at t* = -27 M and t* = 10 M")
    assert plain(rt) == "peak at t* = -27 M and t* = 10 M"
    assert not any(x.get("annotations", {}).get("italic") for x in rt)


def test_rich_math_becomes_equation():
    rt = nb.rich(r"the charge $\hat{Q}(t)$ grows")
    eq = [x for x in rt if x["type"] == "equation"]
    assert eq == [{"type": "equation", "equation": {"expression": r"\hat{Q}(t)"}}]
    assert plain(rt) == r"the charge \hat{Q}(t) grows"


def test_rich_bold():
    rt = nb.rich("a **strong** claim")
    assert [x["text"]["content"] for x in rt if x.get("annotations", {}).get("bold")] == ["strong"]


def test_rich_relative_link_becomes_code_path_external_link_kept():
    rt = nb.rich("see [the plan](tasks/t01/plan.md#goal) and [Notion](https://www.notion.so/x)")
    code = [x["text"]["content"] for x in rt if x.get("annotations", {}).get("code")]
    assert code == ["tasks/t01/plan.md"]
    assert not any(x["text"].get("link") and "plan.md" in x["text"]["link"]["url"] for x in rt)
    ext = [x for x in rt if x["text"].get("link")]
    assert [(x["text"]["content"], x["text"]["link"]["url"]) for x in ext] == [("Notion", "https://www.notion.so/x")]
    # a link whose text is the path shows the path once
    rt = nb.rich("[`src/fit.py`](src/fit.py)")
    assert [(x["text"]["content"], x["annotations"].get("code")) for x in rt] == [("src/fit.py", True)]


def test_rich_unescapes_pipe():
    assert plain(nb.rich(r"a \| b")) == "a | b"


# ---------------------------------------------------------------- blocks: md_to_blocks

def test_hard_wrapped_lines_are_joined():
    b = nb.md_to_blocks("first line\nsecond line\n\nnext para\n")
    assert [x["type"] for x in b] == ["paragraph", "paragraph"]
    assert plain(b[0]["paragraph"]["rich_text"]) == "first line second line"


def test_pipe_line_inside_paragraph_is_not_a_table():
    b = nb.md_to_blocks("the value is\n| not a table\n")
    assert [x["type"] for x in b] == ["paragraph"]
    assert plain(b[0]["paragraph"]["rich_text"]) == "the value is | not a table"


def test_pipe_table():
    b = nb.md_to_blocks("| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 \\| 6 | 7 |\n")
    assert [x["type"] for x in b] == ["table"]
    t = b[0]["table"]
    assert t["table_width"] == 3 and t["has_column_header"]
    rows = [[plain(c) for c in r["table_row"]["cells"]] for r in t["children"]]
    assert rows == [["a", "b", "c"], ["1", "2", "3"], ["4", "5 | 6", "7"]]


def test_nested_bullets():
    b = nb.md_to_blocks("- top\n  - inner\n    - deepest\n- second\n")
    assert [plain(x["bulleted_list_item"]["rich_text"]) for x in b] == ["top", "second"]
    inner = b[0]["bulleted_list_item"]["children"]
    assert [plain(x["bulleted_list_item"]["rich_text"]) for x in inner] == ["inner"]
    assert plain(inner[0]["bulleted_list_item"]["children"][0]["bulleted_list_item"]["rich_text"]) == "deepest"


def test_mermaid_heading_and_display_equation():
    b = nb.md_to_blocks("#### Deep heading\n\n```mermaid\ngraph TD\n  a --> b\n```\n\n$$\nE = mc^2\n$$\n")
    assert [x["type"] for x in b] == ["heading_3", "code", "equation"]
    assert b[1]["code"]["language"] == "mermaid"
    assert b[1]["code"]["rich_text"][0]["text"]["content"] == "graph TD\n  a --> b"
    assert b[2]["equation"]["expression"] == "E = mc^2"


# ---------------------------------------------------------------- plots_in

def test_plots_in_versions_pdf_twin_and_data(tmp_path):
    root = tmp_path
    td = root / "tasks" / TASK
    plot(td / "fig" / "x_2026-09-25.png", b"old")
    plot(td / "fig" / "x_2026-09-28.png", b"new", caption="The new $\\alpha$ plot.\n")
    plot(td / "fig" / "y.png", b"y")
    plot(td / "fig" / "y.pdf", b"%PDF y")          # PDF twin of y.png: skipped
    plot(td / "fig" / "z.pdf", b"%PDF z")          # a PDF on its own: shown
    plot(td / "data" / "w.png", b"w")              # under data/: ignored
    ps = mirror.plots_in(root, td)
    assert [p["path"] for p in ps] == [f"tasks/{TASK}/fig/x_2026-09-28.png", f"tasks/{TASK}/fig/y.png",
                                       f"tasks/{TASK}/fig/z.pdf"]
    assert ps[0]["key"] == f"tasks/{TASK}/fig/x.png"
    assert [p["kind"] for p in ps] == ["image", "image", "pdf"]
    assert [p["has_caption"] for p in ps] == [True, False, False]
    # the caption is a list of paragraphs, each a rich text list with the LaTeX as equations
    (para,) = ps[0]["caption"]
    assert [x["equation"]["expression"] for x in para if x["type"] == "equation"] == ["\\alpha"]
    assert ps[1]["caption"] == []


def test_rich_text_limits_of_100_items():
    rt = nb.rich(" ".join(f"$a_{{{i}}}$" for i in range(150)))
    assert len(rt) > 100
    parts = nb.chunks100(rt)
    assert len(rt) == 299 and [len(p) for p in parts] == [100, 100, 99] and sum(parts, []) == rt
    flat = nb.fit100(rt)                       # a place with room for one list: plain text
    assert len(flat) <= 100 and all(x["type"] == "text" for x in flat)
    assert "".join(x["text"]["content"] for x in flat).startswith("$a_{0}$ $a_{1}$")
    assert nb.fit100(rt[:5]) == rt[:5]


# ---------------------------------------------------------------- end to end: init, diff, sync

@pytest.fixture
def mirrored(S):
    """A project made with --notion, with one task holding one captioned plot, mirrored."""
    proj = make_project(S)
    td = add_task(proj)
    plot(td / "fig" / "x_2026-09-25.png", b"png-x-25",
         caption="Amplitude $A(t)$ against time.\n\nSecond paragraph with $$\\omega$$ nothing.\n")
    S.mock.max_page_size = 4          # exercise pagination of children lists
    r = run(S, "notion", "init", cwd=proj)
    assert "created: https://www.notion.so/" in r.stdout
    S.proj, S.td = proj, td
    return S


def test_init_creates_pages_and_is_in_sync(mirrored):
    S, m = mirrored, mirrored.mock
    st = state(S.proj)
    roots = m.child_pages(m.parent_id)
    assert list(roots) == ["proj — Demo project"]
    root = roots["proj — Demo project"]
    assert st["root_page"] == root
    kids = m.child_pages(root)
    for title in ("Tasks", "Feed", "Project", "Context", "Log"):
        assert title in kids, title
    assert st["tasks_db"] == kids["Tasks"] and st["feed_page"] == kids["Feed"]
    # the Feed header is the Feed page's first block
    assert live_ids(m, st["feed_page"])[0] == st["feed_header"]
    # the task row is a page in the Tasks database, with its properties
    pg = m.pages[task_page(S.proj).replace("-", "")]
    assert pg["parent"] == {"type": "database_id", "database_id": st["tasks_db"]}
    assert pg["properties"]["Status"]["select"] == {"name": "active"}
    assert MockNotion.plain(pg["properties"]["Name"]["title"]) == f"{TASK}: Demo task"
    # the task body has the equation, and the plot with its caption
    body = m.kids(task_page(S.proj))
    assert any(x["type"] == "equation" and x["equation"]["expression"] == r"\hat{Q}(t)"
               for n in body for x in n["body"].get("rich_text", []))
    assert run(S, "notion", "diff", cwd=S.proj).stdout == "in sync\n"
    assert run(S, "notion", "sync", cwd=S.proj).stdout == "in sync\n"
    # a second init is refused
    r = run(S, "notion", "init", cwd=S.proj, rc=2)
    assert "already has Notion pages" in r.stderr


def test_text_edit_shows_in_diff_and_sync_fixes_it(mirrored):
    S, m = mirrored, mirrored.mock
    ctx = S.proj / "context.md"
    ctx.write_text(ctx.read_text() + "\nA new line about $\\beta$.\n")
    assert run(S, "notion", "diff", cwd=S.proj).stdout == "text   context\n"
    assert run(S, "notion", "sync", cwd=S.proj).stdout == "text   context\n"
    assert run(S, "notion", "diff", cwd=S.proj).stdout == "in sync\n"
    page = state(S.proj)["pages"]["context"]["page_id"]
    texts = [text_of(n) for n in m.kids(page)]
    assert "A new line about \\beta." in texts
    assert sum(t.startswith("A new line") for t in texts) == 1       # old body replaced, not appended


def test_caption_text_and_missing_caption(mirrored):
    S, m = mirrored, mirrored.mock
    (e,) = plot_entries(m, task_page(S.proj))
    assert m.file_of(e["image"]["id"]) == ("x_2026-09-25.png", b"png-x-25")
    assert "Amplitude A(t) against time." in e["text"]
    assert "Second paragraph" in e["text"]
    assert {"A(t)"} <= {x["equation"]["expression"] for x in e["rich"] if x["type"] == "equation"}
    # the file path is shown too, in code
    img_cap = e["image"]["body"]["caption"]
    assert f"tasks/{TASK}/fig/x_2026-09-25.png" in MockNotion.plain(img_cap + e["rich"])
    # a plot with no caption file is reported by diff (and still synced)
    plot(S.td / "fig" / "bare.png", b"bare")
    from opsci.notion.project import Project
    d = mirror.diff(Project(str(S.proj)))
    assert d["missing_captions"] == [f"tasks/{TASK}/fig/bare.png"]
    assert d["changes"] == [("plots", f"task:{TASK}")]
    out = run(S, "notion", "diff", cwd=S.proj).stdout
    assert f"no caption: tasks/{TASK}/fig/bare.png  (write tasks/{TASK}/fig/bare.caption.md)" in out
    assert run(S, "notion", "diff", "--strict", cwd=S.proj, rc=1)


def test_plot_remade_caption_edited_added_removed(mirrored):
    S, m = mirrored, mirrored.mock
    page = task_page(S.proj)
    (e0,) = plot_entries(m, page)
    before = live_ids(m, page)

    # remade under a new dated name: same image block, same position, new file
    plot(S.td / "fig" / "x_2026-09-28.png", b"png-x-28", caption="Remade amplitude $A_2(t)$.\n")
    assert run(S, "notion", "diff", cwd=S.proj).stdout == f"plots  task:{TASK}\n"
    run(S, "notion", "sync", "--plots-only", cwd=S.proj)
    assert live_ids(m, page) == before
    (e1,) = plot_entries(m, page)
    assert e1["image"]["id"] == e0["image"]["id"]
    assert m.file_of(e1["image"]["id"]) == ("x_2026-09-28.png", b"png-x-28")
    assert "Remade amplitude" in e1["text"] and "Amplitude A(t)" not in e1["text"]
    assert run(S, "notion", "diff", cwd=S.proj).stdout == "in sync\n"

    # only the caption file changes: caption updated in place, file kept
    n_uploads = len(m.uploads)
    (S.td / "fig" / "x_2026-09-28.caption.md").write_text("Edited caption with $\\gamma$.\n")
    run(S, "notion", "sync", "--plots-only", cwd=S.proj)
    assert live_ids(m, page) == before
    (e2,) = plot_entries(m, page)
    assert e2["image"]["id"] == e0["image"]["id"]
    assert "Edited caption with" in e2["text"] and "Remade" not in e2["text"]
    assert any(x["type"] == "equation" and x["equation"]["expression"] == r"\gamma" for x in e2["rich"])
    assert m.file_of(e2["image"]["id"]) == ("x_2026-09-28.png", b"png-x-28")
    assert len(m.uploads) == n_uploads               # no new upload for a caption change

    # a second plot is inserted after the first
    plot(S.td / "fig" / "y_2026-09-28.png", b"png-y", caption="The y plot.\n")
    run(S, "notion", "sync", "--plots-only", cwd=S.proj)
    ids = live_ids(m, page)
    assert ids[:len(before)] == before
    es = plot_entries(m, page)
    assert [m.file_of(e["image"]["id"])[0] for e in es] == ["x_2026-09-28.png", "y_2026-09-28.png"]
    assert es[0]["image"]["id"] == e0["image"]["id"]
    assert ids.index(es[1]["image"]["id"]) > ids.index(es[0]["image"]["id"])
    assert run(S, "notion", "diff", cwd=S.proj).stdout == "in sync\n"

    # deleting the first plot (every version) removes its blocks
    for p in (S.td / "fig").glob("x_*"):
        p.unlink()
    run(S, "notion", "sync", "--plots-only", cwd=S.proj)
    es = plot_entries(m, page)
    assert [m.file_of(e["image"]["id"])[0] for e in es] == ["y_2026-09-28.png"]
    assert m.node(e0["image"]["id"])["archived"]
    if e0["cap"]:
        assert m.node(e0["cap"]["id"])["archived"]
    assert run(S, "notion", "diff", cwd=S.proj).stdout == "in sync\n"


def test_long_caption_is_shown_whole(mirrored):
    """A caption with more equations than a rich text list holds (100) is not cut."""
    S, m = mirrored, mirrored.mock
    eqs = " ".join(f"$c_{{{i}}}$" for i in range(130))
    plot(S.td / "fig" / "long.png", b"png-long", caption=f"Many symbols: {eqs}.\n\nThe end.\n")
    run(S, "notion", "sync", cwd=S.proj)
    e = [e for e in plot_entries(m, task_page(S.proj)) if m.file_of(e["image"]["id"])[0] == "long.png"][0]
    got = [x["equation"]["expression"] for x in e["rich"] if x["type"] == "equation"]
    assert got == [f"c_{{{i}}}" for i in range(130)]
    assert e["text"].endswith("The end.")


def test_blocks_deleted_by_hand_in_notion(mirrored):
    """A plot block the user already deleted in Notion: removing the plot still syncs."""
    S, m = mirrored, mirrored.mock
    (e,) = plot_entries(m, task_page(S.proj))
    m.node(e["image"]["id"])["archived"] = True
    m.node(e["cap"]["id"])["archived"] = True
    for p in (S.td / "fig").glob("x_*"):
        p.unlink()
    run(S, "notion", "sync", cwd=S.proj)
    assert plot_entries(m, task_page(S.proj)) == []
    assert run(S, "notion", "diff", cwd=S.proj).stdout == "in sync\n"


def test_old_plot_state_without_caption_block_is_replaced(mirrored):
    """State written before captions had their own block: the plot is replaced by the pair."""
    S, m = mirrored, mirrored.mock
    page = task_page(S.proj)
    (e,) = plot_entries(m, page)
    st = state(S.proj)
    ent = st["pages"][f"task:{TASK}"]["plots"][f"tasks/{TASK}/fig/x.png"]
    del ent["caption_block"]
    m.node(e["cap"]["id"])["archived"] = True          # the old layout had no caption box
    (S.proj / "config" / "notion.local.yaml").write_text(yaml.safe_dump(st))
    plot(S.td / "fig" / "x_2026-09-28.png", b"png-x-28", caption="Remade.\n")
    run(S, "notion", "sync", "--plots-only", cwd=S.proj)
    (e2,) = plot_entries(m, page)
    assert m.node(e["image"]["id"])["archived"] and e2["image"]["id"] != e["image"]["id"]
    assert m.file_of(e2["image"]["id"])[0] == "x_2026-09-28.png" and e2["text"] == "Remade."
    assert state(S.proj)["pages"][f"task:{TASK}"]["plots"][f"tasks/{TASK}/fig/x.png"]["caption_block"] == e2["cap"]["id"]
    assert run(S, "notion", "diff", cwd=S.proj).stdout == "in sync\n"


# ---------------------------------------------------------------- Feed

def test_feed_post_newest_first_and_prune(mirrored):
    S, m = mirrored, mirrored.mock
    st = state(S.proj)
    feed, header = st["feed_page"], st["feed_header"]
    pl = S.td / "fig" / "x_2026-09-25.png"
    run(S, "notion", "post", "--kind", "result", "--mention", "--task", TASK, "--file", pl,
        "First\n\nBody with $x^2$", cwd=S.proj)
    run(S, "notion", "post", "--kind", "status", "Second message", cwd=S.proj)
    ids = live_ids(m, feed)
    assert ids[0] == header and len(ids) == 3
    first, second = m.node(ids[2]), m.node(ids[1])
    assert text_of(second) == "Second message"
    # the result: an icon, a mention of the user, the bold title
    assert first["type"] == "callout" and first["body"]["icon"]["emoji"] == "🟢"
    rt = first["body"]["rich_text"]
    assert rt[0]["type"] == "mention" and rt[0]["mention"]["user"]["id"] == m.person["id"]
    assert any(x["plain_text"] == "First" and x["annotations"]["bold"] for x in rt)
    kids = m.kids(first["id"])
    assert any(x["type"] == "equation" and x["equation"]["expression"] == "x^2"
               for n in kids for x in n["body"].get("rich_text", []))
    imgs = [n for n in kids if n["type"] == "image"]
    assert len(imgs) == 1 and m.file_of(imgs[0]["id"]) == ("x_2026-09-25.png", b"png-x-25")
    after = kids[kids.index(imgs[0]):]
    cap = MockNotion.plain(imgs[0]["body"]["caption"]) + " ".join(
        MockNotion.plain(n["body"].get("rich_text", [])) for n in after[1:])
    assert "Amplitude A(t) against time." in cap
    # the ledger
    led = [json.loads(l) for l in (S.proj / "messages" / "notion-feed.jsonl").read_text().splitlines()]
    assert [(x["kind"], x["title"], x["block_id"], x["mention"], x["pruned"]) for x in led] == [
        ("result", "First", first["id"], True, False), ("status", "Second message", second["id"], False, False)]
    # back-date the first; prune removes it from Notion and marks it
    led[0]["posted"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat(timespec="seconds")
    (S.proj / "messages" / "notion-feed.jsonl").write_text("".join(json.dumps(x) + "\n" for x in led))
    assert run(S, "notion", "prune", "--days", "3", cwd=S.proj).stdout == "removed 1 message(s)\n"
    assert m.node(first["id"])["archived"]
    assert live_ids(m, feed) == [header, second["id"]]
    led = [json.loads(l) for l in (S.proj / "messages" / "notion-feed.jsonl").read_text().splitlines()]
    assert [x["pruned"] for x in led] == [True, False]
    assert run(S, "notion", "prune", "--days", "3", cwd=S.proj).stdout == "removed 0 message(s)\n"
    # a message the user already deleted in Notion is pruned without error
    led[1]["posted"] = led[0]["posted"]
    (S.proj / "messages" / "notion-feed.jsonl").write_text("".join(json.dumps(x) + "\n" for x in led))
    m.node(second["id"])["archived"] = True
    assert run(S, "notion", "prune", "--days", "3", cwd=S.proj).stdout == "removed 1 message(s)\n"


# ---------------------------------------------------------------- notify back end

def test_notify_backend_posts_to_feed(mirrored):
    S, m = mirrored, mirrored.mock
    r = run(S, "notify", "done", cwd=S.proj)
    assert "posted to the project's Notion Feed" in r.stdout
    st = state(S.proj)
    ids = live_ids(m, st["feed_page"])
    post = m.node(ids[1])
    rt = post["body"]["rich_text"]
    assert rt[0]["type"] == "mention" and rt[0]["mention"]["user"]["id"] == m.person["id"]
    assert "done" in MockNotion.plain(rt)
    assert not list((S.proj / "messages").glob("*.md")) or \
        all(p.name == "README.md" for p in (S.proj / "messages").glob("*.md"))


def _message_files(proj):
    return [p for p in (proj / "messages").glob("*.md") if p.name != "README.md"]


def test_notify_without_notion_pages_writes_file(S):
    proj = make_project(S)
    r = run(S, "notify", "done", cwd=proj)
    assert "opsci notion init" in r.stdout + r.stderr
    assert len(_message_files(proj)) == 1
    assert not [q for q in S.mock.requests if q[1] != ME_PATH]   # nothing sent to Notion


def test_notify_without_token_file_writes_file(S):
    proj = make_project(S)
    S.creds.unlink()
    r = run(S, "notify", "done", cwd=proj)
    assert "open-science:onboard" in r.stdout + r.stderr
    assert len(_message_files(proj)) == 1
    assert S.mock.requests == []


# ---------------------------------------------------------------- check, retry, security

def test_check_ok(S):
    proj = make_project(S)
    r = run(S, "notion", "check", cwd=proj)
    assert r.stdout.splitlines() == ["backend=notion", "token=ok", "integration=ok:opsci test bot",
                                     "parent_page=ok:Research", "user=ok:User Person"]


def test_check_reads_the_old_key_name(S):
    # configs written before the rename name the user `owner`
    cfg = yaml.safe_load(S.cfg.read_text())
    cfg["notify"]["notion"]["owner"] = cfg["notify"]["notion"].pop("user")
    S.cfg.write_text(yaml.safe_dump(cfg))
    r = run(S, "notion", "check", cwd=make_project(S))
    assert "user=ok:User Person" in r.stdout.splitlines()


def test_check_lists_user_candidates(S):
    write_config(S.cfg, S.mock, S.creds, user=False, backend="file")
    r = run(S, "notion", "check", cwd=make_project(S), rc=1)
    lines = r.stdout.splitlines()
    assert lines[0] == "backend=file"
    assert "user=missing" in lines
    assert f"user_candidate={S.mock.person['id']} User Person user@example.org" in lines


def test_single_429_is_retried(S):
    S.mock.fail_once.add(("GET", ME_PATH))
    run(S, "notion", "check", cwd=make_project(S))
    assert S.mock.requests.count(("GET", ME_PATH)) == 2


def test_token_file_mode_644_refused(S):
    os.chmod(S.creds, 0o644)
    proj = make_project(S)
    r = run(S, "notion", "check", cwd=proj, rc=1)
    assert "token=problem" in r.stdout and f"chmod 600 {S.creds}" in r.stdout
    r = run(S, "notion", "sync", cwd=proj, rc=2)
    assert "chmod 600" in r.stderr
    assert S.mock.requests == []


def test_credentials_inside_project_refused(S):
    proj = make_project(S)
    inside = write_creds(proj / "notion.env")
    write_config(S.cfg, S.mock, inside)
    r = run(S, "notion", "check", cwd=proj, rc=1)
    assert "refusing credentials file inside the project" in r.stdout
    assert S.mock.requests == []


def test_token_echoed_by_api_is_scrubbed(S):
    S.mock.echo_token.add(("GET", ME_PATH))
    r = run(S, "notion", "check", cwd=make_project(S), rc=1)      # run() asserts TOKEN is absent
    assert "integration=problem" in r.stdout and "<token>" in r.stdout


def test_token_sent_only_as_bearer(S):
    """A wrong token gets 401 (so the mock does check the header), and is reported safely."""
    write_creds(S.creds, token="ntn_" + "1" * 30 + "WrongTokenXX")
    r = run(S, "notion", "check", cwd=make_project(S), rc=1)
    assert "integration=problem: Notion GET" in r.stdout and "users/me: unauthorized" in r.stdout
    assert "1" * 30 not in r.stdout + r.stderr


def test_test_api_override_refused_for_non_loopback(S, monkeypatch):
    env = dict(S.env, **{NC.TEST_API_ENV: "http://example.org/v1"})
    r = run(S, "notion", "check", cwd=make_project(S), rc=2, env=env)
    assert "loopback" in r.stderr
    for url in ("http://example.org/v1", "https://127.0.0.1:9/v1", "http://10.0.0.1/v1"):
        monkeypatch.setenv(NC.TEST_API_ENV, url)
        with pytest.raises(NC.NotionError, match="loopback"):
            NC.Client("x")
    monkeypatch.setenv(NC.TEST_API_ENV, "http://127.0.0.1:9/v1")
    assert NC.Client("x").base == "http://127.0.0.1:9/v1"


# ---------------------------------------------------------------- setup: template and enable

def _notion_block(text: str) -> str:
    blocks = [m.group(2) for m in T.BLOCK_RE.finditer(text) if m.group(1) == "notion"]
    assert len(blocks) == 1
    return blocks[0]


def test_template_notion_block_equals_agents_section():
    assert _notion_block((TEMPLATE / "AGENTS.md").read_text()).strip("\n") == NS.AGENTS_SECTION.strip("\n")


def _hooks(proj):
    data = json.loads((proj / ".claude" / "settings.json").read_text())
    return data, [h["command"] for e in data.get("hooks", {}).get("Stop", []) for h in e["hooks"]]


def test_project_with_and_without_notion(S):
    on = make_project(S, notion=True, name="on")
    off = make_project(S, notion=False, name="off")
    assert NS.AGENTS_SECTION in (on / "AGENTS.md").read_text()
    assert "## 10. Notion" not in (off / "AGENTS.md").read_text()
    data, cmds = _hooks(on)
    assert cmds == [NS.HOOK_COMMAND] and NS.HOOK_COMMAND.endswith("|| true")
    data, cmds = _hooks(off)
    assert "hooks" not in data
    assert re.search(r"^notion: true$", (on / "config" / "framework.yaml").read_text(), re.M)
    assert re.search(r"^notion: false$", (off / "config" / "framework.yaml").read_text(), re.M)
    for p in (on, off):
        assert run_opsci("template", "check", p).returncode == 0, p


def test_enable_existing_project_idempotent(S):
    proj = make_project(S, notion=False)
    gi = proj / ".gitignore"          # an older project: no Notion line in .gitignore
    gi.write_text("".join(l for l in gi.read_text().splitlines(True) if "notion" not in l))
    r = run(S, "notion", "enable", "--project-root", proj)
    assert r.stdout.count("added:") == 4, r.stdout
    assert NS.AGENTS_SECTION in (proj / "AGENTS.md").read_text()
    _, cmds = _hooks(proj)
    assert cmds == [NS.HOOK_COMMAND] and cmds[0].endswith("|| true")
    assert re.search(r"^notion: true$", (proj / "config" / "framework.yaml").read_text(), re.M)
    assert "/" + STATE_FILE in gi.read_text().splitlines()
    assert run_opsci("template", "check", proj).returncode == 0
    # the permissions in settings.json are kept
    data, _ = _hooks(proj)
    assert "Bash(git:*)" in data["permissions"]["allow"]
    files = [gi, proj / "AGENTS.md", proj / ".claude" / "settings.json", proj / "config" / "framework.yaml"]
    snap = [f.read_text() for f in files]
    r = run(S, "notion", "enable", "--project-root", proj)
    assert r.stdout.count("already there:") == 4, r.stdout
    assert [f.read_text() for f in files] == snap


def test_enable_upgrades_old_hook_in_place(S):
    proj = make_project(S, notion=False)
    settings = proj / ".claude" / "settings.json"
    data = json.loads(settings.read_text())
    data["hooks"] = {"Stop": [{"hooks": [{"type": "command", "command": "opsci notion sync --hook"}]}]}
    settings.write_text(json.dumps(data))
    run(S, "notion", "enable", "--project-root", proj)
    data, cmds = _hooks(proj)
    assert cmds == [NS.HOOK_COMMAND]
    assert len(data["hooks"]["Stop"]) == 1


# ---------------------------------------------------------------- the real API (manual)

@pytest.mark.manual
def test_real_notion_check():
    """The user's real config and token; only reads (users/me, the parent page, the user)."""
    env = {k: v for k, v in os.environ.items() if k != NC.TEST_API_ENV}
    r = subprocess.run([sys.executable, "-m", "opsci.cli", "notion", "check", "--project-root", REPO],
                       capture_output=True, text=True, env=env)
    print(r.stdout, r.stderr)
    assert not NC.TOKEN_RE.search(r.stdout + r.stderr)
    assert r.returncode == 0
    assert "token=ok" in r.stdout and "integration=ok:" in r.stdout
