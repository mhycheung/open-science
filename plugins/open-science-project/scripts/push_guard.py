#!/usr/bin/env python3
"""Refuse a direct push to a project's public repo (Claude Code and Codex PreToolUse hook).

Publication goes through `opsci publish push` after the user approved the checked export
(AGENTS.md rule 3). This hook catches the direct forms an agent might type by mistake. It
is not a shell interpreter or a security boundary against arbitrary programs. Claude Code
runs `push_guard.py` on Bash commands; Codex runs it through codex_hook.py. Python 3.6+.
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

PUSH_MESSAGE = ('Use opsci publish push after approval of the checked export; direct pushes to '
                'the public repo (git push public, git push --mirror, a push from .opsci/public '
                'or to the public repo\'s URL) are refused.')


def project_for(path):
    directory = Path(os.path.realpath(str(path))).parent
    for candidate in (directory,) + tuple(directory.parents):
        if (candidate / 'AGENTS.md').is_file() and (candidate / 'config/framework.yaml').is_file():
            return candidate
    return None


SHELLS = ('sh', 'bash', 'zsh', 'dash', 'ksh')
GIT_OPTS_WITH_VALUE = ('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--config-env',
                       '--exec-path', '--super-prefix')


def _words(command):
    try:
        # No whitespace_split: before Python 3.8 it keeps `;` and `&&` inside the words.
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.wordchars += ':@%+,{}[]^!$'
        return list(lexer)
    except ValueError:
        return None


def _segments(words):
    seg = []
    for w in words:
        if w and all(c in ';&|()<> ' for c in w):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(w)
    if seg:
        yield seg


def _in_public_checkout(path):
    parts = Path(os.path.realpath(str(path))).parts
    return any(a == '.opsci' and b == 'public' for a, b in zip(parts, parts[1:]))


def _norm_url(url):
    url = url.strip().rstrip('/')
    return url[:-4] if url.endswith('.git') else url


def public_urls(root):
    """The public repo's URLs this project knows: publish/manifest.yaml `public_repo:`, the
    `public` remote, and the origin of the .opsci/public checkout."""
    urls = set()
    try:
        text = (root / 'publish' / 'manifest.yaml').read_text(encoding='utf-8')
        m = re.search(r'^public_repo:\s*["\']?([^"\'\s#]+)', text, re.M)
        if m:
            urls.add(_norm_url(m.group(1)))
    except OSError:
        pass
    for repo, remote in ((root, 'public'), (root / '.opsci' / 'public', 'origin')):
        if not (repo / '.git').exists():
            continue
        try:
            r = subprocess.run(['git', '-C', str(repo), 'config', '--get', 'remote.%s.url' % remote],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               universal_newlines=True, timeout=5)
            if r.stdout.strip():
                urls.add(_norm_url(r.stdout))
        except (OSError, subprocess.SubprocessError):
            pass
    return urls


def direct_public_push(command, cwd=None, root=None, depth=0):
    """True if the command pushes to the public repo directly: `git push public`, `git push
    --mirror`, a push run in or aimed at the .opsci/public checkout (by `cd`, `-C`,
    `--git-dir` or GIT_DIR), or a push to the public repo's URL; also inside `sh -c`,
    `bash -c` and `eval`. This mirrors the Claude project's accidental-push protection. It
    is not a shell interpreter or a security boundary against arbitrary programs (a git
    alias, a script file or a variable still gets past it)."""
    cwd = Path(cwd or os.getcwd())
    words = _words(command)
    if words is None or depth > 3:
        return False
    urls = None
    for seg in _segments(words):
        here = cwd
        if seg[0] == 'cd' and len(seg) > 1:
            cwd = cwd / os.path.expanduser(seg[1])
            continue
        for i, word in enumerate(seg):
            name = Path(word).name
            if name in SHELLS and '-c' in seg[i + 1:]:
                j = seg.index('-c', i + 1)
                if j + 1 < len(seg) and direct_public_push(seg[j + 1], cwd, root, depth + 1):
                    return True
            if name == 'eval' and direct_public_push(' '.join(seg[i + 1:]), cwd, root, depth + 1):
                return True
            if name != 'git':
                continue
            tail, n = seg[i + 1:], 0
            gitdir = here
            while n < len(tail) and tail[n].startswith('-'):
                opt = tail[n]
                if opt == '-C' and n + 1 < len(tail):
                    gitdir = gitdir / os.path.expanduser(tail[n + 1])
                elif opt.startswith(('--git-dir=', '--work-tree=')):
                    gitdir = gitdir / os.path.expanduser(opt.split('=', 1)[1])
                elif opt in ('--git-dir', '--work-tree') and n + 1 < len(tail):
                    gitdir = gitdir / os.path.expanduser(tail[n + 1])
                n += 2 if opt in GIT_OPTS_WITH_VALUE else 1
            if n >= len(tail) or tail[n] != 'push':
                continue
            args = tail[n + 1:]
            if 'public' in args or '--mirror' in args:
                return True
            if _in_public_checkout(gitdir) or any('.opsci/public' in w for w in seg):
                return True
            if root is not None and args:
                if urls is None:
                    urls = public_urls(root)
                if any(_norm_url(a) in urls for a in args if not a.startswith('-')):
                    return True
    return False


def main():
    """Claude Code PreToolUse hook on Bash: exit 2 with the message on stderr."""
    try:
        payload = json.load(sys.stdin)
        inp = payload.get('tool_input') or {}
        command = inp.get('command', '')
        cwd = Path(payload.get('cwd') or os.getcwd())
    except (ValueError, AttributeError):
        return 0
    if not isinstance(command, str):
        return 0
    root = project_for(cwd / '_')
    if root and direct_public_push(command, cwd, root):
        print('open-science-project: ' + PUSH_MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
