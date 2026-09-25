# opsci: planted-leaks (this file uses fake absolute paths on purpose)
"""Context-management tests (report, Tests table, rows "context-management" and
"jumps (tmux)", CI-able part).

The Stop hook is fed synthetic payloads. The jump and cache-cold paths drive a fake
Claude TUI (tests/fixtures/fake_claude/) in a private tmux server, so no real
Claude session and no user tmux server is touched. Every check has a case it
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

SCRIPTS = REPO / "plugins" / "open-science-context" / "scripts"
PROJECT_SCRIPTS = REPO / "plugins" / "open-science-project" / "scripts"
FAKE = REPO / "tests" / "fixtures" / "fake_claude"

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
pytestmark = needs_jq

SOCK = "/tmp/os-test-sock"          # only a string in the pane key, never connected to
PANE = "%99"


def pane_key(sock=SOCK, pane=PANE):
    k = lambda s: "".join(c if c.isalnum() or c in "._-" else "_" for c in s)
    return f"{k(sock)}__{k(pane)}"


def register(env, sid, key=None):
    """Opt the pane in, as `pane_context.sh set` does, for session `sid`."""
    rec = Path(env["OPSCI_STATE_DIR"]) / "pane_context" / f"{key or pane_key()}.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"version": 1, "pane_id": PANE, "doc_path": "/x/context.md",
                               "registered_at": "", "session_id": sid}))
    return rec


def registered_sid(env, key=None):
    rec = Path(env["OPSCI_STATE_DIR"]) / "pane_context" / f"{key or pane_key()}.json"
    return json.loads(rec.read_text())["session_id"] if rec.exists() else None


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("TMUX", "CLAUDE", "OPSCI", "SLURM"))}
    e.update(OPSCI_STATE_DIR=str(tmp_path / "state"), CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"),
             TMUX=f"{SOCK},1,0", TMUX_PANE=PANE, OPSCI_JUMP_IDLE_TIMEOUT="2",
             OPSCI_JUMP_RECOVER_WINDOW="1")
    (tmp_path / "state").mkdir()
    register(e, "sid-A")
    yield e
    # kill any detached timer left behind
    for f in (tmp_path / "state").glob("timer/*.pid"):
        try:
            os.killpg(int(f.read_text()), 15)
        except (ProcessLookupError, ValueError, PermissionError):
            pass


def stop(env, payload):
    # Under the fake claude when a fake session exists, so the hook never walks up to a
    # real Claude process that happens to be running the tests.
    cmd = ["bash", str(SCRIPTS / "cm_stop.sh")]
    if "FAKE_STATE" in env:
        cmd = [str(FAKE / "claude"), *cmd]
    r = subprocess.run(cmd, input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def payload(sid="sid-A", tasks=(), crons=(), active=False, transcript="", msg=""):
    return {"session_id": sid, "transcript_path": transcript, "hook_event_name": "Stop",
            "stop_hook_active": active, "last_assistant_message": msg,
            "background_tasks": list(tasks), "session_crons": list(crons)}


SUBAGENT = {"id": "a1", "type": "subagent", "status": "running", "description": "x"}
SHELL = {"id": "b1", "type": "shell", "status": "running", "command": "sleep 99"}


def timer_file(env):
    return Path(env["OPSCI_STATE_DIR"]) / "timer" / f"{pane_key()}.pid"


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def write_request(env, kind, sid="sid-A", **extra):
    req = Path(env["OPSCI_STATE_DIR"]) / "jump" / f"{pane_key()}.json"
    req.parent.mkdir(parents=True, exist_ok=True)
    d = {"version": 1, "kind": kind, "context": "/x/context.md", "prompt": "", "sock": SOCK,
         "pane": PANE, "key": pane_key(), "state_file": "/nonexistent", "old_sid": sid,
         "requested_at": "", "phase": "requested"}
    d.update(extra)
    req.write_text(json.dumps(d))
    return req


def transcript(tmp_path, tokens):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "sessionId": "sid-A", "message": {
        "usage": {"input_tokens": tokens, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0, "output_tokens": 1}}}) + "\n")
    return str(p)


# ---- Stop hook: the cache-cold timer -------------------------------------------------

@pytest.mark.parametrize("tasks", [[SUBAGENT], [SHELL]], ids=["subagent", "shell"])
def test_stop_arms_timer_with_a_waker_and_kills_it_without(env, tasks):
    assert stop(env, payload(tasks=tasks)) is None
    pid = int(timer_file(env).read_text())
    assert alive(pid)
    stop(env, payload())                              # refusal: nothing running
    time.sleep(0.3)
    assert not timer_file(env).exists() and not alive(pid)


def test_stop_rearms_timer_at_each_stop(env):
    stop(env, payload(tasks=[SUBAGENT]))
    first = int(timer_file(env).read_text())
    stop(env, payload(tasks=[SUBAGENT]))
    second = int(timer_file(env).read_text())
    time.sleep(0.3)
    assert first != second and not alive(first) and alive(second)


def test_finished_task_is_not_a_waker(env):
    done = dict(SUBAGENT, status="completed")
    stop(env, payload(tasks=[done]))
    assert not timer_file(env).exists()


def test_cron_is_a_waker(env):
    stop(env, payload(crons=[{"id": "c1"}]))
    assert timer_file(env).exists()


def test_no_tmux_means_no_action(env):
    del env["TMUX"], env["TMUX_PANE"]
    assert stop(env, payload(tasks=[SUBAGENT])) is None
    assert not (Path(env["OPSCI_STATE_DIR"]) / "timer").exists()


# ---- Stop hook: jump requests ---------------------------------------------------------

def test_wait_jump_without_waker_is_blocked_and_cancelled(env):
    req = write_request(env, "wait")
    out = stop(env, payload())
    assert out["decision"] == "block" and "Nothing is running" in out["reason"]
    assert not req.exists()


def test_wait_jump_with_waker_starts_the_worker(env):
    req = write_request(env, "wait")
    assert stop(env, payload(tasks=[SHELL])) is None
    time.sleep(0.5)
    # the worker ran against a pane that does not exist and gave up after its 2 s timeout
    deadline = time.time() + 15
    done = Path(env["OPSCI_STATE_DIR"]) / "jump" / "done"
    while time.time() < deadline and not (done.exists() and any(done.iterdir())):
        time.sleep(0.5)
    assert not req.exists()
    rec = json.loads(next(done.iterdir()).read_text())
    assert rec["phase"] == "failed"                  # nothing to clear, so it must not claim success
    assert not timer_file(env).exists()              # a jump kills the timer


def test_stale_request_from_an_earlier_session_is_dropped(env):
    req = write_request(env, "active", sid="sid-OLD")
    assert stop(env, payload(sid="sid-A")) is None
    assert not req.exists()


def test_message_that_mentions_a_jump_does_not_trigger_anything(env):
    # control for the retired wording-based tripwire: prose alone must do nothing
    msg = "Next: session jump. I will jump now and resume from main_context.md."
    assert stop(env, payload(msg=msg)) is None
    assert not (Path(env["OPSCI_STATE_DIR"]) / "jump").exists()


# ---- Stop hook: context-size threshold ------------------------------------------------

def test_threshold_blocks_above_and_not_below(env, tmp_path):
    out = stop(env, payload(transcript=transcript(tmp_path, 300_000)))
    assert out["decision"] == "block" and "300000" in out["reason"]
    assert stop(env, payload(transcript=transcript(tmp_path, 200_000))) is None


def test_threshold_blocks_only_once_per_stop(env, tmp_path):
    assert stop(env, payload(active=True, transcript=transcript(tmp_path, 300_000))) is None


def test_threshold_repeats_only_after_growth(env, tmp_path):
    # a session waiting for the user must not be blocked at every reply above the threshold
    assert stop(env, payload(transcript=transcript(tmp_path, 300_000)))["decision"] == "block"
    assert stop(env, payload(transcript=transcript(tmp_path, 330_000))) is None
    assert stop(env, payload(transcript=transcript(tmp_path, 350_000)))["decision"] == "block"
    # a new session in the same pane (handed the registration) starts its own count
    register(env, "sid-B")
    assert stop(env, payload(sid="sid-B", transcript=transcript(tmp_path, 360_000)))["decision"] == "block"


def test_threshold_repeat_is_a_setting(env, tmp_path):
    env["OPSCI_JUMP_REPEAT"] = "10000"
    assert stop(env, payload(transcript=transcript(tmp_path, 300_000)))["decision"] == "block"
    assert stop(env, payload(transcript=transcript(tmp_path, 311_000)))["decision"] == "block"


def test_stop_outside_tmux_is_silent(env, tmp_path):
    env.pop("TMUX"); env.pop("TMUX_PANE")
    r = subprocess.run(["bash", str(SCRIPTS / "cm_stop.sh")],
                       input=json.dumps(payload(transcript=transcript(tmp_path, 300_000))),
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_threshold_is_a_setting(env, tmp_path):
    env["OPSCI_JUMP_THRESHOLD"] = "100000"
    assert stop(env, payload(transcript=transcript(tmp_path, 150_000)))["decision"] == "block"


# ---- Stop hook: only a registered session is managed ------------------------------------

def test_unregistered_pane_gets_no_timer_and_no_size_notice(env, tmp_path):
    (Path(env["OPSCI_STATE_DIR"]) / "pane_context" / f"{pane_key()}.json").unlink()
    assert stop(env, payload(tasks=[SUBAGENT], transcript=transcript(tmp_path, 300_000))) is None
    assert not timer_file(env).exists()


def test_other_session_in_a_registered_pane_is_not_managed(env, tmp_path):
    # a plain /clear or a new `claude` in the pane does not inherit the hook
    assert stop(env, payload(sid="sid-B", tasks=[SUBAGENT],
                             transcript=transcript(tmp_path, 300_000))) is None
    assert not timer_file(env).exists()
    assert stop(env, payload(sid="sid-A", tasks=[SUBAGENT])) is None      # the registrant is
    assert timer_file(env).exists()


def test_unregistering_kills_the_timer(env):
    stop(env, payload(tasks=[SUBAGENT]))
    pid = int(timer_file(env).read_text())
    subprocess.run(["bash", str(SCRIPTS / "pane_context.sh"), "clear"], env=env, check=True,
                   capture_output=True)
    stop(env, payload(tasks=[SUBAGENT]))
    time.sleep(0.3)
    assert not timer_file(env).exists() and not alive(pid)


def test_jump_request_is_honoured_without_registration(env):
    # jump.sh is itself the opt-in; the request path does not depend on the registration
    (Path(env["OPSCI_STATE_DIR"]) / "pane_context" / f"{pane_key()}.json").unlink()
    write_request(env, "wait")
    assert stop(env, payload())["decision"] == "block"


# ---- jump.sh launcher refusals --------------------------------------------------------

def jump(env, *args, fake_claude=True):
    cmd = ["bash", str(SCRIPTS / "jump.sh"), *map(str, args)]
    if fake_claude:
        cmd = [str(FAKE / "claude"), *cmd]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


@pytest.fixture
def session(env, tmp_path):
    """A fake claude session: state file + a transcript of a chosen size."""
    sid = str(uuid.uuid4())
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"sessionId": sid, "status": "busy"}))
    env["FAKE_STATE"] = str(st)
    proj = Path(env["CLAUDE_CONFIG_DIR"]) / "projects" / "p"
    proj.mkdir(parents=True)

    def size(tokens):
        (proj / f"{sid}.jsonl").write_text(Path(transcript(tmp_path, tokens)).read_text())
    size(150_000)
    make_project(tmp_path)
    ctx = tmp_path / "context.md"
    ctx.write_text("# state\n")
    return {"sid": sid, "state": st, "ctx": ctx, "size": size}


def request(env):
    p = Path(env["OPSCI_STATE_DIR"]) / "jump" / f"{pane_key()}.json"
    return json.loads(p.read_text()) if p.exists() else None


def test_active_jump_writes_a_request_naming_its_context_file(env, session):
    r = jump(env, "active", session["ctx"])
    assert r.returncode == 0, r.stderr
    req = request(env)
    assert req["kind"] == "active" and req["old_sid"] == session["sid"]
    assert req["prompt"] == f"/open-science-context:continue-context {session['ctx']}"


def test_wait_jump_writes_a_request_without_prompt(env, session):
    assert jump(env, "wait", session["ctx"]).returncode == 0
    assert request(env)["prompt"] == ""


def pane_registration(env):
    r = subprocess.run(["bash", str(SCRIPTS / "pane_context.sh"), "get"], env=env,
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def test_jump_request_registers_the_pane(env, session, tmp_path):
    missing = tmp_path / "nope.md"
    assert jump(env, "wait", str(missing)).returncode != 0      # refused: nothing registered
    assert pane_registration(env) is None
    assert jump(env, "wait", session["ctx"]).returncode == 0
    assert pane_registration(env) == str(Path(session["ctx"]).resolve())
    # the live session id, read from the claude state file, not from the environment
    assert "CLAUDE_CODE_SESSION_ID" not in env and registered_sid(env) == session["sid"]


@pytest.mark.parametrize("case", ["no_tmux", "missing_ctx", "stale_ctx", "inhibited",
                                  "no_claude", "below_floor", "whitespace_path", "not_a_project"])
def test_jump_refusals(env, session, tmp_path, case):
    args = ["active", session["ctx"]]
    fake = True
    if case == "no_tmux":
        del env["TMUX"], env["TMUX_PANE"]
    elif case == "missing_ctx":
        args[1] = tmp_path / "nope.md"
    elif case == "stale_ctx":
        old = time.time() - 3600
        os.utime(session["ctx"], (old, old))
    elif case == "inhibited":
        (Path(env["OPSCI_STATE_DIR"]) / "inhibit_jump").write_text("resurrection owns this pane")
    elif case == "no_claude":
        fake = False
    elif case == "below_floor":
        session["size"](50_000)
    elif case == "whitespace_path":
        d = tmp_path / "a b"
        d.mkdir()
        (d / "context.md").write_text("x\n")
        args[1] = d / "context.md"
    elif case == "not_a_project":           # the context plugin needs project management
        (tmp_path / "AGENTS.md").unlink()
    r = jump(env, *args, fake_claude=fake)
    assert r.returncode != 0, r.stdout
    assert request(env) is None


def test_force_overrides_the_floor(env, session):
    session["size"](50_000)
    assert jump(env, "active", session["ctx"], "--force").returncode == 0


def test_cancel_removes_a_pending_request(env, session):
    jump(env, "wait", session["ctx"])
    assert jump(env, "cancel").returncode == 0 and request(env) is None


# ---- wait_slurm.sh ---------------------------------------------------------------------

@pytest.mark.parametrize("args", [[], ["12a"], ["1;rm"]])
def test_wait_slurm_rejects_bad_arguments(args):
    r = subprocess.run(["bash", str(SCRIPTS / "wait_slurm.sh"), *args], capture_output=True, text=True)
    assert r.returncode == 2


def test_wait_slurm_exits_when_jobs_leave_the_queue(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    # fake squeue: job queued for the first two polls, then gone
    (bin_ / "squeue").write_text('#!/bin/bash\nn=$(cat %s/n 2>/dev/null || echo 0); echo $((n+1)) > %s/n\n'
                                 '[ "$n" -lt 2 ] && echo 42\nexit 0\n' % (tmp_path, tmp_path))
    (bin_ / "sacct").write_text("#!/bin/bash\necho ' FAILED'\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", OPSCI_WAIT_POLL="0.1")
    r = subprocess.run(["bash", str(SCRIPTS / "wait_slurm.sh"), "42"], capture_output=True,
                       text=True, env=env, timeout=30)
    assert r.returncode == 1 and "job 42: FAILED" in r.stdout
    assert (tmp_path / "n").read_text().strip() == "3"


# ---- context-file hook ------------------------------------------------------------------

def make_project(root):
    """The two files that mark a template project for the project plugin's hooks."""
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text("x\n")
    (root / "config" / "framework.yaml").write_text("x: 1\n")


