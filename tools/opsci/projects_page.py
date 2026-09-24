"""Checks for the personal projects page (`projects-page/` in the framework repo).

Two checks, both run by `opsci projects-page check DIR`:

- `validate_projects_text`: `projects.yaml` has the fields the page expects, and uses only
  the part of YAML that the page's own reader (the JavaScript in index.html) understands.
- `check_style`: the page contains none of the banned style elements listed in AGENTS.md:
  cream or off-white background, italic accent words in headlines, numbered section
  labels, monospace labels, pill-shaped buttons.
"""

from __future__ import annotations

import colorsys
import datetime as dt
import re
from html.parser import HTMLParser
from pathlib import Path

import yaml

# ------------------------------------------------------------------------------------------
# projects.yaml
# ------------------------------------------------------------------------------------------

TOP_KEYS = {"title", "intro", "projects"}
PROJECT_KEYS = {"title", "description", "tags", "status", "updated", "links"}
PROJECT_REQUIRED = ("title", "description", "tags", "status")
LINK_KEYS = {"repo", "site", "doi"}
STATUSES = ("active", "paused", "finished", "archived")
UPDATED_RE = re.compile(r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$")
URL_RE = re.compile(r"^https?://\S+$")
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
MAX_TAG_LEN = 40

_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*) *:(?: +(.*))?$")


def _strip_comment(text: str) -> str:
    """Same rule as stripComment() in index.html."""
    quote = None
    i = 0
    while i < len(text):
        c = text[i]
        prev = " " if i == 0 else text[i - 1]
        if quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = None
        elif quote == "'":
            if c == "'":
                if text[i + 1:i + 2] == "'":
                    i += 1
                else:
                    quote = None
        elif c in "\"'" and (prev.isspace() or prev in "[,:-"):
            quote = c
        elif c == "#" and prev.isspace():
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


def _lint_value(v: str, n: int) -> list[str]:
    v = v.strip()
    if not v:
        return []
    c = v[0]
    if c in "\"'":
        body = v[1:]
        closed = re.fullmatch(r'(?:[^"\\]|\\.)*"', body) if c == '"' else \
            re.fullmatch(r"(?:[^']|'')*'", body)
        return [] if closed else [f"line {n}: quoted text must close on the same line, with "
                                  "nothing after the closing quote"]
    if c == "[":
        if not v.endswith("]"):
            return [f"line {n}: a [ ] list must close on the same line"]
        if re.search(r"[\[\]{}]", v[1:-1]):
            return [f"line {n}: nested [ ] or {{ }} are not supported"]
        return []
    if c == ">":
        return [f"line {n}: folded text (>) is not supported; use |"]
    if c in "{&*!%@`|":
        return [f"line {n}: unsupported YAML feature starting with '{c}'"]
    if re.search(r":(\s|$)", v):
        return [f"line {n}: unquoted text contains ': '; put the value in quotes"]
    return []


def lint_subset(text: str) -> list[str]:
    """Refuse YAML that the page's reader in index.html does not understand.

    Line by line: outside "|" blocks, every line must be blank, a comment, "key: value",
    "key:", "- value" or "- key: value", with one-line values.
    """
    errors = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    block_indent = None  # indent of the key that opened a "|" block
    for n, raw in enumerate(lines, 1):
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw[indent:]
        if block_indent is not None:
            if not raw.strip() or indent > block_indent:
                continue
            block_indent = None
        if not raw.strip() or body.startswith("#"):
            continue
        if body.startswith("\t"):
            errors.append(f"line {n}: tab used for indentation; use spaces")
            continue
        if indent == 0 and re.match(r"(---|\.\.\.|%)", body):
            errors.append(f"line {n}: document markers and directives are not supported")
            continue
        t = _strip_comment(body)
        col = indent
        while t == "-" or t.startswith("- "):
            rest = t[1:]
            t = rest.lstrip(" ")
            col += 1 + len(rest) - len(t)
        if not t:
            continue
        m = _KEY_LINE.match(t)
        if m:
            value = (m.group(2) or "").strip()
            if re.fullmatch(r"\|[+-]?", value):
                block_indent = col
                continue
            if value.startswith("|"):
                errors.append(f"line {n}: unsupported block text header '{value}'")
                continue
            errors.extend(_lint_value(value, n))
        elif col == indent:
            # A bare value on its own line (not after "- ") continues the previous value.
            errors.append(f"line {n}: text over several lines needs '|' (see the comments at "
                          "the top of projects.yaml)")
        else:
            errors.extend(_lint_value(t, n))
    return errors


