#!/usr/bin/env python3
"""Codex adapters for project edit checks. Input is hook JSON, never a transcript.

Unlike Claude Edit/Write, apply_patch sends one patch in tool_input.command,
possibly touching several files. Keep the Claude hooks and their payload intact.
"""
import json
import os
from pathlib import Path
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import human_verified  # noqa: E402  (shared with the Claude Code hooks)
from push_guard import PUSH_MESSAGE, direct_public_push  # noqa: E402


def project_for(path):
    directory = Path(os.path.realpath(str(path))).parent
    for candidate in (directory,) + tuple(directory.parents):
        if (candidate / 'AGENTS.md').is_file() and (candidate / 'config/framework.yaml').is_file():
            return candidate
    return None


def patch_files(command, cwd):
    """Return (path, old side, new side) per file, including moves. A side is the hunk text
    as it reads before or after the patch: context lines plus removed or added lines."""
    files = []
    path, old, new = None, [], []
    for line in command.splitlines():
        match = re.match(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$', line)
        if match:
            if path is not None:
                files.append((path, '\n'.join(old), '\n'.join(new)))
            path, old, new = (cwd / match.group(1)).resolve(), [], []
        elif line.startswith('*** Move to: ') and path is not None:
            files.append((path, '\n'.join(old), '\n'.join(new)))
            path = (cwd / line[len('*** Move to: '):]).resolve()
        elif path is None or line.startswith('***'):
            continue
        elif line.startswith('+'):
            new.append(line[1:])
        elif line.startswith('-'):
            old.append(line[1:])
        elif line.startswith(' '):
            old.append(line[1:])
            new.append(line[1:])
    if path is not None:
        files.append((path, '\n'.join(old), '\n'.join(new)))
    return files


def check(payload, event):
    cwd = Path(payload.get('cwd') or os.getcwd()).resolve()
    tool = payload.get('tool_name', '')
    inp = payload.get('tool_input') or {}
    if not isinstance(inp, dict):
        return []
    command = inp.get('command', inp.get('cmd', ''))
    if tool in ('Bash', 'exec_command'):
        if isinstance(command, list):
            command = ' '.join(shlex.quote(str(w)) for w in command)
        if not isinstance(command, str):
            return []
        workdir = inp.get('workdir')
        here = cwd / workdir if isinstance(workdir, str) and workdir else cwd
        root = project_for(here / '_')
        if event == 'pre' and root and direct_public_push(command, here, root):
            return [PUSH_MESSAGE]
        return []
    if not isinstance(command, str):
        return []
    if tool != 'apply_patch':
        return []
    problems = []
    for path, old, new in patch_files(command, cwd):
        if not project_for(path):
            continue
        if event == 'pre':
            if human_verified.adds_human_verified(str(path), old, new):
                problems.append(human_verified.MESSAGE)
        elif path.is_file():
            cap = 200 if path.name == 'context.md' else (
                150 if path.name == 'README.md' and path.parent.name == 'map' else None)
            if cap:
                count = len(path.read_text(encoding='utf-8').splitlines())
                if count > cap:
                    problems.append('{} has {} lines, over its cap of {}. Prune it now: move finished '
                                    'or background material to a subcontext/ file or the log, and keep '
                                    'only what the next agent needs.'.format(path, count, cap))
    return problems


TASK_SAVED = (
    "open-science-project: task context saved ({n}/{cap} lines). If a subtask just finished, check: "
    "(1) did a status or edge change, or a subtask start, finish, fail or branch? update the header "
    "and the task map.md; did a result land, change or fail (something later work relies on, or that "
    "answers part of the task goal; not a debugging finding)? update its file in the task's results/; "
    "run opsci map build; (2) does the next agent need it? update the project context.md; (3) did you "
    "use or consult a source or package? update citations/, and the uses: of any result that relies "
    "on it; (4) one line in the task log.md.")


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else 'pre'
    try:
        payload = json.load(sys.stdin)
        problems = check(payload, event)
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        print('open-science-project: cannot check hook input ({})'.format(type(exc).__name__), file=sys.stderr)
        return 2
    if problems:
        print('open-science-project: ' + '\n'.join(problems), file=sys.stderr)
        return 2
    cwd = Path(payload.get('cwd') or os.getcwd()).resolve()
    inp = payload.get('tool_input') or {}
    command = inp.get('command', '') if isinstance(inp, dict) else ''
    changed = patch_files(command, cwd) if isinstance(command, str) else []
    if event == 'post' and payload.get('tool_name') == 'apply_patch':
        # Same message and condition as context_hook.sh: a saved task or verification context.md.
        for p, _, _ in changed:
            if (p.name == 'context.md' and p.parent.parent.name in ('tasks', 'verifications')
                    and project_for(p) and p.is_file()):
                n = len(p.read_text(encoding='utf-8').splitlines())
                print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                      'additionalContext': TASK_SAVED.format(n=n, cap=200)}}))
                break
    return 0


if __name__ == '__main__':
    sys.exit(main())
