"""`opsci task new`, `opsci context check`, `opsci migrate inventory/compare` (S2)."""

import re

import pytest
import yaml

from conftest import git, run_opsci


@pytest.fixture
def project(tmp_path):
    dest = tmp_path / "proj"
    r = run_opsci("template", "instantiate", dest, "--name", "demo", "--title", "Demo",
                  "--author", "A. Person", "--date", "2026-01-02")
    assert r.returncode == 0, r.stderr
    return dest


def header(path):
    text = path.read_text()
    return yaml.safe_load(text.split("---\n")[1])


# ---- task new ---------------------------------------------------------------------------

def test_task_new_creates_layout_and_map_builds(project):
    r = run_opsci("task", "new", "t01-noise", "--title", "Noise model", "--root", project)
    assert r.returncode == 0, r.stderr
    t = project / "tasks" / "t01-noise"
    for f in ("context.md", "log.md", "subcontext/README.md"):
        assert (t / f).is_file(), f
    assert not (t / "plan.md").exists()
    h = header(t / "context.md")
    assert h["id"] == "t01-noise" and h["type"] == "task" and h["status"] == "active"
    r = run_opsci("map", "build", project)
    assert r.returncode == 0, r.stderr
    assert "t01-noise" in (project / "map" / "graph.md").read_text()



def test_task_new_short_name(project):
    r = run_opsci("task", "new", "t01-noise", "--title", "Noise model", "--short-name", "noise",
                  "--plan", "--root", project)
    assert r.returncode == 0, r.stderr
    t = project / "tasks" / "t01-noise"
    assert header(t / "context.md")["short_name"] == "noise"
    assert header(t / "plan.md")["short_name"] == "noise"
    r = run_opsci("map", "build", project)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("bad", ["Noise", "noise model", "x" * 25])
def test_task_new_rejects_bad_short_name(project, bad):
    r = run_opsci("task", "new", "t01-noise", "--title", "N", "--short-name", bad, "--root", project)
    assert r.returncode != 0
    assert not (project / "tasks" / "t01-noise").exists()


def test_task_new_without_short_name_omits_it(project):
    r = run_opsci("task", "new", "t01-noise", "--title", "N", "--root", project)
    assert r.returncode == 0, r.stderr
    assert "short_name" not in header(project / "tasks" / "t01-noise" / "context.md")


def test_task_new_plan_follows_template(project):
    run_opsci("task", "new", "t01-a", "--title", "A", "--root", project)
    r = run_opsci("task", "new", "t02-b", "--title", "B", "--plan", "--depends-on", "t01-a",
                  "--autonomy", "checkpoints", "--hold-at", "S2", "before-publish", "--root", project)
    assert r.returncode == 0, r.stderr
    plan = project / "tasks" / "t02-b" / "plan.md"
    h = header(plan)
    assert h["autonomy"] == "checkpoints" and h["hold_at"] == ["S2", "before-publish"]
    assert h["depends_on"] == ["t01-a"]
    headings = re.findall(r"^## (.+)$", plan.read_text(), re.M)
    want = ["Goal", "Design", "Constraints", "Delegation", "Subtask S1", "Escalate only if fatal", "Budget"]
    got = [next((g for g in headings if g.startswith(w)), None) for w in want]
    assert None not in got and headings.index(got[0]) < headings.index(got[-1])
    assert "Fatal if:" in plan.read_text()
    # the plan repeats the context header, so map build must not warn about drift
    r = run_opsci("map", "build", project)
    assert r.returncode == 0 and "differs" not in r.stderr, r.stderr


def test_task_new_privacy(project):
    run_opsci("task", "new", "t01-a", "--title", "A", "--root", project)
    assert header(project / "tasks" / "t01-a" / "context.md")["privacy"] == "public"  # the default
    r = run_opsci("task", "new", "t02-b", "--title", "B", "--plan", "--privacy", "hard-private", "--root", project)
    assert r.returncode == 0, r.stderr
    for f in ("context.md", "plan.md"):
        assert header(project / "tasks" / "t02-b" / f)["privacy"] == "hard-private", f
    r = run_opsci("task", "new", "t03-c", "--title", "C", "--privacy", "secret", "--root", project)
    assert r.returncode == 2 and "invalid choice" in r.stderr  # control
    assert not (project / "tasks" / "t03-c").exists()


def test_task_new_default_autonomy_is_maximal(project):
    run_opsci("task", "new", "t01-a", "--title", "A", "--plan", "--root", project)
    assert header(project / "tasks" / "t01-a" / "plan.md")["autonomy"] == "maximal"