def _as_text(x):
    """PyYAML turns some plain values into dates and numbers; the page reads them as text."""
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return str(x)
    return x


def validate_projects_data(data) -> list[str]:
    errors = []
    if not isinstance(data, dict):
        return ["the file must be a set of 'key: value' lines with a 'projects:' list"]
    for k in sorted(set(data) - TOP_KEYS):
        errors.append(f"unknown top-level key '{k}' (allowed: {', '.join(sorted(TOP_KEYS))})")
    for k in ("title", "intro"):
        if data.get(k) is not None and not isinstance(data[k], str):
            errors.append(f"'{k}' must be text")
    projects = data.get("projects")
    if not isinstance(projects, list):
        errors.append("'projects' must be a list ('- title: ...' entries)")
        return errors
    titles = {}
    for i, p in enumerate(projects, 1):
        where = f"project {i}"
        if not isinstance(p, dict):
            errors.append(f"{where}: must be a set of 'key: value' lines")
            continue
        if isinstance(p.get("title"), str) and p["title"].strip():
            where = f"project {i} ('{p['title']}')"
        for k in sorted(set(p) - PROJECT_KEYS):
            errors.append(f"{where}: unknown key '{k}' (allowed: {', '.join(sorted(PROJECT_KEYS))})")
        for k in PROJECT_REQUIRED:
            if k not in p:
                errors.append(f"{where}: missing '{k}'")
        title = p.get("title")
        if "title" in p:
            if not isinstance(title, str) or not title.strip():
                errors.append(f"{where}: 'title' must be non-empty text")
            elif title.strip().casefold() in titles:
                errors.append(f"{where}: same title as project {titles[title.strip().casefold()]}")
            else:
                titles[title.strip().casefold()] = i
        if p.get("description") is not None and not isinstance(p["description"], str):
            errors.append(f"{where}: 'description' must be text (or empty)")
        if "tags" in p:
            tags = p["tags"]
            if not isinstance(tags, list):
                errors.append(f"{where}: 'tags' must be a list, e.g. [one, two] or []")
            else:
                seen = set()
                for t in tags:
                    if not isinstance(t, str) or not t.strip():
                        errors.append(f"{where}: tag {t!r} must be non-empty text")
                    elif t != t.strip() or "," in t or len(t) > MAX_TAG_LEN:
                        errors.append(f"{where}: tag {t!r}: no commas, no surrounding spaces, "
                                      f"at most {MAX_TAG_LEN} characters")
                    elif t in seen:
                        errors.append(f"{where}: tag '{t}' listed twice")
                    else:
                        seen.add(t)
        if "status" in p and p["status"] not in STATUSES:
            errors.append(f"{where}: 'status' is {p['status']!r}; use one of {', '.join(STATUSES)}")
        if p.get("updated") is not None:
            u = _as_text(p["updated"])
            if not isinstance(u, str) or not UPDATED_RE.match(u):
                errors.append(f"{where}: 'updated' is {p['updated']!r}; use YYYY, YYYY-MM or YYYY-MM-DD")
        links = p.get("links")
        if links is not None:
            if not isinstance(links, dict):
                errors.append(f"{where}: 'links' must contain repo:, site: and/or doi: lines")
            else:
                for k in sorted(set(links) - LINK_KEYS):
                    errors.append(f"{where}: unknown link '{k}' (allowed: {', '.join(sorted(LINK_KEYS))})")
                for k in ("repo", "site"):
                    v = links.get(k)
                    if v is not None and not (isinstance(v, str) and URL_RE.match(v)):
                        errors.append(f"{where}: link '{k}' must be an http:// or https:// URL")
                d = links.get("doi")
                if d is not None and not (isinstance(d, str) and (DOI_RE.match(d) or
                                                                  d.startswith("https://doi.org/10."))):
                    errors.append(f"{where}: 'doi' must look like 10.1234/abc or https://doi.org/10.1234/abc")
    return errors


