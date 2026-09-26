"""Read the object structure of a PDF without a PDF library.

The leak scan and the copyright check need the dictionaries and strings of a PDF, not its
compressed page content, fonts and images. Scanning those bytes raw produces chance matches
(a compressed stream is random bytes), and PDF syntax itself looks like a path: a list of
glyph names is written without spaces, each name starting with a slash.
"""

from __future__ import annotations

import re
import zlib

PDF_MAGIC = b"%PDF-"
_STREAM_RE = re.compile(rb"(?<![A-Za-z])stream\r?\n")
_ENDSTREAM = b"endstream"
# Streams whose content is PDF objects or metadata, not page content, fonts or images.
_READ_TYPES = re.compile(rb"/Type\s*/(?:ObjStm|Metadata)\b")
_FLATE = re.compile(rb"/Filter\s*\[?\s*/FlateDecode\s*\]?")
_OTHER_FILTER = re.compile(rb"/Filter\b")
_PAGE_RE = re.compile(r"/Type\s*/Page(?![\w])")
# A PDF name: '/' then any characters but whitespace and delimiters.
_NAME_RE = re.compile(r"/[^\s()<>\[\]{}/%]*")
_NEXT_RE = re.compile(r"[(/]")
_CHARSET_RE = re.compile(r"/CharSet\s*$")


def is_pdf(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(PDF_MAGIC)


def objects_text(data: bytes) -> str:
    """The PDF with every stream body removed, except object and metadata streams, which are
    decompressed in place. Removed bodies keep their line breaks, so line numbers match the
    file up to the first decompressed stream."""
    out, pos = [], 0
    for m in _STREAM_RE.finditer(data):
        if m.start() < pos:
            continue
        end = data.find(_ENDSTREAM, m.end())
        if end < 0:
            break
        head = data[max(pos, data.rfind(b"obj", pos, m.start())):m.start()]
        body = data[m.end():end]
        out.append(data[pos:m.end()].decode("latin-1"))
        text = None
        if _READ_TYPES.search(head):
            if _FLATE.search(head):
                try:
                    text = zlib.decompress(body).decode("latin-1")
                except zlib.error:
                    text = None
            elif not _OTHER_FILTER.search(head):
                text = body.decode("latin-1")
        out.append(text if text is not None else "\n" * body.count(b"\n"))
        pos = end
    out.append(data[pos:].decode("latin-1"))
    return "".join(out)


def page_count(data: bytes) -> int:
    return len(_PAGE_RE.findall(objects_text(data)))


def _blank(s: str) -> str:
    return re.sub(r"[^\n]", " ", s)


def scan_text(data: bytes) -> str:
    """objects_text with PDF names and /CharSet glyph lists blanked out, leaving the strings
    (titles, authors, file names) and other values that can carry a leak."""
    text = objects_text(data)
    out, i, n = [], 0, len(text)
    while i < n:
        m = _NEXT_RE.search(text, i)
        if m is None:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        i = m.start()
        if text[i] == "/":
            name = _NAME_RE.match(text, i).group()
            out.append(_blank(name) if len(name) > 1 else "/")
            i += len(name)
            continue
        # A literal string: balanced parentheses, backslash escapes.
        j, depth = i + 1, 1
        while j < n and depth:
            c = text[j]
            if c == "\\":
                j += 2
                continue
            depth += 1 if c == "(" else -1 if c == ")" else 0
            j += 1
        s = text[i:j]
        out.append(_blank(s) if _CHARSET_RE.search(text[max(0, i - 40):i]) else s)
        i = j
    return "".join(out)
