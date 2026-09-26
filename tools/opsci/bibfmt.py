"""Read `citations/used.bib` and write each entry as a reference line for the project site, in
the style of a physics journal's reference list:

    B. P. Abbott et al. (LIGO Scientific, Virgo), Phys. Rev. D 93, 122003 (2016),
    arXiv:1602.03839 [gr-qc].

with the journal reference linked to the DOI (or URL) and the arXiv number to arXiv. An entry's
`usage` field, which BibTeX styles ignore, says in a few words how the project uses the work.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# LaTeX accents -> combining characters, for names like G\"{o}del or Schr\"odinger.
ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "c": "\u0327",
           "v": "\u030c", "=": "\u0304", ".": "\u0307", "u": "\u0306", "H": "\u030b"}
SYMBOLS = {r"\&": "&", r"\%": "%", r"\_": "_", r"\$": "$", r"\#": "#", r"\ss": "ß", r"\o": "ø",
           r"\O": "Ø", r"\aa": "å", r"\AA": "Å", r"\ae": "æ", r"\l": "ł", r"\L": "Ł", "~": " ",
           "---": "—", "--": "–"}
MAX_AUTHORS = 3  # more authors than this: the first one and "et al."
ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")


@dataclass
class Entry:
    type: str
    key: str
    fields: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str:
        return self.fields.get(name.lower(), "")


def _value(text: str, i: int) -> tuple[str, int]:
    """The field value starting at ``text[i]`` (braced, quoted or bare) and the index after it."""
    if text[i] == "{":
        depth, j = 0, i
        while j < len(text):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                return text[i + 1:j], j + 1
            j += 1
        return text[i + 1:], len(text)
    if text[i] == '"':
        j = i + 1
        depth = 0
        while j < len(text) and not (text[j] == '"' and depth == 0):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        return text[i + 1:j], j + 1
    m = re.match(r"[^,}\s]*", text[i:])
    return m.group(0), i + m.end()


def parse(text: str) -> list[Entry]:
    """The entries of a .bib file, in order. Comments (`%` lines, @comment) are skipped."""
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("%"))
    out = []
    for m in re.finditer(r"@(\w+)\s*[{(]\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("comment", "preamble", "string"):
            continue
        e, i = Entry(m.group(1).lower(), m.group(2)), m.end()
        while True:
            f = re.compile(r"\s*,?\s*([A-Za-z][\w-]*)\s*=\s*").match(text, i)
            if not f:
                break
            val, i = _value(text, f.end())
            e.fields[f.group(1).lower()] = " ".join(val.split())
        out.append(e)
    return out


def detex(s: str) -> str:
    """Plain text of a short BibTeX value: accents, escapes and braces resolved. Math ($...$)
    is kept for MathJax."""
    s = re.sub(r"\\([\"'`^~cv=.uH])\s*\{?\s*([A-Za-z])\}?",
               lambda m: m.group(2) + ACCENTS[m.group(1)], s)
    s = re.sub(r"\\(?:emph|textit|textbf|textrm|mathrm)\s*\{", "{", s)
    for k, v in SYMBOLS.items():
        s = s.replace(k, v)
    return unicodedata.normalize("NFC", s.replace("{", "").replace("}", ""))


def _split_top(s: str, sep: str) -> list[str]:
    """``s`` split at ``sep`` outside braces."""
    parts, depth, cur, i = [], 0, "", 0
    while i < len(s):
        if s[i] in "{}":
            depth += 1 if s[i] == "{" else -1
        if depth == 0 and s.startswith(sep, i):
            parts.append(cur)
            cur, i = "", i + len(sep)
            continue
        cur += s[i]
        i += 1
    return parts + [cur]


def _initials(first: str) -> str:
    """`Benjamin Philip` -> `B. P.`; `Jean-Luc` -> `J.-L.`; an initial already given stays."""
    out = []
    for word in first.split():
        out.append("-".join(p[0] + "." for p in word.split("-") if p))
    return " ".join(out)


def _name(author: str) -> str:
    a = author.strip()
    if a.startswith("{") and a.endswith("}"):  # a corporate name, e.g. {LIGO Scientific Collaboration}
        return detex(a)
    parts = [p.strip() for p in _split_top(a, ",")]
    if len(parts) >= 2:  # von Last, Jr, First  or  Last, First
        last, first = parts[0], parts[-1]
    else:
        words = _split_top(a, " ")
        words = [w for w in words if w]
        if len(words) == 1:
            return detex(words[0])
        # "First von Last": the lowercase words before the last one belong to the last name
        k = len(words) - 1
        while k > 1 and words[k - 1][:1].islower():
            k -= 1
        first, last = " ".join(words[:k]), " ".join(words[k:])
    initials = _initials(detex(first))
    return f"{initials} {detex(last)}".strip()


def authors(e: Entry) -> str:
    raw = e.get("author") or e.get("editor")
    if not raw:
        return ""
    names = [n for n in (x.strip() for x in _split_top(raw, " and ")) if n]
    more = any(n.lower() == "others" for n in names)
    names = [_name(n) for n in names if n.lower() != "others"]
    if len(names) > MAX_AUTHORS or (more and names):
        text = f"{names[0]} et al."
    elif len(names) == 1:
        text = names[0]
    elif len(names) == 2:
        text = f"{names[0]} and {names[1]}"
    else:
        text = ", ".join(names[:-1]) + f", and {names[-1]}"
    collab = e.get("collaboration")
    return text + (f" ({detex(collab)})" if collab else "")


def _link(text: str, url: str) -> str:
    return f"[{text}]({url})" if url else text


def doi_url(e: Entry) -> str:
    doi = e.get("doi")
    if doi:
        return doi if doi.startswith("http") else "https://doi.org/" + doi
    return e.get("url")


def arxiv(e: Entry) -> str:
    """`[arXiv:1602.03839 [gr-qc]](https://arxiv.org/abs/1602.03839)`, or '' if the entry has none."""
    eprint = e.get("eprint")
    prefix = e.get("archiveprefix").lower()
    if not eprint or (prefix and prefix != "arxiv") or not ARXIV_ID.fullmatch(eprint):
        return ""
    cls = e.get("primaryclass")
    text = f"arXiv:{eprint}" + (f" [{cls}]" if cls and "/" not in eprint else "")
    return f"[{text.replace('[', chr(92) + '[').replace(']', chr(92) + ']')}](https://arxiv.org/abs/{eprint})"


def reference(e: Entry) -> str:
    """The entry as one line of markdown, ending with a full stop."""
    who = authors(e)
    year = e.get("year")
    parts = [who] if who else []
    journal = detex(e.get("journal"))
    url = doi_url(e)
    if journal:
        pub = " ".join(x for x in (journal, detex(e.get("volume"))) if x)
        pages = detex(e.get("pages") or e.get("eid"))
        pub = pub + (f", {pages}" if pages else "") + (f" ({year})" if year else "")
        parts.append(_link(pub, url))
    else:
        title = detex(e.get("title"))
        what = []
        if title:
            what.append(f"*{title}*")
        if e.get("version"):
            what.append(f"version {detex(e.get('version'))}")
        where = detex(e.get("publisher") or e.get("howpublished") or e.get("institution")
                      or e.get("school") or e.get("organization"))
        if where and not where.startswith("\\url"):
            what.append(where)
        tail = ", ".join(what) + (f" ({year})" if year else "")
        if tail:
            parts.append(_link(tail, url) if not e.get("eprint") or url else tail)
        elif url:
            parts.append(_link(url, url))
    ax = arxiv(e)
    if ax:
        parts.append(ax)
    return ", ".join(parts).rstrip(".") + "." if parts else f"`{e.key}`."
