"""`opsci pdf`: which pandoc and LaTeX engine it runs, with what arguments, and its errors.

pandoc, pixi and the engines are stand-in scripts on a PATH of their own: a stand-in pandoc
records its arguments and writes the output file. The real rendering is checked by hand
(pandoc through `pixi exec` and lualatex; see the pull request)."""
import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO

FAKE_PANDOC = """#!/bin/sh
printf '%s\\n' "$0" "$@" > "$ARGS_LOG"
pwd >> "$ARGS_LOG"
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { shift; printf '%%PDF-1.5' > "$1"; }; shift; done
"""
FAKE_PIXI = """#!/bin/sh
[ "$1 $2 $3 $4" = "exec -s pandoc pandoc" ] || exit 9
shift 4
exec "${0%/*}/pandoc-real" "$@"
"""


def tool(bin_dir: Path, name: str, body: str = "#!/bin/sh\nexit 0\n") -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / name).write_text(body)
    (bin_dir / name).chmod(0o755)


def run_pdf(tmp_path: Path, bin_dir: Path, *args):
    env = {"PATH": str(bin_dir), "ARGS_LOG": str(tmp_path / "args.log"), "HOME": str(tmp_path),
           "PYTHONPATH": str(REPO / "tools")}
    return subprocess.run([sys.executable, "-m", "opsci.cli", "pdf", *map(str, args)],
                          capture_output=True, text=True, cwd=tmp_path, env=env)


def doc(tmp_path: Path) -> Path:
    d = tmp_path / "tasks" / "t04" / "notes.md"
    d.parent.mkdir(parents=True)
    d.write_text("# Notes\n\n$$x^2$$\n")
    return d


def test_pdf_runs_pandoc_on_path_with_lualatex_first(tmp_path):
    bin_dir = tmp_path / "bin"
    tool(bin_dir, "pandoc", FAKE_PANDOC)
    for e in ("lualatex", "xelatex", "pdflatex"):
        tool(bin_dir, e)
    src = doc(tmp_path)
    r = run_pdf(tmp_path, bin_dir, src.relative_to(tmp_path))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "tasks/t04/notes.pdf"
    assert (src.parent / "notes.pdf").read_bytes().startswith(b"%PDF")
    log = (tmp_path / "args.log").read_text().splitlines()
    assert Path(log[0]).name == "pandoc" and log[1] == str(src)
    assert "--pdf-engine=lualatex" in log and f"--resource-path={src.parent}" in log
    assert log[-1] == str(src.parent)          # run from the document's directory: relative figures


def test_pdf_falls_back_to_pixi_exec_and_another_engine(tmp_path):
    bin_dir = tmp_path / "bin"
    tool(bin_dir, "pixi", FAKE_PIXI)
    tool(bin_dir, "pandoc-real", FAKE_PANDOC)
    tool(bin_dir, "xelatex")
    src = doc(tmp_path)
    out = tmp_path / "out" / "x.pdf"
    out.parent.mkdir()
    r = run_pdf(tmp_path, bin_dir, src, "-o", out)
    assert r.returncode == 0, r.stderr
    assert out.read_bytes().startswith(b"%PDF") and not (src.parent / "notes.pdf").exists()
    assert "--pdf-engine=xelatex" in (tmp_path / "args.log").read_text().splitlines()


def test_pdf_errors(tmp_path):
    bin_dir = tmp_path / "bin"
    tool(bin_dir, "lualatex")
    src = doc(tmp_path)
    r = run_pdf(tmp_path, bin_dir, src)
    assert r.returncode == 1 and "pandoc not found" in r.stderr
    tool(bin_dir, "pandoc", FAKE_PANDOC)
    os.remove(bin_dir / "lualatex")
    r = run_pdf(tmp_path, bin_dir, src)
    assert r.returncode == 1 and "no LaTeX engine found" in r.stderr
    tool(bin_dir, "lualatex")
    r = run_pdf(tmp_path, bin_dir, src, "--engine", "tectonic")
    assert r.returncode == 1 and "LaTeX engine not found: tectonic" in r.stderr
    tool(bin_dir, "pandoc", "#!/bin/sh\necho 'Undefined control sequence' >&2\nexit 43\n")
    r = run_pdf(tmp_path, bin_dir, src)
    assert r.returncode == 1 and "pandoc failed (exit 43)" in r.stderr and "Undefined control" in r.stderr
    (tmp_path / "a.txt").write_text("x")
    r = run_pdf(tmp_path, bin_dir, tmp_path / "a.txt")
    assert r.returncode == 1 and "not a markdown file" in r.stderr
