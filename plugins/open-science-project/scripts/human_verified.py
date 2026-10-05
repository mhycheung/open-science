#!/usr/bin/env python3
"""Find `verification: human-verified` in text, in any YAML spelling.

Shared by the Claude Code hook (human_verified_guard.sh) and the Codex hook (codex_hook.py).
Only the user sets `verification: human-verified` (AGENTS.md rule 5). The hooks refuse an
agent edit after which a file holds more such settings than before. They guard against
mistakes; they are not a security boundary (Bash is not checked; the publish check is the
backstop).

Two counts, and an edit is refused if either grows:
- a YAML count: the file's YAML (front matter of a markdown file, or a whole .yaml file) is
  parsed with PyYAML, if installed, and every mapping key `verification` whose value is the
  string `human-verified` is counted. This handles quoting, escapes, tags, block scalars,
  anchors and flow mappings.
- a text count, which needs no PyYAML and also works on fragments (an Edit's new_string, a
  patch hunk): the text is normalised (escapes decoded, quotes, tags and anchors removed)
  and `verification: human-verified` is searched for, with the value allowed on the next
  line or after a block scalar indicator.

Runs on Python 3.6 or later (the system python3 of some clusters).
"""
import json
import os
import re
import sys

MESSAGE = ("only the user sets 'verification: human-verified' (AGENTS.md rule 5). An agent "
           "may set 'verified' with an 'evidence:' pointer. Leave human-verification to the "
           "user, and tell them the result is ready for it.")

try:
    import yaml
except ImportError:  # the text count still runs
    yaml = None

_ESCAPE = re.compile(r'\\(?:x([0-9A-Fa-f]{2})|u([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8}))')
_TAG = re.compile(r'!(?:<[^>]*>|[^\s,\]}]*)')
_END = r'(?![\w-])'
_ANCHOR_DEF = re.compile(r'&([^\s,\]}]+)\s*human-verified' + _END)
_SETTING = re.compile(r'verification\s*:\s*(?:[|>][-+0-9]*\s*)?(?:&[^\s,\]}]+\s*)?human-verified' + _END)
_ALIAS = re.compile(r'verification\s*:\s*\*([^\s,\]}]+)')


def _unescape(m):
    code = m.group(1) or m.group(2) or m.group(3)
    try:
        return chr(int(code, 16))
    except (ValueError, OverflowError):
        return m.group(0)


def normalise(text):
    text = _ESCAPE.sub(_unescape, text)
    text = text.replace('\\', '')            # "human\-verified", escaped line ends
    text = text.replace('"', '').replace("'", '')
    return _TAG.sub(' ', text)


def text_count(text):
    t = normalise(text)
    anchors = set(_ANCHOR_DEF.findall(t))
    return len(_SETTING.findall(t)) + sum(1 for a in _ALIAS.findall(t) if a in anchors)


def _yaml_text(path, text):
    """The YAML a file holds: a markdown file's front matter, or a whole .yaml file."""
    name = os.path.basename(path or '')
    if name.endswith(('.yaml', '.yml')):
        return text
    lines = text.splitlines()
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() in ('---', '...'):
                return '\n'.join(lines[1:i])
    return None


def _walk(obj, seen):
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'verification' and isinstance(v, str) and v.strip() == 'human-verified':
                n += 1
            n += _walk(v, seen)
    elif isinstance(obj, list):
        for v in obj:
            n += _walk(v, seen)
    return n


def yaml_count(path, text, use_yaml=True):
    if yaml is None or not use_yaml or not text:
        return 0
    src = _yaml_text(path, text)
    if not src:
        return 0
    try:
        return sum(_walk(doc, set()) for doc in yaml.safe_load_all(src))
    except Exception:  # not valid YAML: the text count decides
        return 0


def adds_human_verified(path, old, new, use_yaml=True):
    """True if `new` (the text after an edit) holds more human-verified settings than `old`."""
    old, new = old or '', new or ''
    return (text_count(new) > text_count(old)
            or yaml_count(path, new, use_yaml) > yaml_count(path, old, use_yaml))


def project_for(path):
    """The template project holding `path` (symlinks resolved), or None."""
    d = os.path.dirname(os.path.realpath(path))
    while True:
        if os.path.isfile(os.path.join(d, 'AGENTS.md')) and os.path.isfile(
                os.path.join(d, 'config', 'framework.yaml')):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _read(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except (OSError, ValueError):
        return ''


def _replace(text, old, new, every):
    if not old or old not in text:
        return text
    return text.replace(old, new) if every else text.replace(old, new, 1)


def claude_refuses(payload):
    """True if a Claude Code Edit, MultiEdit, Write or NotebookEdit would add a setting."""
    inp = payload.get('tool_input') or {}
    if not isinstance(inp, dict):
        return False
    path = inp.get('file_path') or inp.get('notebook_path')
    if not isinstance(path, str) or not path:
        return False
    if not os.path.isabs(path):
        path = os.path.join(payload.get('cwd') or os.getcwd(), path)
    if project_for(path) is None:
        return False
    current = _read(path) if os.path.isfile(path) else ''
    tool = payload.get('tool_name', '')
    if 'new_source' in inp:  # NotebookEdit: one cell's new source
        return text_count(str(inp.get('new_source') or '')) > 0
    if 'content' in inp:  # Write
        return adds_human_verified(path, current, str(inp.get('content') or ''))
    edits = inp.get('edits') if isinstance(inp.get('edits'), list) else [inp]
    after = current
    for e in edits:
        if not isinstance(e, dict):
            continue
        old_s, new_s = str(e.get('old_string') or ''), str(e.get('new_string') or '')
        # The fragment on its own: Claude Code may match old_string where an exact search
        # does not, so the fragment is checked as well as the whole file.
        if text_count(new_s) > text_count(old_s):
            return True
        after = _replace(after, old_s, new_s, bool(e.get('replace_all')))
    return tool != 'NotebookEdit' and adds_human_verified(path, current, after)


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if isinstance(payload, dict) and claude_refuses(payload):
        print('open-science-project: ' + MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
