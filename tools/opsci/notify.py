"""`opsci notify`: send a message (and optionally a file) to the user.

Back ends are classes registered in BACKENDS; config selects one. Two ship here:

- `file` (the default): each message becomes its own document in the project's
  `messages/` directory. Needs no configuration.
- `slack`: posts to a Slack channel with a bot token read from a private credentials file.
  The token is sent only in the HTTPS Authorization header. It is never put on a command
  line, never put in any environment, and never printed.

Config (see docs/notify.md): the `notify:` section of the project's
`config/site.local.yaml`, over the same section of the user-level file
`~/.config/opsci/config.yaml` (or $OPSCI_CONFIG). The project file wins key by key.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

EXIT_CONFIG = 2    # misconfiguration: nothing was sent by the selected back end
EXIT_DELIVERY = 3  # the back end was configured but the send failed

SLACK_API = "https://slack.com/api"
# Tests only: point the Slack back end at a local mock server. Honoured only for a loopback
# http URL, so it cannot be used to send a token anywhere else.
TEST_API_ENV = "OPSCI_NOTIFY_TEST_API_BASE"
TOKEN_RE = re.compile(r"xox[a-z]-[A-Za-z0-9-]+")
COPY_LIMIT = 50 * 1024 * 1024  # attachments up to this size are copied into messages/


class NotifyError(Exception):
    """Misconfiguration or bad input. The message is safe to print."""


class DeliveryError(Exception):
    """The send itself failed. The message is safe to print."""


# ---------------------------------------------------------------- configuration

def user_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "opsci"


def _git(cwd: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def find_roots(project_root: str | None) -> tuple[Path, Path]:
    """(project root, main checkout root).

    The project root is --project-root, else the git top level of the current directory,
    else the current directory. In a linked git worktree the main checkout is where the
    gitignored files live (config/site.local.yaml, messages/), so both are returned.
    """
    root = Path(project_root) if project_root else None
    if root is None:
        top = _git(Path.cwd(), "rev-parse", "--show-toplevel")
        root = Path(top) if top else Path.cwd()
    root = root.resolve()
    main = root
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        c = Path(common)
        if c.name == ".git" and c.parent.is_dir():
            main = c.parent.resolve()
    return root, main


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise NotifyError(f"cannot read config {path}: {type(exc).__name__}")
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark else ""
        raise NotifyError(f"config {path} is not valid YAML{where}")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise NotifyError(f"config {path}: top level must be a mapping")
    notify = data.get("notify") or {}
    if not isinstance(notify, dict):
        raise NotifyError(f"config {path}: 'notify' must be a mapping")
    _refuse_secrets(notify, path)
    return notify


def _refuse_secrets(node, path: Path, trail: str = "notify") -> None:
    """Config files are not private: a token found in one is an error."""
    if isinstance(node, dict):
        for k, v in node.items():
            if "token" in str(k).lower():
                raise NotifyError(f"config {path}: key '{trail}.{k}' is not allowed; the token "
                                  f"goes in the private credentials file (docs/notify.md)")
            _refuse_secrets(v, path, f"{trail}.{k}")
    elif isinstance(node, list):
        for v in node:
            _refuse_secrets(v, path, trail)
    elif isinstance(node, str) and TOKEN_RE.search(node):
        raise NotifyError(f"config {path}: '{trail}' holds what looks like a Slack token; "
                          f"the token goes in the private credentials file (docs/notify.md)")


def load_config(root: Path, main: Path) -> dict:
    """The merged `notify` config: user-level file, then the project file over it."""
    cfg: dict = {}
    user = Path(os.environ["OPSCI_CONFIG"]) if os.environ.get("OPSCI_CONFIG") \
        else user_config_dir() / "config.yaml"
    project = next((p for p in (root / "config" / "site.local.yaml",
                                main / "config" / "site.local.yaml") if p.is_file()), None)
    for path in (user, project):
        if path is None or not path.is_file():
            continue
        for k, v in _read_yaml(path).items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    return cfg


# ---------------------------------------------------------------- back ends

BACKENDS: dict[str, type] = {}


def register(cls):
    """Class decorator: make a back end selectable by its `name`."""
    BACKENDS[cls.name] = cls
    return cls


class Backend:
    """Interface for a notify back end.

    A back end is built from the merged config (`section` is config['notify'][name], {} if
    absent) and the project roots. `send` delivers one message and returns a one-line
    description of where it went. It raises NotifyError for misconfiguration and
    DeliveryError when the send fails; the message of either must never contain a secret.
    """

    name = ""

    def __init__(self, section: dict, root: Path, main: Path):
        self.section = section or {}
        self.root = root
        self.main = main

    def send(self, text: str, attachment: Path | None) -> str:
        raise NotImplementedError


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = ""
    for w in words:
        cand = f"{slug}-{w}" if slug else w
        if len(cand) > 40:
            slug = slug or w[:40]
            break
        slug = cand
    return slug or "message"


@register
class FileBackend(Backend):
    """One markdown document per message in <main checkout>/messages/ (or notify.file.dir)."""

    name = "file"

    def directory(self) -> Path:
        d = self.section.get("dir")
        if not d:
            return self.main / "messages"
        d = Path(os.path.expanduser(str(d)))
        return d if d.is_absolute() else self.main / d

    def send(self, text: str, attachment: Path | None, now: dt.datetime | None = None) -> str:
        now = now or dt.datetime.now().astimezone()
        d = self.directory()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise NotifyError(f"cannot create messages directory {d}: {exc.strerror}")
        base = f"{now:%Y-%m-%d_%H%M%S}_{_slug(text)}"
        n = 1
        while True:
            stem = base if n == 1 else f"{base}-{n}"
            path = d / f"{stem}.md"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                break
            except FileExistsError:
                n += 1
        lines = [f"# Message, {now:%Y-%m-%d %H:%M:%S %Z}".rstrip(), "", text.rstrip(), ""]
        if attachment is not None:
            size = attachment.stat().st_size
            if size <= COPY_LIMIT:
                copy = d / f"{stem}_{attachment.name}"
                shutil.copyfile(attachment, copy)
                lines.append(f"Attached file (copy): [{attachment.name}]({copy.name})")
            else:
                rel = os.path.relpath(attachment.resolve(), d.resolve())
                lines.append(f"Attached file ({size} bytes, not copied): [{attachment.name}]({rel})")
            lines.append("")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        try:
            shown = path.relative_to(self.main)
        except ValueError:
            shown = path
        return f"wrote {shown}"


def check_private_file(path: Path) -> None:
    """Refuse a credentials file that is not a regular file owned by us with mode 600 or tighter."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise NotifyError(f"credentials file not found: {path} (see docs/notify.md)")
    except OSError as exc:
        raise NotifyError(f"cannot stat credentials file {path}: {exc.strerror}")
    if not stat.S_ISREG(st.st_mode):
        raise NotifyError(f"credentials file {path} is not a regular file")
    if st.st_uid != os.getuid():
        raise NotifyError(f"refusing credentials file {path}: it is not owned by you")
    if st.st_mode & 0o077:
        raise NotifyError(f"refusing credentials file {path}: group or others have access "
                          f"(mode {stat.S_IMODE(st.st_mode):o}); run: chmod 600 {path}")


