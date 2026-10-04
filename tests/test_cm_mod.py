# opsci: planted-leaks (this file uses fake absolute paths on purpose)
"""Context management through the Claude Code mod (plugins/open-science-context/hooks/
context_mod.js).

The mod clears, resumes, wakes and renames from inside Claude Code; the policy and the
records stay in the shell scripts, which these tests drive the way the mod does
(`cm_stop.sh --mod`, `cm_mod.sh`, `wait_slurm.sh --notify/--check`, `session_name.sh
want`, and jump.sh with OPSCI_MOD=1). The mod's own code is tested with Claude Code's mod
test kit (plugins/open-science-context/tests/context_mod.test.ts), run here when a
Claude Code that has `claude plugin test` is found ($OPSCI_TEST_CLAUDE, else `claude`).
Every check has a case it must refuse. The tmux path stays covered by tests/test_cm.py.
"""
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from conftest import REPO

PLUGIN = REPO / "plugins" / "open-science-context"
SCRIPTS = PLUGIN / "scripts"
FAKE = REPO / "tests" / "fixtures" / "fake_claude"
OPSCI_FAKE = REPO / "tests" / "fixtures" / "fake_opsci"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def key(s):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s)


def skey(sid):
    return f"claude-sid__{key(sid)}"


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("TMUX", "CLAUDE", "OPSCI", "SLURM", "CODEX"))}
    e.update(OPSCI_STATE_DIR=str(tmp_path / "state"), CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"),
             PATH=f"{OPSCI_FAKE}:{e['PATH']}", FAKE_OPSCI_LOG=str(tmp_path / "opsci.log"))
    (tmp_path / "state").mkdir()
    return e


@pytest.fixture
def session(env, tmp_path):
    """A fake claude process with a state file, a transcript, and a template project."""
    sid = str(uuid.uuid4())
    st = tmp_path / "statefile.json"
    st.write_text(json.dumps({"sessionId": sid, "status": "busy"}))
    env["FAKE_STATE"] = str(st)
    proj = Path(env["CLAUDE_CONFIG_DIR"]) / "projects" / "p"
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text(json.dumps({"type": "assistant", "sessionId": sid, "message": {
        "usage": {"input_tokens": 150_000, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0, "output_tokens": 1}}}) + "\n")
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "AGENTS.md").write_text("x\n")
    (root / "config" / "framework.yaml").write_text("x: 1\n")
    ctx = root / "context.md"
    ctx.write_text("# state\n")
    return {"sid": sid, "ctx": ctx, "root": root}


def run(env, script, *args, stdin="", fake_claude=False, **extra):
    if script == "jump.sh" and args and args[0] in ("active", "wait") and "--report" not in args:
        args = (*args, "--report", "r")
    cmd = ["bash", str(SCRIPTS / script), *map(str, args)]
    if fake_claude:
        cmd = [str(FAKE / "claude"), *cmd]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                          env=dict(env, **extra), timeout=60)


def mod_env(env):
    """What a tool shell or hook sees once the mod has loaded."""
    return dict(env, OPSCI_MOD="1", CLAUDECODE="1")


def register(env, sid, doc="/x/context.md"):
    f = Path(env["OPSCI_STATE_DIR"]) / "session_context" / f"claude__{key(sid)}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"version": 1, "runtime": "claude", "session_id": sid, "doc_path": doc}))
    return f


def request(env, sid, kind, **extra):
    f = Path(env["OPSCI_STATE_DIR"]) / "jump" / f"{skey(sid)}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    d = {"version": 1, "runtime": "claude-mod", "kind": kind, "context": "/x/context.md",
         "prompt": "/open-science-context:continue-context /x/context.md" if kind == "active" else "",
         "key": skey(sid), "old_sid": sid, "phase": "requested"}
    d.update(extra)
    f.write_text(json.dumps(d))
    return f


def waker(env, sid, jobs=("42",)):
    f = Path(env["OPSCI_STATE_DIR"]) / "wakers" / skey(sid) / "1-1.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"version": 1, "runtime": "claude-mod", "jobs": list(jobs), "session_id": sid}))
    return f


def mod_stop(env, sid="sid-A", tokens=None, tasks=(), active=False):
    p = {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": active,
         "background_tasks": list(tasks), "session_crons": [], "agent_id": ""}
    if tokens is not None:
        p["opsci_tokens"] = tokens
    r = run(env, "cm_stop.sh", "--mod", stdin=json.dumps(p))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


