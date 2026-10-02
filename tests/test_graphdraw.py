"""The graph images of `opsci map build` (opsci.graphdraw): the drawing, its LaTeX, and, where
Graphviz, pdflatex and Poppler are installed, a real render."""

import re

import pytest

from opsci import graphdraw as G

CARDS = [
    {"id": "t01-noise", "title": r"Noise PSD $S_n(f)$ with 50% & #1_x", "meta": "task · done",
     "status": "done", "box": "brainstorm", "badge": "public"},
    {"id": "t02-fit", "title": "Fit", "meta": "task · superseded", "status": "superseded", "badge": "soft private"},
    {"id": "t03-fit-v2", "title": r"Fit, bad math $\frac{1}{$", "meta": "task · active", "status": "active",
     "at_risk": True, "tags": ["Isi2019"], "badge": "hard private"},
    {"id": "v01-audit", "title": "Audit", "meta": "verification · active", "status": "active",
     "verification": True, "badge": "not published"},
]
BOXES = [{"id": "brainstorm", "kicker": "brainstorm", "title": "ideas, not yet project work", "style": "brainstorm",
          "badge": "soft private"}]
EDGES = [("t01-noise", "t02-fit", "dep"), ("t01-noise", "t03-fit-v2", "dep"), ("t02-fit", "t03-fit-v2", "superseded"),
         ("t03-fit-v2", "v01-audit", "verified"), ("t02-fit", "v01-audit", "related")]


def drawing():
    return G.drawing(CARDS, BOXES, EDGES, "used by")


def test_tex_text_escapes_text_and_keeps_math():
    assert G.tex_text(r"50% & $S_n(f)$ #1_x") == r"50\% \& $S_n(f)$ \#1\_x"
    assert G.tex_text("costs $5") == r"costs \$5"  # a lone $ is text


def test_transitive_reduction_drops_implied_dependencies_only():
    edges = [("a", "b", "dep"), ("b", "c", "dep"), ("a", "c", "dep"), ("a", "c", "superseded")]
    assert G.transitive_reduction(edges) == [("a", "b", "dep"), ("b", "c", "dep"), ("a", "c", "superseded")]


def test_stub_images_carry_the_drawing_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSCI_GRAPH_IMAGES", "stub")
    d = drawing()
    assert not G.is_current(tmp_path / "graph", d)
    G.render(d, tmp_path / "graph")
    assert G.is_current(tmp_path / "graph", d)
    assert (tmp_path / "graph.png").read_bytes().startswith(b"\x89PNG")
    assert G.SVG_MARK.format(G.digest(d)) in (tmp_path / "graph.html").read_text()
    changed = G.drawing(CARDS[:2], BOXES, EDGES[:1], "used by")
    assert not G.is_current(tmp_path / "graph", changed)  # a changed graph is out of date


@pytest.mark.skipif(bool(G.missing_tools()), reason="needs Graphviz, pdflatex and Poppler")
def test_real_render_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.delenv("OPSCI_GRAPH_IMAGES", raising=False)
    d = drawing()
    G.render(d, tmp_path / "a")  # the title whose math does not compile is set as text
    G.render(d, tmp_path / "b")
    svg = (tmp_path / "a.svg").read_text()
    assert svg.startswith("<?xml") and G.SVG_MARK.format(G.digest(d)) in svg
    assert G.is_current(tmp_path / "a", d)
    assert (tmp_path / "a.svg").read_bytes() == (tmp_path / "b.svg").read_bytes()
    assert (tmp_path / "a.png").read_bytes() == (tmp_path / "b.png").read_bytes()
    assert (tmp_path / "a.html").read_bytes() == (tmp_path / "b.html").read_bytes()
    assert not re.search(r"/home|/tmp|/anvil", svg)  # no local path in the image


@pytest.mark.skipif(G._tool("dot") is None, reason="needs Graphviz")
def test_layout_runs_left_to_right(tmp_path):
    cards = [{"id": f"n{i}", "title": "", "meta": "", "status": "done"} for i in range(6)]
    chain = G.drawing(cards, [], [(f"n{i}", f"n{i + 1}", "dep") for i in range(5)], "used by")
    dims = {f"c{i}": (G.CARD_TEXT_PT, 30.0) for i in range(6)}
    lay = G.layout(tmp_path, chain, dims)
    assert lay["rankdir"] == "LR"  # even a long chain is drawn left to right
    x1, y1, x2, y2 = lay["bb"]
    assert x2 - x1 > y2 - y1


def test_interactive_view_holds_the_drawing_and_takes_page_links():
    from opsci import graphview
    cards = [dict(CARDS[0], summary="The noise.", href="../tasks/t01-noise/context.md"), CARDS[1]]
    d = G.drawing(cards, BOXES, EDGES[:1], "used by")
    lay = {"bb": (0, 0, 400, 200), "pos": {"c0": (100, 150, 160, 40), "c1": (300, 50, 160, 40)},
           "bbs": {"b0": (10, 100, 190, 195)},
           "splines": {"e0": ([(100, 130), (150, 100), (250, 80), (300, 70)], (300, 72), None)}}
    html = graphview.page(d, lay, {"done": ["dcfce7", "15803d"]}, G.EDGE_LABELS, "<!-- m -->")
    D = __import__("json").loads(graphview.DATA_RE.search(html).group(2))
    c0 = D["cards"][0]
    assert (c0["x"], c0["y"], c0["w"], c0["h"]) == (36.0, 46.0, 160, 40)  # top left, y down, margin 16
    assert c0["summary"] == "The noise." and c0["href"] == "../tasks/t01-noise/context.md"
    assert D["edges"][0]["d"].startswith("M116.0 86.0 C") and D["edges"][0]["d"].endswith("L316.0 144.0")
    assert "</script>" not in graphview.DATA_RE.search(html).group(2)
    linked = graphview.with_links(html, {"t01-noise": "https://www.notion.so/abc"})
    L = __import__("json").loads(graphview.DATA_RE.search(linked).group(2))
    assert L["cards"][0]["url"] == "https://www.notion.so/abc" and "href" not in L["cards"][0]
    assert "url" not in L["cards"][1] and L["onlyUrls"] is True  # no page: no link at all
