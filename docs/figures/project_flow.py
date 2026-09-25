"""The overview figure of docs/index.md, written as SVG by hand.

    python3 docs/figures/project_flow.py            # writes project_flow.svg next to this file
    pixi exec -s librsvg rsvg-convert docs/figures/project_flow.svg -o /tmp/project_flow.png
"""
from pathlib import Path

OUT = Path(__file__).with_name("project_flow.svg")
FS = 1.25                                  # scale of every font size
FONT = "Inter, 'Helvetica Neue', Helvetica, Arial, 'Nimbus Sans', sans-serif"

INK, MUTED, LINE = "#1f2937", "#6b7280", "#94a3b8"
PRIV = dict(fill="#fef2f2", edge="#ef4444", head="#991b1b", soft="#fecaca")
PUB = dict(fill="#ecfdf5", edge="#10b981", head="#065f46", soft="#a7f3d0")
ZEN = dict(fill="#eff6ff", edge="#3b82f6", head="#1e40af", soft="#bfdbfe")
WEB = dict(fill="#f5f3ff", edge="#8b5cf6", head="#5b21b6", soft="#ddd6fe")
GHOST = dict(fill="#f9fafb", edge="#9ca3af", head="#9ca3af", soft="#e5e7eb")
GATE, GATE_BG, GATE_INK = "#d97706", "#fffbeb", "#92400e"
CTX, CTX_SOFT = "#f59e0b", "#fde68a"       # context files = context management
PM = "#475569"                             # project management
N_PUB, N_PRIV, N_DEAD, N_ROOT = "#10b981", "#ef4444", "#9ca3af", "#334155"

out = []


def add(s):
    out.append(s)


def rect(x, y, w, h, fill, stroke, r=10, sw=1.5, dash=None, extra=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{sw}"{d} {extra}/>')


def text(x, y, s, size=14, color=INK, weight=400, anchor="middle", extra=""):
    add(f'<text x="{x}" y="{y}" font-size="{size * FS:.1f}" fill="{color}" font-weight="{weight}" '
        f'text-anchor="{anchor}" dominant-baseline="central" {extra}>{s}</text>')


def arrow(x1, y1, x2, y2, color, sw=1.8, dash=None, head=True):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    m = f' marker-end="url(#h{color[1:]})"' if head else ""
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"{d}{m}/>')


def badge(x_right, y, label, public):
    fg, bg = ("#047857", "#d1fae5") if public else ("#b91c1c", "#fee2e2")
    w = 8 * FS * len(label) + 18
    rect(x_right - w, y - 11, w, 22, bg, fg, r=11, sw=1)
    text(x_right - w / 2, y + 0.5, label, 11, fg, 700, extra='letter-spacing="0.6"')


def node(x, y, c, r=9, label=""):
    if c == N_DEAD:
        add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="white" stroke="{c}" stroke-width="1.6" '
            f'stroke-dasharray="3 2.2"/>')
        text(x, y, "×", r + 3, c, 700)
    else:
        add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}"/>')
        if label:
            text(x, y + 0.5, label, 10, "white", 700)


def graph(nodes, links, r=9):
    for a, b in links:
        (x1, y1), (x2, y2) = nodes[a][:2], nodes[b][:2]
        dx, dy = x2 - x1, y2 - y1
        n = (dx * dx + dy * dy) ** 0.5
        arrow(x1 + dx / n * r, y1 + dy / n * r, x2 - dx / n * (r + 3), y2 - dy / n * (r + 3), LINE, 1.4)
    for x, y, c, *lab in nodes.values():
        node(x, y, c, r, lab[0] if lab else "")


def page(x, y, w=46, h=60, accent=PM, lines=4):
    f = 12
    add(f'<path d="M{x},{y} h{w - f} l{f},{f} v{h - f} h{-w} z" fill="white" stroke="{accent}" stroke-width="1.5"/>')
    add(f'<path d="M{x + w - f},{y} v{f} h{f}" fill="none" stroke="{accent}" stroke-width="1.5"/>')
    return f


def context_doc(x, y):
    page(x, y, accent=CTX)
    add(f'<rect x="{x + 1}" y="{y + 1}" width="33" height="10" fill="{CTX_SOFT}"/>')
    for i, L in enumerate((30, 34, 26)):
        add(f'<line x1="{x + 8}" y1="{y + 24 + 10 * i}" x2="{x + 8 + L}" y2="{y + 24 + 10 * i}" '
            f'stroke="{CTX}" stroke-width="2.2" stroke-linecap="round" opacity="0.55"/>')


