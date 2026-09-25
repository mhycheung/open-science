"""The publish review's tone and claims check, scored on a labelled set (manual).

Runs Claude once over tests/fixtures/tone_review/labelled.yaml with the publish skill's
rubric, and reports accuracy. It does not gate on accuracy (plan, S4): the LLM review writes
a report for the user; it never blocks a publish by itself.

    OS_CLAUDE_CMD="claude" tests/run_all --run-manual -k tone_review -s
"""
import json
import os
import random
import re
import shlex
import subprocess
from collections import Counter

import pytest
import yaml

from conftest import REPO

RUBRIC = REPO / "plugins/open-science-publish/skills/publish/reference/review-rubric.md"
LABELLED = REPO / "tests/fixtures/tone_review/labelled.yaml"


def prompt(items) -> str:
    lines = [
        "You are doing the review step of a publish, following this rubric:",
        "", RUBRIC.read_text(), "",
        "Below, each passage is one added line of a document. Its node header is given. Apply",
        "the rubric to each passage separately. Reply with ONLY a JSON object mapping each id to",
        'one of "none", "tone", "claim" (the category you would flag it under, or "none").', "",
    ]
    for it in items:
        lines.append(f"[{it['id']}] header {json.dumps(it['header'])}: {it['text']}")
    return "\n".join(lines)


def score(items, got) -> dict:
    want = {it["id"]: it["expect"] for it in items}
    confusion = Counter((want[i], got.get(i, "missing")) for i in want)
    correct = sum(n for (w, g), n in confusion.items() if w == g)
    criticism = [it["id"] for it in items if it["id"].startswith("c")]
    return {
        "n": len(items),
        "accuracy": correct / len(items),
        "flagged_criticism": [i for i in criticism if got.get(i) != "none"],
        "missed": [i for i in want if want[i] != "none" and got.get(i) != want[i]],
        "false_flags": [i for i in want if want[i] == "none" and got.get(i) != "none"],
        "confusion": {f"{w}->{g}": n for (w, g), n in sorted(confusion.items())},
    }


@pytest.mark.manual
def test_tone_review_accuracy(tmp_path):
    cmd = os.environ.get("OS_CLAUDE_CMD")
    if not cmd:
        pytest.skip("set OS_CLAUDE_CMD to the command that runs Claude Code")
    items = yaml.safe_load(LABELLED.read_text())["items"]
    random.Random(7).shuffle(items)  # labels are grouped in the file; the model must not see that
    r = subprocess.run([*shlex.split(cmd), "-p", prompt(items)], capture_output=True, text=True,
                       timeout=600, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    m = re.search(r"\{.*\}", r.stdout, re.S)
    assert m, r.stdout
    got = json.loads(m.group(0))
    result = score(items, got)
    out = os.environ.get("OS_TONE_REPORT")
    if out:
        with open(out, "w") as f:
            json.dump({"result": result, "answers": got}, f, indent=2)
    print(json.dumps(result, indent=2))
    assert set(got) >= {it["id"] for it in items}  # every passage answered; accuracy is reported, not gated
