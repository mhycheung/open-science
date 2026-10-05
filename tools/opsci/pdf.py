"""Read the object structure and the page text of a PDF without a PDF library.

The leak scan and the copyright check need the dictionaries and strings of a PDF and the text
shown on its pages, not its compressed bytes, fonts and images. Scanning those bytes raw
produces chance matches (a compressed stream is random bytes), and PDF syntax itself looks like
a path: a list of glyph names is written without spaces, each name starting with a slash.

The page text is read from the string operands of the text operators (Tj, TJ, ' and ") in the
page content streams. A font with a one-byte encoding (pdfLaTeX, matplotlib, most generators)
gives its text directly. A font with two-byte glyph ids (Identity-H, from some word processors)
gives no readable text: decoding it needs the font's ToUnicode map, which this module does not
read.
"""

from __future__ import annotations

import re
import zlib

PDF_MAGIC = b"%PDF-"
_STREAM_RE = re.compile(rb"(?<![A-Za-z])stream\r?\n")
_ENDSTREAM = b"endstream"
# Streams whose content is PDF objects or metadata, not page content, fonts or images.
_READ_TYPES = re.compile(rb"/Type\s*/(?:ObjStm|Metadata)\b")
# Streams that are never page content: images, fonts, colour profiles, embedded files.
_NOT_CONTENT = re.compile(rb"/Subtype\s*/(?:Image|Type1C|CIDFontType0C|OpenType)\b|/Length[123]\b"
                          rb"|/Type\s*/XRef\b")
# Decompressed bytes read from one content stream.
MAX_CONTENT = 64 << 20
_FLATE = re.compile(rb"/Filter\s*\[?\s*/FlateDecode\s*\]?")
_OTHER_FILTER = re.compile(rb"/Filter\b")
_PAGE_RE = re.compile(r"/Type\s*/Page(?![\w])")
# A PDF name: '/' then any characters but whitespace and delimiters.
_NAME_RE = re.compile(r"/[^\s()<>\[\]{}/%]*")
_NEXT_RE = re.compile(r"[(/<]")
_HEX_RE = re.compile(r"<[0-9A-Fa-f\s]*>")
_CHARSET_RE = re.compile(r"/CharSet\s*$")


def is_pdf(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(PDF_MAGIC)


def objects_text(data: bytes, page_text: list | None = None) -> str:
    """The PDF with every stream body removed, except object and metadata streams, which are
    decompressed in place. Removed bodies keep their line breaks, so line numbers match the
    file up to the first decompressed stream. If ``page_text`` is a list, the text shown by
    each other stream that can be decompressed (page content, forms) is appended to it."""
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
        elif page_text is not None and not _NOT_CONTENT.search(head):
            content = None
            if _FLATE.search(head):
                try:
                    content = zlib.decompressobj().decompress(body, MAX_CONTENT)
                except zlib.error:
                    content = None
            elif not _OTHER_FILTER.search(head):
                content = body
            if content:
                page_text.append(shown_text(content))
        out.append(text if text is not None else "\n" * body.count(b"\n"))
        pos = end
    out.append(data[pos:].decode("latin-1"))
    return "".join(out)


# Content stream tokens: a literal string, a hex string, an array bracket, a dictionary
# bracket, a name, a number or an operator.
_TOKEN = re.compile(rb"\(|<[0-9A-Fa-f\s]*>|<<|>>|\[|\]|/[^\s()<>\[\]{}/%]*|[-+.\d]+|[A-Za-z'\"*]+|%[^\r\n]*")
_SHOW_OPS = {b"Tj", b"TJ", b"'", b'"'}
# Operators that move to a new line or end a text object.
_BREAK_OPS = {b"Td", b"TD", b"T*", b"Tm", b"ET", b"BT", b"'", b'"'}
# A TJ adjustment this negative (thousandths of the font size) is a space between words.
_TJ_SPACE = -200
_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _literal(data: bytes, i: int) -> tuple[bytes, int]:
    """(the bytes of the literal string that opens at data[i], the index after it)."""
    out, depth, j, n = bytearray(), 1, i + 1, len(data)
    while j < n:
        c = data[j:j + 1]
        if c == b"\\":
            nxt = data[j + 1:j + 2]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                j += 2
            elif nxt.isdigit():
                m = re.match(rb"[0-7]{1,3}", data[j + 1:j + 4])
                out.append(int(m.group(), 8) & 0xFF if m else 0)
                j += 1 + (len(m.group()) if m else 1)
            elif nxt in (b"\r", b"\n"):
                j += 2 + (data[j + 1:j + 3] == b"\r\n")
            else:
                out += nxt
                j += 2
            continue
        if c == b"(":
            depth += 1
        elif c == b")":
            depth -= 1
            if depth == 0:
                return bytes(out), j + 1
        out += c
        j += 1
    return bytes(out), j


def _hex(token: bytes) -> bytes:
    digits = re.sub(rb"\s", b"", token[1:-1])
    return bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode())


