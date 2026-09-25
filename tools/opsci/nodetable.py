"""The readable table of a task's node header, written under the title of its context.md and
plan.md. The YAML front matter stays the source; `opsci task new` writes the table and
`opsci map build` rewrites it whenever the front matter changes."""

from __future__ import annotations

import re

import yaml

from . import nodes

START = "<!-- opsci:node-table: generated from the front matter by `opsci map build`; edit the front matter, not this table -->"
END = "<!-- /opsci:node-table -->"
BLOCK_RE = re.compile(r"^<!-- opsci:node-table\b.*?^<!-- /opsci:node-table -->[ \t]*\n", re.M | re.S)
H1_RE = re.compile(r"^# .*\n", re.M)

# (key, label) in display order; the id and type make the table's header row, and the title
# is the H1 above the table.
ROWS = (
    ("short_name", "short name"),
    ("status", "status"),
    ("privacy", "privacy"),
    ("verification", "verification"),
    ("evidence", "evidence"),
    ("autonomy", "autonomy"),
    ("hold_at", "hold at"),
    ("depends_on", "depends on"),
    ("supersedes", "supersedes"),
    ("related", "related"),
    ("tags", "tags"),
    ("summary", "summary"),
)
CODE_KEYS = {"short_name", "evidence", "hold_at", "depends_on", "supersedes", "related"}


def _cell(key: str, value) -> str:
    items = value if isinstance(value, list) else [value]
    items = [" ".join(str(v).split()).replace("|", "\\|") for v in items]
    if key in CODE_KEYS:
        items = [f"`{v}`" for v in items]
    return ", ".join(items)


def render(header: dict) -> str:
    """The table block, markers included, ending in a newline."""
    out = [START, "", f"| {header.get('type', 'node')} | `{header['id']}` |", "|---|---|"]
    for key, label in ROWS:
        value = header.get(key)
        if value is None or value == [] or value == "":
            continue
        out.append(f"| **{label}** | {_cell(key, value)} |")
    return "\n".join(out + ["", END]) + "\n"


def refresh(text: str) -> str:
    """``text`` with its node table rewritten from its front matter. A file without a readable
    node header is returned unchanged; one without a table gets it after its first H1."""
    raw, present = nodes.read_front_matter(text)
    if not present or raw is None:
        return text
    try:
        header = yaml.safe_load(raw)
    except yaml.YAMLError:
        return text
    if not isinstance(header, dict) or not isinstance(header.get("id"), str):
        return text
    block = render(header)
    if BLOCK_RE.search(text):
        return BLOCK_RE.sub(lambda _: block, text, count=1)
    body_start = text.index("\n") + 1  # past the opening '---' line
    end = re.compile(r"^(---|\.\.\.)[ \t]*$\n?", re.M).search(text, body_start)
    h1 = H1_RE.search(text, end.end()) if end else None
    if h1 is None:
        return text
    return text[:h1.end()] + "\n" + block + text[h1.end():]
