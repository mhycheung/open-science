"""Tests for the personal projects page (extras/projects-page/).

The filter, sort, YAML reader and rendering are the JavaScript functions in the
`projects-page-logic` block of index.html; these tests extract that block and run it under
Node, so they test the code the browser runs. The page's start-up script (fetch, clicks) is
run the same way against a small stand-in for the browser's document.
"""
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import REPO, run_opsci
from opsci import projects_page as pp

PAGE_DIR = REPO / "extras" / "projects-page"
PAGE_HTML = (PAGE_DIR / "index.html").read_text(encoding="utf-8")
SAMPLE = (PAGE_DIR / "projects.yaml").read_text(encoding="utf-8")
FIX = Path(__file__).parent / "fixtures" / "projects_page"
YAML_FIX = FIX / "yaml"
NODE = shutil.which("node")

LOGIC_RE = re.compile(r'<script id="projects-page-logic">(.*?)</script>', re.S)
START_RE = re.compile(r"<script>(.*?)</script>", re.S)


# ------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def logic(tmp_path_factory):
    if NODE is None:
        pytest.fail("node not found; run the tests with tests/run_all (the pixi env has nodejs)")
    p = tmp_path_factory.mktemp("js") / "logic.js"
    p.write_text(LOGIC_RE.search(PAGE_HTML).group(1))
    return p


