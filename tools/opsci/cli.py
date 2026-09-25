"""The `opsci` command line."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import layout, mapbuild, nodes, template
from . import notify


def cmd_map_build(args) -> int:
    layout.warn_if_outdated(Path(args.root))
    res, stale = mapbuild.build(Path(args.root), check=args.check)
    for w in res.warnings:
        print(f"warning: {w}", file=sys.stderr)
    for e in res.errors:
        print(f"error: {e}", file=sys.stderr)
    if res.errors:
        print(f"map build: {len(res.errors)} error(s); nothing written.", file=sys.stderr)
        return 1
    if args.check:
        if stale:
            print("map build --check: out of date: " + ", ".join(stale), file=sys.stderr)
            return 1
        print(f"map build --check: {len(res.nodes)} nodes, generated files up to date.")
        return 0
    root, _ = nodes.graph_root(Path(args.root))
    subs = [f"{d}/map/" for d in nodes.SUBROOTS if (root / d).is_dir()]
    tables = [s for s in stale if nodes.is_task_context(s) or nodes.is_task_plan(s)]
    print(f"map build: {len(res.nodes)} nodes; wrote map/graph.md, map/dead_ends.md, map/claims.md "
          "and the results pages"
          + (f", and the same in {', '.join(subs)}" if subs else "")
          + (f"; rewrote the node table in {', '.join(tables)}" if tables else "") + ".")
    return 0


def cmd_template_instantiate(args) -> int:
    try:
        date = dt.date.fromisoformat(args.date) if args.date else None
        dest = template.instantiate(Path(args.dest), args.name, args.title, args.author,
                                    template=args.template, date=date,
                                    framework_repo=args.framework_repo,
                                    context_management=not args.no_context_management,
                                    notion=args.notion)
    except template.TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created project '{args.name}' in {dest}")
    if args.notion:
        print("next: in the new project, run `opsci notion init` to create its Notion pages")
    return 0


def cmd_template_check(args) -> int:
    problems = template.verify_instance(Path(args.root))
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if problems:
        return 1
    print("template check: ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="opsci", description="open-science project tools")
    sub = ap.add_subparsers(dest="group", required=True)

    m = sub.add_parser("map", help="project graph").add_subparsers(dest="cmd", required=True)
    b = m.add_parser("build", help="write map/graph.md, map/dead_ends.md, map/claims.md and the results pages from node headers")
    b.add_argument("root", nargs="?", default=".")
    b.add_argument("--check", action="store_true", help="report only; fail if the generated files are stale")
    b.set_defaults(func=cmd_map_build)

    t = sub.add_parser("template", help="project template").add_subparsers(dest="cmd", required=True)
    i = t.add_parser("instantiate", help="copy the template into DEST and fill placeholders")
    i.add_argument("dest")
    i.add_argument("--name", required=True, help="project slug: lower case, digits, hyphens")
    i.add_argument("--title", required=True)
    i.add_argument("--author", required=True)
    i.add_argument("--template", help="path to the framework's template/ directory")
    i.add_argument("--date", help="creation date YYYY-MM-DD (default: today)")
    i.add_argument("--framework-repo", help="framework repo URL to record")
    i.add_argument("--no-context-management", action="store_true",
                   help="leave out the context-management component (plugin open-science-context)")
    i.add_argument("--notion", action="store_true",
                   help="mirror the project to Notion: AGENTS.md section 10 and the auto-sync hook "
                        "(then run opsci notion init)")
    i.set_defaults(func=cmd_template_instantiate)
    c = t.add_parser("check", help="check a project made from the template")
    c.add_argument("root", nargs="?", default=".")
    c.set_defaults(func=cmd_template_check)

    notify.add_parser(sub)

    from . import notion  # `opsci notion ...`
    notion.add_parser(sub)

    from . import zenodo_cli  # S5: `opsci zenodo ...`
    zenodo_cli.add_parser(sub)

    from . import publish_cli  # S4: `opsci publish ...`, `opsci site ...`
    publish_cli.add_parser(sub)

    # projects-page: check the personal projects page (projects_page.py)
    from . import projects_page

    def cmd_projects_page_check(args) -> int:
        errors = projects_page.check_page_dir(Path(args.dir))
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        if not errors:
            print("projects-page check: ok")
        return 1 if errors else 0

    pp = sub.add_parser("projects-page", help="personal projects page").add_subparsers(dest="cmd", required=True)
    ppc = pp.add_parser("check", help="validate projects.yaml and check index.html for banned styles")
    ppc.add_argument("dir", nargs="?", default=".", help="directory with index.html and projects.yaml")
    ppc.set_defaults(func=cmd_projects_page_check)

    # S2: context caps, task creation, migration inventory
    from . import contextcheck, migrate, nodes, tasks

    def cmd_context_check(args) -> int:
        layout.warn_if_outdated(Path(args.root))
        problems, report = contextcheck.check(Path(args.root))
        if args.verbose:
            print("\n".join(report))
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        if not problems:
            print(f"context check: ok ({len(report)} files within their caps)")
        return 1 if problems else 0

    cx = sub.add_parser("context", help="context files").add_subparsers(dest="cmd", required=True)
    cxc = cx.add_parser("check", help="fail if context.md, a task context.md or map/README.md (also under brainstorm/) is over its line cap")
    cxc.add_argument("root", nargs="?", default=".")
    cxc.add_argument("-v", "--verbose", action="store_true", help="print every file with its count")
    cxc.set_defaults(func=cmd_context_check)

    def cmd_task_new(args) -> int:
        layout.warn_if_outdated(Path(args.root))
        try:
            tdir = tasks.new_task(Path(args.root), args.id, args.title, summary=args.summary,
                                  depends_on=args.depends_on, related=args.related,
                                  supersedes=args.supersedes, plan=args.plan,
                                  autonomy=args.autonomy, hold_at=args.hold_at, goal=args.goal,
                                  privacy=args.privacy, short_name=args.short_name)
        except tasks.TaskError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        made = sorted(p.relative_to(Path(args.root)).as_posix() for p in tdir.rglob("*") if p.is_file())
        print(f"created {tdir}: " + ", ".join(made))
        return 0

    tk = sub.add_parser("task", help="tasks").add_subparsers(dest="cmd", required=True)
    tn = tk.add_parser("new", help="create tasks/ID/ with context.md (node header), log.md, subcontext/ [and plan.md]")
    tn.add_argument("id")
    tn.add_argument("--title", required=True)
    tn.add_argument("--short-name", help="a few words for session names, e.g. pp-real (lower case, digits, hyphens; <= 24)")
    tn.add_argument("--summary", help="one sentence for the node header (default: a TODO)")
    tn.add_argument("--goal", help="the Goal section of context.md")
    tn.add_argument("--depends-on", nargs="*", default=[], metavar="ID")
    tn.add_argument("--related", nargs="*", default=[], metavar="ID")
    tn.add_argument("--supersedes", nargs="*", default=[], metavar="ID")
    tn.add_argument("--plan", action="store_true", help="also write plan.md from the plan template")
    tn.add_argument("--autonomy", default="autonomous", choices=tasks.AUTONOMY)
    tn.add_argument("--hold-at", nargs="*", default=[], metavar="POINT", help="needs --autonomy checkpoints")
    tn.add_argument("--privacy", choices=nodes.PRIVACY_TIERS,
                    help="public, soft-private or hard-private (default: public; soft-private for a brainstorm task)")
    tn.add_argument("--root", default=".", help="project root (default: .); `brainstorm` for a brainstorm task")
    tn.set_defaults(func=cmd_task_new)

    def cmd_migrate_inventory(args) -> int:
        try:
            n = migrate.write_inventory(Path(args.root), Path(args.output))
        except migrate.MigrateError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"migrate inventory: {n} files recorded in {args.output}")
        return 0

    def cmd_migrate_compare(args) -> int:
        try:
            before = migrate.load_inventory(Path(args.inventory))
            res = migrate.compare(before, migrate.inventory(Path(args.root)))
        except migrate.MigrateError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for old, new in res["moved"]:
            if args.verbose:
                print(f"moved: {old} -> {new}")
        for rel in res["modified"]:
            print(f"modified: {rel}")
        for rel in res["lost"]:
            print(f"error: lost: {rel}", file=sys.stderr)
        print(f"migrate compare: {len(res['kept'])} kept, {len(res['moved'])} moved, "
              f"{len(res['modified'])} modified, {len(res['lost'])} lost")
        return 1 if res["lost"] else 0

    mg = sub.add_parser("migrate", help="migration checks").add_subparsers(dest="cmd", required=True)
    mi = mg.add_parser("inventory", help="record every file and its hash before a migration")
    mi.add_argument("root", nargs="?", default=".")
    mi.add_argument("-o", "--output", required=True, help="inventory file to write (keep it outside the project)")
    mi.set_defaults(func=cmd_migrate_inventory)
    mc = mg.add_parser("compare", help="after a migration: fail if any inventoried file was lost")
    mc.add_argument("inventory")
    mc.add_argument("root", nargs="?", default=".")
    mc.add_argument("-v", "--verbose", action="store_true", help="list moved files")
    mc.set_defaults(func=cmd_migrate_compare)

    from . import guidecheck, template as _tpl

    def cmd_guide_check(args) -> int:
        repo = Path(args.repo) if args.repo else (_tpl.default_template_dir() or Path("template")).parent
        problems = guidecheck.check(Path(args.file) if args.file else repo / "USER_GUIDE.md",
                                    repo, args.max_words)
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        if not problems:
            print("guide check: ok")
        return 1 if problems else 0

    gd = sub.add_parser("guide", help="user guide").add_subparsers(dest="cmd", required=True)
    gdc = gd.add_parser("check", help="word limit, and every path, command and skill named exists")
    gdc.add_argument("file", nargs="?", help="guide file (default: USER_GUIDE.md of the framework repo)")
    gdc.add_argument("--repo", help="framework repo root (default: the checkout opsci runs from)")
    gdc.add_argument("--max-words", type=int, default=600)
    gdc.set_defaults(func=cmd_guide_check)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
