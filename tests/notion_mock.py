"""A local stand-in for the Notion REST API (version 2022-06-28), for tests.

Implements what `opsci notion` uses, with the behaviour of the real API as far as that code
depends on it:
  GET    users/me, users, users/<id>  (paths relative to /v1)
  POST   /pages                       parent page_id (adds a child_page block) or database_id
  GET    /pages/<id>, PATCH /pages/<id> (properties, icon)
  POST   /databases                   adds a child_database block to the parent page
  GET    /blocks/<id>                 archived blocks are still returned, with archived: true
  PATCH  /blocks/<id>                 update; an image/pdf object holding "type" is rejected
  DELETE /blocks/<id>                 archive; children lists then omit it; deleting or
                                      updating an archived block is a validation_error
  GET    /blocks/<id>/children        paginated (page_size <= 100, start_cursor)
  PATCH  /blocks/<id>/children        {"children": [...], "after"?}: at most 100 blocks and
                                      two levels of nesting; returns the new top-level blocks
  POST   /file_uploads, POST /file_uploads/<id>/send (multipart field "file")
A block may reference {"file_upload": {"id": X}} only when X is uploaded; the stored block
then shows the file as {"type": "file", "file": {"url": ...}}, and `file_of(block_id)` gives
the test the file's (name, bytes). Rich text is stored as the API returns it (plain_text
filled in); arrays over 100 items and text over 2000 characters are rejected, and so is a
block object with a key other than object, type and the body (such as an internal "_plot").

Every request is recorded in `requests` as (method, path). Test hooks: `fail_once` (a set of
(method, path); the first such request gets 429 with Retry-After 0) and `echo_token` (a set
of (method, path) answered with a 400 whose message contains the token).
"""

from __future__ import annotations

import copy
import itertools
import json
import threading
import uuid
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MEDIA = ("image", "pdf", "file", "video", "audio")
RICH_FIELDS = ("rich_text", "caption")
# Block types that may have children (headings only when toggleable).
PARENT_TYPES = {"paragraph", "bulleted_list_item", "numbered_list_item", "to_do", "toggle", "quote",
                "callout", "table", "synced_block", "column_list", "column", "template",
                "child_page"}


def _norm(i: str) -> str:
    return i.replace("-", "").lower()


def _dashed(h: str) -> str:
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def bad(msg):
    return ApiError(400, "validation_error", msg)