def log_doc(x, y):
    page(x, y, accent=PM)
    for i in range(4):
        yy = y + 20 + 10 * i
        add(f'<circle cx="{x + 10}" cy="{yy}" r="2.2" fill="{PM}"/>')
        add(f'<line x1="{x + 16}" y1="{yy}" x2="{x + 36 - (6 if i % 2 else 0)}" y2="{yy}" '
            f'stroke="#cbd5e1" stroke-width="2.2" stroke-linecap="round"/>')


def caption(x, y, s):
    text(x, y, s, 12, MUTED)


def panel(x, y, w, h, c, title, tag=None, public=True, ghost=False):
    rect(x, y, w, h, GHOST["fill"] if ghost else "white", c["edge"], r=10, sw=1.3,
         dash="6 4" if ghost else None)
    text(x + 16, y + 24, title, 15, GHOST["head"] if ghost else c["head"], 700, "start")
    if tag:
        badge(x + w - 12, y + 24, tag, public)


CAP = 186          # caption offset inside a panel


def task_items(x, y, private=False):
    col = N_PRIV if private else N_PUB
    graph({"p": (x + 44, y + 72, col), "r": (x + 44, y + 142, col), "d": (x + 92, y + 107, N_DEAD)},
          [("p", "r"), ("p", "d")])
    caption(x + 62, y + CAP, "graph")
    context_doc(x + 128, y + 76)
    caption(x + 151, y + CAP, "context")
    log_doc(x + 194, y + 76)
    caption(x + 217, y + CAP, "log")


def project_items(x, y, with_b=True):
    # A and C run in sequence (C builds on A); B is a separate branch
    n = {"Q": (x + 100, y + 66, N_ROOT), "A": (x + 40, y + 108, N_PUB, "A"),
         "C": (x + 112, y + 108, N_PUB, "C"), "a1": (x + 22, y + 150, N_PUB),
         "a2": (x + 58, y + 150, N_DEAD), "c1": (x + 112, y + 150, N_PUB)}
    L = [("Q", "A"), ("A", "C"), ("A", "a1"), ("A", "a2"), ("C", "c1")]
    if not with_b:                       # centre the graph once B's branch is gone
        n = {k: (v[0] + 34, *v[1:]) for k, v in n.items()}
    if with_b:
        n.update({"B": (x + 180, y + 108, N_PRIV, "B"), "b1": (x + 180, y + 150, N_PRIV)})
        L += [("Q", "B"), ("B", "b1")]
    graph(n, L)
    caption(x + 100, y + CAP, "graph")
    context_doc(x + 222, y + 76)
    caption(x + 245, y + CAP, "context")
    log_doc(x + 292, y + 76)
    caption(x + 315, y + CAP, "log")


def row_box(x, y, w, h, c, title, sub):
    rect(x, y, w, h, c["fill"], c["edge"], r=16, sw=2)
    sub = (f'<tspan dx="14" font-size="{14 * FS:.1f}" font-weight="400" fill="{MUTED}">{sub}</tspan>'
           if sub else "")
    text(x + 22, y + 30, title + sub, 21, c["head"], 700, "start")


def browser(x, y, w, h, title):
    rect(x, y, w, h, "white", WEB["edge"], r=9, sw=1.3)
    add(f'<path d="M{x},{y + 28} v-19 a9,9 0 0 1 9,-9 h{w - 18} a9,9 0 0 1 9,9 v19 z" '
        f'fill="{WEB["soft"]}" opacity="0.6"/>')
    add(f'<line x1="{x}" y1="{y + 28}" x2="{x + w}" y2="{y + 28}" stroke="{WEB["edge"]}" stroke-width="1"/>')
    for i, c in enumerate(("#f87171", "#fbbf24", "#34d399")):
        add(f'<circle cx="{x + 14 + 13 * i}" cy="{y + 14}" r="4" fill="{c}"/>')
    text(x + 58, y + 14.5, title, 13, WEB["head"], 700, "start")