def edit_hook(path):
    return subprocess.run(["bash", str(PROJECT_SCRIPTS / "context_hook.sh")],
                          input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(path)}}),
                          capture_output=True, text=True)


@pytest.mark.parametrize("rel,cap", [("context.md", 200), ("tasks/t1/context.md", 200),
                                     ("map/README.md", 150),
                                     # the brainstorm sub-root has the same caps
                                     ("brainstorm/context.md", 200),
                                     ("brainstorm/tasks/b1/context.md", 200),
                                     ("brainstorm/map/README.md", 150)])
def test_context_hook_enforces_caps(tmp_path, rel, cap):
    make_project(tmp_path)
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x\n" * cap)
    assert edit_hook(f).returncode == 0
    f.write_text("x\n" * (cap + 1))                   # refusal: one line over
    r = edit_hook(f)
    assert r.returncode == 2 and "over its cap" in r.stderr


def test_context_hook_ignores_files_outside_a_template_project(tmp_path):
    f = tmp_path / "context.md"                       # no AGENTS.md + config/framework.yaml
    f.write_text("x\n" * 999)
    r = edit_hook(f)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    make_project(tmp_path)                            # control: the same file inside a project
    assert edit_hook(f).returncode == 2


def test_context_hook_reminds_on_task_context_only(tmp_path):
    make_project(tmp_path)
    t = tmp_path / "tasks" / "t1" / "context.md"
    t.parent.mkdir(parents=True)
    t.write_text("x\n")
    out = json.loads(edit_hook(t).stdout)
    assert "one line in the task log.md" in out["hookSpecificOutput"]["additionalContext"]
    p = tmp_path / "context.md"
    p.write_text("x\n")
    assert edit_hook(p).stdout == ""
    other = tmp_path / "notes.md"
    other.write_text("x\n" * 999)
    r = edit_hook(other)
    assert r.returncode == 0 and r.stdout == ""


