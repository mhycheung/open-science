# opsci: planted-leaks (this file plants fake tokens as refusal cases)
"""Onboarding helpers: the environment check and the credentials-file helper.

Each check has a case it must refuse or flag. Neither script may print a token.
"""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from conftest import REPO

SCRIPTS = REPO / "plugins" / "open-science" / "scripts"
CHECK = SCRIPTS / "onboard_check.sh"
SECRET = SCRIPTS / "secret_file.sh"
SLACK_TOKEN = "xox" + "b-000000000000-FAKEFAKEFAKE0000"  # split so secret scanners see no token literal
ZENODO_TOKEN = "FAKEzenodoTOKEN0123456789abcdefFAKE"

# Commands the scripts need, linked into a private bin so that tmux, sbatch, claude, ssh
# and opsci are absent unless a test adds a stub.
TOOLS = ("bash", "sh", "env", "git", "stat", "awk", "sed", "grep", "id", "dirname", "basename",
         "cat", "tr", "wc", "tail", "chmod", "mkdir", "timeout", "cp", "printf")


def make_bin(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    for t in TOOLS:
        p = shutil.which(t)
        if p:
            (b / t).symlink_to(p)
    return b


def stub(b, name, body):
    p = b / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def env_for(tmp_path, b, **extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": str(b), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
           "XDG_CONFIG_HOME": str(home / ".config"), "XDG_STATE_HOME": str(home / ".state"),
           "CLAUDE_PLUGIN_ROOT": str(REPO / "plugins" / "open-science"),
           "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}
    env.update(extra)
    return env


def check(tmp_path, b, **extra):
    r = subprocess.run(["bash", str(CHECK), "--no-network"], env=env_for(tmp_path, b, **extra),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return dict(line.split("=", 1) for line in r.stdout.splitlines()), r.stdout + r.stderr


def test_check_bare_machine_reports_everything_missing(tmp_path):
    kv, _ = check(tmp_path, make_bin(tmp_path))
    assert kv["tmux"] == "missing" and kv["in_tmux"] == "no"
    assert kv["slurm"] == "missing" and kv["batch_job"] == "no"
    assert kv["batch_tools"].startswith("missing:") and "sbatch" in kv["batch_tools"]
    assert kv["claude"] == "missing" and kv["opsci"] == "missing"
    for p in ("open_science_publish", "open_science_project", "open_science_context", "slurm_resurrect"):
        assert kv[p] == "not-installed", p
    assert kv["same_name_skills"] == "none"
    assert kv["secret_dir"] == "missing" and kv["secret_slack.env"] == "missing"
    assert kv["deny_rule"] == "absent" and kv["github_ssh"] == "skipped"


def test_check_configured_machine_and_never_prints_secrets(tmp_path):
    b = make_bin(tmp_path)
    stub(b, "tmux", 'case "$1" in -V) echo "tmux 9.9";; show) echo on;; esac')
    stub(b, "sbatch", "true")
    env = env_for(tmp_path, b)
    home = Path(env["HOME"])
    (home / ".claude" / "skills" / "new-task").mkdir(parents=True)
    (home / ".claude" / "plugins").mkdir()
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        '{"slurm-resurrect@open-science": [], "open-science-context@open-science": []}')
    (home / ".claude" / "settings.json").write_text('{"permissions": {"deny": ["Read(~/.config/opsci/**)"]}}')
    d = home / ".config" / "opsci"
    d.mkdir(parents=True)
    d.chmod(0o700)
    (d / "slack.env").write_text(f"SLACK_TOKEN={SLACK_TOKEN}\n")
    (d / "slack.env").chmod(0o600)
    (d / "zenodo.token").write_text(ZENODO_TOKEN)
    (d / "zenodo.token").chmod(0o644)  # refusal case: readable by others
    kv, out = check(tmp_path, b, TMUX="/tmp/x,1,0", SLURM_JOB_ID="123")
    assert kv["tmux"] == "9.9" and kv["in_tmux"] == "yes" and kv["tmux_mouse"] == "on"
    assert kv["slurm"] == "present" and kv["batch_job"] == "123"
    assert kv["slurm_resurrect"] == "installed" and kv["same_name_skills"] == "new-task"
    assert kv["open_science_context"] == "installed" and kv["open_science_project"] == "not-installed"
    assert kv["deny_rule"] == "present" and kv["secret_dir"] == "ok"
    assert kv["secret_slack.env"] == "ok" and kv["secret_zenodo.token"] == "mode-644"
    assert kv["secret_zenodo-sandbox.token"] == "missing" and kv["secret_notion.env"] == "missing"
    assert SLACK_TOKEN not in out and ZENODO_TOKEN not in out


RECOMMENDED_TMUX_CONF = """set -g mouse on
set -g set-clipboard on
set -g history-limit 50000
set -g default-terminal "tmux-256color"
set -ag terminal-overrides ",xterm-256color:RGB"
"""


def test_check_tmux_conf_lists_missing_settings(tmp_path):
    b = make_bin(tmp_path)
    stub(b, "tmux", 'case "$1" in -V) echo "tmux 9.9";; esac')
    home = Path(env_for(tmp_path, b)["HOME"])
    (home / ".tmux.conf").write_text(RECOMMENDED_TMUX_CONF)
    kv, _ = check(tmp_path, b)
    assert kv["tmux_mouse"] == "on" and kv["tmux_conf_missing"] == "none"
    # refusal case: a short history and no clipboard line are reported
    (home / ".tmux.conf").write_text(RECOMMENDED_TMUX_CONF.replace("50000", "2000")
                                     .replace("set -g set-clipboard on\n", ""))
    kv, _ = check(tmp_path, b)
    assert kv["tmux_conf_missing"] == "set-clipboard,history-limit"


def test_check_tmux_conf_reads_live_values_inside_tmux(tmp_path):
    b = make_bin(tmp_path)
    stub(b, "tmux", """case "$1 $2 $3" in
  "-V  ") echo "tmux 9.9";;
  "show -gv mouse") echo on;;
  "show -sv set-clipboard") echo external;;
  "show -gv history-limit") echo 50000;;
  "show -sv default-terminal") echo tmux-256color;;
  "show -sv terminal-overrides") echo "xterm-256color:Tc";;
esac""")
    kv, _ = check(tmp_path, b, TMUX="/tmp/x,1,0")
    assert kv["tmux_conf_missing"] == "set-clipboard"


def test_check_flags_open_secret_dir(tmp_path):
    b = make_bin(tmp_path)
    env = env_for(tmp_path, b)
    d = Path(env["XDG_CONFIG_HOME"]) / "opsci"
    d.mkdir(parents=True)
    d.chmod(0o755)
    kv, _ = check(tmp_path, b)
    assert kv["secret_dir"] == "mode-755"


# ------------------------------------------------------------------- secret_file.sh

def run_secret(tmp_path, kind, content, **extra):
    """Run the helper with an 'editor' that writes CONTENT into the file."""
    b = tmp_path / "ebin"
    b.mkdir(exist_ok=True)
    ed = b / "fake-editor"
    ed.write_text('#!/bin/sh\nprintf "%s" "$CONTENT" > "$1"\n')
    ed.chmod(0o755)
    env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
           "PATH": os.environ["PATH"], "EDITOR": str(ed), "CONTENT": content}
    env.update(extra)
    r = subprocess.run(["bash", str(SECRET), kind], env=env, capture_output=True, text=True)
    return r, tmp_path / "cfg" / "opsci"


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def test_secret_zenodo_ok_private_and_silent(tmp_path):
    (tmp_path / "cfg" / "opsci").mkdir(parents=True)
    (tmp_path / "cfg" / "opsci").chmod(0o755)  # tightened by the helper
    r, d = run_secret(tmp_path, "zenodo-sandbox", ZENODO_TOKEN + "\n")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1].startswith("ok: ")
    assert mode(d) == 0o700 and mode(d / "zenodo-sandbox.token") == 0o600
    assert ZENODO_TOKEN not in r.stdout + r.stderr


