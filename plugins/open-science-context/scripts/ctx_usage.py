#!/usr/bin/env python3
"""Report a Claude Code session's current context usage, read from its transcript.

Current context = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
of the LAST main-agent assistant message in the transcript. That reading lags the
true value by one assistant message, which is immaterial at a 250k threshold.

Written for Python 3.6 (the oldest `python3` it is expected to meet): no f-string
`=`, no `datetime.fromisoformat`, no dataclasses, no walrus.

Usage
-----
    ctx_usage.py                          # this session, from $CLAUDE_CODE_SESSION_ID
    ctx_usage.py --transcript FILE.jsonl  # a specific transcript
    ctx_usage.py --session-id SID
    ctx_usage.py --json                   # machine-readable
    ctx_usage.py --subagents              # per-subagent usage for the same session
    ctx_usage.py --transcript AGENT.jsonl --sidechain    # ONE subagent's own usage

`--sidechain` is required when pointing `--transcript` at a subagent transcript
(`.../<session-id>/subagents/agent-<agent-id>.jsonl`). Subagent messages are
marked `isSidechain`, which the default reading skips; without the flag such a
file reports "no assistant messages with usage yet".

Prints the integer token count on stdout by default, so it can be used as
    ctx=$(ctx_usage.py) || ctx=0
Exit status 0 on a successful reading, 1 on any failure (message on stderr).
"""

from __future__ import print_function

import argparse
import glob
import json
import os
import sys
from datetime import datetime

DEFAULT_WINDOW = int(os.environ.get("OPSCI_CONTEXT_WINDOW", "1000000"))

# Config dirs that may hold a `projects/` transcript tree: $CLAUDE_CONFIG_DIR
# (set when Claude Code runs with a non-default config) and the default ~/.claude.
CONFIG_DIRS = [
    os.environ.get("CLAUDE_CONFIG_DIR", ""),
    os.path.expanduser("~/.claude"),
]


def _iter_config_dirs():
    seen = set()
    for d in CONFIG_DIRS:
        if not d:
            continue
        d = os.path.abspath(os.path.expanduser(d))
        if d in seen or not os.path.isdir(d):
            continue
        seen.add(d)
        yield d


def find_transcript(session_id):
    """Locate <config>/projects/*/<session_id>.jsonl. Newest wins if several."""
    hits = []
    for cdir in _iter_config_dirs():
        pattern = os.path.join(cdir, "projects", "*", session_id + ".jsonl")
        hits.extend(glob.glob(pattern))
    if not hits:
        return None
    hits.sort(key=lambda p: os.path.getmtime(p))
    return hits[-1]


def parse_ts(s):
    """'2026-08-02T21:44:18.880Z' -> datetime, or None. 3.6-safe."""
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None


def _tokens(usage):
    if not isinstance(usage, dict):
        return None
    total = 0
    for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        v = usage.get(k)
        if isinstance(v, int):
            total += v
    return total


def read_usage(path, main_only=True):
    """Return a dict describing the last assistant message's context usage.

    main_only=True skips sidechain (subagent) messages, whose usage describes the
    subagent's own context, not the main agent's.
    """
    result = {
        "transcript": path,
        "tokens": None,
        "assistant_messages": 0,
        "timestamp": None,
        "session_id": None,
        "output_tokens": None,
        "cache_read": None,
        "cache_creation": None,
        "model": None,
        "first_timestamp": None,
        "session_age_seconds": None,
    }
    if not path or not os.path.isfile(path):
        result["error"] = "transcript not found: %s" % path
        return result

    last = None
    first_ts = None
    n = 0
    try:
        with open(path, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue          # a partially-flushed final line is normal
                if first_ts is None and d.get("timestamp"):
                    first_ts = d["timestamp"]
                if d.get("type") != "assistant":
                    continue
                if main_only and d.get("isSidechain") is True:
                    continue
                usage = (d.get("message") or {}).get("usage")
                if _tokens(usage) is None:
                    continue
                n += 1
                last = d
    except (IOError, OSError) as exc:
        result["error"] = "cannot read transcript: %s" % exc
        return result

    result["assistant_messages"] = n
    result["first_timestamp"] = first_ts
    started = parse_ts(first_ts)
    if started is not None:
        # Transcript timestamps are UTC ("…Z"); compare against UTC now.
        result["session_age_seconds"] = int(
            (datetime.utcnow() - started).total_seconds())
    if last is None:
        result["error"] = "no assistant messages with usage yet"
        return result

    msg = last.get("message") or {}
    usage = msg.get("usage") or {}
    result["tokens"] = _tokens(usage)
    result["timestamp"] = last.get("timestamp")
    result["session_id"] = last.get("sessionId")
    result["output_tokens"] = usage.get("output_tokens")
    result["cache_read"] = usage.get("cache_read_input_tokens")
    result["cache_creation"] = usage.get("cache_creation_input_tokens")
    result["model"] = msg.get("model")
    return result


def subagent_report(transcript_path, session_id):
    """Per-subagent usage for a session.

    Subagent transcripts live beside the parent's, at
    <projects>/<slug>/<parent-session-id>/subagents/agent-*.jsonl, each with a
    sibling agent-*.meta.json carrying {agentType, description}.
    """
    if not transcript_path:
        return []
    base = os.path.join(os.path.dirname(transcript_path), session_id, "subagents")
    out = []
    for jf in sorted(glob.glob(os.path.join(base, "agent-*.jsonl"))):
        info = read_usage(jf, main_only=False)
        meta_path = jf[: -len(".jsonl")] + ".meta.json"
        meta = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path) as fh:
                    meta = json.load(fh)
            except (ValueError, IOError, OSError):
                meta = {}
        info["agent_type"] = meta.get("agentType")
        info["description"] = meta.get("description")
        info["agent_file"] = os.path.basename(jf)
        out.append(info)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--transcript", help="path to a transcript .jsonl")
    ap.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID"),
                    help="session id (default: $CLAUDE_CODE_SESSION_ID)")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                    help="context window for the percentage (default %d)" % DEFAULT_WINDOW)
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--subagents", action="store_true",
                    help="also report per-subagent usage")
    ap.add_argument("--sidechain", action="store_true",
                    help="read sidechain (subagent) messages; required when "
                         "--transcript points at an agent-*.jsonl")
    args = ap.parse_args(argv)

    path = args.transcript
    if not path:
        if not args.session_id:
            print("no --transcript and no session id ($CLAUDE_CODE_SESSION_ID unset)",
                  file=sys.stderr)
            return 1
        path = find_transcript(args.session_id)
        if not path:
            print("no transcript found for session %s" % args.session_id, file=sys.stderr)
            return 1

    info = read_usage(path, main_only=not args.sidechain)
    if args.subagents:
        sid = info.get("session_id") or args.session_id
        info["subagents"] = subagent_report(path, sid) if sid else []

    if args.json:
        info["window"] = args.window
        if info.get("tokens") is not None:
            info["pct"] = round(100.0 * info["tokens"] / args.window, 1)
        print(json.dumps(info, indent=1, sort_keys=True))
        return 0 if info.get("tokens") is not None else 1

    if info.get("tokens") is None:
        print(info.get("error", "unknown error"), file=sys.stderr)
        return 1

    print(info["tokens"])
    if args.subagents:
        for s in info.get("subagents", []):
            print("  %-28s %-22s %s" % (
                s.get("agent_file", "?"),
                (s.get("agent_type") or "-")[:22],
                s.get("tokens") if s.get("tokens") is not None else s.get("error", "?"),
            ), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
