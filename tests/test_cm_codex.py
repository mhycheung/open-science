# opsci: planted-leaks (this file uses fake absolute paths on purpose)
"""Context management under Codex: runtime detection, session registration without
tmux, the Codex hook (cx_hook.sh), Codex session jumps and the Codex waker.

A fake `codex` (tests/fixtures/fake_codex/) stands in for the CLI: as a TUI in a private
tmux server it runs the hook as its child, the way codex-cli 0.159.3 does, and
`codex queue` only logs. No real Codex, SLURM or user tmux server is touched. The
Claude paths are covered by test_cm.py and must not change.
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
FAKE = REPO / "tests" / "fixtures" / "fake_codex"

pytestmark = [
    pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed"),
]
needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


def key(sock, pane):
    k = lambda s: "".join(c if c.isalnum() or c in "._-" else "_" for c in s)
    return f"{k(sock)}__{k(pane)}"


def make_project(root):
    (root / "AGENTS.md").write_text("# agents\n")
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "framework.yaml").write_text("x: 1\n")


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("TMUX", "CLAUDE", "OPSCI", "SLURM", "CODEX"))}
    e.update(OPSCI_STATE_DIR=str(tmp_path / "state"), CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"),
             PATH=f"{FAKE}:{e['PATH']}", FAKE_CX_HOOK=str(SCRIPTS / "cx_hook.sh"),
             FAKE_CX_ARGV_LOG=str(tmp_path / "argv.log"),
             FAKE_CX_QUEUE_LOG=str(tmp_path / "queue.log"),
             FAKE_CX_HOOK_OUT=str(tmp_path / "hook.out"),
             FAKE_SQUEUE_HOLD=str(tmp_path / "hold"), OPSCI_WAIT_POLL="1")
    (tmp_path / "state").mkdir()
    for f in ("argv.log", "queue.log", "hook.out"):
        (tmp_path / f).touch()
    return e


def run(env, script, *args, stdin=None):
    return subprocess.run(["bash", str(SCRIPTS / script), *map(str, args)], input=stdin,
                          capture_output=True, text=True, env=env, timeout=60)


def wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.3)
    return False


def lines(p):
    return Path(p).read_text().splitlines()


# ---- runtime detection ---------------------------------------------------------------

def runtime(env, **extra):
    e = dict(env, **extra)
    return subprocess.run(["bash", "-c", f". {SCRIPTS / 'cm_lib.sh'}; cm_runtime"],
                          capture_output=True, text=True, env=e).stdout.strip()


def test_runtime_detection(env):
    # The test runner itself may run under Claude or Codex; OPSCI_RUNTIME is the override.
    assert runtime(env, CODEX_THREAD_ID="t1", OPSCI_RUNTIME="") in ("codex",)
    assert runtime(env, CLAUDECODE="1") == "claude"
    assert runtime(env, OPSCI_RUNTIME="codex") == "codex"
    assert runtime(env, OPSCI_RUNTIME="claude", CODEX_THREAD_ID="t1") == "claude"


# ---- registration without tmux --------------------------------------------------------

@pytest.mark.parametrize("who", ["codex", "claude"])
def test_context_registration_works_without_tmux(env, tmp_path, who):
    ctx = tmp_path / "context.md"
    ctx.write_text("x\n")
    if who == "codex":
        me, other = dict(env, CODEX_THREAD_ID="thread-1"), dict(env, CODEX_THREAD_ID="thread-2")
    else:
        me = dict(env, CLAUDECODE="1", CLAUDE_CODE_SESSION_ID="sid-1")
        other = dict(env, CLAUDECODE="1", CLAUDE_CODE_SESSION_ID="sid-2")
    r = run(me, "pane_context.sh", "set", ctx)
    assert r.returncode == 0, r.stderr
    assert run(me, "pane_context.sh", "get").stdout.strip() == str(ctx)
    assert run(other, "pane_context.sh", "get").returncode == 1      # another session: nothing
    run(me, "pane_context.sh", "clear")
    assert run(me, "pane_context.sh", "get").returncode == 1


def test_session_record_is_not_used_for_a_deleted_file(env, tmp_path):
    ctx = tmp_path / "context.md"
    ctx.write_text("x\n")
    me = dict(env, CODEX_THREAD_ID="thread-1")
    run(me, "pane_context.sh", "set", ctx)
    ctx.unlink()
    assert run(me, "pane_context.sh", "get").returncode == 1


# ---- the hook ----------------------------------------------------------------------------

def hook(env, event, sid="thread-1", **extra):
    p = {"session_id": sid, "transcript_path": extra.pop("tp", ""), "cwd": "/x",
         "hook_event_name": event, "model": "m1", "permission_mode": "bypassPermissions",
         "stop_hook_active": False, **extra}
    r = run(env, "cx_hook.sh", stdin=json.dumps(p))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def test_hook_tracks_status_by_thread_and_pane(env):
    e = dict(env, TMUX="/tmp/os-cx-sock,1,0", TMUX_PANE="%7")
    hook(e, "SessionStart")
    st = Path(env["OPSCI_STATE_DIR"]) / "codex"
    thread = st / "threads" / "thread-1.json"
    pane = st / "panes" / f"{key('/tmp/os-cx-sock', '%7')}.json"
    assert json.loads(thread.read_text())["status"] == "idle"
    hook(e, "UserPromptSubmit", prompt="hi")
    assert json.loads(thread.read_text())["status"] == "busy"
    assert json.loads(pane.read_text())["thread_id"] == "thread-1"
    hook(e, "Stop")
    assert json.loads(pane.read_text())["status"] == "idle"
    assert json.loads(pane.read_text())["model"] == "m1"


def rollout(tmp_path, tokens, window=258400):
    p = tmp_path / "rollout.jsonl"
    p.write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
        "last_token_usage": {"input_tokens": tokens}, "model_context_window": window}}}) + "\n")
    return p


def register_pane(env, sock, pane, sid):
    rec = Path(env["OPSCI_STATE_DIR"]) / "pane_context" / f"{key(sock, pane)}.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"version": 1, "pane_id": pane, "doc_path": "/x/c.md",
                               "session_id": sid}))


def test_size_notice_above_the_threshold_only_for_the_registered_thread(env, tmp_path):
    e = dict(env, TMUX="/tmp/os-cx-sock,1,0", TMUX_PANE="%7")
    big, small = rollout(tmp_path, 200_000), tmp_path / "small.jsonl"
    small.write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
        "last_token_usage": {"input_tokens": 1000}, "model_context_window": 258400}}}) + "\n")
    assert hook(e, "Stop", tp=str(big)) is None                   # not registered
    register_pane(env, "/tmp/os-cx-sock", "%7", "thread-1")
    assert hook(e, "Stop", tp=str(small)) is None                 # below 60% of the window
    out = hook(e, "Stop", tp=str(big))
    assert out["decision"] == "block" and "active jump" in out["reason"]
    assert hook(e, "Stop", tp=str(big)) is None                   # once, until it grows
    assert hook(dict(e, OPSCI_JUMPS="wait"), "Stop", tp=str(rollout(tmp_path, 255_000))) is None


def test_unreadable_rollout_gives_no_notice(env, tmp_path):
    e = dict(env, TMUX="/tmp/os-cx-sock,1,0", TMUX_PANE="%7")
    register_pane(env, "/tmp/os-cx-sock", "%7", "thread-1")
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"type":"something_else"}\n')
    assert hook(e, "Stop", tp=str(bad)) is None


def test_wait_jump_without_waker_is_refused(env):
    e = dict(env, TMUX="/tmp/os-cx-sock,1,0", TMUX_PANE="%7")
    req = Path(env["OPSCI_STATE_DIR"]) / "jump" / f"{key('/tmp/os-cx-sock', '%7')}.json"
    req.parent.mkdir(parents=True)
    req.write_text(json.dumps({"runtime": "codex", "kind": "wait", "old_sid": "thread-1",
                               "phase": "requested", "key": key('/tmp/os-cx-sock', '%7')}))
    out = hook(e, "Stop")
    assert out["decision"] == "block" and "refused" in out["reason"]
    assert not req.exists()


def test_claude_request_is_not_driven_by_the_codex_hook(env):
    e = dict(env, TMUX="/tmp/os-cx-sock,1,0", TMUX_PANE="%7")
    req = Path(env["OPSCI_STATE_DIR"]) / "jump" / f"{key('/tmp/os-cx-sock', '%7')}.json"
    req.parent.mkdir(parents=True)
    req.write_text(json.dumps({"kind": "active", "old_sid": "thread-1", "phase": "requested"}))
    assert hook(e, "Stop") is None
    assert not req.exists()          # dropped as stale, never handed to a worker


# ---- relaunch command -----------------------------------------------------------------------

def relaunch(env, args, model=""):
    p = subprocess.Popen([str(FAKE / "codex"), *args], env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    try:
        return subprocess.run(["bash", "-c", f'. {SCRIPTS / "cm_lib.sh"}; cm_codex_relaunch_cmd {p.pid} "go on" "{model}"'],
                              capture_output=True, text=True, env=env)
    finally:
        p.terminate()
        p.wait()


def test_relaunch_keeps_options_and_drops_the_old_thread(env):
    r = relaunch(env, ["-m", "old", "-s", "workspace-write", "-a", "on-request", "-c", 'x="a b"',
                       "--add-dir=/d", "resume", "0000-thread", "old prompt"], model="new")
    assert r.returncode == 0, r.stderr
    words = r.stdout.split()
    assert words[:3] == ["codex", "-m", "new"]
    assert "workspace-write" in words and "on-request" in words and "--add-dir=/d" in words
    assert "resume" not in words and "0000-thread" not in r.stdout and "old" not in words
    assert "--dangerously-bypass-approvals-and-sandbox" not in r.stdout    # nothing added
    assert r.stdout.strip().endswith("go\\ on")


@pytest.mark.parametrize("args", [["exec", "x"], ["--unknown-flag"], ["--worktree"]])
def test_relaunch_refuses_what_it_does_not_understand(env, args):
    assert relaunch(env, args).returncode == 1


# ---- end to end in a private tmux server ----------------------------------------------------------

@pytest.fixture
def pane(env, tmp_path):
    sockdir = Path("/tmp") / f"os-cx-{uuid.uuid4().hex[:8]}"
    sockdir.mkdir()
    sock = str(sockdir / "s")
    tm = ["tmux", "-S", sock, "-f", "/dev/null"]
    env.update(OPSCI_IDLE_STABLE="2", OPSCI_JUMP_IDLE_TIMEOUT="30", OPSCI_CODEX_START_TIMEOUT="30")
    subprocess.run([*tm, "new-session", "-d", "-x", "150", "-y", "30", "-c", str(tmp_path),
                    "bash --norc --noprofile"], check=True, env=env)
    p = subprocess.run([*tm, "display-message", "-p", "-t", "0", "#{pane_id}"],
                       capture_output=True, text=True, check=True).stdout.strip()
    e = dict(env, TMUX=f"{sock},1,0", TMUX_PANE=p)
    make_project(tmp_path)
    ctx = tmp_path / "context.md"
    ctx.write_text("state\n")
    k = key(sock, p)
    prec = Path(env["OPSCI_STATE_DIR"]) / "codex" / "panes" / f"{k}.json"

    def start(cmd):
        subprocess.run([*tm, "send-keys", "-t", p, "-l", cmd], check=True)
        subprocess.run([*tm, "send-keys", "-t", p, "Enter"], check=True)
        assert wait_for(lambda: prec.exists() and json.loads(prec.read_text())["status"] == "idle"
                        and json.loads(prec.read_text())["event"] == "Stop", 20)
        return json.loads(prec.read_text())

    yield {"sock": sock, "pane": p, "env": e, "ctx": ctx, "key": k, "prec": prec, "start": start,
           "tm": tm}
    subprocess.run([*tm, "kill-server"], capture_output=True)
    shutil.rmtree(sockdir, ignore_errors=True)


def as_tool_shell(pane, rec):
    """The environment of a Codex tool shell in that pane."""
    return dict(pane["env"], CODEX_THREAD_ID=rec["thread_id"])


def state(env):
    return Path(env["OPSCI_STATE_DIR"])


@needs_tmux
def test_active_jump_ends_the_tui_and_starts_a_fresh_one(env, pane, tmp_path):
    rec = pane["start"]("codex -s workspace-write -a on-request 'hello'")
    old = rec["thread_id"]
    sh = as_tool_shell(pane, rec)
    r = run(sh, "jump.sh", "active", pane["ctx"], "--force")
    assert r.returncode == 0, r.stderr
    os.kill(int(rec["tui_pid"]), 10)          # the turn ends: the fake fires Stop
    assert wait_for(lambda: json.loads(pane["prec"].read_text())["thread_id"] != old, 60), \
        (state(env) / "cm.log").read_text()
    argv = lines(env["FAKE_CX_ARGV_LOG"])
    assert len(argv) == 2
    assert "-s workspace-write" in argv[1] and "-a on-request" in argv[1] and "-m fake-model" in argv[1]
    assert f"continue-context skill to continue from {pane['ctx']}" in argv[1]
    new = json.loads(pane["prec"].read_text())["thread_id"]
    reg = state(env) / "pane_context" / f"{pane['key']}.json"
    assert wait_for(lambda: not (state(env) / "jump" / f"{pane['key']}.json").exists(), 10)
    assert json.loads(reg.read_text())["session_id"] == new      # the new thread is managed
    done = sorted((state(env) / "jump" / "done").glob("*.json"))
    assert json.loads(done[-1].read_text())["phase"] == "done"
    assert lines(env["FAKE_CX_QUEUE_LOG"]) == []          # nothing typed or queued into Codex


@needs_tmux
def test_jump_needs_the_hook_record_of_this_thread(env, pane):
    rec = pane["start"]("codex 'hello'")
    sh = dict(pane["env"], CODEX_THREAD_ID="another-thread")
    r = run(sh, "jump.sh", "active", pane["ctx"], "--force")
    assert r.returncode == 1 and "hook" in r.stderr


@needs_tmux
def test_jump_fails_safe_when_the_pane_has_no_shell(env, pane, tmp_path):
    # Codex started as the pane's own command: ending it would close the pane.
    tm, p = pane["tm"], pane["pane"]
    subprocess.run([*tm, "respawn-pane", "-k", "-t", p, f"{FAKE / 'codex'} hello"], check=True, env=pane["env"])
    assert wait_for(lambda: pane["prec"].exists() and json.loads(pane["prec"].read_text())["event"] == "Stop", 20)
    rec = json.loads(pane["prec"].read_text())
    r = run(as_tool_shell(pane, rec), "jump.sh", "active", pane["ctx"], "--force")
    assert r.returncode == 0, r.stderr
    os.kill(int(rec["tui_pid"]), 10)
    assert wait_for(lambda: lines(env["FAKE_CX_QUEUE_LOG"]), 40)
    tid, msg = lines(env["FAKE_CX_QUEUE_LOG"])[0].split("\t", 1)
    assert tid == rec["thread_id"] and "jump FAILED before anything was changed" in msg and "shell" in msg
    assert os.path.exists(f"/proc/{rec['tui_pid']}")      # the session was left running


@needs_tmux
def test_wait_jump_with_a_waker_relaunches_and_the_waker_wakes_the_new_thread(env, pane, tmp_path):
    hold = tmp_path / "hold"
    rec = pane["start"]("codex 'hello'")
    sh = as_tool_shell(pane, rec)
    r = run(sh, "wait_slurm.sh", "--notify", "123")
    assert r.returncode == 0, r.stderr
    assert run(sh, "jump.sh", "wait", pane["ctx"]).returncode == 0
    hold.touch()                               # job 123 stays queued until the relaunch is done
    os.kill(int(rec["tui_pid"]), 10)
    assert wait_for(lambda: json.loads(pane["prec"].read_text())["thread_id"] != rec["thread_id"], 60), \
        (state(env) / "cm.log").read_text()
    new = json.loads(pane["prec"].read_text())["thread_id"]
    assert lines(env["FAKE_CX_QUEUE_LOG"]) == []
    hold.unlink()                              # the job ends
    assert wait_for(lambda: lines(env["FAKE_CX_QUEUE_LOG"]), 30)
    tid, msg = lines(env["FAKE_CX_QUEUE_LOG"])[0].split("\t", 1)
    assert "job 123: COMPLETED" in msg
    assert tid == new                          # the thread running in the pane now, not the ended one


def test_notify_is_refused_under_claude_without_the_mod(env):
    r = run(dict(env, CLAUDECODE="1"), "wait_slurm.sh", "--notify", "123")
    assert r.returncode == 4 and "background Bash" in r.stderr and "mod is not loaded" in r.stderr


# ---- follow-ups: sandbox state dir, waker claims, pid reuse, worker ordering -------------------

def test_unwritable_state_dir_is_reported_with_the_fix_under_codex(env, tmp_path):
    make_project(tmp_path)
    ctx = tmp_path / "context.md"
    ctx.write_text("x\n")
    me = dict(env, CODEX_THREAD_ID="thread-1")
    assert run(me, "pane_context.sh", "set", ctx).returncode == 0     # recorded while writable
    st = Path(env["OPSCI_STATE_DIR"])
    for d in [st, *st.rglob("*")]:
        if d.is_dir():
            d.chmod(0o555)
    try:
        for args in (("pane_context.sh", "set", ctx), ("pane_context.sh", "check"),
                     ("wait_slurm.sh", "--notify", "123")):
            r = run(me, *args)
            assert r.returncode == 3, (args, r.stderr)
            assert f"--add-dir {st}" in r.stderr and "mkdir -p" in r.stderr
        r = run(dict(me, TMUX="/tmp/os-cx-sock,1,0", TMUX_PANE="%7"), "jump.sh", "active", ctx, "--force")
        assert r.returncode == 3 and "--add-dir" in r.stderr
        assert run(me, "pane_context.sh", "get").stdout.strip() == str(ctx)   # reading still works
    finally:
        for d in [st, *st.rglob("*")]:
            if d.is_dir():
                d.chmod(0o755)
    assert run(me, "pane_context.sh", "check").returncode == 0


def test_two_overlapping_stops_start_a_queued_waker_once(env):
    e = dict(env, TMUX="/tmp/os-cx-sock,1,0", TMUX_PANE="%7", CODEX_THREAD_ID="thread-1")
    assert run(e, "wait_slurm.sh", "--notify", "123").returncode == 0
    payload = json.dumps({"session_id": "thread-1", "hook_event_name": "Stop", "cwd": "/x",
                          "transcript_path": "", "stop_hook_active": False})
    e.pop("CODEX_THREAD_ID")
    procs = [subprocess.Popen(["bash", str(SCRIPTS / "cx_hook.sh")], stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, env=e, text=True) for _ in range(2)]
    for p in procs:
        p.communicate(payload, timeout=30)
    st = state(env)
    assert wait_for(lambda: list((st / "wakers" / "done").glob("*.json")), 30)
    time.sleep(2)
    log = (st / "cm.log").read_text()
    assert log.count("started (pid") == 1, log
    assert len(lines(env["FAKE_CX_QUEUE_LOG"])) == 1
    left = [f for f in (st / "wakers").glob("*/*.json") if f.parent.name != "done"]
    assert left == []                                              # nothing left behind or rewritten
    done = json.loads(next((st / "wakers" / "done").glob("*.json")).read_text())
    assert done["state"] == "done" and done["pid"] > 0


def test_a_reused_pid_is_not_taken_for_the_recorded_tui(env):
    p = subprocess.Popen([str(FAKE / "codex")], env=env, stdout=subprocess.DEVNULL)
    time.sleep(0.5)
    try:
        lib = f". {SCRIPTS / 'cm_lib.sh'}"
        sh = lambda c: subprocess.run(["bash", "-c", f"{lib}; {c}"], env=env).returncode
        start = subprocess.run(["bash", "-c", f"{lib}; cm_proc_start {p.pid}"], env=env,
                               capture_output=True, text=True).stdout.strip()
        assert start
        assert sh(f"cm_is_codex_proc {p.pid} {start}") == 0
        assert sh(f"cm_is_codex_proc {p.pid} 1") == 1               # same pid, another process
        assert sh(f"cm_is_codex_proc {p.pid}") == 0                 # no start recorded: pid only
    finally:
        p.terminate()
        p.wait()


@needs_tmux
def test_jump_refuses_to_type_when_the_pane_does_not_return_to_its_shell(env, pane):
    rec = pane["start"]("FAKE_CX_ON_TERM='sleep 60' codex 'hello'")
    assert run(as_tool_shell(pane, rec), "jump.sh", "active", pane["ctx"], "--force").returncode == 0
    os.kill(int(rec["tui_pid"]), 10)
    assert wait_for(lambda: lines(env["FAKE_CX_QUEUE_LOG"]), 60), (state(env) / "cm.log").read_text()
    tid, msg = lines(env["FAKE_CX_QUEUE_LOG"])[0].split("\t", 1)
    assert tid == rec["thread_id"]
    assert "ENDED" in msg and f"codex resume {rec['thread_id']}" in msg and "did not return" in msg
    assert "nothing was changed" not in msg
    assert len(lines(env["FAKE_CX_ARGV_LOG"])) == 1                  # no new codex was typed
    assert "codex -m" not in subprocess.run([*pane["tm"], "capture-pane", "-p", "-t", pane["pane"]],
                                            capture_output=True, text=True).stdout


@needs_tmux
def test_request_says_relaunched_before_the_new_session_starts(env, pane, tmp_path):
    reqlog = tmp_path / "req.log"
    req = state(env) / "jump" / f"{pane['key']}.json"
    rec = pane["start"](f"export FAKE_CX_REQ={req} FAKE_CX_REQ_LOG={reqlog}; codex 'hello'")
    assert run(as_tool_shell(pane, rec), "jump.sh", "active", pane["ctx"], "--force").returncode == 0
    os.kill(int(rec["tui_pid"]), 10)
    assert wait_for(lambda: len(lines(reqlog)) == 2, 60), (state(env) / "cm.log").read_text()
    assert lines(reqlog) == ["none", "relaunched"]


@needs_tmux
def test_jobs_ending_during_the_jump_wake_the_new_thread_not_the_ended_one(env, pane, tmp_path):
    hold = tmp_path / "hold"
    hold.touch()
    # Every codex in this pane takes 6 s to start, so the jump is still in flight when the
    # job ends.
    rec = pane["start"]("export FAKE_CX_START_DELAY=6; codex 'hello'")
    sh = as_tool_shell(pane, rec)
    assert run(sh, "wait_slurm.sh", "--notify", "123").returncode == 0
    assert run(sh, "jump.sh", "wait", pane["ctx"]).returncode == 0
    os.kill(int(rec["tui_pid"]), 10)
    req = state(env) / "jump" / f"{pane['key']}.json"
    assert wait_for(lambda: req.exists() and json.loads(req.read_text())["phase"] != "requested", 20)
    hold.unlink()                                   # the job ends mid-jump
    assert wait_for(lambda: lines(env["FAKE_CX_QUEUE_LOG"]), 90), (state(env) / "cm.log").read_text()
    new = json.loads(pane["prec"].read_text())["thread_id"]
    assert new != rec["thread_id"]
    tid, msg = lines(env["FAKE_CX_QUEUE_LOG"])[0].split("\t", 1)
    assert "job 123: COMPLETED" in msg and tid == new