SHELL = {"id": "b1", "type": "shell", "status": "running", "command": "sleep 99"}


# ---- the Stop policy the mod runs (cm_stop.sh --mod) -----------------------------------

def test_unregistered_session_gets_no_timer_and_no_notice(env):
    out = mod_stop(env, tokens=900_000, tasks=[SHELL])
    assert "decision" not in out and out["opsci"]["cold"] == 0 and "jump" not in out["opsci"]


def test_registered_session_with_a_waker_arms_the_cache_cold_timer(env):
    register(env, "sid-A")
    out = mod_stop(env, tasks=[SHELL])
    assert out["opsci"]["cold"] == 45 * 60
    assert out["opsci"]["notice"].startswith("[open-science] cache-cold: 45 min idle")
    assert mod_stop(env)["opsci"]["cold"] == 0          # nothing running: no timer


def test_a_queued_waker_counts_as_a_waker(env):
    register(env, "sid-A")
    waker(env, "sid-A")
    assert mod_stop(env)["opsci"]["cold"] > 0


def test_size_notice_above_the_threshold_once_per_growth(env):
    register(env, "sid-A")
    out = mod_stop(env, tokens=260_000)
    assert out["decision"] == "block" and "260000 tokens" in out["reason"]
    assert "decision" not in mod_stop(env, tokens=270_000)           # not again before +50k
    assert mod_stop(env, tokens=320_000)["decision"] == "block"
    assert "decision" not in mod_stop(env, tokens=240_000, sid="sid-B")  # below: none


def test_size_notice_obeys_jump_settings(env):
    register(env, "sid-A")
    env["OPSCI_JUMPS"] = "wait"
    assert "decision" not in mod_stop(env, tokens=900_000)
    env["OPSCI_JUMPS"] = "off"
    request(env, "sid-A", "active")
    out = mod_stop(env, tokens=900_000, tasks=[SHELL])
    assert "decision" not in out and out["opsci"]["cold"] == 0 and "jump" not in out["opsci"]


def test_active_request_is_handed_to_the_mod(env):
    req = request(env, "sid-A", "active")
    out = mod_stop(env)
    assert out["opsci"]["jump"] == {"kind": "active", "context": "/x/context.md",
                                    "prompt": "/open-science-context:continue-context /x/context.md",
                                    "report": "", "wakers": 0}
    assert not req.exists()
    done = list((Path(env["OPSCI_STATE_DIR"]) / "jump" / "done").glob(f"{skey('sid-A')}-*.json"))
    assert len(done) == 1 and json.loads(done[0].read_text())["phase"] == "handed_to_mod"


def test_wait_request_without_a_waker_is_refused(env):
    req = request(env, "sid-A", "wait")
    out = mod_stop(env)
    assert out["decision"] == "block" and "wait jump refused" in out["reason"]
    assert "jump" not in out["opsci"] and not req.exists()


@pytest.mark.parametrize("how", ["task", "queued"])
def test_wait_request_with_a_waker_is_handed_to_the_mod(env, how):
    request(env, "sid-A", "wait")
    if how == "queued":
        waker(env, "sid-A")
    out = mod_stop(env, tasks=[SHELL] if how == "task" else [])
    assert out["opsci"]["jump"]["kind"] == "wait" and "decision" not in out
    assert out["opsci"]["jump"]["wakers"] == 1          # the mod's note says what it waits for


def test_the_jump_report_reaches_the_mod(env):
    request(env, "sid-A", "wait", report="Runs queued.\n\nWait jump: the session is cleared.")
    out = mod_stop(env, tasks=[SHELL, dict(SHELL, id="b2")])
    assert out["opsci"]["jump"]["report"] == "Runs queued.\n\nWait jump: the session is cleared."
    assert out["opsci"]["jump"]["wakers"] == 2


def test_stale_request_of_another_session_is_dropped(env):
    req = request(env, "sid-A", "active", old_sid="sid-OLD")
    assert "jump" not in mod_stop(env)["opsci"] and not req.exists()


def test_plain_stop_hook_does_nothing_when_the_mod_is_loaded(env):
    req = request(env, "sid-A", "active")
    p = {"session_id": "sid-A", "hook_event_name": "Stop", "background_tasks": [], "session_crons": []}
    r = run(env, "cm_stop.sh", stdin=json.dumps(p), OPSCI_MOD="1", TMUX="/tmp/s,1,0", TMUX_PANE="%1")
    assert r.returncode == 0 and r.stdout == "" and req.exists()


