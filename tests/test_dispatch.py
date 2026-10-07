"""open-science:dispatch (plugins/open-science/scripts/dispatch.sh and hooks/dispatch_mod.js).

dispatch.sh opens a window in the current tmux session, types the agent's launch command into
its shell, and hands the new session its prompt: through the mod (OPSCI_DISPATCH_PROMPT and
`dispatch.sh claim`), by pasting it into the pane when no mod claims it, or, for Codex, as the
command's argument. These tests run it in a private tmux server, with the launch command
replaced by the fake Claude TUI (tests/fixtures/fake_claude/fake_tui.sh) or a script that
records its arguments, so no real agent and no user tmux server is touched. The mod's own code
is tested with Claude Code's mod test kit (plugins/open-science/tests/dispatch_mod.test.ts),
run here when a Claude Code with `claude plugin test` is found. Every check has a case it
must refuse.
"""
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from conftest import REPO

PLUGIN = REPO / "plugins" / "open-science"
DISPATCH = PLUGIN / "scripts" / "dispatch.sh"
FAKE_TUI = REPO / "tests" / "fixtures" / "fake_claude" / "fake_tui.sh"

needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None or shutil.which("jq") is None,
                                reason="tmux or jq not installed")


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("TMUX", "CLAUDE", "OPSCI", "CODEX"))}
    e.update(OPSCI_STATE_DIR=str(tmp_path / "state"), OPSCI_DISPATCH_GRACE="1",
             CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"),
             OPSCI_DISPATCH_MAXWAIT="40")
    return e


def run(env, *args):
    return subprocess.run(["bash", str(DISPATCH), *map(str, args)], env=env,
                          capture_output=True, text=True, timeout=120)


def kv(out):
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


@pytest.fixture
def tm(env, tmp_path):
    """A private tmux server whose windows start a plain bash; the env looks like a pane in it."""
    sockdir = Path("/tmp") / f"os-test-{uuid.uuid4().hex[:8]}"
    sockdir.mkdir()
    sock = str(sockdir / "s")
    base = ["tmux", "-S", sock, "-f", "/dev/null"]
    subprocess.run([*base, "new-session", "-d", "-s", "main", "-x", "150", "-y", "40",
                    "bash --norc --noprofile"], check=True)
    subprocess.run([*base, "set", "-g", "default-command", "bash --norc --noprofile"], check=True)
    pane = subprocess.run([*base, "display-message", "-p", "-t", "main:0", "#{pane_id}"],
                          capture_output=True, text=True, check=True).stdout.strip()
    env.update(TMUX=f"{sock},1,0", TMUX_PANE=pane)
    st = tmp_path / "tui_state.json"
    st.write_text(json.dumps({"sessionId": "sid-A", "status": "idle"}))
    rec = tmp_path / "received.log"
    rec.touch()
    yield {"tm": base, "pane": pane, "state": st, "rec": rec}
    subprocess.run([*base, "kill-server"], capture_output=True)
    shutil.rmtree(sockdir, ignore_errors=True)


def tmux(t, *args):
    return subprocess.run([*t["tm"], *args], capture_output=True, text=True).stdout.strip()


def wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.5)
    return False


def fake_claude(env, t):
    env["OPSCI_DISPATCH_CMD"] = f"FAKE_BUSY=1 bash {FAKE_TUI} {t['state']} {t['rec']}"


# ---- claim --------------------------------------------------------------------------------

def test_claim_prints_the_prompt_once(env, tmp_path):
    q = tmp_path / "state" / "dispatch"
    q.mkdir(parents=True)
    f = q / "a.prompt"
    f.write_text("tidy up\n")
    r = run(env, "claim", f)
    assert r.returncode == 0 and r.stdout == "tidy up\n"
    assert not f.exists()
    assert run(env, "claim", f).returncode == 1          # a second claim gets nothing


def test_claim_refuses_a_file_outside_a_dispatch_directory(env, tmp_path):
    f = tmp_path / "other.prompt"
    f.write_text("secret\n")
    r = run(env, "claim", f)
    assert r.returncode == 1 and r.stdout == "" and f.exists()


