"""Results: result nodes in tasks/<id>/results/ and results/, the results pages and the
claims graph (map/claims.md) that `opsci map build` writes from their headers."""

import time

import pytest

from conftest import git, run_opsci
from opsci import nodes, results
from map_project import header

BIB = """
@article{Isi2019,
  title = {Testing the {No-Hair} Theorem with {GW150914}},
  year = {2019},
}
"""


@pytest.fixture
def project(tmp_path):
    dest = tmp_path / "proj"
    r = run_opsci("template", "instantiate", dest, "--name", "demo", "--title", "Demo",
                  "--author", "A. Person", "--date", "2026-01-02")
    assert r.returncode == 0, r.stderr
    for tid, title in (("t01-noise", "Noise model"), ("t02-fit", "Mode fit")):
        r = run_opsci("task", "new", tid, "--title", title, "--root", dest)
        assert r.returncode == 0, r.stderr
    (dest / "citations" / "used.bib").write_text(BIB)
    for f in ("tasks/t01-noise/S1/psd.png", "tasks/t01-noise/S1/psd.py", "tasks/t02-fit/S2/corner.png"):
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        (dest / f).write_text("x\n")
    return dest


def put(root, rel, **h):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(header(**h))


def psd(root, **extra):
    put(root, "tasks/t01-noise/results/r-psd.md", id="r-psd", title="Noise is stationary",
        type="result", kind="figure", status="done", summary="The PSD varies by less than 5%.",
        artifacts=["tasks/t01-noise/S1/psd.png"], code=["tasks/t01-noise/S1/psd.py"], **extra)


def mass(root, **extra):
    h = dict(id="r-mass", title="Mass ratio 1.8", type="result", kind="value", status="done",
             milestone=True, depends_on=["r-psd"], uses=["Isi2019"],
             artifacts=["tasks/t02-fit/S2/corner.png"], summary="The mass ratio is 1.8 +/- 0.1.")
    h.update(extra)
    put(root, "tasks/t02-fit/results/r-mass.md", **h)


def build(root, *extra):
    return run_opsci("map", "build", *extra, root)


def read(root, rel):
    return (root / rel).read_text()


def test_task_new_writes_an_empty_results_page(project):
    page = read(project, "tasks/t01-noise/results/README.md")
    assert page.startswith("<!-- GENERATED") and "# Results: Noise model" in page and "No results yet." in page
    r = build(project, "--check")  # the page task new wrote is the one map build writes
    assert "tasks/t01-noise/results/README.md" not in r.stderr and "map/graph.md" in r.stderr
    assert build(project).returncode == 0
    assert "No milestone results yet." in read(project, "results/README.md")
    assert "No results yet." in read(project, "map/claims.md")


def test_results_pages_and_claims_graph(project):
    psd(project)
    mass(project)
    r = build(project)
    assert r.returncode == 0, r.stderr
    page = read(project, "tasks/t02-fit/results/README.md")
    assert "## r-mass: Mass ratio 1.8" in page and "![r-mass](../S2/corner.png)" in page
    assert "[r-psd](../../t01-noise/results/r-psd.md)" in page  # rests on, linked from here
    assert "[@Isi2019]" in page
    ms = read(project, "results/README.md")  # milestones only
    assert "## r-mass:" in ms and "## r-psd:" not in ms
    assert "![r-mass](../tasks/t02-fit/S2/corner.png)" in ms
    claims = read(project, "map/claims.md")
    assert "![Claims graph](claims.svg)" in claims
    drawing = results.claims_drawing(nodes.scan(project).nodes, results.read_bib(project))
    assert {"id": "t01-noise", "kicker": "task t01-noise", "title": "Noise model", "style": "task"} in drawing["boxes"]
    assert ["r-psd", "r-mass", "dep"] in drawing["edges"]
    assert next(c for c in drawing["cards"] if c["id"] == "r-mass")["tags"] == ["Isi2019"]
    assert "Testing the No-Hair Theorem with GW150914" in claims
    assert "None: every live result rests only on live work." in claims
    # results in a results directory are drawn in the claims graph, not the task graph
    graph = read(project, "map/graph.md")
    assert "r-mass" not in graph and "[the claims graph](claims.md)" in graph
    assert build(project, "--check").returncode == 0


