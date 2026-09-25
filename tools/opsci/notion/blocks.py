"""Convert the markdown of open-science projects into Notion API block objects.

Supported: headings (h4+ become h3), paragraphs, bulleted and numbered lists with nesting,
quotes, fenced code (mermaid kept as a Mermaid code block), pipe tables, $$ equations,
dividers; inline `code`, **bold**, *italic*, $math$, [links](url). Relative links become the
path in code, since Notion does not have the project's files. Hard-wrapped lines are joined:
in Notion every block is one paragraph.
"""
from __future__ import annotations

import re

import yaml

MAX_TEXT = 2000
CODE_LANGS = {"python", "bash", "shell", "json", "yaml", "markdown", "mermaid", "latex", "toml",
              "c", "c++", "javascript", "typescript", "html", "css", "sql", "diff", "plain text"}
LANG_ALIASES = {"sh": "bash", "py": "python", "yml": "yaml", "md": "markdown", "tex": "latex",
                "cpp": "c++", "js": "javascript", "ts": "typescript", "": "plain text", "text": "plain text"}


# ---------------------------------------------------------------- source cleanup

def split_front_matter(text: str):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return yaml.safe_load(text[4:end]) or {}, text[end + 5:]
    return {}, text


def strip_generated(text: str) -> str:
    text = re.sub(r"<!-- opsci:node-table.*?<!-- /opsci:node-table -->\n?", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->\n?", "", text, flags=re.S)


BLOCK_START = re.compile(r"^\s*(#{1,6} |[-*+] |\d+[.)] |> |```|<|---\s*$|\$\$)")


def _is_sep(line: str) -> bool:
    t = line.strip()
    return bool(t) and "-" in t and set(t) <= set("|-: ")


def unwrap(md: str) -> str:
    """Join hard-wrapped lines into the block they continue."""
    lines = md.splitlines()
    out, in_code, in_table, in_eq = [], False, False, False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if s == "$$":
            in_eq = not in_eq
            out.append(line)
            continue
        if in_code or in_eq:
            out.append(line)
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        is_table = s.startswith("|") and (in_table or _is_sep(nxt))
        in_table = is_table
        prev = out[-1] if out else ""
        starts_block = is_table or (BLOCK_START.match(line) and not s.startswith("|"))
        prev_joinable = (prev.strip() and not prev.lstrip().startswith(("```", "#", "<", "|", "$$"))
                         and not _is_sep(prev) and not prev.rstrip().endswith("  "))
        if s and not starts_block and prev_joinable:
            out[-1] = prev.rstrip() + " " + s
        else:
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- inline

INLINE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<math>(?<![\\$\w])\$(?=[^\s$])[^$\n]*?(?<=[^\s\\])\$(?![\w$]))"
    r"|(?P<bold>(?<![\w*])\*\*(?=\S)(?:.+?)(?<=\S)\*\*(?![\w*]))"
    r"|(?P<italic>(?<![\w*\\])\*(?=[^\s*])(?:[^*]+?)(?<=[^\s*\\])\*(?![\w*]))"
    r"|(?P<link>\[(?P<ltext>[^\]]+)\]\((?P<lurl>[^)\s]+)\))"
)
UNESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~$])")


def _t(content, ann=None, link=None):
    out = []
    for i in range(0, max(len(content), 1), MAX_TEXT):
        piece = content[i:i + MAX_TEXT]
        if not piece:
            continue
        o = {"type": "text", "text": {"content": piece}}
        if link:
            o["text"]["link"] = {"url": link}
        if ann:
            o["annotations"] = dict(ann)
        out.append(o)
    return out


def rich(s: str, ann=None) -> list:
    ann = dict(ann or {})
    out, pos = [], 0
    for m in INLINE.finditer(s):
        if m.start() > pos:
            out += _t(UNESCAPE.sub(r"\1", s[pos:m.start()]), ann)
        g = m.lastgroup if m.lastgroup not in ("ltext", "lurl") else "link"
        tok = m.group(0)
        if m.group("code"):
            out += _t(tok[1:-1], {**ann, "code": True})
        elif m.group("math"):
            out.append({"type": "equation", "equation": {"expression": tok[1:-1]}})
        elif m.group("bold"):
            out += rich(tok[2:-2], {**ann, "bold": True})
        elif m.group("italic"):
            out += rich(tok[1:-1], {**ann, "italic": True})
        elif m.group("link"):
            text, url = m.group("ltext"), m.group("lurl")
            if re.match(r"(https?|mailto):", url):
                out += _t(UNESCAPE.sub(r"\1", text.strip("`")), ann, link=url)
            else:
                path = url.split("#")[0]
                if text.strip("`") == path:
                    out += _t(path, {**ann, "code": True})
                else:
                    out += rich(text, ann) + _t(" (", ann) + _t(path, {**ann, "code": True}) + _t(")", ann)
        pos = m.end()
    if pos < len(s):
        out += _t(UNESCAPE.sub(r"\1", s[pos:]), ann)
    return out


# ---------------------------------------------------------------- blocks

def blk(kind, rt=None, **extra):
    body = {}
    if rt is not None:
        body["rich_text"] = rt
    body.update(extra)
    return {"object": "block", "type": kind, kind: body}


