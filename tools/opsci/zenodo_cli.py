"""`opsci zenodo ...`: release project data to Zenodo (see docs/zenodo.md)."""

from __future__ import annotations

import sys
from pathlib import Path

from . import zenodo as Z


def cmd_release(args) -> int:
    root = Path(args.root)
    try:
        if args.dry_run:
            server, base = Z.resolve_server(args.production, args.api_url)
            plan = Z.make_plan(root, server, base, build_dir=None)
            print("zenodo release plan (dry run: no network access, nothing written)")
            print(Z.format_plan(plan, args.version))
            return 1 if plan.errors else 0
        if not args.version:
            raise Z.ZenodoError("--version is required (except with --dry-run)")
        Z.release(root, args.version, production=args.production, api_url=args.api_url,
                  token_file=args.token_file,
                  build_dir=Path(args.build_dir) if args.build_dir else None,
                  cite=True if args.write_citation else None)
    except Z.ZenodoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_check_token(args) -> int:
    try:
        print(Z.check_token(args.production, args.api_url, args.token_file))
    except Z.ZenodoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_checksum(args) -> int:
    root = Path(args.root)
    rc = 0
    for p in args.paths:
        rel = p.rstrip("/")
        try:
            if not rel.startswith("data/") or ".." in rel.split("/") or not (root / rel).exists():
                raise Z.ZenodoError(f"{p}: not an existing path under data/ (give it relative to the project root)")
            sha, size = Z.tar_checksum(root, [rel])
        except Z.ZenodoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            rc = 1
            continue
        print(f"{sha}  {size}  {rel}")
    return rc


def add_parser(sub) -> None:
    z = sub.add_parser("zenodo", help="release data to Zenodo").add_subparsers(dest="cmd", required=True)
    r = z.add_parser("release", help="pack data/ into tar groups and publish a Zenodo version")
    r.add_argument("root", nargs="?", default=".")
    r.add_argument("--version", help="version label of this release, e.g. 1.0")
    r.add_argument("--dry-run", action="store_true",
                   help="print the plan (groups, checksums, reuse, limits); no network, no files written")
    r.add_argument("--production", action="store_true",
                   help="release to zenodo.org (permanent and public); default is the sandbox")
    r.add_argument("--api-url", help="API base URL (default: the sandbox, or production with --production)")
    r.add_argument("--token-file", help="token file (default: ~/.config/opsci/zenodo-sandbox.token, "
                                        "or zenodo.token with --production); must be mode 600")
    r.add_argument("--build-dir", help="where the tars are written (default: data/.zenodo-build/<server>)")
    r.add_argument("--write-citation", action="store_true",
                   help="also write the DOIs to CITATION.cff and the datasets (default: production only)")
    r.set_defaults(func=cmd_release)
    k = z.add_parser("check-token", help="check the token file and that Zenodo accepts the token; "
                                         "creates nothing")
    k.add_argument("--production", action="store_true", help="check the zenodo.org token")
    k.add_argument("--api-url", help="API base URL (default: the sandbox, or production with --production)")
    k.add_argument("--token-file", help="token file (default as for release)")
    k.set_defaults(func=cmd_check_token)
    c = z.add_parser("checksum", help="sha256 and size of the reproducible tar of data paths")
    c.add_argument("paths", nargs="+", help="paths under data/, relative to ROOT")
    c.add_argument("--root", default=".")
    c.set_defaults(func=cmd_checksum)