@pytest.mark.parametrize("content,msg", [
    (ZENODO_TOKEN + "\nsecond-line-word\n", "exactly one token"),
    ("", "exactly one token"),
    ("short\n", "only 5 characters"),
])
def test_secret_zenodo_refuses_bad_shape(tmp_path, content, msg):
    r, _ = run_secret(tmp_path, "zenodo", content)
    assert r.returncode == 1 and msg in r.stderr
    assert "second-line-word" not in r.stderr


def test_secret_slack_ok(tmp_path):
    r, d = run_secret(tmp_path, "slack", f"SLACK_TOKEN={SLACK_TOKEN}\nSLACK_CHANNEL=C0123456789\n")
    assert r.returncode == 0, r.stderr
    assert mode(d / "slack.env") == 0o600 and SLACK_TOKEN not in r.stdout + r.stderr


@pytest.mark.parametrize("content,msg", [
    ("SLACK_TOKEN=xox" "p-user-token-FAKE\nSLACK_CHANNEL=C0123456789\n", "does not start with xoxb-"),
    (f"SLACK_TOKEN={SLACK_TOKEN}\nSLACK_CHANNEL=#general\n", "not a channel ID"),
    ("SLACK_TOKEN=\nSLACK_CHANNEL=C0123456789\n", "SLACK_TOKEN is empty"),
])
def test_secret_slack_refuses_bad_shape(tmp_path, content, msg):
    r, _ = run_secret(tmp_path, "slack", content)
    assert r.returncode == 1 and msg in r.stderr
    assert "xox" "p-user-token-FAKE" not in r.stderr and SLACK_TOKEN not in r.stderr