# ---- jumps and cache-cold against a fake TUI in a private tmux server --------------------

@pytest.fixture
def tui(env, tmp_path):
    sockdir = Path("/tmp") / f"os-test-{uuid.uuid4().hex[:8]}"
    sockdir.mkdir()
    sock = str(sockdir / "s")
    st = tmp_path / "tui_state.json"
    st.write_text(json.dumps({"sessionId": "sid-A", "status": "idle"}))
    rec = tmp_path / "received.log"
    rec.touch()
    tm = ["tmux", "-S", sock, "-f", "/dev/null"]
    subprocess.run([*tm, "new-session", "-d", "-x", "120", "-y", "30",
                    f"env FAKE_BUSY=1 bash {FAKE / 'fake_tui.sh'} {st} {rec}"], check=True)
    pane = subprocess.run([*tm, "display-message", "-p", "-t", "0", "#{pane_id}"],
                          capture_output=True, text=True, check=True).stdout.strip()
    env.update(TMUX=f"{sock},1,0", TMUX_PANE=pane, FAKE_STATE=str(st), OPSCI_IDLE_STABLE="2",
               OPSCI_JUMP_IDLE_TIMEOUT="60", OPSCI_JUMP_RECOVER_WINDOW="5")
    time.sleep(1)
    register(env, "sid-A", pane_key(sock, pane))
    yield {"sock": sock, "pane": pane, "state": st, "rec": rec, "key": pane_key(sock, pane)}
    subprocess.run([*tm, "kill-server"], capture_output=True)
    shutil.rmtree(sockdir, ignore_errors=True)


def wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.5)
    return False


def lines(p):
    return p.read_text().splitlines()


@needs_tmux
def test_active_jump_clears_and_delivers_the_resume_prompt(env, tui, tmp_path):
    make_project(tmp_path)
    ctx = tmp_path / "context.md"
    ctx.write_text("state\n")
    r = jump(env, "active", ctx, "--force")
    assert r.returncode == 0, r.stderr
    stop(env, payload(sid="sid-A"))
    want = f"/open-science-context:continue-context {ctx}"
    assert wait_for(lambda: want in lines(tui["rec"]), 60), lines(tui["rec"])
    assert lines(tui["rec"]) == ["/clear", want]
    new_sid = json.loads(tui["state"].read_text())["sessionId"]
    assert new_sid != "sid-A"
    assert registered_sid(env, tui["key"]) == new_sid        # the new session is managed


@needs_tmux
def test_wait_jump_clears_and_delivers_nothing(env, tui, tmp_path):
    make_project(tmp_path)
    ctx = tmp_path / "context.md"
    ctx.write_text("state\n")
    assert jump(env, "wait", ctx).returncode == 0
    stop(env, payload(sid="sid-A", tasks=[SUBAGENT]))
    assert wait_for(lambda: lines(tui["rec"]) == ["/clear"], 40)
    time.sleep(6)
    assert lines(tui["rec"]) == ["/clear"]
    new_sid = json.loads(tui["state"].read_text())["sessionId"]
    assert new_sid != "sid-A" and registered_sid(env, tui["key"]) == new_sid