def decode_string(raw: bytes) -> str:
    """A PDF string's text: UTF-16 after a byte order mark (how PDF writes non-Latin text in
    metadata), else Latin-1 (close to PDFDocEncoding)."""
    if raw[:2] == b"\xfe\xff":
        return raw[2:].decode("utf-16-be", "replace")
    if raw[:2] == b"\xff\xfe":
        return raw[2:].decode("utf-16-le", "replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "replace")
    return raw.decode("latin-1")


def shown_text(content: bytes) -> str:
    """The text a content stream shows: the string operands of its text operators, one line
    per text line. Inline image data (BI ... ID ... EI) is skipped."""
    lines, line, operands, array, i, n = [], [], [], None, 0, len(content)
    while i < n:
        m = _TOKEN.search(content, i)
        if m is None:
            break
        tok, i = m.group(), m.end()
        if tok == b"(":
            raw, i = _literal(content, m.start())
            (array if array is not None else operands).append(raw)
        elif tok.startswith(b"<") and tok != b"<<":
            (array if array is not None else operands).append(_hex(tok))
        elif tok == b"[":
            array = []
        elif tok == b"]":
            operands.append(array or [])
            array = None
        elif array is not None and re.fullmatch(rb"[-+]?\d*\.?\d+", tok):
            if float(tok) <= _TJ_SPACE:
                array.append(b" ")
        elif re.fullmatch(rb"[A-Za-z'\"*]+", tok):
            if tok == b"ID":  # inline image data up to EI
                end = re.compile(rb"\sEI(?=\s|$)").search(content, i)
                i = end.end() if end else n
            elif tok in _SHOW_OPS:
                if tok in (b"'", b'"'):
                    lines.append("".join(line))
                    line = []
                for op in operands:
                    parts = op if isinstance(op, list) else [op]
                    line.append("".join(decode_string(p) for p in parts if isinstance(p, bytes)))
            elif tok in _BREAK_OPS and line:
                lines.append("".join(line))
                line = []
            operands = []
    if line:
        lines.append("".join(line))
    return "\n".join(l for l in lines if l.strip())


def page_text(data: bytes) -> str:
    """The text shown on the pages (and in forms) of a PDF."""
    found: list[str] = []
    objects_text(data, found)
    return "\n".join(t for t in found if t)


def page_count(data: bytes) -> int:
    return len(_PAGE_RE.findall(objects_text(data)))


def _blank(s: str) -> str:
    return re.sub(r"[^\n]", " ", s)


def scan_text(data: bytes) -> str:
    """objects_text with PDF names and /CharSet glyph lists blanked out, leaving the strings
    (titles, authors, file names) and other values that can carry a leak; hex strings and
    UTF-16 strings decoded to text; then the text shown on the pages."""
    shown: list[str] = []
    text = objects_text(data, shown)
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
        if text[i] == "<":
            h = _HEX_RE.match(text, i)
            if text.startswith("<<", i) or h is None:
                out.append(text[i:i + 2] if text.startswith("<<", i) else "<")
                i += 2 if text.startswith("<<", i) else 1
                continue
            raw = _hex(h.group().encode("latin-1"))
            out.append("<" + decode_string(raw).replace("\n", " ").replace("\r", " ") + ">"
                       + "\n" * h.group().count("\n"))
            i = h.end()
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
        if _CHARSET_RE.search(text[max(0, i - 40):i]):
            s = _blank(s)
        elif s.startswith(("(\xfe\xff", "(\xff\xfe", "(\\376\\377", "(\\377\\376")):
            # A UTF-16 string: its NUL bytes would split every word.
            s = "(" + decode_string(_literal(s.encode("latin-1"), 0)[0]).replace("\n", " ") + ")"
        out.append(s)
        i = j
    return "".join(out) + "".join("\n" + t for t in shown if t)
