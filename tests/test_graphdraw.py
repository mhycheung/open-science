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
    assert not re.search(r"/home|/tmp|/anvil", svg)  # no local path in the image