def test_superseded_premise_marks_every_result_resting_on_it(project):
    psd(project)
    mass(project)
    put(project, "results/r-paper-claim.md", id="r-paper-claim", title="Headline", type="result",
        kind="statement", status="active", depends_on=["r-mass"], summary="The headline claim.")
    assert build(project).returncode == 0
    put(project, "tasks/t01-noise/results/r-psd.md", id="r-psd", title="Noise is stationary",
        type="result", kind="figure", status="superseded", summary="Window was wrong.")
    put(project, "tasks/t01-noise/results/r-psd-v2.md", id="r-psd-v2", title="Noise, corrected",
        type="result", kind="figure", status="done", supersedes=["r-psd"], summary="Corrected window.")
    r = build(project)
    assert r.returncode == 0, r.stderr
    assert "r-mass.md: rests on r-psd, which is superseded (by r-psd-v2)" in r.stderr
    assert "r-paper-claim.md: rests on r-psd (through r-mass), which is superseded" in r.stderr
    claims = read(project, "map/claims.md")
    drawing = results.claims_drawing(nodes.scan(project).nodes)
    assert {c["id"] for c in drawing["cards"] if c.get("at_risk")} == {"r-mass", "r-paper-claim"}
    assert ["r-psd", "r-psd-v2", "superseded"] in drawing["edges"]  # old -> new
    assert "- `r-psd` is superseded by `r-psd-v2`." in claims
    assert "## May no longer hold" in claims and "None: every live" not in claims
    assert "May no longer hold:" in read(project, "tasks/t02-fit/results/README.md")
    # the project-level result sits in its own box and is a milestone
    assert next(c for c in drawing["cards"] if c["id"] == "r-paper-claim")["box"] == "results"
    assert "## r-paper-claim:" in read(project, "results/README.md")
    # the superseded result moves to the withdrawn table of its task page
    t1 = read(project, "tasks/t01-noise/results/README.md")
    assert "## Withdrawn" in t1 and "| superseded | Window was wrong. | [r-psd-v2](r-psd-v2.md) |" in t1
    # control: once the dependent result is moved to the new premise, nothing is at risk
    mass(project, depends_on=["r-psd-v2"])
    r = build(project)
    assert "superseded" not in r.stderr
    assert not any(c.get("at_risk") for c in results.claims_drawing(nodes.scan(project).nodes)["cards"])


def test_dead_task_marks_its_live_results(project):
    psd(project)
    ctx = project / "tasks/t01-noise/context.md"
    ctx.write_text(ctx.read_text().replace("status: active", "status: failed", 1))
    r = build(project)
    assert r.returncode == 0, r.stderr
    assert "r-psd.md: its task t01-noise is failed" in r.stderr


def test_verified_result_on_unverified_premise_is_listed(project):
    (project / "tasks/t02-fit/prov.yaml").write_text("command: x\n")
    psd(project)
    mass(project, verification="verified", evidence="tasks/t02-fit/prov.yaml")
    assert build(project).returncode == 0
    claims = read(project, "map/claims.md")
    assert "## Verified, but resting on unverified results" in claims
    assert "| verified | unverified |" in claims  # own level, weakest in the chain


@pytest.mark.parametrize("change, message", [
    (dict(uses=["Nobody2020"]), "uses: 'Nobody2020' is not a key in citations/used.bib"),
    (dict(artifacts=["tasks/t02-fit/S2/missing.png"]), "artifacts: 'tasks/t02-fit/S2/missing.png' does not exist"),
    (dict(code=["src/nothing.py"]), "code: 'src/nothing.py' does not exist"),
    (dict(kind="plot"), "kind: 'plot' is not one of"),
    (dict(artifacts=["~" + "x.png"]), "does not match"),  # outside the project
])
def test_bad_result_fields_are_refused(project, change, message):
    psd(project)
    mass(project, **change)
    r = build(project)
    assert r.returncode == 1 and message in r.stderr, r.stderr


def test_result_fields_on_a_task_are_refused(project):
    ctx = project / "tasks/t01-noise/context.md"
    ctx.write_text(ctx.read_text().replace("type: task", "type: task\nmilestone: true", 1))
    r = build(project)
    assert r.returncode == 1 and "milestone: only a result (type: result) has it" in r.stderr


def test_missing_data_artifact_is_a_warning(project):
    psd(project)
    mass(project, artifacts=["data/t02-fit/chain.h5"])
    r = build(project)
    assert r.returncode == 0 and "'data/t02-fit/chain.h5' is not in this checkout" in r.stderr


def test_artifact_changed_after_its_result_is_reported(project):
    psd(project)
    git(project, "init", "-q")
    git(project, "add", "-A")
    git(project, "-c", "user.name=U", "-c", "user.email=u@example.org", "commit", "-qm", "results")
    assert "changed after" not in build(project).stderr  # control: nothing changed
    time.sleep(1.1)  # commit times have a resolution of one second
    (project / "tasks/t01-noise/S1/psd.png").write_text("new\n")
    assert "'tasks/t01-noise/S1/psd.png' changed after this result file" in build(project).stderr
    git(project, "add", "-A")
    git(project, "-c", "user.name=U", "-c", "user.email=u@example.org", "commit", "-qm", "new figure")
    assert "'tasks/t01-noise/S1/psd.png' changed after this result file" in build(project).stderr