def validate_projects_text(text: str) -> list[str]:
    errors = lint_subset(text)
    if errors:
        return errors
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"not valid YAML: {exc}"]
    return validate_projects_data(data)


# ------------------------------------------------------------------------------------------
# Style check
# ------------------------------------------------------------------------------------------

CREAM = "cream/off-white background"
ITALIC = "italic headline"
NUMBERED = "numbered section label"
MONO = "monospace"
PILL = "pill-shaped button"

# Any background lighter than this on every channel (after blending over white) must be pure
# white: cream, ivory, beige and light greys are all refused.
LIGHT_CHANNEL_MIN = 224
# Largest corner radius allowed anywhere on the page.
MAX_RADIUS_PX = 6.0
MAX_RADIUS_EM = 0.4
MAX_RADIUS_PERCENT = 15.0

_NAMED_LIGHT = {
    "white": (255, 255, 255), "snow": (255, 250, 250), "ivory": (255, 255, 240),
    "beige": (245, 245, 220), "linen": (250, 240, 230), "oldlace": (253, 245, 230),
    "cornsilk": (255, 248, 220), "floralwhite": (255, 250, 240), "antiquewhite": (250, 235, 215),
    "seashell": (255, 245, 238), "whitesmoke": (245, 245, 245), "ghostwhite": (248, 248, 255),
    "mintcream": (245, 255, 250), "azure": (240, 255, 255), "aliceblue": (240, 248, 255),
    "honeydew": (240, 255, 240), "lavenderblush": (255, 240, 245), "lemonchiffon": (255, 250, 205),
    "lightyellow": (255, 255, 224), "papayawhip": (255, 239, 213), "blanchedalmond": (255, 235, 205),
    "bisque": (255, 228, 196), "mistyrose": (255, 228, 225), "moccasin": (255, 228, 181),
    "wheat": (245, 222, 179), "navajowhite": (255, 222, 173), "peachpuff": (255, 218, 185),
    "gainsboro": (220, 220, 220), "lightgoldenrodyellow": (250, 250, 210), "lavender": (230, 230, 250),
}
_MONO_RE = re.compile(r"mono|courier|consolas|menlo|monaco|inconsolata|lucida console|cascadia",
                      re.I)
_MONO_TAGS = {"code", "kbd", "samp", "tt", "pre", "var"}
_ITALIC_TAGS = {"em", "i", "cite", "dfn", "var", "address"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_HEADING_SEL = re.compile(r"(^|[^\w-])(h[1-6]|header|hgroup)\b|title|headline|heading|hero", re.I)
_LABEL_CLASS = re.compile(r"label|eyebrow|kicker|section|overline", re.I)
_NUM_LABEL = re.compile(r"^(§\s*)?0\d(\s*[/.:)|·—–-]|\s|$)|^\d{1,2}\s*/\s*\d{1,2}$")
_NUM_HEADING = re.compile(r"^(§\s*)?\d{1,2}(\.\d{1,2})*[.):]?\s+\S")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.styles, self.scripts = [], []
        self.inline = []          # (tag, style attribute)
        self.problems = []
        self.stack = []           # open elements: (tag, class)
        self._in = None           # "style" or "script" while inside one
        self._buf = []
        self.texts = []           # (text, [(tag, class), ...])

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script"):
            self._in, self._buf = tag, []
            return
        if a.get("style"):
            self.inline.append((tag, a["style"]))
        if a.get("bgcolor"):
            self.inline.append((tag, f"background-color: {a['bgcolor']}"))
        tags = [t for t, _ in self.stack]
        if tag in _MONO_TAGS:
            self.problems.append(f"{MONO}: <{tag}> element (browsers show it in a monospace font)")
        if tag in _ITALIC_TAGS and (set(tags) & (_HEADING_TAGS | {"header"})):
            self.problems.append(f"{ITALIC}: <{tag}> inside a headline")
        if tag not in ("br", "img", "input", "meta", "link", "hr", "wbr", "source"):
            self.stack.append((tag, a.get("class") or ""))

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self._in == tag:
            (self.styles if tag == "style" else self.scripts).append("".join(self._buf))
            self._in = None
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)
        elif data.strip():
            self.texts.append((data.strip(), list(self.stack)))


