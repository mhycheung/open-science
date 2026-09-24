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
