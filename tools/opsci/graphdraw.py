"""Draw the project graph and the claims graph as images: Graphviz places the cards and the
arrows, pdflatex typesets them with TikZ, so titles may hold $LaTeX$.

A drawing is a plain dict (``drawing()``), built by ``mapbuild`` and ``results`` from the node
headers. ``render`` writes ``<base>.svg`` (for the project site and GitHub) and ``<base>.png``
(for Notion). The SVG starts with a comment holding the hash of the drawing, so an image is
redrawn only when its graph changed (``is_current``).

Tools: Graphviz ``dot``, ``pdflatex`` with the TikZ, standalone, lmodern and xcolor
packages, and Poppler's ``pdftocairo`` and ``pdftoppm``. With ``OPSCI_GRAPH_IMAGES=stub``
(the tests set it) ``render`` writes placeholder images that need none of them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

STATUS_COLOURS = {  # fill, stroke
    "active": ("dbeafe", "1d4ed8"), "done": ("dcfce7", "15803d"), "paused": ("fef9c3", "a16207"),
    "failed": ("fee2e2", "b91c1c"), "superseded": ("e5e7eb", "4b5563"), "abandoned": ("e5e7eb", "4b5563"),
}
EDGE_LABELS = {"superseded": "superseded by", "verified": "verified by"}
CARD_TEXT_PT = 142.0  # text width of a card (5 cm)
PAD = 5.0             # space between a card's border and its text
BAR = 3.0             # width of the status bar on a card's left edge
TOOLS = ("dot", "pdflatex", "pdftocairo", "pdftoppm")
SVG_MARK = "<!-- opsci-graph {} -->"
BADGES = {  # the label on a card or box -> its colour, and the text after it in the legend
    "public": ("publicB", "files published"),
    "soft private": ("softB", "named in the public map, files not published"),
    "hard private": ("hardB", "left out of the public map"),
    "not published": ("black!65", "named here; its files are not published"),
}
_SOURCE = Path(__file__).read_bytes()

PREAMBLE = r"""\usepackage[T1]{fontenc}\usepackage[utf8]{inputenc}\usepackage{lmodern}
\usepackage{amsmath,amssymb}\usepackage{xcolor}\usepackage{tikz}
\usetikzlibrary{arrows.meta}
\definecolor{citeS}{HTML}{6d28d9}\definecolor{riskS}{HTML}{dc2626}
\definecolor{brainF}{HTML}{fff7ed}\definecolor{brainS}{HTML}{c2410c}
\definecolor{publicB}{HTML}{15803d}\definecolor{softB}{HTML}{b45309}\definecolor{hardB}{HTML}{b91c1c}
\newcommand\badge[2]{\tikz[baseline=(u.base)]\node[fill=#1,text=white,rounded corners=1.5pt,
  inner sep=1.3pt](u){\scriptsize\textsc{#2}};}"""


class DrawError(Exception):
    pass


# ---------------------------------------------------------------- the drawing

def drawing(cards: list[dict], boxes: list[dict], edges: list[tuple], legend: str) -> dict:
    """A drawing. ``cards``: dicts with ``id``, ``title``, ``meta`` (the line under the
    title), ``status``, and optionally ``box`` (a box id), ``verification`` (drawn with a
    double border), ``at_risk`` (a thick red border), ``tags`` (citation keys),
    ``badge`` (a label on the card: a key of ``BADGES``) and ``rank``: ``premise`` (an assumption or a
    starting point, drawn quieter) or ``milestone`` (drawn stronger). ``boxes``: dicts with ``id``, ``kicker`` (the
    first line of the heading), ``title``, ``style`` (``task`` or ``brainstorm``) and
    optionally ``badge``. ``edges``: ``(from, to, kind)``, with kind
    ``dep`` (an arrow), ``superseded`` (old to new), ``verified`` (checked node to the
    verification task) or ``related`` (a dotted line); an end may be a box id. ``legend``:
    the name of a ``dep`` arrow."""
    return {"cards": cards, "boxes": boxes, "edges": [list(e) for e in edges], "legend": legend}


def digest(d: dict) -> str:
    """The hash of drawing ``d`` and of this module, so that a change in either redraws the image."""
    return hashlib.sha256(_SOURCE + json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def transitive_reduction(edges: list[tuple]) -> list[tuple]:
    """``edges`` without each ``dep`` edge a -> b for which a longer path of ``dep`` edges
    from a to b exists. The other kinds are kept."""
    out = {}
    for a, b, k in edges:
        if k == "dep":
            out.setdefault(a, set()).add(b)

    def reach(start, skip):
        seen, todo = set(), [c for c in out.get(start, ()) if c != skip]
        while todo:
            c = todo.pop()
            if c not in seen:
                seen.add(c)
                todo += out.get(c, ())
        return seen

    return [(a, b, k) for a, b, k in edges if k != "dep" or b not in reach(a, b)]


# ---------------------------------------------------------------- tools

def _tool(name: str) -> str | None:
    beside = Path(sys.executable).parent / name  # the environment opsci runs in
    return str(beside) if beside.is_file() else shutil.which(name)


def missing_tools() -> list[str]:
    return [t for t in TOOLS if _tool(t) is None]


def is_current(base: Path, d: dict) -> bool:
    """Whether ``<base>.svg`` and ``<base>.png`` exist and were drawn from ``d``."""
    svg, png = Path(f"{base}.svg"), Path(f"{base}.png")
    if not (svg.is_file() and png.is_file()):
        return False
    with svg.open(encoding="utf-8", errors="replace") as f:
        head = f.read(300)
    return SVG_MARK.format(digest(d)) in head


# ---------------------------------------------------------------- TeX

def tex_text(t: str) -> str:
    """``t`` as LaTeX: text outside $...$ escaped, math kept."""
    out = []
    for i, chunk in enumerate(re.split(r"(\$[^$]+\$)", str(t or ""))):
        if i % 2:
            out.append(chunk)
            continue
        chunk = re.sub(r"[\\{}&%#_$~^|<>]", lambda m: {
            "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "#": r"\#",
            "_": r"\_", "$": r"\$", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
            "|": r"\textbar{}", "<": r"\textless{}", ">": r"\textgreater{}"}[m.group()], chunk)
        out.append(chunk.replace("·", r"\,\textperiodcentered\,"))
    return "".join(out)


def _plain(t: str) -> str:
    """``t`` with its math shown as text, for a title whose math does not compile."""
    return tex_text(str(t or "").replace("$", "\u0000")).replace("\u0000", r"\$")


def _card_tex(c: dict, plain: bool = False) -> str:
    title = _plain(c["title"]) if plain else tex_text(c["title"])
    s = r"\raggedright{\footnotesize\ttfamily\color{black!55}" + tex_text(c["id"]) + r"\par}"
    if title:
        look = r"\small\color{black!70}" if c.get("rank") == "premise" else ""
        s += r"\vspace{2pt}{" + look + title + r"\par}"
    if c.get("meta"):
        s += r"\vspace{6pt}{\scriptsize\itshape\color{black!60}" + tex_text(c["meta"]) + r"\par}"
    if c.get("tags"):
        s += r"\vspace{3pt}{\scriptsize\ttfamily\color{citeS}[" + tex_text(", ".join(c["tags"])) + r"]\par}"
    return s


def _box_tex(b: dict, plain: bool = False) -> str:
    title = _plain(b.get("title")) if plain else tex_text(b.get("title"))
    colour = r"\color{brainS}\bfseries" if b.get("style") == "brainstorm" else ""
    s = r"{\small" + colour + r"\textsc{" + tex_text(b["kicker"]) + "}}"
    if b.get("badge"):
        s += r"\enspace" + _badge(b["badge"])
    if title:
        s += r"\\{\footnotesize\itshape " + (r"\color{brainS}" if colour else "") + title + "}"
    return r"\raggedright " + s


def _pdflatex(work: Path, name: str, body: str) -> subprocess.CompletedProcess:
    (work / f"{name}.tex").write_text(body, encoding="utf-8")
    env = {**os.environ, "SOURCE_DATE_EPOCH": "0", "FORCE_SOURCE_DATE": "1"}
    return subprocess.run([_tool("pdflatex"), "-interaction=nonstopmode", "-halt-on-error", f"{name}.tex"],
                          cwd=work, capture_output=True, text=True, env=env, timeout=300)


def _measure(work: Path, texts: dict[str, tuple[str, float]]) -> dict[str, tuple[float, float]]:
    """(width, height) in pt of each text, set in a parbox of the given width. A text that
    stops pdflatex is left out."""
    lines = [r"\documentclass{article}", PREAMBLE, r"\newsavebox\bx", r"\begin{document}"]
    for k, (t, w) in texts.items():
        lines.append(r"\sbox\bx{\parbox{%.1fpt}{%s}}\typeout{DIM %s \the\wd\bx\space\the\ht\bx\space\the\dp\bx}"
                     % (w, t, k))
    lines.append(r"\end{document}")
    r = _pdflatex(work, "measure", "\n".join(lines))
    dims = {}
    for m in re.finditer(r"DIM (\S+) ([\d.]+)pt ([\d.]+)pt ([\d.]+)pt", r.stdout):
        dims[m[1]] = (float(m[2]), float(m[3]) + float(m[4]))
    return dims


def _texts(d: dict) -> dict[str, tuple[str, float]]:
    out = {}
    for i, c in enumerate(d["cards"]):
        out[f"c{i}"] = (_card_tex(c), CARD_TEXT_PT)
    for i, b in enumerate(d["boxes"]):
        out[f"b{i}"] = (_box_tex(b), CARD_TEXT_PT + 2 * PAD + BAR)
    for k, lab in EDGE_LABELS.items():
        out[f"e{k}"] = (_edge_label_tex(lab), 80.0)
    return out


def _edge_label_tex(lab: str) -> str:
    return r"\hbox{\scriptsize\itshape\color{black!60}" + lab + "}"


def measure_all(work: Path, d: dict) -> tuple[dict, dict]:
    """The sizes of every text, and the TeX of each (a title whose math stops pdflatex is
    set as plain text instead)."""
    texts = _texts(d)
    dims = _measure(work, texts)
    missing = [k for k in texts if k not in dims]
    if missing:  # measure each alone, and fall back to plain text for the ones that fail
        for k in missing:
            one = _measure(work, {k: texts[k]})
            if k not in one:
                i = int(k[1:])
                texts[k] = ((_card_tex(d["cards"][i], plain=True), CARD_TEXT_PT) if k[0] == "c"
                            else (_box_tex(d["boxes"][i], plain=True), texts[k][1]))
                one = _measure(work, {k: texts[k]})
            if k not in one:
                raise DrawError(f"pdflatex cannot set the label of {k}")
            dims.update(one)
    return dims, {k: t for k, (t, _) in texts.items()}


# ---------------------------------------------------------------- layout

def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _html_box(w: float, h: float) -> str:
    return (f'<<TABLE BORDER="0" CELLPADDING="0" CELLSPACING="0"><TR><TD FIXEDSIZE="TRUE" '
            f'WIDTH="{max(w, 1):.0f}" HEIGHT="{max(h, 1):.0f}"></TD></TR></TABLE>>')


def layout(work: Path, d: dict, dims: dict) -> dict:
    """Graphviz's placement: node centres and sizes, box corners, edge splines, in pt."""
    card_w = CARD_TEXT_PT + 2 * PAD + BAR + 3
    cid = {c["id"]: f"c{i}" for i, c in enumerate(d["cards"])}
    bid = {b["id"]: f"b{i}" for i, b in enumerate(d["boxes"])}
    members = {b["id"]: [c["id"] for c in d["cards"] if c.get("box") == b["id"]] for b in d["boxes"]}
    lines = ["digraph G {", 'rankdir=LR; nodesep=0.3; ranksep=0.6; splines=spline; compound=true; '
             'newrank=true; node [shape=box, fixedsize=true, label=""]; edge [arrowsize=0.5];']
    for b in d["boxes"]:
        w, h = dims[bid[b["id"]]]
        lines.append(f'subgraph cluster_{bid[b["id"]]} {{ margin=14; labeljust=l; label={_html_box(w, h + 4)};')
        for c in members[b["id"]]:
            lines.append(f"  {cid[c]};")
        if not members[b["id"]]:
            lines.append(f"  {bid[b['id']]}_anchor [shape=point, width=0.01];")
        lines.append("}")
    for i, c in enumerate(d["cards"]):
        h = dims[f"c{i}"][1] + 2 * PAD + 2 + (6 if c.get("badge") else 0)  # room for the label
        lines.append(f"c{i} [width={card_w / 72:.4f}, height={h / 72:.4f}];")

    def end(x):  # a card, or a box: an edge to a box goes to one of its cards, cut at the box
        if x in cid:
            return cid[x], None
        m = members.get(x)
        return (cid[m[0]], f"cluster_{bid[x]}") if m else (f"{bid[x]}_anchor", None)

    for i, (a, b, k) in enumerate(d["edges"]):
        (ta, ca), (tb, cb) = end(a), end(b)
        attrs = [f"id=e{i}"]
        if ca:
            attrs.append(f"ltail={ca}")
        if cb:
            attrs.append(f"lhead={cb}")
        if k == "related":
            attrs += ["dir=none", "constraint=false"]
        if k in EDGE_LABELS:
            w, h = dims[f"e{k}"]
            attrs.append(f"label={_html_box(w + 4, h + 2)}")
        lines.append(f"{ta} -> {tb} [{', '.join(attrs)}];")
    lines.append("}")
    r = subprocess.run([_tool("dot"), "-Tjson"], input="\n".join(lines), capture_output=True, text=True,
                       timeout=300)
    if r.returncode:
        raise DrawError("dot failed: " + r.stderr.strip()[-500:])
    g = json.loads(r.stdout)
    pos, bbs, splines = {}, {}, {}
    for o in g.get("objects", []):
        name = o.get("name", "")
        if name.startswith("cluster_"):
            bbs[name[len("cluster_"):]] = tuple(map(float, o["bb"].split(",")))
        elif "pos" in o:
            x, y = map(float, o["pos"].split(","))
            pos[name] = (x, y, float(o["width"]) * 72, float(o["height"]) * 72)
    for e in g.get("edges", []):
        endp, pts = None, []
        for tok in e.get("pos", "").split():
            if tok.startswith("e,"):
                endp = tuple(map(float, tok[2:].split(",")))
            elif not tok.startswith("s,"):
                pts.append(tuple(map(float, tok.split(","))))
        lp = tuple(map(float, e["lp"].split(","))) if "lp" in e else None
        splines[e["id"]] = (pts, endp, lp)
    return {"pos": pos, "bbs": bbs, "splines": splines, "bb": tuple(map(float, g["bb"].split(",")))}


# ---------------------------------------------------------------- TikZ

def _colour_defs() -> list[str]:
    return [r"\definecolor{%sF}{HTML}{%s}\definecolor{%sS}{HTML}{%s}" % (s, f, s, st)
            for s, (f, st) in STATUS_COLOURS.items()]


def _badge(k: str) -> str:
    return r"\badge{%s}{%s}" % (BADGES[k][0], k)


def _status(c: dict) -> str:
    return c.get("status") if c.get("status") in STATUS_COLOURS else "active"


def tikz(d: dict, dims: dict, tex: dict, lay: dict) -> str:
    L = [r"\documentclass[border=8pt]{standalone}", PREAMBLE] + _colour_defs()
    L += [r"\begin{document}", r"\begin{tikzpicture}[x=1pt,y=1pt]"]
    for i, b in enumerate(d["boxes"]):
        x1, y1, x2, y2 = lay["bbs"][f"b{i}"]
        if b.get("style") == "brainstorm":
            L.append(r"\draw[brainS,dash pattern=on 5pt off 3pt,line width=1.1pt,fill=brainF,rounded corners=6pt]"
                     r" (%.1f,%.1f) rectangle (%.1f,%.1f);" % (x1, y1, x2, y2))
        else:
            L.append(r"\draw[black!55,line width=1.1pt,fill=black!4,rounded corners=6pt]"
                     r" (%.1f,%.1f) rectangle (%.1f,%.1f);" % (x1, y1, x2, y2))
    heads = []  # drawn after the arrows, so that an arrow passes under a heading
    for i, b in enumerate(d["boxes"]):
        x1, y1, x2, y2 = lay["bbs"][f"b{i}"]
        fill = "brainF" if b.get("style") == "brainstorm" else "black!4"
        w = min(dims[f"b{i}"][0], x2 - x1 - 16)
        heads.append(r"\node[anchor=north west,inner sep=1.5pt,fill=%s] at (%.1f,%.1f) {\parbox{%.1fpt}{%s}};"
                     % (fill, x1 + 6.5, y2 - 5.5, w, tex[f"b{i}"]))
    for i, (a, b, k) in enumerate(d["edges"]):
        pts, endp, lp = lay["splines"].get(f"e{i}", ([], None, None))
        if not pts:
            continue
        opts = ["black!45", "line width=0.7pt"]
        if k in EDGE_LABELS:
            opts.append("dash pattern=on 3pt off 2pt")
        if k == "related":
            opts.append("densely dotted")
        path = "(%.1f,%.1f)" % pts[0]
        for j in range(1, len(pts) - 2, 3):
            path += " .. controls (%.1f,%.1f) and (%.1f,%.1f) .. (%.1f,%.1f)" % (*pts[j], *pts[j + 1], *pts[j + 2])
        L.append(r"\draw[%s] %s;" % (",".join(opts), path))
        if endp:
            L.append(r"\draw[black!45,line width=0.7pt,-{Stealth[length=5pt,width=4pt]}] (%.1f,%.1f) -- (%.1f,%.1f);"
                     % (*pts[-1], *endp))
        if lp and k in EDGE_LABELS:
            L.append(r"\node[fill=white,inner sep=1pt] at (%.1f,%.1f) {%s};" % (*lp, tex[f"e{k}"]))
    L += heads
    for i, c in enumerate(d["cards"]):
        x, y, w, h = lay["pos"][f"c{i}"]
        s = _status(c)
        rank = c.get("rank")
        border, fill, bar = ["draw=%sS!70" % s, "line width=0.6pt"], "white", "%sS" % s
        if rank == "premise":  # quieter: a thin grey border, a muted bar
            border, fill, bar = ["draw=black!28", "line width=0.4pt"], "black!2", "%sS!40" % s
        if rank == "milestone":  # sharper: a heavy border in the status colour, a light tint
            border, fill = ["draw=%sS" % s, "line width=1.4pt"], "%sF!45" % s
        if c.get("verification"):
            border = ["draw=%sS!80" % s, "double=white", "double distance=1.2pt", "line width=0.5pt"]
        if s == "abandoned":
            border.append("dash pattern=on 3pt off 2pt")
        if c.get("at_risk"):
            border = ["draw=riskS", "line width=1.8pt"]
        L.append(r"\node[%s,fill=%s,rounded corners=3pt,minimum width=%.1fpt,minimum height=%.1fpt,"
                 r"inner sep=0pt] at (%.1f,%.1f) {};" % (",".join(border), fill, w, h, x, y))
        L.append(r"\fill[%s] (%.1f,%.1f) rectangle (%.1f,%.1f);"
                 % (bar, x - w / 2 + 0.9, y - h / 2 + 0.9, x - w / 2 + 0.9 + BAR, y + h / 2 - 0.9))
        L.append(r"\node[anchor=west,inner sep=0pt] at (%.1f,%.1f) {\parbox{%.1fpt}{%s}};"
                 % (x - w / 2 + PAD + BAR + 2, y - (3 if c.get("badge") else 0), CARD_TEXT_PT, tex[f"c{i}"]))
        if c.get("badge"):  # on the top border, at the right
            L.append(r"\node[anchor=east,inner sep=0pt] at (%.1f,%.1f) {%s};" % (x + w / 2 - 6, y + h / 2,
                                                                                 _badge(c["badge"])))
    L.append(_legend(d, lay))
    L += [r"\end{tikzpicture}", r"\end{document}"]
    return "\n".join(L)


def _legend(d: dict, lay: dict) -> str:
    """One line of keys under the graph, wrapped to its width."""
    x1, y1, x2, _ = lay["bb"]
    sw = r"\tikz[baseline=-0.5ex]{\fill[%sS] (0,-4pt) rectangle (2.5pt,4pt);" \
         r"\draw[%sS!70,line width=0.6pt,rounded corners=1.5pt] (0,-4pt) rectangle (12pt,4pt);}~%s"
    arrow = r"\tikz[baseline=-0.5ex]{\draw[black!45,line width=0.7pt%s] (0,0) -- (20pt,0);}~%s"
    items = [sw % (s, s, s) for s in STATUS_COLOURS if any(_status(c) == s for c in d["cards"])]
    kinds = {k for _, _, k in d["edges"]}
    if "dep" in kinds:
        items.append(arrow % (",-{Stealth[length=5pt,width=4pt]}", d["legend"]))
    for k, lab in EDGE_LABELS.items():
        if k in kinds:
            items.append(arrow % (",dash pattern=on 3pt off 2pt,-{Stealth[length=5pt,width=4pt]}", lab))
    if "related" in kinds:
        items.append(arrow % (",densely dotted", "related"))
    if any(c.get("rank") == "milestone" for c in d["cards"]):
        items.append(r"\tikz[baseline=-0.5ex]{\fill[doneF!45,draw=doneS,line width=1.4pt,rounded corners=1.5pt]"
                     r" (0,-4pt) rectangle (12pt,4pt);}~milestone")
    if any(c.get("rank") == "premise" for c in d["cards"]):
        items.append(r"\tikz[baseline=-0.5ex]{\fill[black!2,draw=black!28,line width=0.4pt,rounded corners=1.5pt]"
                     r" (0,-4pt) rectangle (12pt,4pt);}~assumption or starting point")
    if any(c.get("verification") for c in d["cards"]):
        items.append(r"\tikz[baseline=-0.5ex]{\draw[black!60,double=white,double distance=1.2pt,line width=0.5pt,"
                     r"rounded corners=1.5pt] (0,-4pt) rectangle (12pt,4pt);}~verification task")
    if any(c.get("at_risk") for c in d["cards"]):
        items.append(r"\tikz[baseline=-0.5ex]{\draw[riskS,line width=1.8pt,rounded corners=1.5pt]"
                     r" (0,-4pt) rectangle (12pt,4pt);}~may no longer hold")
    used = {x.get("badge") for x in d["cards"] + d["boxes"]}
    items += [_badge(k) + "~" + BADGES[k][1] for k in BADGES if k in used]
    if any(b.get("style") == "brainstorm" for b in d["boxes"]):
        items.append(r"\tikz[baseline=-0.5ex]{\draw[brainS,dash pattern=on 3pt off 2pt,line width=0.9pt,"
                     r"fill=brainF,rounded corners=1.5pt] (0,-4pt) rectangle (12pt,4pt);}~brainstorm: ideas, not yet project work")
    width = max(x2 - x1, 330)
    body = r"\hspace{1.6em}".join(r"\mbox{" + i + "}" for i in items)
    return (r"\node[anchor=north west,inner sep=0pt] at (%.1f,%.1f) {\parbox{%.1fpt}{\footnotesize\raggedright "
            r"\lineskip=5pt %s}};" % (x1, y1 - 12, width, body))


# ---------------------------------------------------------------- render

def _stub(d: dict, base: Path) -> None:
    """Placeholder images for ``d``: an empty SVG with the drawing's hash, a 1x1 PNG."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00\xff")) + chunk(b"IEND", b""))
    base.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{base}.svg").write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + SVG_MARK.format(digest(d))
                                   + '\n<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>\n',
                                   encoding="utf-8")
    Path(f"{base}.png").write_bytes(png)


def render(d: dict, base: Path) -> None:
    """Write ``<base>.svg`` and ``<base>.png`` for drawing ``d``. Raises DrawError."""
    base = Path(base)
    if os.environ.get("OPSCI_GRAPH_IMAGES") == "stub":
        return _stub(d, base)
    miss = missing_tools()
    if miss:
        raise DrawError("missing " + ", ".join(miss) + " (install Graphviz, a LaTeX with pdflatex and "
                        "TikZ, and Poppler)")
    with tempfile.TemporaryDirectory(prefix="opsci-graph-") as tmp:
        work = Path(tmp)
        dims, tex = measure_all(work, d)
        lay = layout(work, d, dims)
        r = _pdflatex(work, "graph", tikz(d, dims, tex, lay))
        if r.returncode or not (work / "graph.pdf").is_file():
            err = [l for l in r.stdout.splitlines() if l.startswith("!")]
            raise DrawError("pdflatex failed on the graph: " + ("; ".join(err[:3]) or r.stdout[-300:]))
        subprocess.run([_tool("pdftocairo"), "-svg", "graph.pdf", "graph.svg"], cwd=work, check=True,
                       capture_output=True, timeout=300)
        subprocess.run([_tool("pdftoppm"), "-png", "-r", "170", "-singlefile", "graph.pdf", "graph"], cwd=work,
                       check=True, capture_output=True, timeout=300)
        svg = (work / "graph.svg").read_text(encoding="utf-8")
        first, _, rest = svg.partition("\n")
        mark = SVG_MARK.format(digest(d))
        svg = f"{first}\n{mark}\n{rest}" if first.startswith("<?xml") else f"{mark}\n{svg}"
        base.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{base}.svg").write_text(svg, encoding="utf-8")
        shutil.copyfile(work / "graph.png", f"{base}.png")