# ---- jump.sh with the mod: keyed by session, no tmux ------------------------------------

def test_jump_without_tmux_works_with_the_mod(env, session):
    r = run(mod_env(env), "jump.sh", "active", session["ctx"], fake_claude=True)
    assert r.returncode == 0, r.stderr
    req = json.loads((Path(env["OPSCI_STATE_DIR"]) / "jump" / f"{skey(session['sid'])}.json").read_text())
    assert req["runtime"] == "claude-mod" and req["old_sid"] == session["sid"]
    assert req["prompt"] == f"/open-science-context:continue-context {session['ctx']}"
    # the report, with the jump's own line, for the top of the cleared session
    assert req["report"].startswith("r\n\nActive jump: the session is cleared and resumes from ")
    # ... and registers the session, which is the mod's registration
    reg = Path(env["OPSCI_STATE_DIR"]) / "session_context" / f"claude__{key(session['sid'])}.json"
    assert json.loads(reg.read_text())["doc_path"] == str(session["ctx"])


def test_jump_without_tmux_and_without_the_mod_is_refused(env, session):
    r = run(dict(env, CLAUDECODE="1"), "jump.sh", "active", session["ctx"], fake_claude=True)
    assert r.returncode == 1 and "mod is not loaded" in r.stderr and "claude update" in r.stderr


def test_jump_status_says_who_does_the_jump(env, session):
    assert "open-science mod" in run(mod_env(env), "jump.sh", "status", fake_claude=True).stdout


def test_jump_with_the_mod_keeps_the_refusals(env, session):
    env2 = mod_env(env)
    assert run(dict(env2, OPSCI_JUMPS="off"), "jump.sh", "wait", session["ctx"], fake_claude=True).returncode == 1
    (Path(env["OPSCI_STATE_DIR"]) / "inhibit_jump").write_text("winding down")
    r = run(env2, "jump.sh", "wait", session["ctx"], fake_claude=True)
    assert r.returncode == 1 and "inhibited" in r.stderr


def test_jump_inhibit_file_of_the_pane_applies_with_the_mod(env, session):
    env2 = dict(mod_env(env), TMUX="/tmp/s,1,0", TMUX_PANE="%5")
    (Path(env["OPSCI_STATE_DIR"]) / f"inhibit_jump_{key('/tmp/s')}__{key('%5')}").write_text("hop")
    r = run(env2, "jump.sh", "wait", session["ctx"], fake_claude=True)
    assert r.returncode == 1 and "inhibited" in r.stderr


# ---- cm_mod.sh: the records the mod keeps -----------------------------------------------

def test_handover_copies_the_registration_and_moves_the_wakers(env):
    register(env, "sid-A", doc="/p/c.md")
    w = waker(env, "sid-A")
    r = run(env, "cm_mod.sh", "handover", "sid-A", "sid-B")
    assert r.returncode == 0
    new = Path(env["OPSCI_STATE_DIR"]) / "session_context" / f"claude__{key('sid-B')}.json"
    assert json.loads(new.read_text())["doc_path"] == "/p/c.md"
    assert not w.exists() and (Path(env["OPSCI_STATE_DIR"]) / "wakers" / skey("sid-B") / w.name).exists()


def test_handover_of_an_unregistered_session_registers_nothing(env):
    run(env, "cm_mod.sh", "handover", "sid-A", "sid-B")
    assert not (Path(env["OPSCI_STATE_DIR"]) / "session_context").exists()


def test_handover_moves_the_pane_record_only_from_its_own_session(env):
    register(env, "sid-A")
    pane = Path(env["OPSCI_STATE_DIR"]) / "pane_context" / f"{key('/tmp/s')}__{key('%5')}.json"
    pane.parent.mkdir(parents=True)
    tmux = dict(TMUX="/tmp/s,1,0", TMUX_PANE="%5")
    pane.write_text(json.dumps({"doc_path": "/x/context.md", "session_id": "sid-A"}))
    run(env, "cm_mod.sh", "handover", "sid-A", "sid-B", **tmux)
    assert json.loads(pane.read_text())["session_id"] == "sid-B"
    pane.write_text(json.dumps({"doc_path": "/x/context.md", "session_id": "sid-OTHER"}))
    run(env, "cm_mod.sh", "handover", "sid-A", "sid-C", **tmux)
    assert json.loads(pane.read_text())["session_id"] == "sid-OTHER"


