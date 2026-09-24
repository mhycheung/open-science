"""Node-header and `map build` tests (report, Tests table, row "task headers + map build",
plus "every published result page and paper is in the graph").

Valid headers produce the expected graph and dead-ends page (compared with reviewed files in
tests/fixtures/map_valid/). Every class of bad header is refused, and nothing is written.
"""
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from conftest import REPO, git, run_opsci
from map_project import header, valid_files, write
from opsci import mapbuild, nodes

FIXTURE = REPO / "tests" / "fixtures" / "map_valid"


@pytest.fixture
def proj(tmp_path):
    return write(tmp_path / "proj", valid_files())


def test_schema_is_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(nodes.load_schema())


def test_valid_project_gives_expected_graph(proj):
    r = run_opsci("map", "build", proj)
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""  # no warnings either
    for name in ("graph.md", "dead_ends.md"):
        assert (proj / "map" / name).read_text() == (FIXTURE / name).read_text(), name


def test_data_and_lit_cache_are_not_scanned(proj):
    res, _ = mapbuild.build(proj)
    ids = {n.id for n in res.nodes}
    assert "x-in-data" not in ids and "x-in-lit" not in ids  # x-in-lit has a bad status
    assert "t00-pilot" in ids  # archive/ is scanned


def test_check_mode_detects_stale_files(proj):
    assert run_opsci("map", "build", proj, "--check").returncode == 1  # never built
    assert run_opsci("map", "build", proj).returncode == 0
    assert run_opsci("map", "build", proj, "--check").returncode == 0
    ctx = proj / "tasks" / "t04-scan" / "context.md"
    ctx.write_text(ctx.read_text().replace("status: failed", "status: abandoned"))
    r = run_opsci("map", "build", proj, "--check")
    assert r.returncode == 1 and "out of date" in r.stderr


def _edit(files, path, **changes):
    text = files[path]
    head = yaml.safe_load(text.split("---")[1])
    for k, v in changes.items():
        if v is None:
            head.pop(k, None)
        else:
            head[k] = v
    files[path] = header(**head)


T3 = "tasks/t03-fit-v2/context.md"
T4 = "tasks/t04-scan/context.md"
BAD_CASES = {
    "missing summary": (lambda f: _edit(f, T4, summary=None), "'summary' is a required property"),
    "missing type": (lambda f: _edit(f, T4, type=None), "'type' is a required property"),
    "unknown status": (lambda f: _edit(f, T4, status="finished"), "status: 'finished' is not one of"),
    "unknown type": (lambda f: _edit(f, T4, type="notebook"), "type: 'notebook' is not one of"),
    "unknown key (typo)": (lambda f: _edit(f, T4, depend_on=["t01-noise-model"]), "Additional properties are not allowed ('depend_on'"),
    "bad id": (lambda f: _edit(f, "site/results/fit.md", id="Fit_Result"), "id: 'Fit_Result' does not match"),
    "dangling depends_on": (lambda f: _edit(f, T4, depends_on=["t99-missing"]), "depends_on: 't99-missing' is not a node"),
    "dangling supersedes": (lambda f: _edit(f, T4, supersedes=["t98-missing"]), "supersedes: 't98-missing' is not a node"),
    "dangling related": (lambda f: _edit(f, T4, related=["t97-missing"]), "related: 't97-missing' is not a node"),
    "depends_on cycle": (lambda f: _edit(f, "tasks/t01-noise-model/context.md", depends_on=["t03-fit-v2"]), "depends_on cycle"),
    "duplicate id": (lambda f: f.update({"site/results/other.md": header(
        id="t04-scan", title="x", type="page", status="done", summary="x")}), "duplicate id 't04-scan'"),
    "task id != directory": (lambda f: _edit(f, T4, id="t04-other"), "does not match task directory 't04-scan'"),
    "task context without header": (lambda f: f.update({T4: "# Start-time scan\n"}), "task context has no node header"),
    "verified without evidence": (lambda f: _edit(f, T4, verification="verified"), "'evidence' is a required property"),
    "evidence file missing": (lambda f: _edit(f, T4, verification="verified", evidence="tasks/t04-scan/nope.yaml"), "does not exist"),
    "invalid YAML": (lambda f: f.update({T4: "---\nid: t04-scan\ntitle: [unclosed\n---\n"}), "not valid YAML"),
    "invalid YAML, not a task, has id": (lambda f: f.update({"archive/old.md": "---\nid: old-page\ntitle: [unclosed\n---\n"}), "not valid YAML"),
    "invalid YAML in task context, no id": (lambda f: f.update({T4: "---\ntitle: a: b\n---\n"}), "not valid YAML"),
    "unclosed front matter": (lambda f: f.update({T4: "---\nid: t04-scan\n"}), "never closed"),
    "plan id without task": (lambda f: f.update({"tasks/t05-new/plan.md": header(
        id="t05-new", title="x", type="task", status="active", summary="x")}), "does not match a task context"),
    # Published result pages and papers must be in the graph.
    "published paper without node": (lambda f: f.pop("paper/node.yaml"), "published paper 'paper/main.tex' is not in the graph"),
    "published result of wrong type": (lambda f: _edit(f, "site/results/fit.md", type="page"), "published result 'site/results/fit.md' is not in the graph"),
    "manifest entry matches nothing": (lambda f: f.update({"publish/manifest.yaml": yaml.safe_dump(
        {"include": [{"path": "figures/*.png", "type": "result"}]})}), "matches no file"),
}