def test_claim_refuses_a_file_that_is_not_a_prompt(env, tmp_path):
    q = tmp_path / "state" / "dispatch"
    q.mkdir(parents=True)
    f = q / "notes.txt"
    f.write_text("x\n")
    assert run(env, "claim", f).returncode == 1 and f.exists()


def test_claim_refuses_a_symlink(env, tmp_path):
    q = tmp_path / "state" / "dispatch"
    q.mkdir(parents=True)
    target = tmp_path / "secret.txt"
    target.write_text("secret\n")
    (q / "a.prompt").symlink_to(target)
    r = run(env, "claim", q / "a.prompt")
    assert r.returncode == 1 and r.stdout == "" and target.exists()


# ---- names -------------------------------------------------------------------------------

def proc_start(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def session(env, n, name, live=True):
    d = Path(env["CLAUDE_CONFIG_DIR"]) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    (d / f"{n}.json").write_text(json.dumps({"pid": pid, "procStart": proc_start(pid) if live else "1",
                                             "sessionId": f"s{n}", "name": name}))


def name(env, d):
    r = run(env, "name", d)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_name_is_the_project_when_free(env, tmp_path):
    d = tmp_path / "My Project"
    d.mkdir()
    assert name(env, d) == "my-project"


def test_name_takes_the_lowest_free_number(env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    session(env, 1, "proj · some task")
    session(env, 2, "proj-3")
    assert name(env, d) == "proj-2"
    session(env, 3, "proj-2")
    assert name(env, d) == "proj-4"


def test_name_ignores_ended_sessions_and_other_projects(env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    session(env, 1, "proj", live=False)
    session(env, 2, "proj-x")
    assert name(env, d) == "proj"


def test_name_uses_the_git_top_level(env, tmp_path):
    d = tmp_path / "repo" / "sub"
    d.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    assert name(env, d) == "repo"


def test_a_dispatched_name_is_held_until_its_session_records_it(env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    held = tmp_path / "state" / "dispatch" / "names"
    held.mkdir(parents=True)
    (held / "proj").touch()
    assert name(env, d) == "proj-2"
    os.utime(held / "proj", (time.time() - 600, time.time() - 600))   # past OPSCI_DISPATCH_HOLD
    assert name(env, d) == "proj"


# ---- launch -------------------------------------------------------------------------------

def test_launch_outside_tmux_is_refused(env, tmp_path):
    r = run(env, "launch", "--dir", tmp_path)
    assert r.returncode == 1 and "not inside tmux" in r.stderr


@needs_tmux
def test_launch_refuses_a_missing_directory(env, tm, tmp_path):
    r = run(env, "launch", "--dir", tmp_path / "nope")
    assert r.returncode == 1 and "no such directory" in r.stderr
    assert tmux(tm, "list-windows", "-t", "main", "-F", "#{window_index}") == "0"


@needs_tmux
def test_launch_without_a_prompt_starts_the_agent_and_sends_nothing(env, tm, tmp_path):
    work = tmp_path / "home dir"
    work.mkdir()
    fake_claude(env, tm)
    r = run(env, "launch", "--dir", work)
    assert r.returncode == 0, r.stderr
    o = kv(r.stdout)
    assert o["prompt"] == "none" and o["dir"] == str(work.resolve())
    assert o["name"] == "home-dir"
    assert o["command"].endswith("--name home-dir --remote-control home-dir")
    assert wait_for(lambda: "❯" in tmux(tm, "capture-pane", "-p", "-t", o["pane"]), 20)
    # A window of the same session, named after the directory, in it; not made current.
    assert tmux(tm, "display-message", "-p", "-t", o["pane"], "#{session_name}|#{window_name}|#{pane_current_path}") \
        == f"main|home-dir|{work.resolve()}"
    assert tmux(tm, "display-message", "-p", "-t", "main", "#{window_index}") == "0"
    time.sleep(3)
    assert tm["rec"].read_text() == ""


@needs_tmux
def test_launch_types_the_prompt_when_no_mod_claims_it(env, tm, tmp_path):
    fake_claude(env, tm)
    p = tmp_path / "prompt.txt"
    p.write_text("clean up the home directory\n")
    r = run(env, "launch", "--dir", tmp_path, "--name", "cleanup", "--prompt-file", p)
    assert r.returncode == 0, r.stderr
    o = kv(r.stdout)
    assert o["prompt"] == "typed"
    assert o["name"] == "cleanup"
    assert tmux(tm, "display-message", "-p", "-t", o["pane"], "#{window_name}") == "cleanup"
    assert wait_for(lambda: tm["rec"].read_text().splitlines() == ["clean up the home directory"], 10), \
        tm["rec"].read_text()
    assert list((tmp_path / "state" / "dispatch").glob("*.prompt*")) == []


@needs_tmux
def test_launch_leaves_the_prompt_to_the_mod_when_it_claims_it(env, tm, tmp_path):
    # Stands in for Claude Code with the mod: the session's start claims the prompt named in
    # OPSCI_DISPATCH_PROMPT (in the window's environment) and records it, then the TUI runs.
    got = tmp_path / "mod_got.txt"
    env["OPSCI_DISPATCH_CMD"] = (f'bash {DISPATCH} claim "$OPSCI_DISPATCH_PROMPT" > {got}; '
                                 f"bash {FAKE_TUI} {tm['state']} {tm['rec']}")
    p = tmp_path / "prompt.txt"
    p.write_text("line one\nline two\n")
    r = run(env, "launch", "--dir", tmp_path, "--prompt-file", p)
    assert r.returncode == 0, r.stderr
    assert kv(r.stdout)["prompt"] == "mod"
    assert got.read_text() == "line one\nline two\n"
    time.sleep(3)
    assert tm["rec"].read_text() == ""                   # not typed as well


FAKE_TRUST = REPO / "tests" / "fixtures" / "fake_claude" / "fake_trust.sh"


def trust_dialog(env, t, tmp_path, kind="claude", then="cat"):
    """The launch command shows a fake folder-trust question; its answers go to answers.log."""
    log = tmp_path / "answers.log"
    log.touch()
    var = "OPSCI_DISPATCH_CODEX_CMD" if kind == "codex" else "OPSCI_DISPATCH_CMD"
    env[var] = f"bash {FAKE_TRUST} {kind} {log} {then}"
    return log


@needs_tmux
def test_launch_stops_at_a_folder_trust_question_and_answers_nothing(env, tm, tmp_path):
    env["OPSCI_DISPATCH_MAXWAIT"] = "60"
    log = trust_dialog(env, tm, tmp_path)
    p = tmp_path / "prompt.txt"
    p.write_text("hello\n")
    t0 = time.time()
    r = run(env, "launch", "--dir", tmp_path, "--prompt-file", p)
    o = kv(r.stdout)
    assert r.returncode == 0 and o["prompt"] == "pending" and o["trust"] == "asked"
    assert time.time() - t0 < 30                         # reported at once, not after MAXWAIT
    assert Path(o["queued"]).read_text() == "hello\n"    # left for the mod once answered
    time.sleep(2)
    assert log.read_text() == ""                         # no answer given
    assert "Yes, I trust this folder" in tmux(tm, "capture-pane", "-p", "-t", o["pane"])


@needs_tmux
def test_launch_without_a_prompt_reports_the_trust_question(env, tm, tmp_path):
    log = trust_dialog(env, tm, tmp_path)
    o = kv(run(env, "launch", "--dir", tmp_path).stdout)
    assert o["prompt"] == "none" and o["trust"] == "asked" and log.read_text() == ""


@needs_tmux
def test_launch_reports_no_trust_question_when_there_is_none(env, tm, tmp_path):
    fake_claude(env, tm)
    o = kv(run(env, "launch", "--dir", tmp_path).stdout)
    assert o["trust"] == "none"


@needs_tmux
def test_trust_selects_yes_and_then_delivers_the_prompt(env, tm, tmp_path):
    log = trust_dialog(env, tm, tmp_path, then=f"bash {FAKE_TUI} {tm['state']} {tm['rec']}")
    p = tmp_path / "prompt.txt"
    p.write_text("do the thing\n")
    o = kv(run(env, "launch", "--dir", tmp_path, "--prompt-file", p).stdout)
    assert o["trust"] == "asked"
    r = run(env, "trust", "--pane", o["pane"], "--queued", o["queued"])
    assert r.returncode == 0, r.stderr
    t = kv(r.stdout)
    assert t["trust"] == "accepted" and t["prompt"] == "typed"
    assert log.read_text() == "yes\n"                   # the cursor was moved off "No, exit"
    assert wait_for(lambda: "do the thing" in tm["rec"].read_text(), 10)


@needs_tmux
def test_trust_answers_the_codex_question(env, tm, tmp_path):
    ran = tmp_path / "ran"
    log = trust_dialog(env, tm, tmp_path, kind="codex", then=f"touch {ran}")
    o = kv(run(env, "launch", "--agent", "codex", "--dir", tmp_path).stdout)
    assert o["trust"] == "asked"
    r = run(env, "trust", "--pane", o["pane"], "--agent", "codex")
    assert r.returncode == 0 and kv(r.stdout)["trust"] == "accepted", r.stderr
    assert log.read_text() == "yes\n" and wait_for(ran.exists, 10)


@needs_tmux
def test_trust_presses_nothing_when_yes_cannot_be_selected(env, tm, tmp_path):
    log = trust_dialog(env, tm, tmp_path, kind="stuck")
    o = kv(run(env, "launch", "--dir", tmp_path).stdout)
    r = run(env, "trust", "--pane", o["pane"])
    assert r.returncode == 1 and "pressed nothing" in r.stderr
    time.sleep(1)
    assert log.read_text() == ""                         # Enter never sent on "No, exit"


@needs_tmux
def test_trust_refuses_a_pane_without_a_trust_question(env, tm, tmp_path):
    fake_claude(env, tm)
    o = kv(run(env, "launch", "--dir", tmp_path).stdout)
    r = run(env, "trust", "--pane", o["pane"])
    assert r.returncode == 1 and "no folder-trust question" in r.stderr
    time.sleep(1)
    assert tm["rec"].read_text() == ""                   # nothing typed into the session


@needs_tmux
def test_codex_gets_the_prompt_as_one_argument(env, tm, tmp_path):
    rec = tmp_path / "args.json"
    rec_script = tmp_path / "fake_codex.sh"
    rec_script.write_text(f'#!/bin/bash\npython3 -c \'import json,sys; json.dump(sys.argv[1:], open("{rec}","w"))\' "$@"\n')
    env["OPSCI_DISPATCH_CODEX_CMD"] = f"bash {rec_script}"
    p = tmp_path / "prompt.txt"
    text = "it's \"quoted\" $HOME `x` \\ and\nsecond line"
    p.write_text(text)
    r = run(env, "launch", "--agent", "codex", "--dir", tmp_path, "--prompt-file", p)
    assert r.returncode == 0, r.stderr
    o = kv(r.stdout)
    assert o["prompt"] == "argument" and "--remote-control" not in o["command"]
    assert wait_for(rec.exists, 20)
    assert json.loads(rec.read_text()) == [text]


# ---- the mod ------------------------------------------------------------------------------

def test_hooks_json_lists_the_mod_and_keeps_the_update_check():
    d = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    assert d["modules"] == ["./dispatch_mod.js"] and (PLUGIN / "hooks" / "dispatch_mod.js").exists()
    assert set(d["hooks"]) == {"SessionStart", "Stop"}


def test_codex_hooks_do_not_load_the_mod():
    assert "modules" not in json.loads((PLUGIN / "hooks" / "codex.json").read_text())


def test_mod_tests_pass(tmp_path):
    c = os.environ.get("OPSCI_TEST_CLAUDE") or shutil.which("claude")
    if not c:
        pytest.skip("no claude binary (set OPSCI_TEST_CLAUDE)")
    # A copy without scripts/mod_probe, whose own test passes only as its own plugin.
    mod = tmp_path / "open-science"
    shutil.copytree(PLUGIN, mod, ignore=shutil.ignore_patterns("mod_probe"))
    r = subprocess.run([c, "plugin", "test", str(mod)], capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    if "hooks modules are turned off" in out or "unknown command" in out.lower():
        pytest.skip("this Claude Code runs no mods: " + out.strip().splitlines()[0][:200])
    assert r.returncode == 0 and " 3 pass" in out and " 0 fail" in out, out
