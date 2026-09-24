"""notify tests (report, Tests table, row "notify").

The token never appears in any process's argv or in the sender's environment (checked from
outside while a send is in progress, against a local mock Slack server); a group- or
world-readable credentials file is refused; the file back end writes one document per
message with unique names; with no configuration, notify falls back to the file back end.
Each check has a case it must refuse next to one it must pass. The real Slack send is manual.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import REPO
from opsci import notify as N

# Fake, for the mock server only; built in pieces so secret scanners do not flag this file.
TOKEN = "xox" + "b-000000000000-test-token-" + "q7Zp" * 4
CHANNEL = "C0TESTCHAN"


# ---------------------------------------------------------------- helpers

def base_env(home: Path) -> dict:
    """Environment for a child `opsci notify`: an empty home, so the real user's config and
    credentials are never read by a test."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPSCI_CONFIG", "XDG_CONFIG_HOME", N.TEST_API_ENV)}
    env["HOME"] = str(home)
    return env


def run_notify(args, cwd, env, **kw):
    return subprocess.run([sys.executable, "-m", "opsci.cli", "notify", *map(str, args)],
                          cwd=cwd, env=env, capture_output=True, text=True, **kw)


def make_project(tmp_path, name="proj") -> Path:
    p = tmp_path / name
    (p / "config").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    return p


def write_creds(home: Path, mode=0o600, token=TOKEN, channel=CHANNEL) -> Path:
    d = home / ".config" / "opsci"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    f = d / "slack.env"
    f.write_text(f"SLACK_TOKEN={token}\nSLACK_CHANNEL={channel}\n")
    os.chmod(f, mode)
    return f


def use_slack(project: Path, extra=""):
    (project / "config" / "site.local.yaml").write_text("notify:\n  backend: slack\n" + extra)


def messages(project: Path):
    return sorted((project / "messages").glob("*.md")) if (project / "messages").exists() else []


class MockSlack:
    """A local stand-in for the Slack Web API. Optionally blocks inside the first request
    until the test releases it, so the test can inspect the sender while it is sending."""

    def __init__(self, block=False, fail_with=None):
        self.requests = []
        self.uploads = []
        self.arrived = threading.Event()
        self.release = threading.Event()
        if not block:
            self.release.set()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, obj, ctype="application/json"):
                body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                u = urlsplit(self.path)
                path = u.path.lstrip("/")  # compared without the leading slash
                outer.requests.append({"method": self.command, "path": path,
                                       "query": parse_qs(u.query), "body": body,
                                       "auth": self.headers.get("Authorization"),
                                       # this server runs in the test process: record whether
                                       # the token is in that process's environment right now
                                       "env_token": any(TOKEN in f"{k}={v}"
                                                        for k, v in os.environ.items())})
                outer.arrived.set()
                outer.release.wait(30)
                if fail_with and path.startswith("api/"):
                    return self._reply({"ok": False, "error": fail_with})
                if path == "api/chat.postMessage":
                    return self._reply({"ok": True, "ts": "1.2"})
                if path == "api/files.getUploadURLExternal":
                    return self._reply({"ok": True, "file_id": "F123",
                                        "upload_url": f"{outer.root}/upload/F123"})
                if path == "upload/F123":
                    outer.uploads.append(body)
                    return self._reply(b"OK - 5", "text/plain")
                if path == "api/files.completeUploadExternal":
                    return self._reply({"ok": True, "files": [{"id": "F123"}]})
                self.send_response(404)
                self.end_headers()

            do_GET = do_POST = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.root = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.api = self.root + "/api"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def mock():
    servers = []

    def make(**kw):
        s = MockSlack(**kw)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def token_exposure(pid: int, token: str) -> list[str]:
    """Where `token` is visible from outside process `pid`: its argv, its environment, and
    the argv of every process `ps` lists."""
    found = []
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    if token.encode() in cmdline:
        found.append("argv")
    environ = Path(f"/proc/{pid}/environ").read_bytes()
    if token.encode() in environ:
        found.append("environ")
    ps = subprocess.run(["ps", "-eww", "-o", "pid=,args="], capture_output=True, text=True).stdout
    if token in ps:
        found.append("ps")
    return found