@pytest.mark.parametrize("case", sorted(BAD_CASES))
def test_bad_header_is_refused(tmp_path, case):
    mutate, message = BAD_CASES[case]
    files = valid_files()
    mutate(files)
    root = write(tmp_path / "proj", files)
    r = run_opsci("map", "build", root)
    assert r.returncode == 1, f"{case}: accepted\n{r.stdout}{r.stderr}"
    assert message in r.stderr, f"{case}: wrong message\n{r.stderr}"
    assert not (root / "map" / "graph.md").exists()  # nothing written on error


def test_plan_that_disagrees_with_context_warns(tmp_path):
    files = valid_files()
    _edit(files, "tasks/t03-fit-v2/plan.md", status="paused")
    root = write(tmp_path / "proj", files)
    r = run_opsci("map", "build", root)
    assert r.returncode == 0
    assert "status differs from tasks/t03-fit-v2/context.md" in r.stderr


def test_invalid_front_matter_without_id_warns(tmp_path):
    # Not a node header (no `id:` line), e.g. an archived agent definition: warn, don't fail.
    files = valid_files()
    files["archive/agents/tier.md"] = "---\nname: tier\ndescription: Use for: anything\n---\nbody\n"
    root = write(tmp_path / "proj", files)
    r = run_opsci("map", "build", root)
    assert r.returncode == 0, r.stderr
    assert "archive/agents/tier.md" in r.stderr and "not a node header" in r.stderr


def test_superseded_without_successor_warns(tmp_path):
    files = valid_files()
    _edit(files, T3, supersedes=None)
    root = write(tmp_path / "proj", files)
    r = run_opsci("map", "build", root)
    assert r.returncode == 0 and "no node supersedes 't02-fit-v1'" in r.stderr


def test_gitignored_files_are_not_scanned(tmp_path):
    files = valid_files()
    files["scratch/notes.md"] = header(id="bad", title="x", type="task", status="nonsense", summary="x")
    files[".gitignore"] = "/scratch/\n"
    root = write(tmp_path / "proj", files)
    git(root, "init", "-q")
    assert run_opsci("map", "build", root).returncode == 0
    (root / ".gitignore").write_text("")  # control: the same file, no longer ignored
    r = run_opsci("map", "build", root)
    assert r.returncode == 1 and "scratch/notes.md" in r.stderr


def test_one_sided_related_edge_is_drawn(tmp_path):
    files = valid_files()
    _edit(files, T3, related=None)  # only t04 (the later id) lists the pair now
    root = write(tmp_path / "proj", files)
    assert run_opsci("map", "build", root).returncode == 0
    assert "n_t03_fit_v2 --- n_t04_scan" in (root / "map" / "graph.md").read_text()


def test_empty_project(tmp_path):
    (tmp_path / "p").mkdir()
    assert run_opsci("map", "build", tmp_path / "p").returncode == 0
    assert "None yet." in (tmp_path / "p" / "map" / "dead_ends.md").read_text()
