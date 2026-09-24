# opsci: planted-leaks (a control case plants a path in a page)
"""Project site (the "project site" test row)."""
import re

import pytest
import yaml

from opsci import site


def page(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_repo(r):
    """A sample public repo: what an export looks like."""
    page(r / "README.md", "# Demo project\n\nSee the [context](context.md) and the [fit](tasks/t01-fit/context.md).\n"
         "Directory links: [map](map/) has a README; [citations](citations/) gets a generated listing.\n")
    page(r / "CITATION.cff", "cff-version: 1.2.0\ntitle: Demo project\nmessage: cite it\n")
    page(r / "context.md", "# Context\n\nWhere things stand.\n")
    page(r / "log/2026-09.md", "# Log 2026-09\n\n- started\n")
    page(r / "map/README.md", "# Map\n\nThe [graph](graph.md).\n")
    page(r / "map/graph.md", "# Project graph\n\n```mermaid\nflowchart LR\n  a --> b\n```\n")
    page(r / "map/dead_ends.md", "# Dead ends\n\n- [t00-old](../tasks/t00-old/context.md)\n")
    page(r / "citations/used.bib", "@article{smith2020,\n  title={A}\n}\n")
    page(r / "citations/consulted.md", "# Consulted\n")
    page(r / "tasks/t01-fit/context.md",
         "---\nid: t01-fit\ntitle: Fit\ntype: task\nstatus: active\nprivacy: public\nsummary: s\n---\n"
         "# Fit\n\nWork in progress. Evidence in [provenance](provenance.yaml).\n")
    page(r / "tasks/t01-fit/provenance.yaml", "command: fit\n")
    page(r / "tasks/t00-old/context.md",
         "---\nid: t00-old\ntitle: Old\ntype: task\nstatus: failed\nprivacy: public\nsummary: did not work\n---\n# Old\n")
    page(r / "results/fit.md",
         "---\nid: r01-fit\ntitle: Fit result\ntype: result\nstatus: done\nprivacy: public\nsummary: s\n"
         "verification: human-verified\nevidence: tasks/t01-fit/provenance.yaml\n---\n# Fit result\n")
    page(r / "notes/extra.md", "# An extra page\n")
    return r


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "public")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One build of the sample repo, shared by the tests that only read it (a build takes seconds)."""
    d = tmp_path_factory.mktemp("site")
    out = d / "site"
    return site.build(make_repo(d / "public"), out), out


def hrefs(html: str) -> set[str]:
    return set(re.findall(r'href="([^"#]+)', html))


def test_site_builds_and_every_page_is_reachable(built):
    problems, out = built
    assert problems == []
    repo = out.parent / "public"
    pages = [p.relative_to(repo).as_posix() for p in repo.rglob("*.md")]
    pages += ["citations/bibliography.md", "citations/index.md"]  # generated
    index = (out / "index.html").read_text()
    linked = hrefs(index)
    for p in pages:
        html = site.html_path(p)
        assert (out / html).is_file(), p
        assert html in linked, f"{p} is not in the navigation"
    # tabs
    tabs = re.findall(r'class="md-tabs__link">\s*([^<]+?)\s*<', index)
    assert tabs == ["Home", "Results", "Map", "Dead ends", "Tasks", "Citations", "Context", "Log", "Other"]


def test_status_banner_and_verification_level(built):
    problems, out = built
    assert problems == []
    assert "Work in progress" in (out / "tasks/t01-fit/context.html").read_text()
    assert "This route did not work" in (out / "tasks/t00-old/context.html").read_text()
    fit = (out / "results/fit.html").read_text()
    assert "Human-verified" in fit and "provenance.yaml" in fit
    assert "Work in progress" not in (out / "context.html").read_text()


def test_broken_link_fails_the_build(repo, tmp_path):
    page(repo / "context.md", "# Context\n\nSee [missing](nowhere.md).\n")
    problems = site.build(repo, tmp_path / "site")
    assert problems and any("nowhere.md" in p for p in problems)


def test_leak_in_built_site_fails(repo, tmp_path):
    page(repo / "context.md", "# Context\n\nOutput in /scratch/grp/run1.\n")
    problems = site.build(repo, tmp_path / "site")
    assert any("leak in built site" in p and "absolute-path" in p for p in problems)


def test_workflow_installs_opsci_at_the_recorded_commit(tmp_path):
    (tmp_path / "config").mkdir()
    fw = tmp_path / "config/framework.yaml"
    fw.write_text('framework_repo: "https://github.com/someone/open-science"\ncopied_at_commit: "abc1234"\n')
    wf = yaml.safe_load(site.workflow(tmp_path))
    steps = wf["jobs"]["build"]["steps"]
    assert any("git+https://github.com/someone/open-science@abc1234#subdirectory=tools" in s.get("run", "")
               for s in steps)
    assert wf["jobs"]["deploy"]["steps"][0]["uses"].startswith("actions/deploy-pages")
    # a framework that is not public yet: the workflow says so and fails, instead of guessing
    fw.write_text('framework_repo: "local copy"\ncopied_at_commit: "abc1234"\n')
    run = [s.get("run", "") for s in yaml.safe_load(site.workflow(tmp_path))["jobs"]["build"]["steps"]]
    assert any("exit 1" in r for r in run)