needs_proc = pytest.mark.skipif(not Path("/proc", "self", "environ").exists(), reason="needs /proc")


# ---------------------------------------------------------------- token exposure

@needs_proc
def test_token_not_in_argv_or_env_during_send(tmp_path, mock):
    home = tmp_path / "home"
    home.mkdir()
    write_creds(home)
    proj = make_project(tmp_path)
    use_slack(proj)
    att = tmp_path / "plot.txt"
    att.write_text("hello")
    srv = mock(block=True)
    env = base_env(home) | {N.TEST_API_ENV: srv.api}
    child = subprocess.Popen([sys.executable, "-m", "opsci.cli", "notify", "run finished", str(att)],
                             cwd=proj, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    try:
        assert srv.arrived.wait(30), "the sender never reached the mock server"
        exposure = token_exposure(child.pid, TOKEN)  # the send is in progress now
    finally:
        srv.release.set()
        out, err = child.communicate(timeout=60)
    assert exposure == []
    assert child.returncode == 0, err
    # The send really carried the token (so the checks above were not vacuous) ...
    assert srv.requests[0]["path"] == "api/chat.postMessage"
    assert srv.requests[0]["auth"] == f"Bearer {TOKEN}"
    assert json.loads(srv.requests[0]["body"]) == {"channel": CHANNEL, "text": "run finished"}
    # ... only in the Authorization header, and the file went through the upload flow.
    paths = [r["path"] for r in srv.requests]
    assert paths == ["api/chat.postMessage", "api/files.getUploadURLExternal",
                     "upload/F123", "api/files.completeUploadExternal"]
    assert srv.requests[2]["auth"] is None  # the pre-signed upload URL gets no token
    assert srv.uploads == [b"hello"]
    assert all(TOKEN.encode() not in r["body"] for r in srv.requests)
    assert TOKEN not in out + err
    assert messages(proj) == []  # delivered, so nothing kept locally


@needs_proc
def test_exposure_check_detects_token_in_argv_and_env(tmp_path):
    """Control: the same check must find a token that is on a command line or in an environment."""
    code = "import time; time.sleep(30)"
    p1 = subprocess.Popen([sys.executable, "-c", code, TOKEN])
    p2 = subprocess.Popen([sys.executable, "-c", code], env=os.environ | {"SLACK_TOKEN": TOKEN})
    try:
        assert set(token_exposure(p1.pid, TOKEN)) == {"argv", "ps"}
        assert "environ" in token_exposure(p2.pid, TOKEN)
    finally:
        p1.kill()
        p2.kill()
        p1.wait()
        p2.wait()


def run_in_process(tmp_path, monkeypatch, srv, backend="slack"):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    write_creds(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPSCI_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv(N.TEST_API_ENV, srv.api)
    proj = make_project(tmp_path)
    return N.notify("in process", backend=backend, project_root=str(proj))


def test_token_not_put_in_own_environment(tmp_path, mock, monkeypatch):
    """/proc/<pid>/environ shows only the environment a process started with, so a token the
    sender put into its own os.environ (and so into every child it starts) would not show
    there. This checks the live environment of the sending process during the send."""
    srv = mock()
    assert run_in_process(tmp_path, monkeypatch, srv) == 0
    assert [r["env_token"] for r in srv.requests] == [False]
    assert srv.requests[0]["auth"] == f"Bearer {TOKEN}"


def test_own_environment_check_detects_exported_token(tmp_path, mock, monkeypatch):
    """Control: a back end that exports the token is caught by the same check."""
    class Leaky(N.SlackBackend):
        name = "leaky"

        def _call(self, method, data=None, query=None):
            os.environ["SLACK_TOKEN"] = self._token
            return super()._call(method, data, query)

    monkeypatch.setitem(N.BACKENDS, "leaky", Leaky)
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    srv = mock()
    assert run_in_process(tmp_path, monkeypatch, srv, backend="leaky") == 0
    os.environ.pop("SLACK_TOKEN", None)
    assert [r["env_token"] for r in srv.requests] == [True]


def test_token_echoed_by_server_is_redacted(tmp_path, mock):
    """A failed send reports the error without the token, exits 3, and keeps the message."""
    home = tmp_path / "home"
    home.mkdir()
    write_creds(home)
    proj = make_project(tmp_path)
    use_slack(proj)
    srv = mock(fail_with=f"bad token {TOKEN}")
    r = run_notify(["job done"], proj, base_env(home) | {N.TEST_API_ENV: srv.api})
    assert r.returncode == N.EXIT_DELIVERY
    assert "Slack chat.postMessage failed" in r.stderr and "<token>" in r.stderr
    assert TOKEN not in r.stdout + r.stderr and "Traceback" not in r.stderr
    assert len(messages(proj)) == 1  # kept locally
    # control: the same setup with a working server succeeds
    ok = mock()
    r = run_notify(["job done"], proj, base_env(home) | {N.TEST_API_ENV: ok.api})
    assert r.returncode == 0, r.stderr


def test_api_override_only_for_loopback(tmp_path, mock, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    write_creds(home)
    proj = make_project(tmp_path)
    use_slack(proj)
    r = run_notify(["x"], proj, base_env(home) | {N.TEST_API_ENV: "http://example.org/api"})
    assert r.returncode == N.EXIT_CONFIG and "loopback" in r.stderr
    srv = mock()
    r = run_notify(["x"], proj, base_env(home) | {N.TEST_API_ENV: srv.api})
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- credentials file

@pytest.mark.parametrize("mode,ok", [(0o600, True), (0o400, True), (0o640, False),
                                     (0o604, False), (0o644, False), (0o660, False)])
def test_credentials_file_mode(tmp_path, mock, mode, ok):
    home = tmp_path / "home"
    home.mkdir()
    f = write_creds(home, mode=mode)
    proj = make_project(tmp_path)
    use_slack(proj)
    srv = mock()
    r = run_notify(["hello"], proj, base_env(home) | {N.TEST_API_ENV: srv.api})
    os.chmod(f, 0o600)
    if ok:
        assert r.returncode == 0, r.stderr
        assert [q["path"] for q in srv.requests] == ["api/chat.postMessage"]
    else:
        assert r.returncode == N.EXIT_CONFIG
        assert "group or others have access" in r.stderr and "chmod 600" in r.stderr
        assert srv.requests == []  # nothing was sent
        assert "Traceback" not in r.stderr


def test_credentials_file_not_owned_is_refused(tmp_path, monkeypatch):
    f = write_creds(tmp_path)
    N.check_private_file(f)  # control: ours, mode 600
    monkeypatch.setattr(N.os, "getuid", lambda: os.stat(f).st_uid + 1)
    with pytest.raises(N.NotifyError, match="not owned by you"):
        N.check_private_file(f)


def test_credentials_file_inside_project_is_refused(tmp_path):
    proj = make_project(tmp_path)
    inside = proj / "config" / "slack.env"
    b = N.SlackBackend({"credentials_file": str(inside)}, proj, proj)
    with pytest.raises(N.NotifyError, match="inside the project"):
        b.credentials_path()
    outside = tmp_path / "home" / "slack.env"
    assert N.SlackBackend({"credentials_file": str(outside)}, proj, proj).credentials_path() == outside


def test_missing_credentials_is_a_clear_error(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proj = make_project(tmp_path)
    use_slack(proj)
    r = run_notify(["hello"], proj, base_env(home))
    assert r.returncode == N.EXIT_CONFIG
    assert "credentials file not found" in r.stderr and "Traceback" not in r.stderr
    assert "kept the message locally" in r.stderr and len(messages(proj)) == 1


def test_token_in_config_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proj = make_project(tmp_path)
    for extra in (f"  slack:\n    token: {TOKEN}\n", f"  slack:\n    channel: {TOKEN}\n"):
        use_slack(proj, extra)
        r = run_notify(["hello"], proj, base_env(home))
        assert r.returncode == N.EXIT_CONFIG and "credentials file" in r.stderr
        assert TOKEN not in r.stderr
    # control: the same config without a token is accepted (it then selects the file back end)
    (proj / "config" / "site.local.yaml").write_text("notify:\n  backend: file\n  slack:\n    channel: C1\n")
    assert run_notify(["hello"], proj, base_env(home)).returncode == 0


def test_bad_yaml_is_a_clear_error(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proj = make_project(tmp_path)
    (proj / "config" / "site.local.yaml").write_text("notify: [unclosed\n")
    r = run_notify(["hello"], proj, base_env(home))
    assert r.returncode == N.EXIT_CONFIG
    assert "not valid YAML" in r.stderr and "Traceback" not in r.stderr


# ---------------------------------------------------------------- file back end, fallback

def test_zero_config_falls_back_to_file_backend(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proj = make_project(tmp_path)
    r = run_notify(["Session jump: see context.md"], proj, base_env(home))
    assert r.returncode == 0, r.stderr
    [m] = messages(proj)
    assert "Session jump: see context.md" in m.read_text()
    assert r.stdout.strip() == f"opsci notify: wrote messages/{m.name}"
    # control: once slack is selected, the file back end is no longer used silently
    use_slack(proj)
    r = run_notify(["second"], proj, base_env(home))
    assert r.returncode == N.EXIT_CONFIG


def test_zero_config_outside_git(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    d = tmp_path / "plain"
    d.mkdir()
    r = run_notify(["hello"], d, base_env(home))
    assert r.returncode == 0, r.stderr
    assert len(messages(d)) == 1


def test_file_backend_unique_names_same_second(tmp_path):
    b = N.FileBackend({}, tmp_path, tmp_path)
    now = dt.datetime(2026, 9, 23, 10, 15, 0)
    b.send("Run finished: all good", None, now=now)
    b.send("Run finished: all good", None, now=now)
    b.send("Something else", None, now=now)
    names = [m.name for m in messages(tmp_path)]
    assert names == ["2026-09-23_101500_run-finished-all-good-2.md",
                     "2026-09-23_101500_run-finished-all-good.md",
                     "2026-09-23_101500_something-else.md"]
    assert all(("Run finished" in (tmp_path / "messages" / n).read_text()) == ("run-finished" in n)
               for n in names)


def test_file_backend_never_overwrites(tmp_path):
    """Control for uniqueness: an existing file with the would-be name is left untouched."""
    d = tmp_path / "messages"
    d.mkdir()
    (d / "2026-09-23_101500_hello.md").write_text("earlier message\n")
    N.FileBackend({}, tmp_path, tmp_path).send("hello", None, now=dt.datetime(2026, 9, 23, 10, 15))
    assert (d / "2026-09-23_101500_hello.md").read_text() == "earlier message\n"
    assert "hello" in (d / "2026-09-23_101500_hello-2.md").read_text()


def test_file_backend_attachment_copied_and_linked(tmp_path):
    att = tmp_path / "fig.png"
    att.write_bytes(b"\x89PNG fake")
    N.FileBackend({}, tmp_path, tmp_path).send("see plot", att, now=dt.datetime(2026, 9, 23, 9, 0, 1))
    doc = tmp_path / "messages" / "2026-09-23_090001_see-plot.md"
    copy = tmp_path / "messages" / "2026-09-23_090001_see-plot_fig.png"
    assert copy.read_bytes() == att.read_bytes()
    assert f"]({copy.name})" in doc.read_text()


def test_missing_attachment_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proj = make_project(tmp_path)
    r = run_notify(["x", tmp_path / "nope.png"], proj, base_env(home))
    assert r.returncode == N.EXIT_CONFIG and "attachment not found" in r.stderr
    assert messages(proj) == []


def test_worktree_messages_go_to_main_checkout(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proj = make_project(tmp_path)
    g = ["git", "-C", str(proj), "-c", "user.name=t", "-c", "user.email=t@example.org"]
    (proj / "README.md").write_text("x\n")
    subprocess.run(g + ["add", "README.md"], check=True)
    subprocess.run(g + ["commit", "-qm", "init"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(g + ["worktree", "add", "-q", str(wt)], check=True, capture_output=True)
    r = run_notify(["from a worktree"], wt, base_env(home))
    assert r.returncode == 0, r.stderr
    assert len(messages(proj)) == 1 and messages(wt) == []


def test_unknown_backend_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proj = make_project(tmp_path)
    r = run_notify(["--backend", "pigeon", "x"], proj, base_env(home))
    assert r.returncode == N.EXIT_CONFIG and "unknown back end 'pigeon'" in r.stderr
    assert run_notify(["--backend", "file", "x"], proj, base_env(home)).returncode == 0


def test_backend_registry_accepts_new_backends(tmp_path, monkeypatch):
    sent = []

    class Echo(N.Backend):
        name = "echo"

        def send(self, text, attachment):
            sent.append((text, self.section.get("prefix")))
            return "echoed"

    monkeypatch.setitem(N.BACKENDS, "echo", Echo)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "site.local.yaml").write_text("notify:\n  backend: echo\n  echo:\n    prefix: P\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPSCI_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert N.notify("hi", project_root=str(tmp_path)) == 0
    assert sent == [("hi", "P")]


def test_user_level_config_and_project_override(tmp_path, mock):
    home = tmp_path / "home"
    home.mkdir()
    write_creds(home)
    (home / ".config" / "opsci" / "config.yaml").write_text("notify:\n  backend: slack\n")
    proj = make_project(tmp_path)
    srv = mock()
    r = run_notify(["x"], proj, base_env(home) | {N.TEST_API_ENV: srv.api})
    assert r.returncode == 0 and len(srv.requests) == 1  # user-level config selected slack
    (proj / "config" / "site.local.yaml").write_text("notify:\n  backend: file\n")
    r = run_notify(["x"], proj, base_env(home) | {N.TEST_API_ENV: srv.api})
    assert r.returncode == 0 and len(srv.requests) == 1 and len(messages(proj)) == 1


def test_internal_error_prints_no_traceback(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError(f"secret {TOKEN}")
    monkeypatch.setattr(N, "notify", boom)

    class A:
        text, file, backend, project_root = "x", None, None, None
    assert N.cmd_notify(A) == 1
    err = capsys.readouterr().err
    assert "internal error (RuntimeError)" in err and TOKEN not in err


# ---------------------------------------------------------------- template

def test_template_messages_dir_is_ignored_except_readme(tmp_path):
    gi = (REPO / "template" / ".gitignore").read_text()
    d = tmp_path / "g"
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / ".gitignore").write_text(gi)

    def ignored(p):
        return subprocess.run(["git", "-C", str(d), "check-ignore", "-q", "--no-index", p]).returncode == 0
    assert ignored("messages/2026-09-23_101500_hello.md")
    assert ignored("messages/2026-09-23_101500_hello_fig.png")
    assert not ignored("messages/README.md")
    assert (REPO / "template" / "messages" / "README.md").exists()


# ---------------------------------------------------------------- manual: real Slack

@pytest.mark.manual
def test_real_slack_send(tmp_path):
    """Sends one real text message and one real file to the user's own channel.

    Uses the user's own config (~/.config/opsci/config.yaml or $OPSCI_CONFIG, and the
    credentials file it names). Run: tests/run_all --run-manual -k real_slack
    """
    att = tmp_path / "opsci-notify-test.txt"
    att.write_text("opsci notify manual test attachment\n")
    env = {k: v for k, v in os.environ.items() if k != N.TEST_API_ENV}
    r = run_notify(["--backend", "slack", "--project-root", tmp_path,
                    "opsci notify manual test: text and file", att], tmp_path, env)
    assert r.returncode == 0, r.stderr
    assert "sent to Slack" in r.stdout
    assert messages(tmp_path) == []
