"""Project layout 2: the brainstorm/, docs/ and private-docs/ directories, the layout
version and its warning, and the changelog's migration sections. Each check has an input it
must refuse (or warn on) next to one it must pass."""

import json
import re

import pytest

from conftest import REPO, git, run_opsci
from opsci import layout, publish

WARN = "run open-science-project:update-from-template"


@pytest.fixture
def project(tmp_path):
    dest = tmp_path / "proj"
    r = run_opsci("template", "instantiate", dest, "--name", "demo", "--title", "Demo",
                  "--author", "A. Person", "--date", "2026-01-02")
    assert r.returncode == 0, r.stderr
    return dest


def set_layout(project, line):
    """Replace the layout_version line of config/framework.yaml (None: remove it)."""
    fw = project / "config" / "framework.yaml"
    text = re.sub(r"^layout_version:.*\n", "" if line is None else line + "\n", fw.read_text(), flags=re.M)
    fw.write_text(text)


# ---- layout version ---------------------------------------------------------------------

def test_read_layout_version():
    assert layout.read_layout_version("framework_repo: x\nlayout_version: 2\n") == 2
    assert layout.read_layout_version("# layout_version: 7\n") == 1        # a comment is not the key


COMMANDS = [
    ("task", "new", "t01", "--title", "x", "--root", "{p}"),
    ("map", "build", "{p}"),
    ("context", "check", "{p}"),
    ("task", "new", "b01", "--title", "x", "--root", "{p}/brainstorm"),
    ("map", "build", "{p}/brainstorm"),
]


@pytest.mark.parametrize("cmd", COMMANDS, ids=lambda c: " ".join(c[:2]) + (" brainstorm" if "brainstorm" in c[-1] else ""))
@pytest.mark.parametrize("line,warns", [(None, True), ("layout_version: 1", True),
                                        (f"layout_version: {layout.LAYOUT_VERSION}", False)])
def test_warns_on_an_older_layout_only(project, cmd, line, warns):
    set_layout(project, line)
    r = run_opsci(*(a.replace("{p}", str(project)) for a in cmd))
    assert r.returncode == 0, r.stderr                                    # exit status unchanged
    assert (WARN in r.stderr) is warns, r.stderr
    if warns:
        assert f"layout (1) is older than the framework's ({layout.LAYOUT_VERSION})" in r.stderr


def test_no_warning_outside_a_template_project(tmp_path):
    (tmp_path / "tasks").mkdir()
    (tmp_path / "context.md").write_text("# c\n")
    for cmd in (("map", "build", tmp_path), ("context", "check", tmp_path),
                ("task", "new", "t01", "--title", "x", "--root", tmp_path)):
        r = run_opsci(*cmd)
        assert r.returncode == 0 and WARN not in r.stderr, r.stderr
    assert layout.outdated_message(tmp_path) is None


# ---- changelog --------------------------------------------------------------------------

def test_changelog_has_every_layout_migration():
    text = (REPO / "CHANGELOG.md").read_text()
    assert layout.missing_migrations(text) == []
    # refusal: the same changelog without its migration sections
    stripped = "\n".join(l for l in text.splitlines() if not l.startswith("### Project migration"))
    assert layout.missing_migrations(stripped) == list(range(2, layout.LAYOUT_VERSION + 1))


def test_changelog_versions_match_the_plugins():
    text = (REPO / "CHANGELOG.md").read_text()
    releases = re.findall(r"^## (\d+\.\d+\.\d+) - \d{4}-\d{2}-\d{2}$", text, re.M)
    assert releases, "no '## <version> - <date>' section"
    for pj in sorted(REPO.glob("plugins/*/.claude-plugin/plugin.json")):
        assert json.loads(pj.read_text())["version"] == releases[0], pj


# ---- brainstorm/ ------------------------------------------------------------------------

def test_project_task_cannot_name_a_brainstorm_node(project):
    # (the other direction is in test_tasks.py)
    b = project / "brainstorm"
    assert run_opsci("task", "new", "b01", "--title", "x", "--root", b).returncode == 0
    r = run_opsci("task", "new", "t01", "--title", "x", "--depends-on", "b01", "--root", project)
    assert r.returncode == 1 and "not nodes" in r.stderr
    assert not (project / "tasks" / "t01").exists()
    # control: the same edge from another brainstorm task is accepted
    r = run_opsci("task", "new", "b02", "--title", "x", "--depends-on", "b01", "--root", b)
    assert r.returncode == 0, r.stderr


def test_brainstorm_map_works_in_git(project):
    # list_files uses git ls-files inside a work tree; with brainstorm/ as root the paths
    # must come out relative to brainstorm/.
    git(project, "init", "-q")
    b = project / "brainstorm"
    assert run_opsci("task", "new", "b01", "--title", "x", "--root", b).returncode == 0
    r = run_opsci("map", "build", b)
    assert r.returncode == 0, r.stderr
    assert "](../tasks/b01/context.md)" in (b / "map" / "graph.md").read_text()


# ---- the manifest -----------------------------------------------------------------------

def test_export_includes_docs_and_never_private_docs(project):
    (project / "docs" / "howto.md").write_text("---\nstatus: active\n---\n# How to\n")
    (project / "private-docs" / "meeting.md").write_text("# notes\n")
    (project / "brainstorm" / "idea.md").write_text("# idea\n")
    man = publish.load_manifest(project)
    out, excluded, _, _ = publish.select(project, man)
    assert "docs/README.md" in out and "docs/howto.md" in out
    assert excluded["private-docs/meeting.md"] == "not in the manifest"
    assert excluded["brainstorm/idea.md"] == "not in the manifest"
    # opting in to private-docs still leaves it out; opting in to brainstorm publishes it
    mf = project / "publish" / "manifest.yaml"
    mf.write_text(mf.read_text().replace("  - path: docs\n", "  - path: docs\n  - path: private-docs\n  - path: brainstorm\n"))
    man = publish.load_manifest(project)
    out, excluded, _, _ = publish.select(project, man)
    assert excluded["private-docs/meeting.md"] == "listed under never"
    assert "brainstorm/idea.md" in out