def read_credentials(path: Path) -> dict:
    """KEY=VALUE lines (the format of a shell env file). Same format as the older slack_send."""
    check_private_file(path)
    creds = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NotifyError(f"cannot read credentials file {path}: {type(exc).__name__}")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        creds[k] = v.strip().strip('"').strip("'")
    if not creds.get("SLACK_TOKEN"):
        raise NotifyError(f"SLACK_TOKEN missing in credentials file {path}")
    return creds


def _loopback_http(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")


@register
class SlackBackend(Backend):
    """Slack bot: chat.postMessage for text, the external-upload flow for a file."""

    name = "slack"

    def __init__(self, section, root, main):
        super().__init__(section, root, main)
        self._token = None

    def credentials_path(self) -> Path:
        raw = self.section.get("credentials_file")
        p = Path(os.path.expanduser(str(raw))) if raw else user_config_dir() / "slack.env"
        if not p.is_absolute():
            raise NotifyError("notify.slack.credentials_file must be an absolute or ~/ path "
                              "outside the project")
        for r in {self.root, self.main}:
            if p.resolve().is_relative_to(r):
                raise NotifyError(f"refusing credentials file inside the project ({p}); "
                                  f"keep it under ~/.config (docs/notify.md)")
        return p

    def api_base(self) -> str:
        override = os.environ.get(TEST_API_ENV)
        if override:
            if not _loopback_http(override):
                raise NotifyError(f"{TEST_API_ENV} is for tests and must be a loopback http URL")
            return override.rstrip("/")
        return SLACK_API

    def _ssl(self):
        ca = self.section.get("ca_file")
        try:
            return ssl.create_default_context(cafile=os.path.expanduser(str(ca)) if ca else None)
        except (OSError, ssl.SSLError):
            raise NotifyError(f"cannot load notify.slack.ca_file {ca}")

    def _scrub(self, s: str) -> str:
        if self._token:
            s = s.replace(self._token, "<token>")
        return TOKEN_RE.sub("<token>", s)

    def _open(self, req):
        ctx = None if req.full_url.startswith("http:") else self._ctx
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            raise DeliveryError(f"Slack HTTP error {exc.code} on {urllib.parse.urlsplit(req.full_url).path}")
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise DeliveryError(self._scrub(f"cannot reach Slack: {reason}"))

    def _call(self, method: str, data: dict | None = None, query: dict | None = None) -> dict:
        url = f"{self.base}/{method}"
        headers = {"Authorization": f"Bearer {self._token}"}
        body = None
        if query is not None:
            url += "?" + urllib.parse.urlencode(query)
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json; charset=utf-8"
        raw = self._open(urllib.request.Request(url, data=body, headers=headers))
        try:
            resp = json.loads(raw)
        except ValueError:
            raise DeliveryError(f"Slack {method}: reply is not JSON")
        if not isinstance(resp, dict) or not resp.get("ok"):
            err = self._scrub(str(resp.get("error") if isinstance(resp, dict) else resp))
            hint = {
                "not_in_channel": "; invite the app to the channel (/invite @<app name>)",
                "channel_not_found": "; check the channel ID, and invite the app to it",
                "missing_scope": "; the app needs the chat:write and files:write bot scopes",
                "invalid_auth": "; the token is wrong or revoked (docs/notify.md)",
                "not_authed": "; the token is wrong or revoked (docs/notify.md)",
            }.get(err, "")
            raise DeliveryError(f"Slack {method} failed: {err}{hint}")
        return resp

    def send(self, text: str, attachment: Path | None) -> str:
        creds = read_credentials(self.credentials_path())
        channel = self.section.get("channel") or creds.get("SLACK_CHANNEL")
        if not channel:
            raise NotifyError("no Slack channel: set notify.slack.channel or SLACK_CHANNEL "
                              "in the credentials file")
        self.base = self.api_base()
        self._ctx = self._ssl()
        self._token = creds["SLACK_TOKEN"]
        try:
            if text:
                self._call("chat.postMessage", {"channel": channel, "text": text})
            if attachment is not None:
                self._upload(channel, attachment)
        finally:
            self._token = None
        return f"sent to Slack channel {channel}" + (" with file" if attachment else "")

    def _upload(self, channel: str, attachment: Path) -> None:
        resp = self._call("files.getUploadURLExternal",
                          query={"filename": attachment.name, "length": attachment.stat().st_size})
        upload_url, file_id = resp.get("upload_url"), resp.get("file_id")
        if not upload_url or not file_id:
            raise DeliveryError("Slack files.getUploadURLExternal: reply lacks upload_url/file_id")
        test_mode = bool(os.environ.get(TEST_API_ENV))
        if not (upload_url.startswith("https://") or (test_mode and _loopback_http(upload_url))):
            raise DeliveryError("Slack returned an upload URL that is not https; not uploading")
        # The upload URL is pre-signed: it gets the bytes only, not the token.
        req = urllib.request.Request(upload_url, data=attachment.read_bytes(), method="POST",
                                     headers={"Content-Type": "application/octet-stream"})
        self._open(req)
        self._call("files.completeUploadExternal",
                   {"files": [{"id": file_id, "title": attachment.name}], "channel_id": channel})


# ---------------------------------------------------------------- command line

def notify(text: str, attachment: str | None = None, backend: str | None = None,
           project_root: str | None = None, out=sys.stdout, err=sys.stderr) -> int:
    """Send one message. Returns the exit status. Never raises for expected failures."""
    root, main = find_roots(project_root)
    att = None
    if attachment:
        att = Path(attachment)
        if not att.is_file():
            print(f"opsci notify: attachment not found or not a file: {attachment}", file=err)
            return EXIT_CONFIG
    cfg: dict = {}
    name = None
    try:
        cfg = load_config(root, main)
        name = backend or cfg.get("backend") or "file"
        if name not in BACKENDS:
            raise NotifyError(f"unknown back end '{name}'; known: {', '.join(sorted(BACKENDS))}")
        section = cfg.get(name) or {}
        if not isinstance(section, dict):
            raise NotifyError(f"config: notify.{name} must be a mapping")
        where = BACKENDS[name](section, root, main).send(text, att)
        print(f"opsci notify: {where}", file=out)
        return 0
    except (NotifyError, DeliveryError) as exc:
        code = EXIT_CONFIG if isinstance(exc, NotifyError) else EXIT_DELIVERY
        print(f"opsci notify: error: {exc}", file=err)
    # The chosen back end failed: keep the message in messages/ so it is not lost.
    if name != "file":
        section = cfg.get("file")
        try:
            where = FileBackend(section if isinstance(section, dict) else {},
                                root, main).send(text, att)
            print(f"opsci notify: kept the message locally instead: {where}", file=err)
        except Exception:  # noqa: BLE001 - the primary error is already reported
            pass
    return code


def cmd_notify(args) -> int:
    try:
        return notify(args.text, args.file, backend=args.backend, project_root=args.project_root)
    except Exception as exc:  # noqa: BLE001 - never print a traceback (it could hold a secret)
        print(f"opsci notify: internal error ({type(exc).__name__}); nothing printed from it "
              f"in case it holds a secret", file=sys.stderr)
        return 1


def add_parser(sub) -> None:
    """Register `opsci notify` on the top-level subparsers."""
    p = sub.add_parser("notify", help="send a message (and optionally a file) to the user")
    p.add_argument("--backend", help=f"back end to use ({', '.join(sorted(BACKENDS))}); "
                                     f"default: config notify.backend, else 'file'")
    p.add_argument("--project-root", help="project root (default: git top level of the current directory)")
    p.add_argument("text", help="message text")
    p.add_argument("file", nargs="?", help="file to attach")
    p.set_defaults(func=cmd_notify)


# The notion back end lives in its own package; importing it registers it.
from .notion import backend as _notion_backend  # noqa: E402,F401
