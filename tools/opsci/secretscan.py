"""The secrets scan: tokens, keys and passwords in a tree.

Built-in patterns always run. When ``gitleaks`` is on PATH it runs as well, over the same
tree, and its findings are added. Secrets should never be in the private repo either; this
scan is the last line of defence, not the first.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .leakscan import Hit, Pattern, _p, scan_tree

# A value that is only a placeholder or a variable reference is not a secret.
_PLACEHOLDER = r"(?!\s*[\"']?(?:<|\$\{?|%\(|\{\{|x{4,}|\*{4,}|changeme|your[_-]|example|dummy|fake|redacted))"



def _mixed_alnum(lo: int, hi: int) -> str:
    """A standalone run of lo..hi letters and digits holding an upper case letter, a lower case
    letter and a digit: a random token, not a word, a hex hash or a number."""
    run = f"[A-Za-z0-9]{{{lo},{hi}}}"
    return (rf"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[0-9]){run}"
            r"(?![A-Za-z0-9])")


def _mixed_b64(n: int) -> str:
    """A standalone run of exactly n base64 characters with upper and lower case letters."""
    return (rf"(?<![A-Za-z0-9/+])(?=[A-Za-z0-9/+]{{0,{n - 1}}}[A-Z])(?=[A-Za-z0-9/+]{{0,{n - 1}}}[a-z])"
            rf"[A-Za-z0-9/+]{{{n}}}(?![A-Za-z0-9/+=])")


SECRET_PATTERNS: tuple[Pattern, ...] = (
    _p("private-key", r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY(?: BLOCK)?-----", "a private key"),
    _p("slack-token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}", "a Slack token"),
    _p("slack-webhook", r"hooks\.slack\.com/services/[A-Za-z0-9/]{20,}", "a Slack webhook URL"),
    _p("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
       "a GitHub token"),
    _p("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "an AWS access key id"),
    _p("google-api-key", r"\bAIza[0-9A-Za-z_\-]{35}\b", "a Google API key"),
    _p("llm-api-key", r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}", "an Anthropic or OpenAI API key"),
    _p("assigned-secret",
       r"(?i)\b[\w.-]*(?:token|secret|passw(?:or)?d|api[_-]?key|access[_-]?key)[\w.-]*[\"']?"
       r"\s*[:=]\s*" + _PLACEHOLDER + r"[\"']?[A-Za-z0-9+/_\-.=]{16,}",
       "a token, password or key assigned to a variable"),
    _p("url-credentials", r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]{6,}@[\w.-]+",
       "a password inside a URL"),
    # Bare tokens, written without a `KEY=` in front. A prefix makes these exact: Notion's
    # integration tokens start with `ntn_` (and `secret_` before 2024, then 43 letters and digits).
    _p("notion-token", r"\b(?:ntn_[A-Za-z0-9]{40,}|secret_[A-Za-z0-9]{43})(?![A-Za-z0-9])",
       "a Notion integration token"),
    # An AWS secret key has no prefix: 40 characters of base64. It is flagged only next to its key
    # id or the words "aws" and "secret" on the same line (a credentials CSV, a pasted pair), and
    # only with both upper and lower case letters, so a 40-digit hex git hash never matches.
    _p("aws-secret-key",
       r"(?:\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|(?i:aws[\w .-]{0,20}secret))[^\n]{0,80}?" + _mixed_b64(40),
       "an AWS secret access key"),
    # A Zenodo token is 60 letters and digits, with no prefix. Flagged with the word "zenodo" on
    # the same line, or alone on its line (a token file); in both cases only with upper case, lower
    # case and digits mixed, so hex hashes and words do not match.
    _p("zenodo-token", r"(?i:zenodo)[^\n]{0,60}?" + _mixed_alnum(56, 64), "a Zenodo token"),
    _p("bare-token", r"^\s*[\"']?" + _mixed_alnum(56, 64) + r"[\"']?\s*$",
       "a line holding only a 56-64 character token (a Zenodo or other access token)"),
)


def gitleaks_available() -> bool:
    return shutil.which("gitleaks") is not None


def _gitleaks(root: Path) -> list[Hit]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(["gitleaks", "dir", str(root), "--no-banner", "--exit-code", "0",
                        "--report-format", "json", "--report-path", str(report)],
                       capture_output=True, text=True)
        if not report.is_file():
            return [Hit("-", "content", "gitleaks", None, "gitleaks produced no report", "")]
        findings = json.loads(report.read_text() or "[]")
    hits = []
    for f in findings:
        rel = Path(f.get("File", "-"))
        try:
            rel = rel.relative_to(root)
        except ValueError:
            pass
        hits.append(Hit(rel.as_posix(), "content", f"gitleaks:{f.get('RuleID', '?')}",
                        f.get("StartLine"), "(secret not shown)", "(redacted)"))
    return hits


def scan(root: Path, files=None, honour_planted_marker: bool = False) -> tuple[list[Hit], list[str]]:
    """(hits, names of the scanners that ran). Matched secrets are redacted in the hits.

    gitleaks, when present, scans the whole of ``root`` whatever ``files`` says.
    """
    hits = [Hit(h.path, h.where, h.pattern, h.line_number, _redact(h.line, h.match),
                _redact(h.match, h.match))
            for h in scan_tree(root, SECRET_PATTERNS, files, honour_planted_marker)]
    ran = ["built-in patterns"]
    if gitleaks_available():
        hits += _gitleaks(Path(root))
        ran.append("gitleaks")
    return hits, ran


def _redact(text: str, secret: str) -> str:
    keep = secret[:6]
    return text.replace(secret, keep + "…(redacted)")
