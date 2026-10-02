"""A small client for the Notion REST API, as the user's own Notion integration (a bot).

The token is read from a private credentials file (default ~/.config/opsci/notion.env, a
line `NOTION_TOKEN=ntn_...`, or a file holding only the token), checked like the Slack one: a regular file, owned by you, mode
600 or tighter, and not inside a project. The token is sent only in the HTTPS Authorization
header. It is never put on a command line or in the environment, and never printed; error
messages replace it with `<token>`.

Uses only the standard library (urllib), like the rest of opsci.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from ..notify import NotifyError, _loopback_http, check_private_file, user_config_dir

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
# Tests only: point the client at a local mock server. Honoured only for a loopback http URL,
# so it cannot be used to send the token anywhere else.
TEST_API_ENV = "OPSCI_NOTION_TEST_API_BASE"
TOKEN_RE = re.compile(r"\b(?:ntn|secret)_[A-Za-z0-9]{20,}")
MAX_UPLOAD = 5 * 1024 * 1024    # upload limit of a free Notion workspace (paid: 5 GiB, multipart);
                                # a larger plot is listed on its page instead of uploaded
MAX_BODY = 350_000              # bytes per append request; Notion rejects bodies over ~500 KB (413)


class NotionError(Exception):
    """A failed request or bad setup. The message is safe to print."""


def credentials_path(section: dict | None = None) -> Path:
    raw = (section or {}).get("credentials_file")
    p = Path(os.path.expanduser(str(raw))) if raw else user_config_dir() / "notion.env"
    if not p.is_absolute():
        raise NotionError("notify.notion.credentials_file must be an absolute or ~/ path")
    return p


def read_token(path: Path, project_roots=()) -> str:
    for r in project_roots:
        if path.resolve().is_relative_to(Path(r).resolve()):
            raise NotionError(f"refusing credentials file inside the project ({path}); keep it under ~/.config")
    try:
        check_private_file(path)
    except NotifyError as exc:
        raise NotionError(str(exc).replace("docs/notify.md", "docs/notion.md"))
    token = None
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    for line in lines:
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line.startswith("NOTION_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
    if token is None and len(lines) == 1 and "=" not in lines[0]:
        token = lines[0]          # a file holding only the token
    if not token:
        raise NotionError(f"NOTION_TOKEN missing in credentials file {path} (docs/notion.md)")
    return token


class Client:
    def __init__(self, token: str):
        self._token = token
        override = os.environ.get(TEST_API_ENV)
        if override:
            if not _loopback_http(override):
                raise NotionError(f"{TEST_API_ENV} is for tests and must be a loopback http URL")
            self.base = override.rstrip("/")
        else:
            self.base = API

    @classmethod
    def from_config(cls, section: dict | None = None, project_roots=()) -> "Client":
        return cls(read_token(credentials_path(section), project_roots))

    def _scrub(self, s: str) -> str:
        return TOKEN_RE.sub("<token>", s.replace(self._token, "<token>") if self._token else s)

    def _request(self, method: str, url: str, body: bytes | None, headers: dict):
        headers = {"Authorization": f"Bearer {self._token}", "Notion-Version": VERSION, **headers}
        for attempt in range(6):
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                if exc.code == 429 or exc.code >= 500:
                    time.sleep(float(exc.headers.get("Retry-After") or 2 ** attempt))
                    continue
                try:
                    detail = json.loads(exc.read())
                    msg = f"{detail.get('code')}: {detail.get('message')}"
                except Exception:  # noqa: BLE001
                    msg = f"HTTP {exc.code}"
                raise NotionError(self._scrub(f"Notion {method} {urllib.parse.urlsplit(url).path}: {msg}")) from None
            except (urllib.error.URLError, OSError) as exc:
                if attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                raise NotionError(self._scrub(f"cannot reach Notion: {getattr(exc, 'reason', exc)}")) from None
        raise NotionError(f"Notion {method} {urllib.parse.urlsplit(url).path}: gave up after retries")

    def call(self, method: str, path: str, data: dict | None = None, ok404: bool = False):
        """One API request; `path` is relative to the API root, with or without a leading /."""
        body = json.dumps(data).encode() if data is not None else None
        try:
            return self._request(method, f"{self.base}/{path.lstrip('/')}", body,
                                 {"Content-Type": "application/json"} if body else {})
        except NotionError as exc:
            if ok404 and "object_not_found" in str(exc):
                return None
            raise

    # ------------------------------------------------------------ helpers

    def children(self, block_id: str) -> list:
        out, cursor = [], None
        while True:
            q = f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            r = self.call("GET", q)
            out += r["results"]
            if not r.get("has_more"):
                return out
            cursor = r["next_cursor"]

    def delete(self, block_id: str) -> None:
        """Delete (archive) a block; a block that is already gone or archived is fine."""
        try:
            self.call("DELETE", f"/blocks/{block_id}", ok404=True)
        except NotionError as exc:
            if "archived" not in str(exc):
                raise

    def append(self, parent_id: str, blocks: list, after: str | None = None) -> list[str]:
        """Append blocks of any nesting depth; return the ids of the top-level ones.

        One request takes at most 100 blocks and two levels of nesting, so children are
        appended in follow-up requests (tables keep their rows: Notion needs them at once).
        """
        ids = []
        for batch in _batches(blocks):
            flat, kids = [], []
            for b in batch:
                b = json.loads(json.dumps(b))
                for k in [k for k in b if k.startswith("_")]:
                    b.pop(k)                         # internal markers (_plot, _cap)
                body = b[b["type"]]
                kids.append([] if b["type"] == "table" else body.pop("children", []))
                flat.append(b)
            payload = {"children": flat}
            if after:
                payload["after"] = after
            res = self.call("PATCH", f"/blocks/{parent_id}/children", payload)["results"]
            new = res[:len(flat)]
            for b, k in zip(new, kids):
                ids.append(b["id"])
                if k:
                    self.append(b["id"], k)
            if after:
                after = new[-1]["id"]
        return ids

    def upload(self, path: Path) -> str:
        """Upload one file (single part, at most MAX_UPLOAD); return the file upload id."""
        if path.stat().st_size > MAX_UPLOAD:
            raise NotionError(f"{path.name} is over {MAX_UPLOAD // 2**20} MiB, the upload limit")
        return self.upload_bytes(path.name, path.read_bytes())

    def upload_bytes(self, name: str, data: bytes) -> str:
        """Upload ``data`` as a file called ``name``; return the file upload id."""
        if len(data) > MAX_UPLOAD:
            raise NotionError(f"{name} is over {MAX_UPLOAD // 2**20} MiB, the upload limit")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        fu = self.call("POST", "/file_uploads", {"filename": name, "content_type": ctype})
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        r = self._request("POST", f"{self.base}/file_uploads/{fu['id']}/send", body,
                          {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        if r.get("status") != "uploaded":
            raise NotionError(f"upload of {name} did not complete (status {r.get('status')})")
        return fu["id"]



def _batches(blocks: list, most: int = 50) -> list[list]:
    """Consecutive groups of at most `most` blocks whose request body (children excluded, table
    rows included: they go with their table) stays under MAX_BODY bytes."""
    out, cur, size = [], [], 0
    for b in blocks:
        body = {k: v for k, v in b[b["type"]].items() if k != "children" or b["type"] == "table"}
        n = len(json.dumps({**b, b["type"]: body}))
        if cur and (len(cur) >= most or size + n > MAX_BODY):
            out.append(cur)
            cur, size = [], 0
        cur.append(b)
        size += n
    return out + [cur] if cur else out


def page_id_from(url_or_id: str) -> str:
    """The page id in a Notion link (the last 32 hex digits), or the id itself."""
    m = re.findall(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", url_or_id)
    if not m:
        raise NotionError(f"no Notion page id in '{url_or_id}'")
    h = m[-1].replace("-", "").lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
