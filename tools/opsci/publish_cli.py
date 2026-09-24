"""`opsci publish ...` and `opsci site ...` (see publish.py and site.py)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from . import publish as P
from . import site as S


def _fail(exc) -> int:
    print(f"error: {exc}", file=sys.stderr)
    return 1


def cmd_export(args) -> int:
    try:
        ex = P.export(Path(args.root), Path(args.out), args.commit)
    except P.PublishError as exc:
        return _fail(exc)
    print(f"exported {len(ex.files)} files of {ex.commit[:12]} into {ex.tree}; export id {ex.export_id}")
    return 0


def cmd_check(args) -> int:
    from . import layout
    layout.warn_if_outdated(Path(args.root))
    try:
        n, report, ex = P.check(Path(args.root), args.commit)
    except P.PublishError as exc:
        return _fail(exc)
    print(f"report: {report}")
    print(f"export id: {ex.export_id} ({len(ex.files)} files of {ex.commit[:12]})")
    if n:
        print(f"publish check: FAILED, {n} problem(s); see the report", file=sys.stderr)
        return 1
    print("publish check: passed")
    return 0


def cmd_push(args) -> int:
    try:
        private, public = P.push(Path(args.root), args.export_id, args.public_repo, args.commit)
    except P.PublishError as exc:
        return _fail(exc)
    print(f"published {private[:12]} as public commit {public[:12]}; recorded in {P.LAST_PUBLISHED}")
    return 0


def cmd_status(args) -> int:
    try:
        drift, pending = P.status(Path(args.root), args.public_repo)
    except P.PublishError as exc:
        return _fail(exc)
    for d in drift:
        print(f"error: drift: {d}", file=sys.stderr)
    for p in pending:
        print(f"pending: {p}")
    print(f"publish status: {len(drift)} difference(s) the private repo lacks, "
          f"{len(pending)} unpublished change(s)")
    return 1 if drift else 0


def cmd_pull_public(args) -> int:
    try:
        branch, names = P.pull_public(Path(args.root), args.public_repo)
    except P.PublishError as exc:
        return _fail(exc)
    print(f"branch {branch}: public changes to {', '.join(names)}. Review it, then merge it into main.")
    return 0


def cmd_site_build(args) -> int:
    problems = S.build(Path(args.src), Path(args.out))
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if not problems:
        print(f"site build: ok, written to {args.out}")
    return 1 if problems else 0


def cmd_site_preview(args) -> int:
    """Build the site of the current export (no public repo needed)."""
    try:
        ex = P.export(Path(args.root), Path(tempfile.mkdtemp(prefix="opsci-preview-")) / "x", args.commit)
    except P.PublishError as exc:
        return _fail(exc)
    args.src = ex.tree
    return cmd_site_build(args)


def add_parser(sub) -> None:
    pb = sub.add_parser("publish", help="export, check and push the public part of a project") \
        .add_subparsers(dest="cmd", required=True)

    e = pb.add_parser("export", help="write the files the manifest allows (of one commit) into OUT")
    e.add_argument("root", nargs="?", default=".")
    e.add_argument("--out", required=True, help="empty directory to write into")
    e.add_argument("--commit", default="HEAD")
    e.set_defaults(func=cmd_export)

    c = pb.add_parser("check", help="export, run every check, write the review report and diff under publish/reports/")
    c.add_argument("root", nargs="?", default=".")
    c.add_argument("--commit", default="HEAD")
    c.set_defaults(func=cmd_check)

    p = pb.add_parser("push", help="after the owner approves the report: push the export to the public repo")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--export-id", required=True, help="the export id printed in the approved report")
    p.add_argument("--public-repo", help="URL or path of the public repo (default: public_repo in the manifest)")
    p.add_argument("--commit", default="HEAD")
    p.set_defaults(func=cmd_push)

    s = pb.add_parser("status", help="consistency check: public changes the private repo lacks, and unpublished changes")
    s.add_argument("root", nargs="?", default=".")
    s.add_argument("--public-repo")
    s.set_defaults(func=cmd_status)

    u = pb.add_parser("pull-public", help="bring public-side changes into a new private branch for review")
    u.add_argument("root", nargs="?", default=".")
    u.add_argument("--public-repo")
    u.set_defaults(func=cmd_pull_public)

    st = sub.add_parser("site", help="project site").add_subparsers(dest="cmd", required=True)
    b = st.add_parser("build", help="build the site of a public repo checkout with MkDocs")
    b.add_argument("src", nargs="?", default=".")
    b.add_argument("--out", default="_site")
    b.set_defaults(func=cmd_site_build)
    v = st.add_parser("preview", help="build the site of this project's current export, before publishing")
    v.add_argument("root", nargs="?", default=".")
    v.add_argument("--commit", default="HEAD")
    v.add_argument("--out", default="_site")
    v.set_defaults(func=cmd_site_preview)
