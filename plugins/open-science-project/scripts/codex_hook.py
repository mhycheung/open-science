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


HUMAN = re.compile(r"verification\s*:\s*[\"']?human-verified")


def project_for(path):
    directory = path.parent
    for candidate in (directory,) + tuple(directory.parents):
        if (candidate / 'AGENTS.md').is_file() and (candidate / 'config/framework.yaml').is_file():
            return candidate
    return None


def patch_files(command, cwd):
    """Return path/new-lines pairs, including moves; exclude removed/context lines."""
    files = []
    path, added = None, []
    for line in command.splitlines():
        match = re.match(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$', line)
        if match:
            if path is not None:
                files.append((path, '\n'.join(added)))
            path, added = (cwd / match.group(1)).resolve(), []
        elif line.startswith('*** Move to: ') and path is not None:
            files.append((path, '\n'.join(added)))
            path = (cwd / line[len('*** Move to: '):]).resolve()
        elif line.startswith('+') and path is not None:
            added.append(line[1:])
    if path is not None:
        files.append((path, '\n'.join(added)))
    return files


def direct_public_push(command):
    # This mirrors the Claude project's accidental-push protection. It is not
    # a shell interpreter or a security boundary against arbitrary programs.
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return False
    for i, word in enumerate(words):
        if Path(word).name != 'git':
            continue
        tail = []
        for value in words[i + 1:]:
            if value and all(c in ';&|()<> ' for c in value):
                break
            tail.append(value)
        n = 0
        while n < len(tail) and tail[n].startswith('-'):
            n += 2 if tail[n] in ('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--config-env') else 1
        if n < len(tail) and tail[n] == 'push':
            args = tail[n + 1:]
            if 'public' in args or '--mirror' in args:
                return True
    return False


def check(payload, event):
    cwd = Path(payload.get('cwd') or os.getcwd()).resolve()
    tool = payload.get('tool_name', '')
    inp = payload.get('tool_input') or {}
    if not isinstance(inp, dict):
        return []
    command = inp.get('command', '')
    if not isinstance(command, str):
        return []
    if tool in ('Bash', 'exec_command'):
        if event == 'pre' and project_for(cwd / '_') and direct_public_push(command):
            return ['Use opsci publish push after approval of the checked export; '
                    'direct git push public and git push --mirror are refused.']
        return []
    if tool != 'apply_patch':
        return []
    problems = []
    for path, added in patch_files(command, cwd):
        if not project_for(path):
            continue
        if event == 'pre':
            if HUMAN.search(added):
                problems.append("only the user sets 'verification: human-verified' (AGENTS.md rule 5). "
                                "An agent may set 'verified' with an 'evidence:' pointer. Leave "
                                "human-verification to the user, and tell them the result is ready for it.")
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
        for p, _ in changed:
            if (p.name == 'context.md' and p.parent.parent.name in ('tasks', 'verifications')
                    and project_for(p) and p.is_file()):
                n = len(p.read_text(encoding='utf-8').splitlines())
                print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                      'additionalContext': TASK_SAVED.format(n=n, cap=200)}}))
                break
    return 0


if __name__ == '__main__':
    sys.exit(main())