def test_waiting_session_resumed_in_another_process_is_woken(env, session):
    run(env, "cm_mod.sh", "waiting", "sid-A", "/p/c.md")      # no claude above: another process
    r = run(env, "cm_mod.sh", "resumed", "sid-A", fake_claude=True)
    assert r.stdout.strip() == "/open-science-context:continue-context /p/c.md"
    assert run(env, "cm_mod.sh", "resumed", "sid-A", fake_claude=True).stdout == ""   # once


def test_waiting_session_in_the_same_process_is_not_woken(env, session):
    # One fake claude process writes the marker and asks; a reloaded mod must not wake it.
    r = subprocess.run([str(FAKE / "claude"), "bash", "-c",
                        f'bash {SCRIPTS}/cm_mod.sh waiting sid-A /p/c.md; bash {SCRIPTS}/cm_mod.sh resumed sid-A'],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.stdout == ""


def test_waiting_session_with_a_queued_waker_is_left_to_it(env, session):
    run(env, "cm_mod.sh", "waiting", "sid-A", "/p/c.md")
    waker(env, "sid-A")
    assert run(env, "cm_mod.sh", "resumed", "sid-A", fake_claude=True).stdout == ""


def test_woken_session_is_no_longer_waiting(env, session):
    run(env, "cm_mod.sh", "waiting", "sid-A", "/p/c.md")
    run(env, "cm_mod.sh", "woken", "sid-A")
    assert run(env, "cm_mod.sh", "resumed", "sid-A", fake_claude=True).stdout == ""


# ---- wakers: wait_slurm.sh --notify / --check --------------------------------------------

@pytest.fixture
def fake_slurm(tmp_path, env):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "squeue").write_text(f'#!/bin/bash\n[ -f {tmp_path}/running ] && echo 42\nexit 0\n')
    (b / "sacct").write_text("#!/bin/bash\necho ' COMPLETED'\n")
    for f in b.iterdir():
        f.chmod(0o755)
    env["PATH"] = f"{b}:{env['PATH']}"
    (tmp_path / "running").touch()
    return tmp_path / "running"


def test_notify_with_the_mod_queues_a_waker_for_the_session(env, session, fake_slurm):
    r = run(mod_env(env), "wait_slurm.sh", "--notify", "42", fake_claude=True)
    assert r.returncode == 0, r.stderr
    [f] = (Path(env["OPSCI_STATE_DIR"]) / "wakers" / skey(session["sid"])).glob("*.json")
    assert json.loads(f.read_text())["jobs"] == ["42"]


def test_notify_does_not_queue_jobs_a_pending_waker_covers(env, session, fake_slurm):
    d = Path(env["OPSCI_STATE_DIR"]) / "wakers" / skey(session["sid"])
    d.mkdir(parents=True)
    (d / "0-0.json").write_text(json.dumps({"jobs": ["42", "77"]}))   # queued before a jump
    for jobs in (["42"], ["77_3"], ["42", "42"]):
        r = run(mod_env(env), "wait_slurm.sh", "--notify", *jobs, fake_claude=True)
        assert r.returncode == 0 and "already queued" in r.stdout, (jobs, r.stdout, r.stderr)
    assert [f.name for f in d.glob("*.json")] == ["0-0.json"]
    r = run(mod_env(env), "wait_slurm.sh", "--notify", "42", "43", "43", fake_claude=True)
    assert r.returncode == 0, r.stderr
    [new] = [f for f in d.glob("*.json") if f.name != "0-0.json"]
    assert json.loads(new.read_text())["jobs"] == ["43"]


def test_check_reports_only_when_the_jobs_have_left_the_queue(env, fake_slurm):
    f = waker(env, "sid-A")
    r = run(env, "wait_slurm.sh", "--check", f)
    assert r.returncode == 10 and r.stdout == "" and f.exists()
    fake_slurm.unlink()
    r = run(env, "wait_slurm.sh", "--check", f)
    assert r.returncode == 0 and "job 42: COMPLETED" in r.stdout and "continue-context" in r.stdout
    assert not f.exists() and list((Path(env["OPSCI_STATE_DIR"]) / "wakers" / "done").glob("*.json"))


# ---- session names -----------------------------------------------------------------------

