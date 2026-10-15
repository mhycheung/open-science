"""`opsci pdf`: render a markdown document with $LaTeX$ to PDF, so that it can be read in
Notion, on a phone, or anywhere a markdown file shows its equations as source.

pandoc converts the markdown and a LaTeX engine typesets it. pandoc is taken from PATH, else
run through `pixi exec -s pandoc` (no install needed; on a machine with a small home
directory, set PIXI_CACHE_DIR and RATTLER_CACHE_DIR to a larger file system). The engine is
the first of lualatex, xelatex and pdflatex on PATH, unless --engine names one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ENGINES = ("lualatex", "xelatex", "pdflatex")


class RenderError(Exception):
    pass


def pandoc_command() -> list[str]:
    if shutil.which("pandoc"):
        return ["pandoc"]
    if shutil.which("pixi"):
        return ["pixi", "exec", "-s", "pandoc", "pandoc"]
    raise RenderError("pandoc not found, and no pixi to run it with (`pixi exec -s pandoc`): "
                      "install pandoc or pixi")


def pick_engine(name: str | None = None) -> str:
    for e in [name] if name else ENGINES:
        if shutil.which(e):
            return e
    raise RenderError(f"LaTeX engine not found: {name}" if name else
                      f"no LaTeX engine found ({', '.join(ENGINES)}): install a TeX distribution")


def render_pdf(src: Path, out: Path | None = None, engine: str | None = None) -> Path:
    """Render ``src`` (markdown) to ``out`` (default: the same stem, .pdf, beside it).
    Relative figure paths are read relative to ``src``."""
    src = Path(src).resolve()
    if not src.is_file():
        raise RenderError(f"not found: {src}")
    if src.suffix.lower() not in (".md", ".markdown"):
        raise RenderError(f"not a markdown file: {src}")
    out = Path(out).resolve() if out else src.with_suffix(".pdf")
    cmd = pandoc_command() + [
        str(src), "-o", str(out), "--from", "markdown", "--standalone",
        f"--pdf-engine={pick_engine(engine)}", f"--resource-path={src.parent}",
        "-V", "geometry:margin=2.5cm", "-V", "colorlinks=true"]
    r = subprocess.run(cmd, cwd=src.parent, capture_output=True, text=True)
    if r.returncode != 0 or not out.is_file():
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-20:])
        raise RenderError(f"pandoc failed (exit {r.returncode}):\n{tail}")
    return out


def cmd_pdf(args) -> int:
    try:
        out = render_pdf(Path(args.file), Path(args.output) if args.output else None, args.engine)
    except RenderError as exc:
        print(f"opsci pdf: error: {exc}", file=sys.stderr)
        return 1
    print(os.path.relpath(out))
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("pdf", help="render a markdown document with $LaTeX$ to PDF (pandoc and a LaTeX engine)")
    p.add_argument("file", help="markdown file")
    p.add_argument("-o", "--output", help="PDF to write (default: the same name, .pdf, beside the file)")
    p.add_argument("--engine", help=f"LaTeX engine (default: the first found of {', '.join(ENGINES)})")
    p.set_defaults(func=cmd_pdf)