def _css_declarations(css: str, where: str):
    """Yield (selector, property, value) from a stylesheet, including inside @media."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    stack, start = [], 0
    for i, ch in enumerate(css):
        if ch == "{":
            stack.append(css[start:i].strip())
            start = i + 1
        elif ch == "}":
            body = css[start:i]
            sel = stack.pop() if stack else ""
            if not sel.startswith("@"):
                for decl in body.split(";"):
                    if ":" in decl:
                        prop, value = decl.split(":", 1)
                        yield sel, prop.strip().lower(), value.strip()
            start = i + 1


def _inline_declarations(style: str):
    for decl in style.split(";"):
        if ":" in decl:
            prop, value = decl.split(":", 1)
            yield prop.strip().lower(), value.strip()


def _js_declarations(js: str):
    """Style set from JavaScript: el.style.fontFamily = "...", setProperty("x", "..."),
    and style="..." attributes inside strings."""
    for m in re.finditer(r"\.style\.([A-Za-z]+)\s*=\s*([\"'`])(.*?)\2", js):
        prop = re.sub(r"[A-Z]", lambda c: "-" + c.group(0).lower(), m.group(1))
        yield "script", prop, m.group(3)
    for m in re.finditer(r"setProperty\(\s*([\"'])([\w-]+)\1\s*,\s*([\"'])(.*?)\3", js):
        yield "script", m.group(2).lower(), m.group(4)
    for m in re.finditer(r"style=\\?[\"']([^\"'\\]*)", js):
        for prop, value in _inline_declarations(m.group(1)):
            yield "script", prop, value
    for m in re.finditer(r"cssText\s*=\s*([\"'`])(.*?)\1", js):
        for prop, value in _inline_declarations(m.group(2)):
            yield "script", prop, value


def _resolve_vars(value: str, variables: dict, depth=0) -> str:
    if depth > 10:
        return value

    def sub(m):
        name, fallback = m.group(1), m.group(2)
        v = variables.get(name, fallback or "")
        return _resolve_vars(v, variables, depth + 1)

    return re.sub(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)", sub, value)


def _parse_colors(value: str):
    """Yield (r, g, b, alpha, source text) for every colour written in a CSS value."""
    v = value.lower()
    for m in re.finditer(r"#([0-9a-f]{3,8})\b", v):
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            continue
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        yield int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a, m.group(0)
    num = r"(\d*\.?\d+%?)"
    sep = r"\s*[,\s]\s*"
    for m in re.finditer(rf"rgba?\(\s*{num}{sep}{num}{sep}{num}(?:\s*[,/]\s*{num})?\s*\)", v):
        ch = [float(x[:-1]) * 2.55 if x.endswith("%") else float(x) for x in m.groups()[:3]]
        a = m.group(4)
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        yield ch[0], ch[1], ch[2], alpha, m.group(0)
    for m in re.finditer(rf"hsla?\(\s*(\d*\.?\d+)(?:deg)?{sep}(\d*\.?\d+)%{sep}(\d*\.?\d+)%(?:\s*[,/]\s*{num})?\s*\)", v):
        r, g, b = colorsys.hls_to_rgb(float(m.group(1)) / 360, float(m.group(3)) / 100,
                                      float(m.group(2)) / 100)
        a = m.group(4)
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        yield r * 255, g * 255, b * 255, alpha, m.group(0)
    for name, rgb in _NAMED_LIGHT.items():
        if re.search(rf"(^|[^\w-]){name}($|[^\w-])", v):
            yield (*rgb, 1.0, name)


def _off_white(r, g, b, a) -> bool:
    # Blend over white, since that is what a see-through background shows on this page.
    r, g, b = (255 - a * (255 - c) for c in (r, g, b))
    if min(r, g, b) < LIGHT_CHANNEL_MIN:
        return False
    return (round(r), round(g), round(b)) != (255, 255, 255)


def _radius_too_big(value: str) -> str | None:
    for m in re.finditer(r"(-?\d*\.?\d+)\s*(px|em|rem|%|vh|vw|pt)?", value.lower()):
        n, unit = float(m.group(1)), m.group(2) or "px"
        if (unit == "px" and n > MAX_RADIUS_PX) or (unit == "pt" and n > MAX_RADIUS_PX * 0.75) or \
                (unit in ("em", "rem") and n > MAX_RADIUS_EM) or \
                (unit == "%" and n > MAX_RADIUS_PERCENT) or (unit in ("vh", "vw") and n > 0):
            return m.group(0)
    return None


def check_style(html: str) -> list[str]:
    """Return one line per banned style element found in the page; empty if none."""
    page = _Page()
    page.feed(html)
    page.close()
    problems = list(page.problems)

    decls = []
    for css in page.styles:
        decls.extend(_css_declarations(css, "style"))
    for tag, style in page.inline:
        decls.extend((f"<{tag} style>", p, v) for p, v in _inline_declarations(style))
    js = "\n".join(page.scripts)
    decls.extend(_js_declarations(js))
    variables = {p: v for _, p, v in decls if p.startswith("--")}

    for sel, prop, raw in decls:
        if prop.startswith("--"):
            continue
        value = _resolve_vars(raw, variables)
        low = value.lower()
        where = f"{sel or '(no selector)'} {{ {prop}: {raw} }}"
        if prop in ("background", "background-color", "background-image"):
            for r, g, b, a, text in _parse_colors(value):
                if _off_white(r, g, b, a):
                    problems.append(f"{CREAM}: {where} ({text} is light but not pure white)")
        if prop in ("font-family", "font") and _MONO_RE.search(low):
            problems.append(f"{MONO}: {where}")
        if (prop == "font-style" or prop == "font") and re.search(r"\b(italic|oblique)\b", low):
            if _HEADING_SEL.search(sel) or sel.startswith("<h") or sel == "script":
                problems.append(f"{ITALIC}: {where}")
        if prop == "content" and re.search(r"\bcounters?\(", low):
            problems.append(f"{NUMBERED}: {where} (CSS counter used as a label)")
        if prop in ("list-style", "list-style-type") and "decimal-leading-zero" in low:
            problems.append(f"{NUMBERED}: {where}")
        if prop == "border-radius" or re.fullmatch(r"border-(top|bottom|start|end)-(left|right|start|end)-radius", prop):
            big = _radius_too_big(value)
            if big:
                problems.append(f"{PILL}: {where} (radius {big}; the limit is {MAX_RADIUS_PX:g}px, "
                                f"{MAX_RADIUS_EM:g}em or {MAX_RADIUS_PERCENT:g}%)")

    for text, stack in page.texts:
        tags = {t for t, _ in stack}
        label_like = bool(tags & _HEADING_TAGS) or any(_LABEL_CLASS.search(c) for _, c in stack)
        if _NUM_LABEL.search(text) or (label_like and _NUM_HEADING.search(text)):
            problems.append(f"{NUMBERED}: text {text[:40]!r}")

    # HTML built inside scripts.
    for m in re.finditer(r"<(code|kbd|samp|tt|pre)\b", js):
        problems.append(f"{MONO}: <{m.group(1)}> built in a script")
    for m in re.finditer(r"<h[1-6]\b[^>]*>(?:(?!</h[1-6]).)*?<(em|i|cite|dfn)\b", js, re.S):
        problems.append(f"{ITALIC}: <{m.group(1)}> inside a headline built in a script")
    if re.search(r"padStart\(\s*2\s*,\s*[\"']0[\"']\s*\)", js):
        problems.append(f"{NUMBERED}: zero-padded number built in a script (padStart(2, \"0\"))")
    return problems


# ------------------------------------------------------------------------------------------
# Both checks on a page directory
# ------------------------------------------------------------------------------------------

def check_page_dir(root: Path) -> list[str]:
    root = Path(root)
    errors = []
    page, data = root / "index.html", root / "projects.yaml"
    for f in (page, data):
        if not f.is_file():
            errors.append(f"missing {f.name} in {root}")
    if page.is_file():
        errors.extend(f"index.html: {p}" for p in check_style(page.read_text(encoding="utf-8")))
    if data.is_file():
        errors.extend(f"projects.yaml: {e}" for e in validate_projects_text(data.read_text(encoding="utf-8")))
    return errors