@pytest.mark.parametrize("args,msg", [
    (["T01", "--title", "x"], "lower case"),
    (["t01", "--title", "x", "--depends-on", "t99-missing"], "not nodes"),
    (["t01", "--title", "x", "--hold-at", "S1"], "checkpoints"),
])
def test_task_new_refuses(project, args, msg):
    r = run_opsci("task", "new", *args, "--root", project)
    assert r.returncode == 1 and msg in r.stderr
    assert sorted(p.name for p in (project / "tasks").iterdir()) == ["README.md"]


def test_task_new_refuses_existing_task(project):
    assert run_opsci("task", "new", "t01", "--title", "x", "--root", project).returncode == 0
    before = (project / "tasks" / "t01" / "context.md").read_text()
    r = run_opsci("task", "new", "t01", "--title", "other", "--root", project)
    assert r.returncode == 1 and "already exists" in r.stderr
    assert (project / "tasks" / "t01" / "context.md").read_text() == before


def test_task_new_refuses_non_project(tmp_path):
    r = run_opsci("task", "new", "t01", "--title", "x", "--root", tmp_path)
    assert r.returncode == 1 and "not a project root" in r.stderr


# ---- context check ----------------------------------------------------------------------

def test_context_check_passes_fresh_project(project):
    run_opsci("task", "new", "t01", "--title", "x", "--root", project)
    r = run_opsci("context", "check", project)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("rel,cap", [("context.md", 200), ("tasks/t01/context.md", 200),
                                     ("map/README.md", 150)])
def test_context_check_refuses_over_cap(project, rel, cap):
    run_opsci("task", "new", "t01", "--title", "x", "--root", project)
    p = project / rel
    n = len(p.read_text().splitlines())
    p.write_text(p.read_text() + "x\n" * (cap - n))       # exactly at the cap: ok
    assert run_opsci("context", "check", project).returncode == 0
    p.write_text(p.read_text() + "x\n")                     # one over: refused
    r = run_opsci("context", "check", project)
    assert r.returncode == 1 and rel in r.stderr and f"cap of {cap}" in r.stderr


# ---- migrate ----------------------------------------------------------------------------

@pytest.fixture
def old_project(tmp_path):
    """A small project in an older layout: plans/, context docs, a pitfalls file, code."""
    root = tmp_path / "old"
    for rel, text in {
        "README.md": "old project\n",
        "docs/plans/2026-01-stage-a.md": "# plan A\n",
        "docs/context/stage-a/main_context.md": "# context A\n",
        "docs/PROJECT_PITFALLS.md": "# pitfalls\n",
        "src/fit.py": "print('fit')\n",
        "scratch.txt": "untracked but not ignored\n",
        "big.h5": "ignored data\n",
        ".gitignore": "*.h5\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git(root.parent, "init", "-q", str(root))
    git(root, "add", "-A", ":!scratch.txt")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "old")
    return root


def test_migrate_moves_lose_nothing(old_project, tmp_path):
    inv = tmp_path / "inventory.json"
    r = run_opsci("migrate", "inventory", old_project, "-o", inv)
    assert r.returncode == 0, r.stderr
    files = __import__("json").loads(inv.read_text())["files"]
    assert "scratch.txt" in files and "big.h5" not in files  # untracked kept, ignored skipped
    (old_project / "tasks/t01-stage-a/subcontext").mkdir(parents=True)
    git(old_project, "mv", "docs/context/stage-a/main_context.md", "tasks/t01-stage-a/subcontext/")
    git(old_project, "mv", "docs/plans/2026-01-stage-a.md", "tasks/t01-stage-a/subcontext/")
    (old_project / "rules").mkdir()
    git(old_project, "mv", "docs/PROJECT_PITFALLS.md", "rules/")
    (old_project / "README.md").write_text("old project, migrated\n")
    r = run_opsci("migrate", "compare", inv, old_project)
    assert r.returncode == 0, r.stderr
    assert "3 moved, 1 modified, 0 lost" in r.stdout


def test_migrate_compare_refuses_lost_file(old_project, tmp_path):
    inv = tmp_path / "inventory.json"
    run_opsci("migrate", "inventory", old_project, "-o", inv)
    (old_project / "src" / "fit.py").unlink()
    r = run_opsci("migrate", "compare", inv, old_project)
    assert r.returncode == 1 and "lost: src/fit.py" in r.stderr


def test_migrate_inventory_refuses_non_git(tmp_path):
    (tmp_path / "x").mkdir()
    r = run_opsci("migrate", "inventory", tmp_path / "x", "-o", tmp_path / "inv.json")
    assert r.returncode == 1 and "not a git work tree" in r.stderr


# ---- brainstorm sub-root and private-docs ------------------------------------------------