# ------------------------------------------------------------------ layout
X0 = 96                                  # left edge of the rows; the skill bars sit left of it
PXL = X0 + 16                            # the project box, which holds the tasks box
TX = PXL + 364                           # the tasks box
CARD = 262
PX = dict(A=(TX + 14, CARD), B=(TX + 30 + CARD, CARD), C=(TX + 46 + 2 * CARD, CARD))
TW = PX["C"][0] + CARD + 14 - TX
PW = TX + TW + 14 - PXL
ROW_W = PW + 32
SIDE = (X0 + ROW_W + 26, 220)            # data / Zenodo column
PROJ_CX = PXL + 270                      # where arrows leave and enter the project box
R1 = (40, 370)                           # (top, height)
BAND = (R1[0] + R1[1] + 30, 44)
R2 = (BAND[0] + BAND[1] + 30, 348)
R3 = (R2[0] + R2[1] + 60, 280)
W, H = SIDE[0] + SIDE[1] + 30, R3[0] + R3[1] + 20

add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    f'font-family="{FONT}">')
add("<defs>")
for c in {LINE, GATE, ZEN["edge"], WEB["edge"], N_PRIV, PUB["edge"]}:
    add(f'<marker id="h{c[1:]}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>')
add("</defs>")
add(f'<rect width="{W}" height="{H}" fill="white"/>')


def project_box(y0, h, c, item_dy, card_h, card_dy, tasks):
    """The project box: project-level items on the left, a tasks box holding the task cards."""
    panel(PXL, y0, PW, h, c, "Project")
    project_items(PXL, y0 + item_dy, with_b=any(k == "B" and not ghost for k, _, ghost in tasks))
    ty, th = y0 + 14, h - 28
    rect(TX, ty, TW, th, c["fill"], c["edge"], r=10, sw=1, dash="5 4")
    text(TX + 16, ty + 20, "Tasks", 14, c["head"], 700, "start")
    for k, pub, ghost in tasks:
        x, w = PX[k]
        cy = ty + 36
        if ghost:
            panel(x, cy, w, card_h, GHOST, f"Task {k}", ghost=True)
            text(x + w / 2, cy + card_h / 2 + 6, "not published", 14, GHOST["head"], 600)
        else:
            panel(x, cy, w, card_h, c, f"Task {k}", "PUBLIC" if pub else "PRIVATE", pub)
            task_items(x, cy + card_dy, private=not pub)


# row 1: private repository (includes data/)
y, h = R1
row_box(X0, y, SIDE[0] + SIDE[1] + 18 - X0, h, PRIV, "Private repository", "where you work")
P1 = (y + 52, 300)
project_box(P1[0], P1[1], PRIV, 30, 222, 0, [("A", True, False), ("B", False, False), ("C", True, False)])
x, w = SIDE
panel(x, P1[0], w, P1[1], PRIV, "Data")
cx, cy = x + w / 2, P1[0] + 150
for i in range(3):                       # a stack of three disks
    yy = cy + 34 - 28 * i
    add(f'<path d="M{cx - 44},{yy - 10} v20 a44,12 0 0 0 88,0 v-20" fill="white" stroke="{PM}" stroke-width="1.5"/>')
    add(f'<ellipse cx="{cx}" cy="{yy - 10}" rx="44" ry="12" fill="white" stroke="{PM}" stroke-width="1.5"/>')

# the filter
by, bh = BAND
rect(X0 + 16, by, ROW_W - 32, bh, GATE_BG, GATE, r=bh / 2, sw=2)
text(X0 + ROW_W / 2, by + bh / 2, "opsci publish filter", 16, GATE_INK, 700)
# arrows leave the task cards and the project-level items, and enter the same in row 2
CARD_TOP = 14 + 36                       # card top, from the top of the project box
card_bottom = P1[0] + CARD_TOP + 222 + 3
item_bottom = P1[0] + P1[1] + 3
EXPORTED = ((PROJ_CX, item_bottom), (PX["A"][0] + CARD / 2, card_bottom), (PX["C"][0] + CARD / 2, card_bottom))
for xc, y0 in EXPORTED:
    arrow(xc, y0, xc, by - 2, GATE, 2)
xb = PX["B"][0] + CARD / 2
arrow(xb, card_bottom, xb, by - 2, N_PRIV, 2)
text(xb, by + bh + 13, "✕ not exported", 13, "#b91c1c", 700)

# row 2: public repository
y, h = R2
row_box(X0, y, ROW_W, h, PUB, "Public repository", "")
project_box(y + 52, 278, PUB, 12, 200, -22, [("A", True, False), ("B", True, True), ("C", True, False)])
for (xc, _), y1 in zip(EXPORTED, (y + 52, y + 52 + CARD_TOP, y + 52 + CARD_TOP)):
    arrow(xc, by + bh + 2, xc, y1 - 3, GATE, 2)