def test_naming_hook_steps_aside_for_the_mod(env, tmp_path):
    p = json.dumps({"session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "UserPromptSubmit"})
    assert run(env, "session_name.sh", "hook", stdin=p, OPSCI_SESSION_NAMES="1").stdout != ""
    assert run(env, "session_name.sh", "hook", stdin=p, OPSCI_SESSION_NAMES="1", OPSCI_MOD="1").stdout == ""


def test_want_names_the_task_of_the_given_session(env, tmp_path):
    t = tmp_path / "proj" / "tasks" / "fit-a"
    t.mkdir(parents=True)
    (t / "context.md").write_text("---\nshort_name: fit-a\n---\n")
    register(env, "s1", doc=str(t / "context.md"))
    r = run(env, "session_name.sh", "want", "s1", tmp_path / "proj", OPSCI_SESSION_NAMES="1")
    assert r.stdout == "proj · fit-a"
    assert run(env, "session_name.sh", "want", "s2", tmp_path / "proj", OPSCI_SESSION_NAMES="1").stdout == "proj"
    assert run(env, "session_name.sh", "want", "s1", tmp_path / "proj").stdout == ""   # names off


# ---- the tmux path adopts a session's registration in a new pane --------------------------

def test_new_pane_adopts_the_session_registration(env, tmp_path):
    doc = tmp_path / "c.md"
    doc.write_text("x")
    register(env, "sid-A", doc=str(doc))
    p = {"session_id": "sid-A", "hook_event_name": "Stop", "background_tasks": [], "session_crons": []}
    r = run(env, "cm_stop.sh", stdin=json.dumps(p), TMUX="/tmp/s,1,0", TMUX_PANE="%7")
    assert r.returncode == 0
    pane = Path(env["OPSCI_STATE_DIR"]) / "pane_context" / f"{key('/tmp/s')}__{key('%7')}.json"
    assert json.loads(pane.read_text()) | {"registered_at": ""} == {
        "version": 1, "pane_id": "%7", "doc_path": str(doc), "registered_at": "", "session_id": "sid-A"}


def test_a_pane_registered_to_another_session_is_not_taken_over(env, tmp_path):
    doc = tmp_path / "c.md"
    doc.write_text("x")
    register(env, "sid-A", doc=str(doc))
    pane = Path(env["OPSCI_STATE_DIR"]) / "pane_context" / f"{key('/tmp/s')}__{key('%7')}.json"
    pane.parent.mkdir(parents=True)
    pane.write_text(json.dumps({"doc_path": "/other.md", "session_id": "sid-Z"}))
    p = {"session_id": "sid-A", "hook_event_name": "Stop", "background_tasks": [], "session_crons": []}
    run(env, "cm_stop.sh", stdin=json.dumps(p), TMUX="/tmp/s,1,0", TMUX_PANE="%7")
    assert json.loads(pane.read_text())["session_id"] == "sid-Z"


# ---- the mod itself ------------------------------------------------------------------------

def claude_bin():
    return os.environ.get("OPSCI_TEST_CLAUDE") or shutil.which("claude")


_RUNS = {}


def plugin_cmd(*args):
    """`claude plugin <args> <plugin>`, once per argument list; skips when this Claude Code
    has no mods (too old, or its mods rollout switch is off for this account)."""
    c = claude_bin()
    if not c:
        pytest.skip("no claude binary (set OPSCI_TEST_CLAUDE)")
    if args not in _RUNS:
        _RUNS[args] = subprocess.run([c, "plugin", *args, str(PLUGIN)], capture_output=True,
                                     text=True, timeout=300)
    r = _RUNS[args]
    out = r.stdout + r.stderr
    if "hooks modules are turned off" in out or "unknown command" in out.lower():
        pytest.skip("this Claude Code runs no mods: " + out.strip().splitlines()[0][:200])
    return r


def test_hooks_json_lists_the_mod_and_keeps_the_shell_hooks():
    d = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    assert d["modules"] == ["./context_mod.js"] and (PLUGIN / "hooks" / "context_mod.js").exists()
    assert set(d["hooks"]) == {"Stop", "SessionStart", "UserPromptSubmit"}


def test_plugin_validates_with_the_mod():
    r = plugin_cmd("validate")
    assert r.returncode == 0, r.stdout + r.stderr
    if "context_mod.js hooks:" not in r.stdout:
        pytest.skip("this Claude Code does not read mods in plugin validate")


def test_mod_unit_tests():
    r = plugin_cmd("test")
    assert r.returncode == 0 and " 0 fail" in r.stdout, r.stdout + r.stderr