def test_brainstorm_nodes_join_the_project_graph(project):
    b = project / "brainstorm"
    r = run_opsci("task", "new", "b01-idea", "--title", "An idea", "--root", b)
    assert r.returncode == 0, r.stderr
    assert header(b / "tasks" / "b01-idea" / "context.md")["id"] == "b01-idea"
    assert run_opsci("task", "new", "t01-work", "--title", "Work", "--root", project).returncode == 0
    for root in (b, project):  # either root builds the same maps
        r = run_opsci("map", "build", root)
        assert r.returncode == 0, r.stderr
        assert "brainstorm/map/" in r.stdout
        r = run_opsci("context", "check", root)
        assert r.returncode == 0, r.stderr
    # the project graph has both, the brainstorm node in its own box, linked from map/
    pgraph = (project / "map" / "graph.md").read_text()
    assert "t01-work" in pgraph and '  subgraph brainstorm["brainstorm"]\n    n_b01_idea[' in pgraph
    assert "[b01-idea](../brainstorm/tasks/b01-idea/context.md)" in pgraph
    assert "n_t01_work[" not in pgraph.split("subgraph brainstorm")[1].split("  end")[0]
    # the brainstorm map has the brainstorm node only, with a link that resolves from there
    bgraph = (b / "map" / "graph.md").read_text()
    assert "[b01-idea](../tasks/b01-idea/context.md)" in bgraph and "t01-work" not in bgraph


def test_brainstorm_task_privacy_defaults_to_soft_private(project):
    b = project / "brainstorm"
    run_opsci("task", "new", "b01", "--title", "x", "--root", b)
    run_opsci("task", "new", "b02", "--title", "x", "--root", b, "--privacy", "public")
    run_opsci("task", "new", "t01", "--title", "x", "--root", project)
    assert header(b / "tasks/b01/context.md")["privacy"] == "soft-private"
    assert header(b / "tasks/b02/context.md")["privacy"] == "public"
    assert header(project / "tasks/t01/context.md")["privacy"] == "public"  # control


def test_edges_cross_between_project_and_brainstorm(project):
    b = project / "brainstorm"
    assert run_opsci("task", "new", "t01-work", "--title", "Work", "--root", project).returncode == 0
    r = run_opsci("task", "new", "b01-idea", "--title", "x", "--depends-on", "t01-work", "--root", b)
    assert r.returncode == 0, r.stderr
    r = run_opsci("task", "new", "t02", "--title", "x", "--related", "b01-idea", "--root", project)
    assert r.returncode == 0, r.stderr
    assert run_opsci("map", "build", project).returncode == 0
    pgraph = (project / "map" / "graph.md").read_text()
    assert "n_t01_work --> n_b01_idea" in pgraph and "n_b01_idea --- n_t02" in pgraph
    # control: an id that is not a node is still refused
    r = run_opsci("task", "new", "b02", "--title", "x", "--depends-on", "nosuch", "--root", b)
    assert r.returncode == 1 and "not nodes" in r.stderr


@pytest.mark.parametrize("first,second", [("project", "brainstorm"), ("brainstorm", "project")])
def test_task_id_unique_across_project_and_brainstorm(project, first, second):
    roots = {"project": project, "brainstorm": project / "brainstorm"}
    assert run_opsci("task", "new", "x01", "--title", "x", "--root", roots[first]).returncode == 0
    r = run_opsci("task", "new", "x01", "--title", "x", "--root", roots[second])
    assert r.returncode == 1 and "already used by" in r.stderr


def test_project_map_includes_brainstorm_but_skips_private_docs(project):
    from opsci import nodes
    text = ("---\nid: x-note\ntitle: A note\ntype: result\nstatus: done\n"
            "summary: Not part of the project graph.\n---\n")
    (project / "private-docs" / "note.md").write_text(text)
    assert "x-note" not in {n.id for n in nodes.scan(project).nodes}
    # control: the same file under brainstorm/ is a node
    (project / "brainstorm" / "note.md").write_text(text)
    assert "x-note" in {n.id for n in nodes.scan(project).nodes}


@pytest.mark.parametrize("rel,cap", [("brainstorm/context.md", 200),
                                     ("brainstorm/tasks/b01/context.md", 200),
                                     ("brainstorm/map/README.md", 150)])
def test_context_check_caps_brainstorm_files(project, rel, cap):
    run_opsci("task", "new", "b01", "--title", "x", "--root", project / "brainstorm")
    p = project / rel
    n = len(p.read_text().splitlines())
    p.write_text(p.read_text() + "x\n" * (cap - n))       # exactly at the cap: ok
    assert run_opsci("context", "check", project).returncode == 0
    p.write_text(p.read_text() + "x\n")                     # one over: refused
    r = run_opsci("context", "check", project)
    assert r.returncode == 1 and rel in r.stderr and f"cap of {cap}" in r.stderr
    # the brainstorm root checked on its own refuses it too
    r = run_opsci("context", "check", project / "brainstorm")
    assert r.returncode == 1 and rel.removeprefix("brainstorm/") in r.stderr