@needs_tmux
def test_jump_does_not_type_into_a_busy_pane(env, tui, tmp_path):
    env["FAKE_BUSY"] = "1"
    # make the pane busy for 8 s: submit a line by hand, with a long busy period
    subprocess.run(["tmux", "-S", tui["sock"], "send-keys", "-t", tui["pane"], "hello", "Enter"])
    make_project(tmp_path)
    ctx = tmp_path / "context.md"
    ctx.write_text("state\n")
    # state file says busy and the pane shows the busy marker for FAKE_BUSY=1 s; the worker
    # must wait for idle, then clear, and the typed line must stay first
    jump(env, "active", ctx, "--force")
    stop(env, payload(sid="sid-A"))
    assert wait_for(lambda: len(lines(tui["rec"])) == 3, 60), lines(tui["rec"])
    assert lines(tui["rec"])[:2] == ["hello", "/clear"]


@needs_tmux
def test_cache_cold_fires_after_its_timer(env, tui):
    env["OPSCI_CACHE_COLD_SECONDS"] = "2"
    stop(env, payload(sid="sid-A", tasks=[SUBAGENT]))
    assert wait_for(lambda: any("cache-cold" in l for l in lines(tui["rec"])), 30)
    assert "Do a wait jump now" in lines(tui["rec"])[0]


@needs_tmux
def test_cache_cold_is_killed_when_nothing_runs(env, tui):
    env["OPSCI_CACHE_COLD_SECONDS"] = "3"
    stop(env, payload(sid="sid-A", tasks=[SUBAGENT]))
    stop(env, payload(sid="sid-A"))                   # the waker finished before the timer
    time.sleep(8)
    assert lines(tui["rec"]) == []


@needs_tmux
def test_cache_cold_does_not_fire_into_a_cleared_session(env, tui):
    env["OPSCI_CACHE_COLD_SECONDS"] = "2"
    stop(env, payload(sid="sid-A", tasks=[SUBAGENT]))
    tui["state"].write_text(json.dumps({"sessionId": "sid-B", "status": "idle"}))
    time.sleep(7)
    assert lines(tui["rec"]) == []
