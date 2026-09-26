# opsci: planted-leaks (a control case plants a path in a page)
"""Project site (the "project site" test row)."""
import json
import re

import pytest
import yaml

from opsci import site


def page(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def make_repo(r):
    """A sample public repo: what an export looks like."""
    page(r / "README.md", "# Demo project\n\nSee the [context](context.md) and the [fit](tasks/t01-fit/context.md).\n"
         "Directory links: [map](map/) has a page; [citations](citations/) too; [src](src/) has none.\n"
         "The [agent rules](AGENTS.md) are not on the site.\n")
    page(r / "AGENTS.md", "# Agent instructions\n")
    page(r / "src/README.md", "# Shared code\n")
    page(r / "CITATION.cff", "cff-version: 1.2.0\ntitle: Demo project\nmessage: cite it\n")
    page(r / "context.md", "# Context\n\nWhere things stand. The [plan of the fit](tasks/t01-fit/plan.md).\n")
    page(r / "log/README.md", "# Project log\n\nOne file per month (for agents).\n")
    page(r / "log/2026-08.md", "# Project log, 2026-08\n\n- 2026-08-30 t01-fit: task created. → tasks/t01-fit/plan.md\n")
    page(r / "log/2026-09.md", "# Project log, 2026-09\n\n- 2026-09-02 t01-fit: fit gives $\\chi^2 = 1.2$;\n"
         "  the second line of the entry. → tasks/t01-fit/log.md\n- 2026-09-03 project: site set up.\n")
    page(r / "map/README.md", "# Map\n\nThe [graph](graph.md) and the [claims](claims.md#results).\n")
    page(r / "map/graph.md", "# Project graph\n\n```mermaid\nflowchart LR\n  a --> b\n```\n")
    page(r / "map/claims.md", "# Claims graph\n\n## Results\n\n| result | verification |\n|---|---|\n"
         "| [r01-fit](../results/fit.md) | unverified |\n")
    page(r / "map/dead_ends.md", "# Dead ends\n\n- [t00-old](../tasks/t00-old/context.md)\n")
    page(r / "citations/used.bib",
         "% works used\n@article{Abbott2016,\n  author = {Abbott, B. P. and others},\n"
         "  collaboration = {LIGO Scientific, Virgo},\n  journal = {Phys. Rev. D}, volume = {93}, pages = {122003},\n"
         "  year = {2016}, doi = {10.1103/PhysRevD.93.122003},\n  eprint = {1602.03839}, archivePrefix = {arXiv},\n"
         "  primaryClass = {gr-qc},\n  usage = {The noise model of every injection.}\n}\n"
         "@software{tool,\n  author = {Doe, Jane},\n  title = {{tool}},\n  version = {1.0},\n  url = {https://example.org/tool}\n}\n")
    page(r / "citations/consulted.md", "# Consulted, not used\n\n- a paper we skimmed\n")
    page(r / "tasks/README.md", "# Tasks\n\nHow tasks are laid out (for agents).\n")
    page(r / "tasks/t01-fit/context.md",
         "---\nid: t01-fit\ntitle: Fit\ntype: task\nstatus: active\nprivacy: public\nsummary: the fit\n---\n"
         "# Fit\n\nWork in progress. Evidence in [provenance](provenance.yaml). Inputs: [S1](S1/inputs.md).\n"
         "Uses [@Abbott2016].\n\n## Next step\n\nRun S2.\n")
    page(r / "tasks/t01-fit/plan.md",
         "---\nid: t01-fit\ntitle: Fit\ntype: task\nstatus: active\n---\n# Fit: the plan\n\n## Goal\n\nFit the data.\n")
    page(r / "tasks/t01-fit/map.md", "# Map: Fit\n\n```mermaid\nflowchart LR\n  S1 --> S2\n```\n")
    page(r / "tasks/t01-fit/log.md", "# Log: Fit\n\n2026-08-30 — task created.\n- 2026-09-02 S1 done. → S1/inputs.md\n")
    page(r / "tasks/t01-fit/S1/inputs.md", "# S1 inputs\n\n## Source\n\nThe data, see the [plan](../plan.md).\n")
    page(r / "tasks/t01-fit/subcontext/README.md", "# Subcontext\n\nFor agents.\n")
    page(r / "tasks/t01-fit/subcontext/S2_notes.md", "# S2 notes\n\nNotes.\n")
    (r / "tasks/t01-fit/S1/hist.png").write_bytes(PNG)
    page(r / "tasks/t01-fit/S1/hist.caption.md", "A histogram of the residuals, $r_i$.\n")
    (r / "tasks/t01-fit/S1/resid.png").write_bytes(PNG)
    page(r / "tasks/t01-fit/S1/resid.caption.md", "The residuals against time.\n")
    page(r / "tasks/t01-fit/results/README.md",
         "# Results: Fit\n\nThe results of this task (generated).\n\n## r02-fit-hist: the histogram\n\n"
         "*figure · done · unverified*\n\n![r02-fit-hist](../S1/hist.png)\n\n"
         "| | |\n|---|---|\n| full description | [r02-fit-hist](r02-fit-hist.md) |\n")
    page(r / "tasks/t01-fit/results/r02-fit-hist.md",
         "---\nid: r02-fit-hist\ntitle: The histogram\ntype: result\nstatus: done\nprivacy: public\nsummary: s\n"
         "verification: unverified\n---\n# The histogram\n\nSee the [notes](../subcontext/S2_notes.md).\n")
    page(r / "tasks/t01-fit/provenance.yaml", "command: fit\n")
    page(r / "tasks/t00-old/context.md",
         "---\nid: t00-old\ntitle: Old\ntype: task\nstatus: failed\nprivacy: public\nsummary: did not work\n---\n# Old\n")
    page(r / "results/README.md", "# Results\n\nThe milestone results. [fit](fit.md)\n")
    page(r / "results/fit.md",
         "---\nid: r01-fit\ntitle: Fit result\ntype: result\nstatus: done\nprivacy: public\nsummary: s\n"
         "verification: human-verified\nevidence: tasks/t01-fit/provenance.yaml\n---\n# Fit result\n")
    page(r / "results/math.md", "---\nid: r03-math\ntitle: Math of $\\iota_Q$ near $90^\\circ$\ntype: result\n"
         "status: done\nprivacy: public\nsummary: s\n---\n"
         "# Math of $\\iota_Q$ near $90^\\circ$\n\nInline $\\iota_Q(0) - 90^\\circ$ and $M = 10\\,\\rm M_\\odot$.\n\n"
         "$$\n\\langle h \\rangle = a_1 * b_2\n$$\n\n| q | value |\n|---|---|\n| $\\iota$ | $\\lvert\\cos\\iota\\rvert < 0.1$ |\n")
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


SITE_PAGES = ["README.md", "context.md", "results/README.md", "results/fit.md", "results/math.md",
              "map/README.md", "map/dead_ends.md", "tasks/README.md", "tasks/t01-fit/context.md",
              "tasks/t00-old/context.md", "tasks/t01-fit/results/r02-fit-hist.md", "citations/README.md",
              "log/README.md"]


def test_site_builds_and_every_page_is_reachable(built):
    problems, out = built
    assert problems == []
    index = (out / "index.html").read_text()
    linked = hrefs(index)
    for p in SITE_PAGES:
        html = site.html_path(p)
        assert (out / html).is_file(), p
        assert html in linked, f"{p} is not in the navigation"
    # tabs; no "Other" tab
    tabs = re.findall(r'class="md-tabs__link">\s*([^<]+?)\s*<', index)
    assert tabs == ["Home", "Results", "Map", "Dead ends", "Tasks", "Citations", "Context", "Log"]


def test_only_the_site_pages_are_built(built):
    problems, out = built
    assert problems == []
    built_pages = {p.relative_to(out).as_posix() for p in out.rglob("*.html")} - {"404.html"}
    assert built_pages == {site.html_path(p) for p in SITE_PAGES}
    # the files merged into a page, the agent documents and the consulted list are not pages
    for gone in ("AGENTS.html", "notes/extra.html", "src/index.html", "map/claims.html", "map/graph.html",
                 "tasks/t01-fit/plan.html", "tasks/t01-fit/log.html", "tasks/t01-fit/S1/inputs.html",
                 "citations/consulted.html", "log/2026-09.html", "tasks/t01-fit/S1/hist.caption.html"):
        assert not (out / gone).exists(), gone
    assert not (out / "citations/consulted.md").exists()
    home = (out / "index.html").read_text()
    assert "agent rules" in home and "AGENTS" not in "".join(hrefs(home))  # a link to a left-out page: plain text
    assert 'href="map/index.html"' in home and 'href="citations/index.html"' in home


def test_main_results_page(built):
    problems, out = built
    html = (out / "results/index.html").read_text()
    assert re.search(r"<h1[^>]*>Main results", html)
    assert re.search(r"md-ellipsis\">\s*Main results", html)  # its title in the navigation


def test_unverified_is_red_and_bold(built):
    problems, out = built
    assert problems == []
    unv = '<span class="opsci-unverified">'
    assert unv + "Not verified.</span>" in (out / "tasks/t01-fit/results/r02-fit-hist.html").read_text()
    task = (out / "tasks/t01-fit/context.html").read_text()
    assert unv + "unverified</span>" in task  # the *figure · done · unverified* line
    assert unv + "unverified</span>" in (out / "map/index.html").read_text()  # a table cell
    assert ".opsci-unverified{color:#b3261e;font-weight:700}" in task


def test_map_is_one_page_with_both_graphs(built):
    problems, out = built
    html = (out / "map/index.html").read_text()
    assert 'id="claims-graph"' in html and 'id="project-graph"' in html
    assert html.count('class="mermaid"') == 1  # the project graph (the sample claims graph has none)
    assert 'href="#project-graph"' in html and 'href="#results"' in html  # links between the old files
    assert 'href="../map/index.html#claims-graph"' not in html


def test_task_page_holds_the_whole_task(built):
    problems, out = built
    html = (out / "tasks/t01-fit/context.html").read_text()
    for anchor in ("results", "figures", "plan", "map", "notes", "log", "note-s1-inputs", "note-subcontext-s2-notes"):
        assert f'id="{anchor}"' in html, anchor
    # the result figure has its caption under it; the other figure has a section of its own
    assert re.search(r'src="S1/hist.png".*?class="opsci-caption".*?residuals, <span class="arithmatex">', html, re.S)
    assert html.count("A histogram of the residuals") == 1
    assert re.search(r'id="fig-resid".*?src="S1/resid.png".*?class="opsci-caption".*?against time', html, re.S)
    # links to merged files point to their section
    assert 'href="#note-s1-inputs"' in html or 'href="context.html#note-s1-inputs"' in html
    assert "For agents." not in html  # subcontext/README.md is left out
    # headings of merged files are demoted under their section
    assert re.search(r"<h4[^>]*>Source", html)
    # a result page links to the notes in the task page
    r = (out / "tasks/t01-fit/results/r02-fit-hist.html").read_text()
    assert 'href="../context.html#note-subcontext-s2-notes"' in r
    # the project context links to the plan section
    assert 'href="tasks/t01-fit/context.html#plan"' in (out / "context.html").read_text()
    # the overview lists every task
    ov = (out / "tasks/index.html").read_text()
    assert 'href="t01-fit/context.html"' in ov and 'href="t00-old/context.html"' in ov and "the fit" in ov


def test_log_is_one_list_by_date(built):
    problems, out = built
    html = (out / "log/index.html").read_text()
    days = re.findall(r"<h2[^>]*>(2026-\d\d-\d\d)", html)
    assert days == ["2026-09-03", "2026-09-02", "2026-08-30"]  # newest first
    assert re.search(r'<strong><a href="../tasks/t01-fit/context.html">t01-fit</a></strong>', html)
    assert "the second line of the entry" in html  # a continuation line joins its entry
    assert '<span class="arithmatex">\\(\\chi^2 = 1.2\\)</span>' in html
    assert 'href="../tasks/t01-fit/context.html#log"' in html  # pointer to the task log
    assert "for agents" not in html


def test_citations_table(built):
    problems, out = built
    html = (out / "citations/index.html").read_text()
    assert 'id="cite-abbott2016"' in html
    assert re.search(r"B\. P\. Abbott et al\. \(LIGO Scientific, Virgo\), "
                     r'<a href="https://doi.org/10.1103/PhysRevD.93.122003">Phys\. Rev\. D 93, 122003 \(2016\)</a>, '
                     r'<a href="https://arxiv.org/abs/1602.03839">arXiv:1602.03839 \[gr-qc\]</a>\.', html)
    assert "The noise model of every injection." in html
    assert 'href="https://example.org/tool"' in html
    assert 'href="used.bib"' in html
    # [@key] in a page links to the row
    assert 'href="../../citations/index.html#cite-abbott2016"' in (out / "tasks/t01-fit/context.html").read_text()


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


def test_workflow_installs_opsci_at_the_recorded_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "running_commit", lambda: None)
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