NOTION_TOKEN = "ntn_FAKEnotionTOKEN0123456789abcdefFAKE"


def test_secret_notion_ok(tmp_path):
    r, d = run_secret(tmp_path, "notion", f"NOTION_TOKEN={NOTION_TOKEN}\n")
    assert r.returncode == 0, r.stderr
    assert mode(d / "notion.env") == 0o600 and NOTION_TOKEN not in r.stdout + r.stderr


@pytest.mark.parametrize("content,msg", [
    ("NOTION_TOKEN=\n", "NOTION_TOKEN is empty"),
    (f"NOTION_TOKEN={SLACK_TOKEN}\n", "does not look like an integration secret"),
])
def test_secret_notion_refuses_bad_shape(tmp_path, content, msg):
    r, _ = run_secret(tmp_path, "notion", content)
    assert r.returncode == 1 and msg in r.stderr
    assert SLACK_TOKEN not in r.stderr


def test_secret_refuses_inside_claude(tmp_path):
    r, d = run_secret(tmp_path, "zenodo", ZENODO_TOKEN, CLAUDECODE="1")
    assert r.returncode == 1 and "own terminal" in r.stderr
    assert not d.exists()


def test_secret_refuses_symlinked_file(tmp_path):
    d = tmp_path / "cfg" / "opsci"
    d.mkdir(parents=True)
    (d / "zenodo.token").symlink_to(tmp_path / "elsewhere")
    r, _ = run_secret(tmp_path, "zenodo", ZENODO_TOKEN)
    assert r.returncode == 1 and "symlink" in r.stderr


def test_secret_refuses_unknown_kind(tmp_path):
    r, _ = run_secret(tmp_path, "github", "x")
    assert r.returncode == 1 and "usage" in r.stderr


def test_check_knows_every_framework_skill_name():
    names = re.search(r'SKILL_NAMES="([^"]*)"', CHECK.read_text()).group(1).split()
    repo = {p.parent.name for p in (REPO / "plugins").glob("open-science*/skills/*/SKILL.md")}
    assert sorted(names) == sorted(repo)