# Zenodo
x, w = SIDE
rect(x, y, w, h, ZEN["fill"], ZEN["edge"], r=16, sw=2)
zy = y + (h - 245) / 2               # centre the content (title top to DOI bottom) vertically
text(x + w / 2, zy + 30, "Zenodo", 21, ZEN["head"], 700)
text(x + w / 2, zy + 62, "data archive", 14, MUTED)
for i in range(3):
    xx = x + 58 + 38 * i
    rect(xx, zy + 100, 30, 40, "white", ZEN["edge"], r=4, sw=1.3)
    add(f'<line x1="{xx + 7}" y1="{zy + 114}" x2="{xx + 23}" y2="{zy + 114}" stroke="{ZEN["soft"]}" stroke-width="2.4"/>')
    add(f'<line x1="{xx + 7}" y1="{zy + 124}" x2="{xx + 19}" y2="{zy + 124}" stroke="{ZEN["soft"]}" stroke-width="2.4"/>')
rect(x + 40, zy + 190, w - 80, 38, ZEN["edge"], ZEN["edge"], r=19, sw=1)
text(x + w / 2, zy + 209.5, "DOI", 17, "white", 800, extra='letter-spacing="1.5"')
arrow(x + w / 2, P1[0] + P1[1] + 3, x + w / 2, y - 3, ZEN["edge"], 2)
text(x + w / 2 + 10, (BAND[0] + BAND[1] / 2) - 8, "opsci zenodo", 13, ZEN["head"], 700, "start")
text(x + w / 2 + 10, (BAND[0] + BAND[1] / 2) + 9, "release", 13, ZEN["head"], 700, "start")

# row 2 -> row 3
mx = X0 + ROW_W / 2
arrow(mx, R2[0] + R2[1] + 3, mx, R3[0] - 3, WEB["edge"], 2)
text(mx + 12, (R2[0] + R2[1] + R3[0]) / 2, "built on every publish", 14, WEB["head"], 700, "start")