def _rt_blocks(kind, rt, **extra):
    """A rich_text array holds at most 100 items: split long ones over several blocks."""
    if len(rt) <= 100:
        return [blk(kind, rt, **extra)]
    return [blk(kind, rt[i:i + 100], **extra) for i in range(0, len(rt), 100)]


def _table(rows):
    cells = []
    for r in rows:
        if _is_sep(r):
            continue
        parts = re.split(r"(?<!\\)\|", r.strip().strip("|"))
        cells.append([p.strip() for p in parts])
    width = max(len(c) for c in cells)
    trs = [{"object": "block", "type": "table_row",
            "table_row": {"cells": [rich(c) for c in (row + [""] * width)[:width]]}} for row in cells]
    return {"object": "block", "type": "table",
            "table": {"table_width": width, "has_column_header": True, "has_row_header": False,
                      "children": trs}}


def _code(lines, lang):
    lang = LANG_ALIASES.get(lang.lower(), lang.lower())
    if lang not in CODE_LANGS:
        lang = "plain text"
    src = "\n".join(lines)
    return {"object": "block", "type": "code",
            "code": {"rich_text": _t(src) or _t(" "), "language": lang}}


LIST_RE = re.compile(r"^(?P<ind>\s*)(?P<mark>[-*+]|\d+[.)]) (?P<text>.*)$")


def md_to_blocks(md: str) -> list:
    lines = unwrap(strip_generated(md)).splitlines()
    out, stack = [], []          # stack: (indent, list block) for nesting
    i = 0

    def put(b):
        stack.clear()
        out.append(b)

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            ind = len(line) - len(line.lstrip())
            lang = s[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i][ind:] if lines[i][:ind].strip() == "" else lines[i])
                i += 1
            put(_code(body, lang))
            i += 1
            continue
        if s == "$$":
            body = []
            i += 1
            while i < len(lines) and lines[i].strip() != "$$":
                body.append(lines[i])
                i += 1
            put(blk("equation", None, expression="\n".join(body).strip()))
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and _is_sep(lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            put(_table(rows))
            continue
        m = re.match(r"^(#{1,6}) (.*)$", s)
        if m:
            level = min(3, len(m.group(1)))
            put(blk(f"heading_{level}", rich(m.group(2))))
            i += 1
            continue
        if re.match(r"^(---+|\*\*\*+)$", s):
            put(blk("divider"))
            i += 1
            continue
        if s.startswith("> "):
            put(blk("quote", rich(s[2:])))
            i += 1
            continue
        m = LIST_RE.match(line)
        if m:
            ind = len(m.group("ind").expandtabs(4))
            text = m.group("text")
            todo = re.match(r"^\[( |x|X)\] (.*)$", text)
            if todo:
                b = blk("to_do", rich(todo.group(2)), checked=todo.group(1) != " ")
            elif m.group("mark")[0].isdigit():
                b = blk("numbered_list_item", rich(text))
            else:
                b = blk("bulleted_list_item", rich(text))
            while stack and stack[-1][0] >= ind:
                stack.pop()
            if stack:
                parent = stack[-1][1]
                parent[parent["type"]].setdefault("children", []).append(b)
            else:
                out.append(b)
            stack.append((ind, b))
            i += 1
            continue
        # paragraph; an indented paragraph under a list item becomes its child
        rt = rich(s)
        ind = len(line) - len(line.lstrip())
        if stack and ind > stack[-1][0]:
            parent = stack[-1][1]
            parent[parent["type"]].setdefault("children", []).extend(_rt_blocks("paragraph", rt))
        else:
            stack.clear()
            out += _rt_blocks("paragraph", rt)
        i += 1
    return out


def toggle(title: str, inner: list, level: int = 2) -> dict:
    return blk(f"heading_{level}", rich(title), is_toggleable=True, children=inner)


def callout(rt, emoji="🔄", color="gray_background", children=None):
    b = blk("callout", rt, icon={"type": "emoji", "emoji": emoji}, color=color)
    if children:
        b["callout"]["children"] = children
    return b


def demote(blocks: list, by: int = 1) -> list:
    """Lower heading levels (h1 -> h2 ...), capped at h3."""
    out = []
    for b in blocks:
        if b["type"].startswith("heading_") and not b[b["type"]].get("is_toggleable"):
            n = min(3, int(b["type"][-1]) + by)
            b = {"object": "block", "type": f"heading_{n}", f"heading_{n}": b[b["type"]]}
        out.append(b)
    return out


def fit100(rt: list) -> list:
    """A rich text list for a place that holds at most 100 items (a property, a caption).

    Longer lists (many equations) become plain text with the LaTeX kept as $...$."""
    if len(rt) <= 100:
        return rt
    flat = "".join(x["text"]["content"] if x["type"] == "text" else f"${x['equation']['expression']}$"
                   for x in rt)
    return _t(flat)[:100]


def chunks100(rt: list) -> list[list]:
    """Split a rich text list into pieces of at most 100 items (one block each)."""
    return [rt[i:i + 100] for i in range(0, len(rt), 100)] or [[]]


def media(file_upload_id: str, caption: list, kind: str = "image", plot_key: str | None = None) -> dict:
    """An image, pdf or file block showing an uploaded file; caption is a rich text list."""
    b = {"object": "block", "type": kind,
         kind: {"type": "file_upload", "file_upload": {"id": file_upload_id}, "caption": caption[:100]}}
    if plot_key:
        b["_plot"] = plot_key
    return b
