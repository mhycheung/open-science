# opsci: planted-leaks (the self-scan control case plants a fake path)
"""Checks on the framework repository itself."""
import json
import subprocess

from conftest import REPO
from opsci.template import ABS_PATH_ALLOWED, ABS_PATH_EXEMPT_FILES, ABS_PATH_RE, PLANTED_MARKER


def tracked_files():
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-co", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout
    return [REPO / f for f in out.split() if (REPO / f).is_file()]


def absolute_path_hits(files):
    hits = []
    for p in files:
        if p.name in ABS_PATH_EXEMPT_FILES or p.suffix == ".lock":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if PLANTED_MARKER in text and p.parent.name == "tests":
            continue
        for m in ABS_PATH_RE.finditer(text):
            if not m.group(0).startswith(ABS_PATH_ALLOWED):
                hits.append(f"{p.name}: {m.group(0)}")
    return hits


def test_no_absolute_paths_in_tracked_files():
    # The full leak scan comes with the publish tool (S4); this is the path part of it.
    assert absolute_path_hits(tracked_files()) == []


def test_self_scan_refuses_planted_path(tmp_path):
    d = tmp_path / "tests"
    d.mkdir()
    (d / "a.md").write_text("output in /scratch/someone/run1\n")
    (d / "b.py").write_text("# " + PLANTED_MARKER + "\nx = '/scratch/someone/run1'\n")
    (tmp_path / "c.md").write_text("# " + PLANTED_MARKER + "\n/scratch/someone/run1\n")
    hits = absolute_path_hits(sorted(tmp_path.rglob("*.*")))
    # the marker only exempts files inside tests/
    assert sorted(hits) == ["a.md: /scratch/someone/run1", "c.md: /scratch/someone/run1"]


def test_agent_working_files_are_not_tracked():
    rels = {p.relative_to(REPO).as_posix() for p in tracked_files()}
    assert not [r for r in rels if r.startswith(("docs/context/", "work/"))]


def test_plugin_manifests():
    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    names = {p["name"] for p in market["plugins"]}
    assert names == {"open-science", "open-science-publish", "open-science-project",
                     "open-science-context", "slurm-resurrect"}
    deps = {}
    for p in market["plugins"]:
        manifest = json.loads((REPO / p["source"] / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["name"] == p["name"]
        deps[p["name"]] = manifest.get("dependencies", [])
    # context management needs the project structure; nothing else depends on another plugin
    assert deps == {n: (["open-science-project"] if n == "open-science-context" else [])
                    for n in names}