def test_workflow_installs_over_https_at_the_running_commit(tmp_path, monkeypatch):
    # an ssh remote is not a pip URL, and CI has no key: the public framework repo is fetched over https,
    # at the commit of the opsci that ran the publish, not the older commit the template was copied from
    monkeypatch.setattr(site, "running_commit", lambda: "f" * 40)
    (tmp_path / "config").mkdir()
    for repo in ("git@github.com:someone/open-science.git", "ssh://git@github.com/someone/open-science.git"):
        (tmp_path / "config/framework.yaml").write_text(f'framework_repo: "{repo}"\ncopied_at_commit: "abc1234"\n')
        run = " ".join(s.get("run", "") for s in yaml.safe_load(site.workflow(tmp_path))["jobs"]["build"]["steps"])
        assert f'"opsci @ git+https://github.com/someone/open-science.git@{"f" * 40}#subdirectory=tools"' in run


def test_math_is_typeset_by_mathjax(built):
    problems, out = built
    assert problems == []
    html = (out / "results/math.html").read_text()
    # arithmatex keeps the TeX whole (no emphasis from `_` or `*`), in spans MathJax typesets
    assert '<span class="arithmatex">\\(\\iota_Q(0) - 90^\\circ\\)</span>' in html
    assert '<div class="arithmatex">\\[' in html and "a_1 * b_2" in html
    assert re.search(r'<td><span class="arithmatex">\\\(\\lvert\\cos\\iota\\rvert &lt; 0.1\\\)</span></td>', html)
    assert f'<script src="{site.MATHJAX}"' in html


def test_banner_on_every_page_by_default(built):
    problems, out = built
    assert problems == []
    for f in ("index.html", "results/math.html", "results/fit.html", "tasks/t01-fit/context.html"):
        html = (out / f).read_text()
        assert f'<strong class="opsci-banner">{site.DEFAULT_BANNER}</strong>' in html, f
        assert "position:sticky" in html


def test_banner_text_can_be_changed_or_turned_off(repo, tmp_path):
    assert site.build(repo, tmp_path / "a", banner="Preprint: <arXiv> & friends") == []
    assert 'class="opsci-banner">Preprint: &lt;arXiv&gt; &amp; friends</strong>' in (tmp_path / "a/index.html").read_text()
    assert site.build(repo, tmp_path / "b", banner="") == []
    assert 'class="md-banner"' not in (tmp_path / "b/index.html").read_text()


def test_workflow_carries_the_banner(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "running_commit", lambda: "f" * 40)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/framework.yaml").write_text('framework_repo: "https://github.com/a/b"\n')
    for banner in (site.DEFAULT_BANNER, 'Say "hi": $x$', ""):
        step = [s for s in yaml.safe_load(site.workflow(tmp_path, banner))["jobs"]["build"]["steps"]
                if "site build" in s.get("run", "")][0]
        assert step["run"] == 'opsci site build . --out _site --banner "$OPSCI_SITE_BANNER"'
        assert step["env"]["OPSCI_SITE_BANNER"] == banner


def test_plain_title():
    assert site.plain_title(r"Bands of $\iota_Q(t)$ near $90^\circ$") == "Bands of ι_Q(t) near 90°"
    assert site.plain_title(r"On \(\langle h \rangle\), $M = 10\,\rm M_\odot$") == "On ⟨ h ⟩, M = 10 M_⊙"
    assert site.plain_title(r"$\lvert\cos\iota\rvert < \frac{1}{2}$") == "|cos ι| < 1/2"
    assert site.plain_title("Costs 5 dollars") == "Costs 5 dollars"


def test_math_in_titles(built):
    problems, out = built
    assert problems == []
    html = (out / "results/math.html").read_text()
    # no renderer runs in the browser tab's title or in search results: plain text there
    assert "<title>Math of ι_Q near 90° - Demo project</title>" in html
    index = json.loads((out / "search/search_index.json").read_text())
    titles = {d["title"] for d in index["docs"] if d["location"].startswith("results/math.html")}
    assert "Math of ι_Q near 90°" in titles and not any("$" in t or "\\(" in t for t in titles)
    # MathJax typesets the $...$ that the theme shows in navigation and header titles
    assert re.search(r'class="md-ellipsis">\s*Math of \$\\iota_Q\$', html)
    assert '["$","$"]' in html and 'processHtmlClass:"arithmatex|md-ellipsis"' in html


def test_bib_reference_style():
    from opsci import bibfmt
    bib = (r'@article{g, author = {G\"{o}del, Kurt and Jean-Luc von Neumann}, journal = {Rev. Mod. Phys.},'
           r' volume = {21}, pages = {447}, year = {1949}}' "\n"
           '@article{p, author = {A and B and C and D}, title = {{A preprint}}, year = {2026}, eprint = {2609.07873},'
           ' archivePrefix = {arXiv}}\n'
           '@misc{d, title = {{Data}}, publisher = {Zenodo}, year = {2025}, url = {https://zenodo.org/records/1}}\n')
    refs = {e.key: bibfmt.reference(e) for e in bibfmt.parse(bib)}
    assert refs["g"] == "K. Gödel and J.-L. von Neumann, Rev. Mod. Phys. 21, 447 (1949)."
    assert refs["p"] == "A et al., *A preprint* (2026), [arXiv:2609.07873](https://arxiv.org/abs/2609.07873)."
    assert refs["d"] == "[*Data*, Zenodo (2025)](https://zenodo.org/records/1)."