def run_js(logic, body, stdin=""):
    code = f"const P = require({json.dumps(str(logic))});\n{body}"
    r = subprocess.run([NODE, "-e", code], input=stdin, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def js_parse(logic, text):
    """Parse with the page's reader; returns {"ok": data} or {"error": message}."""
    return run_js(logic, """
let s = ""; process.stdin.on("data", d => s += d).on("end", () => {
  try { console.log(JSON.stringify({ok: P.parseYaml(s)})); }
  catch (e) { if (!(e instanceof P.YamlError)) throw e; console.log(JSON.stringify({error: e.message})); }
});""", stdin=text)


def js_view(logic, text, state):
    return run_js(logic, f"""
let s = ""; process.stdin.on("data", d => s += d).on("end", () => {{
  const data = P.normalize(P.parseYaml(s));
  const v = P.view(data, {json.dumps(state)});
  console.log(JSON.stringify({{titles: v.shown.map(p => p.title), tags: v.tags,
                               html: P.renderProjects(v.shown, {json.dumps(state["tags"])}),
                               count: P.countText(v)}}));
}});""", stdin=text)


def as_page_reads(x):
    """PyYAML's result in the form the page's reader gives: every scalar is text."""
    if isinstance(x, dict):
        return {k: as_page_reads(v) for k, v in x.items()}
    if isinstance(x, list):
        return [as_page_reads(v) for v in x]
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return str(x)
    return x


def expectation(path, prefix):
    m = re.search(rf"{re.escape(prefix)}\s*expect:\s*(.+?)\s*(\*/|-->)?\s*$", path.read_text(), re.M)
    assert m, f"{path.name} has no 'expect:' line"
    return m.group(1)


def yaml_fixtures(kind):
    return sorted(YAML_FIX.glob(f"{kind}_*.yaml"))


def to_yaml(x, indent=0):
    """Write data in the YAML subset the page reads: block style, strings in double quotes
    (a JSON string is a valid YAML double-quoted string)."""
    pad = " " * indent
    if isinstance(x, dict):
        out = []
        for k, v in x.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:\n" + to_yaml(v, indent + 2))
            else:
                out.append(f"{pad}{k}: " + ("[]" if v == [] else json.dumps(v)) + "\n")
        return "".join(out)
    return "".join(f"{pad}-\n" + to_yaml(v, indent + 2) if isinstance(v, dict)
                   else f"{pad}- {json.dumps(v)}\n" for v in x)


def with_projects(entries, intro=""):
    return to_yaml({"title": "Projects", "intro": intro, "projects": entries})


FOUR = [
    {"title": "Beta", "description": "", "tags": ["x", "y"], "status": "paused", "updated": "2024-05"},
    {"title": "alpha", "description": "", "tags": ["y"], "status": "archived", "updated": "2026-01"},
    {"title": "Gamma", "description": "", "tags": ["x", "y", "z"], "status": "active"},
    {"title": "Delta", "description": "", "tags": [], "status": "finished", "updated": "2025"},
]


# ------------------------------------------------------------------------------------------
# projects.yaml validation
# ------------------------------------------------------------------------------------------

def test_sample_projects_yaml_passes():
    assert pp.validate_projects_text(SAMPLE) == []


def test_valid_fixture_passes():
    assert pp.validate_projects_text((YAML_FIX / "good_features.yaml").read_text()) == []


@pytest.mark.parametrize("path", yaml_fixtures("bad"), ids=lambda p: p.stem)
def test_malformed_yaml_refused(path):
    errors = pp.validate_projects_text(path.read_text())
    want = expectation(path, "#")
    assert any(want in e for e in errors), errors


def test_cli_check(tmp_path):
    ok = run_opsci("projects-page", "check", PAGE_DIR)
    assert ok.returncode == 0, ok.stderr
    bad_data = tmp_path / "bad_data"
    shutil.copytree(PAGE_DIR, bad_data)
    shutil.copy(YAML_FIX / "bad_status.yaml", bad_data / "projects.yaml")
    r = run_opsci("projects-page", "check", bad_data)
    assert r.returncode == 1 and "'status' is 'done'" in r.stderr
    bad_style = tmp_path / "bad_style"
    shutil.copytree(PAGE_DIR, bad_style)
    (bad_style / "index.html").write_text(PAGE_HTML.replace("</style>", "body{background:#fdf6e3}</style>"))
    r = run_opsci("projects-page", "check", bad_style)
    assert r.returncode == 1 and pp.CREAM in r.stderr


def test_sample_ships_with_empty_intro_and_descriptions():
    data = yaml.safe_load(SAMPLE)
    assert not data["intro"]
    assert data["projects"] and all(not p["description"] for p in data["projects"])
    # control: the same check notices a filled-in description
    filled = yaml.safe_load(with_projects([dict(FOUR[0], description="Written by someone.")]))
    assert any(p["description"] for p in filled["projects"])


# ------------------------------------------------------------------------------------------
# the page's YAML reader
# ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["sample", "good_features"])
def test_page_reader_matches_pyyaml(logic, name):
    text = SAMPLE if name == "sample" else (YAML_FIX / "good_features.yaml").read_text()
    got = js_parse(logic, text)
    assert got == {"ok": as_page_reads(yaml.safe_load(text))}


SUBSET_BAD = [p for p in yaml_fixtures("bad") if "# js: refuse" in p.read_text()]


def test_there_are_subset_fixtures():
    assert len(SUBSET_BAD) >= 5


@pytest.mark.parametrize("path", SUBSET_BAD, ids=lambda p: p.stem)
def test_page_reader_refuses_what_validator_refuses(logic, path):
    got = js_parse(logic, path.read_text())
    assert "error" in got, got
    assert re.match(r"line \d+: ", got["error"])


# ------------------------------------------------------------------------------------------
# filtering and sorting (the page's own functions)
# ------------------------------------------------------------------------------------------

def test_no_tags_selected_shows_everything(logic):
    v = js_view(logic, with_projects(FOUR), {"tags": [], "mode": "any", "sort": "title"})
    assert v["titles"] == ["alpha", "Beta", "Delta", "Gamma"]
    assert v["count"] == "4 projects"
    assert [t["tag"] for t in v["tags"]] == ["x", "y", "z"]
    assert [t["count"] for t in v["tags"]] == [2, 3, 1]


def test_filter_any_and_all(logic):
    text = with_projects(FOUR)
    any_ = js_view(logic, text, {"tags": ["x", "z"], "mode": "any", "sort": "title"})
    all_ = js_view(logic, text, {"tags": ["x", "z"], "mode": "all", "sort": "title"})
    assert any_["titles"] == ["Beta", "Gamma"]
    assert all_["titles"] == ["Gamma"]
    assert all_["count"] == "Showing 1 of 4 projects"
    assert [t["selected"] for t in all_["tags"]] == [True, False, True]


def test_filter_with_no_match_shows_message(logic):
    v = js_view(logic, with_projects(FOUR), {"tags": ["x", "q"], "mode": "all", "sort": "title"})
    assert v["titles"] == []
    assert "No project matches" in v["html"]


@pytest.mark.parametrize("sort,tags,want", [
    ("title", [], ["alpha", "Beta", "Delta", "Gamma"]),
    # newest first; Gamma has no date and goes last
    ("updated", [], ["alpha", "Delta", "Beta", "Gamma"]),
    ("status", [], ["Gamma", "Beta", "Delta", "alpha"]),
    # most selected tags first, ties by date
    ("match", ["x", "z"], ["Gamma", "Beta"]),
    ("match", ["y"], ["alpha", "Beta", "Gamma"]),
])
def test_sorting(logic, sort, tags, want):
    v = js_view(logic, with_projects(FOUR), {"tags": tags, "mode": "any", "sort": sort})
    assert v["titles"] == want


def test_state_in_url(logic):
    got = run_js(logic, """
const s = P.parseState("#tags=x,nope,x&mode=all&sort=bogus", ["x", "y"]);
console.log(JSON.stringify({s, back: P.formatState(s), none: P.formatState(P.parseState("", null)),
  toggled: P.toggleTag(P.toggleTag(s, "y"), "x")}));""")
    assert got["s"] == {"tags": ["x"], "mode": "all", "sort": "match"}  # unknown tag and sort dropped
    assert got["back"] == "#tags=x&mode=all"
    assert got["none"] == ""
    assert got["toggled"]["tags"] == ["y"]


# ------------------------------------------------------------------------------------------
# rendering
# ------------------------------------------------------------------------------------------

def test_renders_with_empty_description(logic):
    v = js_view(logic, SAMPLE, {"tags": [], "mode": "any", "sort": "match"})
    assert v["html"].count('<article class="project">') == 4
    assert v["html"].count("<h2>") == 4
    assert 'class="description"' not in v["html"]
    # control: a written description is shown, one paragraph per blank-line-separated block
    text = with_projects([dict(FOUR[0], description="First part.\n\nSecond part.\n")])
    v = js_view(logic, text, {"tags": [], "mode": "any", "sort": "match"})
    assert '<div class="description"><p>First part.</p><p>Second part.</p></div>' in v["html"]


def test_values_are_escaped_and_links_checked(logic):
    entry = dict(FOUR[0], title="<script>alert(1)</script>", tags=['a"b'],
                 links={"repo": "javascript:alert(1)", "site": "https://example.org/a?b=1&c=2",
                        "doi": "10.5281/zenodo.42"})
    v = js_view(logic, with_projects([entry]), {"tags": [], "mode": "any", "sort": "match"})
    assert "<script>" not in v["html"] and "&lt;script&gt;" in v["html"]
    assert 'data-tag="a&quot;b"' in v["html"]
    assert "javascript:" not in v["html"]
    assert 'href="https://example.org/a?b=1&amp;c=2"' in v["html"]
    assert 'href="https://doi.org/10.5281/zenodo.42"' in v["html"]


PAGE_STUB = """
const els = {};
function el(id) {
  if (!els[id]) els[id] = {id, innerHTML: "", textContent: "", hidden: false, value: "",
                           listeners: {}, addEventListener(t, f) { this.listeners[t] = f; }};
  return els[id];
}
el("intro").hidden = true; el("controls").hidden = true;   // as in the HTML
const docListeners = {};
global.window = {ProjectsPage: P, addEventListener() {}};
global.document = {getElementById: el, title: "", addEventListener(t, f) { docListeners[t] = f; }};
global.location = {protocol: CFG.protocol, hash: CFG.hash, pathname: "/projects/", search: ""};
global.history = {replaceState(a, b, url) { location.hash = url.startsWith("#") ? url : ""; }};
global.fetch = () => Promise.resolve(CFG.status === 200
  ? {ok: true, status: 200, text: () => Promise.resolve(CFG.yaml)} : {ok: false, status: CFG.status});
const snap = () => ({title: document.title, h1: el("page-title").textContent,
  intro: el("intro").innerHTML, introHidden: el("intro").hidden, controlsHidden: el("controls").hidden,
  projects: el("projects").innerHTML, count: el("count").textContent, filter: el("tag-filter").innerHTML,
  hash: location.hash, sort: el("sort").value, mode: el("mode").value});
eval(CFG.script);
setTimeout(() => {
  const out = {start: snap()};
  if (docListeners.click) {
    const button = {getAttribute: () => CFG.click};
    docListeners.click({target: {closest: () => button}});
    el("mode").listeners.change({target: {value: "all"}});
    out.after = snap();
  }
  console.log(JSON.stringify(out));
}, 50);
"""


def run_page(logic, yaml_text, *, hash="", status=200, protocol="https:", click="x"):
    cfg = {"yaml": yaml_text, "hash": hash, "status": status, "protocol": protocol,
           "click": click, "script": START_RE.search(PAGE_HTML).group(1)}
    return run_js(logic, f"const CFG = {json.dumps(cfg)};\n{PAGE_STUB}")


def test_page_starts_with_empty_intro_and_descriptions(logic):
    out = run_page(logic, SAMPLE, click="ecology")["start"]
    assert out["h1"] == "Projects" and out["title"] == "Projects"
    assert out["introHidden"] is True and out["intro"] == ""
    assert out["controlsHidden"] is False
    assert out["projects"].count("<article") == 4 and 'class="description"' not in out["projects"]
    assert out["count"] == "4 projects"
    # control: a written intro is shown
    data = yaml.safe_load(SAMPLE)
    data["intro"] = "I work on small things."
    out = run_page(logic, to_yaml(data))["start"]
    assert out["introHidden"] is False and out["intro"] == "<p>I work on small things.</p>"


def test_page_clicks_and_url_state(logic):
    out = run_page(logic, with_projects(FOUR), hash="#sort=title", click="z")
    assert out["start"]["sort"] == "title"
    assert out["start"]["projects"].index("alpha") < out["start"]["projects"].index("Beta")
    after = out["after"]
    assert after["hash"] == "#tags=z&mode=all&sort=title"
    assert after["count"] == "Showing 1 of 4 projects" and "Gamma" in after["projects"]
    assert 'data-tag="z" aria-pressed="true"' in after["filter"]


def test_page_reports_load_errors(logic):
    out = run_page(logic, "", status=404)["start"]
    assert 'class="error"' in out["projects"] and "HTTP 404" in out["projects"]
    out = run_page(logic, (YAML_FIX / "bad_colon.yaml").read_text())["start"]
    assert "line 4:" in out["projects"]
    out = run_page(logic, SAMPLE, protocol="file:")["start"]
    assert "http.server" in out["projects"]


# ------------------------------------------------------------------------------------------
# style check
# ------------------------------------------------------------------------------------------

CATEGORIES = [pp.CREAM, pp.ITALIC, pp.NUMBERED, pp.MONO, pp.PILL]


def plant(path):
    """The shipped page with one fixture added in the place its file type belongs."""
    text = path.read_text()
    if path.suffix == ".css":
        return PAGE_HTML.replace("</style>", text + "\n</style>", 1)
    if path.suffix == ".html":
        return PAGE_HTML.replace("<main>", "<main>\n" + text, 1)
    if path.suffix == ".js":
        return PAGE_HTML.replace("</body>", f"<script>\n{text}\n</script>\n</body>", 1)
    raise ValueError(path)


def categories(problems):
    return {c for c in CATEGORIES for p in problems if p.startswith(c + ":")}


def test_shipped_page_passes_style_check():
    assert pp.check_style(PAGE_HTML) == []


REFUSE = sorted((FIX / "style" / "refuse").iterdir())
PASS = sorted((FIX / "style" / "pass").iterdir())


def test_every_banned_element_has_a_planted_violation():
    prefixes = {"cream": pp.CREAM, "italic": pp.ITALIC, "numbered": pp.NUMBERED,
                "mono": pp.MONO, "pill": pp.PILL}
    assert {prefixes[p.name.split("_")[0]] for p in REFUSE} == set(CATEGORIES)


@pytest.mark.parametrize("path", REFUSE, ids=lambda p: p.name)
def test_planted_violation_refused(path):
    want = expectation(path, {".css": "/*", ".html": "<!--", ".js": "//"}[path.suffix])
    problems = pp.check_style(plant(path))
    assert categories(problems) == {want}, problems


@pytest.mark.parametrize("path", PASS, ids=lambda p: p.name)
def test_near_miss_passes(path):
    assert pp.check_style(plant(path)) == []


# ------------------------------------------------------------------------------------------
# AGENTS.md
# ------------------------------------------------------------------------------------------

def style_sentence():
    text = (REPO / "docs" / "design" / "ORIGINAL_REQUEST.md").read_text(encoding="utf-8")
    return re.search(r"“(Do not use a cream.*?)”", text).group(1)


def has_sentence(agents_text, sentence):
    return sentence in agents_text.splitlines()


def test_agents_md_has_style_sentence_verbatim():
    agents = (PAGE_DIR / "AGENTS.md").read_text(encoding="utf-8")
    sentence = style_sentence()
    assert sentence.startswith("Do not use a cream") and sentence.endswith("pill-shaped buttons.")
    assert has_sentence(agents, sentence)
    # control: a near copy (curly quotes instead of straight ones) is not accepted
    assert not has_sentence(agents.replace('"01/02/03"', "“01/02/03”"), sentence)


def test_agents_md_says_descriptions_are_the_humans():
    agents = (PAGE_DIR / "AGENTS.md").read_text(encoding="utf-8")
    assert "ship empty" in agents and "must not write" in agents