class MockNotion:
    def __init__(self, token: str):
        self.token = token
        self.requests: list[tuple[str, str]] = []
        self.fail_once: set[tuple[str, str]] = set()
        self.echo_token: set[tuple[str, str]] = set()
        self.max_page_size = 100
        self.bot = {"object": "user", "id": str(uuid.uuid4()), "type": "bot", "name": "opsci test bot",
                    "bot": {}}
        self.person = {"object": "user", "id": str(uuid.uuid4()), "type": "person", "name": "Owner Person",
                       "person": {"email": "owner@example.org"}}
        self.blocks: dict[str, dict] = {}      # norm id -> node
        self.pages: dict[str, dict] = {}       # norm id -> page object (without children)
        self.databases: dict[str, dict] = {}
        self.uploads: dict[str, dict] = {}     # norm id -> {status, filename, data}
        self._lock = threading.Lock()
        self._seq = itertools.count()
        self.parent_id = self._new_page(None, "Research", None)
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj, headers=()):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                u = urlparse(self.path)
                path = u.path[len("/v1"):] if u.path.startswith("/v1/") else u.path
                with mock._lock:
                    mock.requests.append((method, path))
                    if self.headers.get("Authorization") != f"Bearer {mock.token}":
                        return self._send(401, {"object": "error", "status": 401, "code": "unauthorized",
                                                "message": "API token is invalid."})
                    if (method, path) in mock.fail_once:
                        mock.fail_once.discard((method, path))
                        return self._send(429, {"object": "error", "status": 429, "code": "rate_limited",
                                                "message": "rate limited"}, [("Retry-After", "0")])
                    if (method, path) in mock.echo_token:
                        return self._send(400, {"object": "error", "status": 400, "code": "validation_error",
                                                "message": f"bad request with token {mock.token}"})
                    try:
                        code, obj = mock.route(method, path, parse_qs(u.query), body,
                                               self.headers.get("Content-Type", ""))
                    except ApiError as e:
                        code, obj = e.status, {"object": "error", "status": e.status, "code": e.code,
                                               "message": e.message}
                self._send(code, obj)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PATCH(self):
                self._handle("PATCH")

            def do_DELETE(self):
                self._handle("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    # ---------------------------------------------------------------- helpers for tests

    def node(self, bid: str) -> dict:
        return self.blocks[_norm(bid)]

    def kids(self, bid: str) -> list[dict]:
        """The live (not archived) children of a block or page, as stored nodes."""
        return [self.blocks[c] for c in self.node(bid)["children"] if not self.blocks[c]["archived"]]

    def file_of(self, bid: str):
        """(filename, bytes) of the uploaded file an image/pdf/file block shows, or None."""
        up = self.node(bid).get("upload")
        return (self.uploads[up]["filename"], self.uploads[up]["data"]) if up else None

    @staticmethod
    def plain(rt: list) -> str:
        return "".join(x["plain_text"] for x in rt)

    def title_of(self, page_id: str) -> str:
        props = self.pages[_norm(page_id)]["properties"]
        return "".join(self.plain(v["title"]) for v in props.values() if v.get("type") == "title")

    def child_pages(self, page_id: str) -> dict[str, str]:
        """{title: id} of the live child pages and databases of a page."""
        out = {}
        for n in self.kids(page_id):
            if n["type"] == "child_page":
                out[n["body"]["title"]] = n["id"]
            elif n["type"] == "child_database":
                out[n["body"]["title"]] = n["id"]
        return out

    # ---------------------------------------------------------------- internals

    def _id(self) -> str:
        return uuid.uuid4().hex

    def _new_node(self, typ, body, parent):
        h = self._id()
        self.blocks[h] = {"id": _dashed(h), "type": typ, "body": body, "children": [], "archived": False,
                          "parent": parent, "upload": None, "seq": next(self._seq)}
        return h

    def _new_page(self, parent, title_rt_or_str, parent_kind):
        title = [{"type": "text", "text": {"content": title_rt_or_str}}] \
            if isinstance(title_rt_or_str, str) else title_rt_or_str
        props = {"title": {"id": "title", "type": "title", "title": self._rich(title)}}
        return self._make_page(parent, parent_kind, props, None)

    def _make_page(self, parent, parent_kind, props, icon):
        h = self._id()
        title = "".join(x["plain_text"] for v in props.values() if v.get("type") == "title" for x in v["title"])
        self.blocks[h] = {"id": _dashed(h), "type": "child_page", "body": {"title": title}, "children": [],
                          "archived": False, "parent": parent, "upload": None, "seq": next(self._seq)}
        self.pages[h] = {"object": "page", "id": _dashed(h), "url": f"https://www.notion.so/{h}",
                         "archived": False, "icon": icon, "properties": props,
                         "parent": {"type": parent_kind or "workspace", parent_kind or "workspace":
                                    _dashed(parent) if parent else True}}
        if parent and parent_kind == "page_id":
            self.blocks[parent]["children"].append(h)
        return h

    def _rich(self, rt) -> list:
        if not isinstance(rt, list):
            raise bad("rich text should be an array")
        if len(rt) > 100:
            raise bad(f"rich text array length should be <= 100, instead was {len(rt)}")
        out = []
        for x in rt:
            x = copy.deepcopy(x)
            t = x.get("type") or ("text" if "text" in x else None)
            x["type"] = t
            if t == "text":
                c = x["text"].get("content", "")
                if len(c) > 2000:
                    raise bad(f"text content length should be <= 2000, instead was {len(c)}")
                link = x["text"].get("link")
                x["plain_text"], x["href"] = c, link["url"] if link else None
            elif t == "equation":
                x["plain_text"], x["href"] = x["equation"]["expression"], None
            elif t == "mention":
                uid = x["mention"]["user"]["id"]
                who = next((u for u in (self.bot, self.person) if _norm(u["id"]) == _norm(uid)), None)
                if who is None:
                    raise bad(f"mentioned user {uid} not found")
                x["plain_text"], x["href"] = "@" + who["name"], None
            else:
                raise bad(f"unknown rich text type {t}")
            x["annotations"] = {"bold": False, "italic": False, "strikethrough": False, "underline": False,
                                "code": False, "color": "default", **x.get("annotations", {})}
            out.append(x)
        return out

    def _media_in(self, body: dict, updating: bool) -> str | None:
        """Validate a media object; return the norm file upload id it references, if any."""
        if updating and "type" in body:
            raise bad("body failed validation: body.image.type should be not present")
        kind = body.get("type") or next((k for k in ("file_upload", "external", "file") if k in body), None)
        if kind == "file_upload":
            fid = _norm(body["file_upload"]["id"])
            up = self.uploads.get(fid)
            if not up or up["status"] != "uploaded":
                raise bad(f"file upload {body['file_upload']['id']} is not uploaded")
            return fid
        if kind == "external":
            return None
        if updating and kind is None:     # e.g. only the caption changes
            return None
        raise bad("media block needs file_upload or external")

    def _store_body(self, typ, body):
        body = copy.deepcopy(body)
        for f in RICH_FIELDS:
            if f in body:
                body[f] = self._rich(body[f])
        if typ == "table_row":
            body["cells"] = [self._rich(c) for c in body["cells"]]
        return body

    def _depth(self, blocks) -> int:
        d = 0
        for b in blocks:
            kids = (b.get(b.get("type"), {}) or {}).get("children") or []
            d = max(d, 1 + self._depth(kids))
        return d

    def _create(self, parent: str, b: dict) -> str:
        typ = b.get("type")
        if not typ or typ not in b:
            raise bad("block needs type and a body under that type")
        extra = set(b) - {"object", "type", typ}
        if extra:
            raise bad(f"body.children[].{sorted(extra)[0]} should be not present")
        body = copy.deepcopy(b[typ])
        kids = body.pop("children", []) or []
        upload = None
        if typ in MEDIA:
            upload = self._media_in(body, updating=False)
            if upload:
                name = self.uploads[upload]["filename"]
                body = {"type": "file", "file": {"url": f"https://files.example/{upload}/{name}",
                                                 "expiry_time": "2099-01-01T00:00:00.000Z"},
                        "caption": body.get("caption", [])}
        if typ == "table":
            if not kids:
                raise bad("table needs at least one table_row child")
            for r in kids:
                if r.get("type") != "table_row" or len(r["table_row"]["cells"]) != body["table_width"]:
                    raise bad("table_row cells must match table_width")
        body = self._store_body(typ, body)
        h = self._new_node(typ, body, parent)
        self.blocks[h]["upload"] = upload
        for k in kids:
            self._check_can_parent(h)
            self.blocks[h]["children"].append(self._create(h, k))
        return h

    def _check_can_parent(self, h):
        n = self.blocks[h]
        ok = n["type"] in PARENT_TYPES or (n["type"].startswith("heading_") and n["body"].get("is_toggleable"))
        if not ok:
            raise bad(f"block type {n['type']} does not support children")
        if n["archived"]:
            raise bad("Can't edit block that is archived. You must unarchive the block before editing.")

    def _view(self, h) -> dict:
        n = self.blocks[h]
        live = [c for c in n["children"] if not self.blocks[c]["archived"]]
        return {"object": "block", "id": n["id"], "type": n["type"], n["type"]: copy.deepcopy(n["body"]),
                "has_children": bool(live), "archived": n["archived"], "in_trash": n["archived"],
                "created_by": {"object": "user", "id": self.bot["id"]},
                "parent": {"type": "block_id", "block_id": _dashed(n["parent"])} if n["parent"] else
                {"type": "workspace", "workspace": True}}

    def _get(self, table, i):
        h = _norm(i)
        if h not in table:
            raise ApiError(404, "object_not_found", f"Could not find object with ID: {i}.")
        return h

    # ---------------------------------------------------------------- routes

    def route(self, method, path, query, body, ctype):
        parts = path.strip("/").split("/")
        data = json.loads(body) if body and ctype.startswith("application/json") else {}
        if parts[0] == "users":
            if len(parts) == 1 and method == "GET":
                return 200, {"object": "list", "results": [self.bot, self.person], "has_more": False,
                             "next_cursor": None}
            if len(parts) == 2 and method == "GET":
                if parts[1] == "me":
                    return 200, self.bot
                for u in (self.bot, self.person):
                    if _norm(u["id"]) == _norm(parts[1]):
                        return 200, u
                raise ApiError(404, "object_not_found", f"Could not find user with ID: {parts[1]}.")
        if parts[0] == "pages":
            if len(parts) == 1 and method == "POST":
                par = data.get("parent", {})
                if "page_id" in par:
                    ph = self._get(self.pages, par["page_id"])
                    props = {k: {"id": k, "type": "title", "title": self._rich(v["title"])}
                             for k, v in data["properties"].items()}
                    h = self._make_page(ph, "page_id", props, data.get("icon"))
                elif "database_id" in par:
                    dh = self._get(self.databases, par["database_id"])
                    h = self._make_page(dh, "database_id", self._props(data["properties"]), data.get("icon"))
                else:
                    raise bad("parent must hold page_id or database_id")
                for b in data.get("children", []):
                    self.blocks[h]["children"].append(self._create(h, b))
                return 200, self.pages[h]
            if len(parts) == 2:
                h = self._get(self.pages, parts[1])
                if method == "GET":
                    return 200, self.pages[h]
                if method == "PATCH":
                    pg = self.pages[h]
                    if "properties" in data:
                        pg["properties"].update(self._props(data["properties"]))
                    if "icon" in data:
                        pg["icon"] = data["icon"]
                    return 200, pg
        if parts[0] == "databases" and len(parts) == 1 and method == "POST":
            ph = self._get(self.pages, data["parent"]["page_id"])
            h = self._id()
            title = "".join(x["plain_text"] for x in self._rich(data.get("title", [])))
            self.databases[h] = {"object": "database", "id": _dashed(h), "properties": data["properties"],
                                 "url": f"https://www.notion.so/{h}"}
            self.blocks[h] = {"id": _dashed(h), "type": "child_database", "body": {"title": title},
                              "children": [], "archived": False, "parent": ph, "upload": None,
                              "seq": next(self._seq)}
            self.blocks[ph]["children"].append(h)
            return 200, self.databases[h]
        if parts[0] == "blocks" and len(parts) >= 2:
            h = self._get(self.blocks, parts[1])
            n = self.blocks[h]
            if len(parts) == 2:
                if method == "GET":
                    return 200, self._view(h)
                if method == "DELETE":
                    if n["archived"]:
                        raise bad("Can't edit block that is archived. You must unarchive the block before editing.")
                    n["archived"] = True
                    if h in self.pages:
                        self.pages[h]["archived"] = True
                    return 200, self._view(h)
                if method == "PATCH":
                    if n["archived"]:
                        raise bad("Can't edit block that is archived. You must unarchive the block before editing.")
                    typ = n["type"]
                    keys = [k for k in data if k not in ("archived", "in_trash")]
                    if keys and keys != [typ]:
                        raise bad(f"block type {typ} cannot be updated with {keys}")
                    if typ in data:
                        upd = copy.deepcopy(data[typ])
                        if typ in MEDIA:
                            up = self._media_in(upd, updating=True)
                            if up:
                                n["upload"] = up
                                name = self.uploads[up]["filename"]
                                n["body"]["file"] = {"url": f"https://files.example/{up}/{name}",
                                                     "expiry_time": "2099-01-01T00:00:00.000Z"}
                            upd.pop("file_upload", None)
                            upd.pop("external", None)
                        n["body"].update(self._store_body(typ, upd))
                    return 200, self._view(h)
            if len(parts) == 3 and parts[2] == "children":
                if method == "GET":
                    live = [c for c in n["children"] if not self.blocks[c]["archived"]]
                    size = min(int(query.get("page_size", ["100"])[0]), self.max_page_size)
                    start = 0
                    if "start_cursor" in query:
                        cur = _norm(query["start_cursor"][0])
                        if cur not in live:
                            raise bad("start_cursor is not valid")
                        start = live.index(cur)
                    chunk = live[start:start + size]
                    more = start + size < len(live)
                    return 200, {"object": "list", "results": [self._view(c) for c in chunk],
                                 "has_more": more, "next_cursor": self.blocks[live[start + size]]["id"]
                                 if more else None}
                if method == "PATCH":
                    kids = data.get("children")
                    if not isinstance(kids, list) or not kids:
                        raise bad("body.children should be a non-empty array")
                    if len(kids) > 100:
                        raise bad(f"body.children.length should be <= 100, instead was {len(kids)}")
                    if self._depth(kids) > 2:
                        raise bad("body.children: at most two levels of nesting in one request")
                    self._check_can_parent(h) if h not in self.pages else None
                    pos = len(n["children"])
                    if data.get("after"):
                        a = _norm(data["after"])
                        if a not in n["children"] or self.blocks[a]["archived"]:
                            raise bad(f"after block {data['after']} is not a child of {parts[1]}")
                        pos = n["children"].index(a) + 1
                    new = [self._create(h, b) for b in kids]
                    n["children"][pos:pos] = new
                    return 200, {"object": "list", "results": [self._view(c) for c in new],
                                 "has_more": False, "next_cursor": None}
        if parts[0] == "file_uploads":
            if len(parts) == 1 and method == "POST":
                h = self._id()
                self.uploads[h] = {"status": "pending", "filename": data.get("filename", "file"), "data": None,
                                   "content_type": data.get("content_type")}
                return 200, {"object": "file_upload", "id": _dashed(h), "status": "pending",
                             "filename": data.get("filename")}
            if len(parts) == 3 and parts[2] == "send" and method == "POST":
                h = self._get(self.uploads, parts[1])
                if not ctype.startswith("multipart/form-data"):
                    raise bad("send needs multipart/form-data")
                msg = BytesParser(policy=email_policy).parsebytes(
                    f"Content-Type: {ctype}\r\n\r\n".encode() + body)
                part = next((p for p in msg.iter_parts()
                             if p.get_param("name", header="content-disposition") == "file"), None)
                if part is None:
                    raise bad("multipart form needs a field named file")
                up = self.uploads[h]
                up.update(status="uploaded", data=part.get_payload(decode=True),
                          filename=part.get_filename() or up["filename"])
                return 200, {"object": "file_upload", "id": _dashed(h), "status": "uploaded",
                             "filename": up["filename"]}
        raise ApiError(404, "invalid_request_url", f"Invalid request URL: {method} {path}")

    def _props(self, props: dict) -> dict:
        out = {}
        for k, v in props.items():
            t = next(iter(v))
            val = self._rich(v[t]) if t in ("title", "rich_text") else v[t]
            out[k] = {"id": k, "type": t, t: val}
        return out
