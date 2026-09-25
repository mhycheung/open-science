"""`opsci notion`: mirror a project into Notion and post to its Feed (docs/notion.md)."""

from __future__ import annotations

import sys

from .client import NotionError

__all__ = ["NotionError", "add_parser"]


def _run(fn):
    def wrapped(args) -> int:
        try:
            return fn(args) or 0
        except NotionError as exc:
            print(f"opsci notion: error: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 - never print a traceback (it could hold a secret)
            print(f"opsci notion: internal error ({type(exc).__name__}); nothing printed from it "
                  f"in case it holds a secret", file=sys.stderr)
            return 1
    return wrapped


@_run
def cmd_check(a):
    from . import setup
    if a.send_test:
        return setup.send_test(a.project_root)
    if a.remove_test:
        return setup.remove_test(a.remove_test, a.project_root)
    return setup.check(a.project_root)


@_run
def cmd_init(a):
    from .setup import init
    url = init(a.project_root, days=a.days)
    print(f"created: {url}")


@_run
def cmd_enable(a):
    from .setup import enable
    enable(a.project_root)


@_run
def cmd_sync(a):
    from . import mirror, setup
    from .project import Project
    if a.hook:
        return setup.run_hook(a.project_root)
    if a.locked:
        return setup.locked_sync(a.project_root)
    done = mirror.sync(Project(a.project_root), only=a.only, plots_only=a.plots_only, dry_run=a.dry_run)
    if not done:
        print("in sync")


@_run
def cmd_diff(a):
    from . import mirror
    from .project import Project
    d = mirror.diff(Project(a.project_root))
    for how, key in d["changes"]:
        print(f"{how:6s} {key}")
    for key in d["gone"]:
        print(f"gone   {key}  (its Notion page is kept; delete it by hand if wanted)")
    for path in d["missing_captions"]:
        print(f"no caption: {path}  (write {path.rsplit('.', 1)[0]}.caption.md)")
    if not d["changes"] and not d["gone"]:
        print("in sync")
    return 1 if d["missing_captions"] and a.strict else 0


@_run
def cmd_post(a):
    from . import feed
    from .project import Project
    text = sys.stdin.read() if a.text == "-" else a.text
    feed.post(Project(a.project_root), text, author=a.author, kind=a.kind, task=a.task or "",
              title=a.title, mention=a.mention, files=a.file or (), days=a.days, prune_old=not a.no_prune)
    print("posted")


@_run
def cmd_prune(a):
    from . import feed
    from .project import Project
    print(f"removed {feed.prune(Project(a.project_root), a.days)} message(s)")


def add_parser(sub) -> None:
    from .feed import DEFAULT_DAYS, KINDS
    n = sub.add_parser("notion", help="mirror the project into Notion; post to its Feed").add_subparsers(
        dest="cmd", required=True)

    def p(name, help, fn):
        q = n.add_parser(name, help=help)
        q.add_argument("--project-root", help="project root (default: git top level of the current directory)")
        q.set_defaults(func=fn)
        return q

    q = p("check", "check the token, the integration, the parent page and the owner (prints key=value)", cmd_check)
    q.add_argument("--send-test", action="store_true",
                   help="make a test page under the parent page that @mentions the owner")
    q.add_argument("--remove-test", metavar="PAGE_ID", help="remove that test page")
    q = p("init", "create this project's Notion pages under the parent page, then sync", cmd_init)
    q.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Feed retention in days (shown on the Feed)")
    p("enable", "add the Notion section to AGENTS.md and the auto-sync hook (existing projects)", cmd_enable)
    q = p("sync", "write the pages whose text or plots changed", cmd_sync)
    q.add_argument("--only", nargs="+", metavar="KEY", help="only these pages (keys as printed by diff)")
    q.add_argument("--plots-only", action="store_true", help="only in-place plot updates")
    q.add_argument("--dry-run", action="store_true", help="list what would be written")
    q.add_argument("--hook", action="store_true", help=argparse_suppress())
    q.add_argument("--locked", action="store_true", help=argparse_suppress())
    q = p("diff", "list pages that differ from Notion, and plots without a caption file", cmd_diff)
    q.add_argument("--strict", action="store_true", help="exit 1 if a plot has no caption file")
    q = p("post", "post a message to the Feed", cmd_post)
    q.add_argument("text", help="message; markdown with $LaTeX$; first paragraph is the title ('-' reads stdin)")
    q.add_argument("--kind", choices=list(KINDS), default="note")
    q.add_argument("--author", default="agent", help="who posts, e.g. main:t02 or subagent:S2")
    q.add_argument("--task", help="task id")
    q.add_argument("--title", help="title (default: the first paragraph)")
    q.add_argument("--mention", action="store_true", help="@mention the owner, so Notion notifies them")
    q.add_argument("--file", action="append", help="attach a file (a plot shows inline with its caption)")
    q.add_argument("--days", type=int, default=DEFAULT_DAYS, help="remove messages older than this")
    q.add_argument("--no-prune", action="store_true")
    q = p("prune", "remove Feed messages older than --days", cmd_prune)
    q.add_argument("--days", type=int, default=DEFAULT_DAYS)


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS
