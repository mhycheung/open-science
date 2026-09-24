"""Tests for the optional slurm-resurrect plugin.

The behaviour tests are shell scripts in tests/slurm_resurrect/ (private tmux
server, private state dirs, stub scheduler; no SLURM or Claude needed). This
file runs them and adds static checks on the plugin. The real-scheduler hop is
marked manual.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO
from opsci.nodes import list_files

PLUGIN = REPO / "plugins" / "slurm-resurrect"
SHELL_TESTS = REPO / "tests" / "slurm_resurrect"
NEEDS = [t for t in ("bash", "tmux", "jq", "flock", "setsid") if shutil.which(t) is None]


def run_shell_test(name, timeout=900, env=None):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(["bash", str(SHELL_TESTS / name)], capture_output=True, text=True,
                       timeout=timeout, env=e)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and re.search(r"=== \d+ passed, 0 failed ===", out), out[-4000:]
    return out


@pytest.mark.skipif(bool(NEEDS), reason=f"missing tools: {NEEDS}")
@pytest.mark.parametrize("name", ["test_registration.sh", "test_settings_carry.sh",
                                  "test_queue_modes.sh", "test_jump_recovery.sh",
                                  "test_trust_dialog.sh"])
def test_shell_suite(name):
    run_shell_test(name)


# ---- static checks, each with a case it must refuse -------------------------------

def syntax_errors(paths):
    return [p.name for p in paths
            if subprocess.run(["bash", "-n", str(p)], capture_output=True).returncode != 0]


def test_scripts_parse(tmp_path):
    scripts = sorted((PLUGIN / "scripts").glob("*.sh")) + sorted(SHELL_TESTS.glob("*.sh"))
    assert scripts and syntax_errors(scripts) == []
    broken = tmp_path / "broken.sh"
    broken.write_text("if then fi\n")
    assert syntax_errors([broken]) == ["broken.sh"]


def hook_commands(hooks_json):
    data = json.loads(hooks_json)
    return [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]]


def test_hooks_point_at_existing_scripts():
    cmds = hook_commands((PLUGIN / "hooks" / "hooks.json").read_text())
    assert cmds
    for c in cmds:
        m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"\s]+)", c)
        assert m and (PLUGIN / m.group(1)).is_file(), c
    json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    with pytest.raises(json.JSONDecodeError):
        hook_commands('{"hooks": {')


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    return dict(re.findall(r"^([\w-]+):\s*(.*)$", m.group(1), re.M)) if m else {}


def user_only(skill_text):
    return frontmatter(skill_text).get("disable-model-invocation") == "true"


def test_skill_is_user_only():
    assert user_only((PLUGIN / "skills" / "resurrect" / "SKILL.md").read_text())
    assert not user_only("---\nname: resurrect\ndescription: x\n---\nbody\n")


README_SECTIONS = ["Requirements", "Enable", "Disable", "Remote Control and permission mode",
                   "Queueing", "Session jumps"]


def missing_sections(text):
    heads = set(re.findall(r"^#+\s+(.*?)\s*$", text, re.M))
    return [s for s in README_SECTIONS if s not in heads]


def test_readme_has_required_sections():
    text = (PLUGIN / "README.md").read_text()
    assert missing_sections(text) == []
    assert "tmux" in text and "bypassPermissions" in text
    assert missing_sections("# slurm-resurrect\n## Enable\n") != []


RETIRED = re.compile(r"jump_exec\.sh|wait_watcher\.sh|CM_SCRIPTS_DIR|CM_JUMP_STATE_DIR")


def retired_interface_hits(root):
    hits = []
    for rel in list_files(root):
        p = root / rel
        if p.suffix in (".sh", ".md", ".json") and RETIRED.search(p.read_text(errors="replace")):
            hits.append(rel)
    return hits


def test_no_retired_jump_interface(tmp_path):
    assert retired_interface_hits(PLUGIN) == []
    (tmp_path / "old.sh").write_text("bash \"$CM_SCRIPTS_DIR/jump_exec.sh\"\n")
    assert retired_interface_hits(tmp_path) == ["old.sh"]


# plugins/open-science (onboarding) is left out on purpose: it offers to install this plugin.
CORE_DIRS = ["plugins/open-science-publish", "plugins/open-science-project",
             "plugins/open-science-context", "tools", "template"]
MENTION = re.compile(r"slurm-resurrect|slurm_resurrect|rr_registry")


def core_mentions(root, dirs):
    hits = []
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for rel in list_files(base):
            p = base / rel
            try:
                if MENTION.search(p.read_text()):
                    hits.append(f"{d}/{rel}")
            except UnicodeDecodeError:
                continue
    return hits


def test_core_does_not_depend_on_slurm_resurrect(tmp_path):
    assert core_mentions(REPO, CORE_DIRS) == []
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "x.py").write_text("import subprocess  # calls slurm-resurrect\n")
    assert core_mentions(tmp_path, ["tools"]) == ["tools/x.py"]


@pytest.mark.manual
def test_real_hop():
    """One real hop per queue mode. Needs SLURM; set RR_TEST_WORKDIR to a directory on
    a filesystem shared with the compute nodes. Uses at most 60 core-minutes; it waits
    for the queue (RR_TEST_QUEUE_WAIT, default 6 h per job)."""
    wd = os.environ.get("RR_TEST_WORKDIR")
    if not wd:
        pytest.skip("set RR_TEST_WORKDIR to a shared directory")
    r = subprocess.run(["bash", str(SHELL_TESTS / "real_hop.sh"), wd, "both"],
                       capture_output=True, text=True, timeout=None)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