# row 3: GitHub Pages
y, h = R3
row_box(X0, y, SIDE[0] + SIDE[1] + 18 - X0, h, WEB, "GitHub Pages", "the project website")
bw, bh, gap = 244, 206, 20
bx0, byy = X0 + 18, y + 56
tabs = ["Map", "Tasks", "Context", "Log", "Results"]
for i, t in enumerate(tabs):
    bx = bx0 + i * (bw + gap)
    if t == "Results":
        bw_r = SIDE[0] + SIDE[1] - bx
        browser(bx, byy, bw_r, bh, t)
    else:
        browser(bx, byy, bw, bh, t)
    cx0, cy0 = bx + 14, byy + 44
    if t == "Map":
        graph({"Q": (bx + 100, cy0 + 16, N_ROOT), "A": (bx + 70, cy0 + 62, N_PUB, "A"),
               "C": (bx + 150, cy0 + 62, N_PUB, "C"), "a1": (bx + 50, cy0 + 110, N_PUB),
               "a2": (bx + 92, cy0 + 110, N_DEAD), "c1": (bx + 150, cy0 + 110, N_PUB)},
              [("Q", "A"), ("A", "C"), ("A", "a1"), ("A", "a2"), ("C", "c1")])
    elif t == "Tasks":
        for j, (name, st, col) in enumerate((("Task A", "done", N_PUB), ("Task C", "active", "#f59e0b"),
                                             ("route 2", "failed", N_DEAD))):
            yy = cy0 + 14 + 44 * j
            text(cx0 + 4, yy, name, 13, INK, 600, "start")
            rect(bx + bw - 86, yy - 11, 66, 22, "white", col, r=11, sw=1.3)
            text(bx + bw - 53, yy + 0.5, st, 11, col, 700)
    elif t in ("Context", "Log"):
        for j in range(6):
            yy = cy0 + 10 + 22 * j
            if t == "Log":
                text(cx0 + 4, yy, f"09-{12 + 2 * j}", 11, MUTED, 600, "start")
                add(f'<line x1="{cx0 + 50}" y1="{yy}" x2="{bx + bw - 22 - (j % 3) * 22}" y2="{yy}" '
                    f'stroke="{WEB["soft"]}" stroke-width="5" stroke-linecap="round"/>')
            else:
                add(f'<line x1="{cx0 + 4}" y1="{yy}" x2="{bx + bw - 22 - (j % 3) * 30}" y2="{yy}" '
                    f'stroke="{CTX_SOFT if j == 0 else WEB["soft"]}" stroke-width="5" stroke-linecap="round"/>')
    else:  # Results: a plot with data points, a model curve and a histogram
        px0, py0, pw, phh = cx0 + 16, cy0 + 132, 190, 118
        add(f'<line x1="{px0}" y1="{py0}" x2="{px0 + pw}" y2="{py0}" stroke="{INK}" stroke-width="1.3"/>')
        add(f'<line x1="{px0}" y1="{py0}" x2="{px0}" y2="{py0 - phh}" stroke="{INK}" stroke-width="1.3"/>')
        pts = [(0.05, 0.18), (0.18, 0.33), (0.31, 0.41), (0.44, 0.57), (0.57, 0.62), (0.7, 0.75), (0.83, 0.79),
               (0.95, 0.9)]
        curve = " ".join(f"{px0 + pw * t:.1f},{py0 - phh * (0.12 + 0.82 * t ** 0.85):.1f}"
                         for t in [i / 40 for i in range(41)])
        add(f'<polyline points="{curve}" fill="none" stroke="{WEB["edge"]}" stroke-width="2.4"/>')
        for t, v in pts:
            X, Y = px0 + pw * t, py0 - phh * v
            add(f'<line x1="{X}" y1="{Y - 9}" x2="{X}" y2="{Y + 9}" stroke="{INK}" stroke-width="1.2"/>')
            add(f'<circle cx="{X}" cy="{Y}" r="3.6" fill="white" stroke="{INK}" stroke-width="1.4"/>')
        hx0 = px0 + pw + 34
        hw = bw_r - (hx0 - bx) - 70
        add(f'<line x1="{hx0}" y1="{py0}" x2="{hx0 + hw}" y2="{py0}" stroke="{INK}" stroke-width="1.3"/>')
        heights = [0.12, 0.3, 0.58, 0.86, 1.0, 0.8, 0.52, 0.27, 0.1]
        bwid = hw / len(heights)
        for j, v in enumerate(heights):
            add(f'<rect x="{hx0 + j * bwid + 1}" y="{py0 - phh * v}" width="{bwid - 2}" height="{phh * v}" '
                f'fill="{WEB["soft"]}" stroke="{WEB["edge"]}" stroke-width="1"/>')
        rect(bx + bw_r - 70, byy + 38, 56, 24, ZEN["edge"], ZEN["edge"], r=12, sw=1)
        text(bx + bw_r - 42, byy + 50.5, "DOI", 12, "white", 800, extra='letter-spacing="1"')
# Zenodo -> the DOI link on the results page
zx = SIDE[0] + SIDE[1] / 2
arrow(zx + 70, R2[0] + R2[1] + 3, zx + 70, byy + 34, ZEN["edge"], 1.8, dash="6 4")
text(zx + 60, (R2[0] + R2[1] + R3[0]) / 2, "linked", 13, ZEN["head"], 700, "end")

# node legend, top right
lx, ly = SIDE[0] - 250, R1[0] + 30
for i, (c, lab) in enumerate(((N_PUB, "public"), (N_PRIV, "private"), (N_DEAD, "dead end"))):
    node(lx + 82 * i, ly, c, r=7)
    text(lx + 12 + 82 * i, ly, lab, 12, MUTED, 400, "start")

# skill bars, left of the rows
def skill_bar(x, y0, y1, color, label, light):
    rect(x, y0, 30, y1 - y0, light, color, r=15, sw=2)
    add(f'<text x="{x + 15}" y="{(y0 + y1) / 2}" font-size="{14 * FS:.1f}" font-weight="700" fill="{color}" '
        f'text-anchor="middle" dominant-baseline="central" letter-spacing="0.5" '
        f'transform="rotate(-90 {x + 15} {(y0 + y1) / 2})">{label}</text>')


skill_bar(14, R1[0], R1[0] + R1[1], PM, "Skill: project management", "#f1f5f9")
skill_bar(52, P1[0], P1[0] + P1[1], CTX, "Skill: context management", "#fffbeb")
skill_bar(14, BAND[0], R3[0] + R3[1], "#b45309", "Skill: publishing", "#fff7ed")
add("</svg>")

OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
print(OUT)
