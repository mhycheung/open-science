"""A local stand-in for the Zenodo deposit API, for tests.

Implements the endpoints `opsci zenodo` uses, with the behaviour described at
developers.zenodo.org for the legacy deposit API:
  POST   deposit/depositions                          create a draft
  GET    deposit/depositions/<id>                     get a deposition
  PUT    deposit/depositions/<id>                     update metadata (drafts only)
  POST   deposit/depositions/<id>/actions/newversion  new draft of a published record; returns
                                                      the old record with links.latest_draft
  POST   deposit/depositions/<id>/actions/publish     publish; assigns doi and conceptdoi
  GET    deposit/depositions/<id>/files               list files
  DELETE deposit/depositions/<id>/files/<file id>     delete a file (drafts only)
  PUT    files/<bucket>/<filename>                    upload into a draft's bucket
A new version starts with copies of the previous version's files. Only one unpublished draft
per concept may exist. Every request is recorded in `requests`.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse


class MockZenodo:
    def __init__(self, token="test-token-123"):
        self.token = token
        self.deps = {}
        self.buckets = {}
        self.requests = []  # (method, path)
        self.uploads = []  # (deposition id, filename)
        self.corrupt_uploads = False
        self._ids = itertools.count(1001)
        self._fids = itertools.count(1)
        self._lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                body = b"" if obj is None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                path = urlparse(self.path).path
                with mock._lock:
                    mock.requests.append((method, path))
                    if self.headers.get("Authorization") != f"Bearer {mock.token}":
                        return self._send(401, {"message": "unauthorized"})
                    code, obj = mock.route(method, path, body)
                self._send(code, obj)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

            def do_DELETE(self):
                self._handle("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/api"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    # ---------------------------------------------------------------- state
    def _view(self, d):
        out = {k: v for k, v in d.items() if k not in ("files",)}
        out["files"] = [self._fview(f) for f in d["files"]]
        out["links"] = {"self": f"{self.base}/deposit/depositions/{d['id']}",
                        "bucket": f"{self.base}/files/{d['bucket']}"}
        return out

    @staticmethod
    def _fview(f):
        return {"id": f["id"], "filename": f["filename"], "filesize": f["filesize"],
                "checksum": f["checksum"]}

    def _new_dep(self, concept):
        dep_id = next(self._ids)
        bucket = uuid.uuid4().hex
        d = {"id": dep_id, "conceptrecid": concept or str(next(self._ids)), "submitted": False,
             "state": "unsubmitted", "metadata": {}, "files": [], "bucket": bucket}
        self.deps[dep_id] = d
        self.buckets[bucket] = dep_id
        return d

    def route(self, method, path, body):
        parts = [unquote(p) for p in path.strip("/").split("/")]
        if parts[:1] != ["api"]:
            return 404, {"message": "not found"}
        parts = parts[1:]
        if parts[:1] == ["files"] and len(parts) == 3 and method == "PUT":
            dep_id = self.buckets.get(parts[1])
            if dep_id is None:
                return 404, {"message": "no bucket"}
            d = self.deps[dep_id]
            if d["submitted"]:
                return 403, {"message": "record is published"}
            name = parts[2]
            d["files"] = [f for f in d["files"] if f["filename"] != name]
            md5 = hashlib.md5(body + (b"x" if self.corrupt_uploads else b"")).hexdigest()
            d["files"].append({"id": uuid.uuid4().hex, "filename": name, "filesize": len(body),
                               "checksum": md5, "data": body})
            self.uploads.append((dep_id, name))
            return 201, {"key": name, "size": len(body), "checksum": f"md5:{md5}"}
        if parts[:2] != ["deposit", "depositions"]:
            return 404, {"message": "not found"}
        rest = parts[2:]
        if not rest:
            if method == "POST":
                return 201, self._view(self._new_dep(None))
            if method == "GET":  # list; used by `opsci zenodo check-token`
                return 200, [self._view(d) for d in list(self.deps.values())[:1]]
            return 405, {"message": "method"}
        try:
            d = self.deps[int(rest[0])]
        except (ValueError, KeyError):
            return 404, {"message": "no such deposition"}
        rest = rest[1:]
        if not rest:
            if method == "GET":
                return 200, self._view(d)
            if method == "PUT":
                if d["submitted"]:
                    return 400, {"message": "published; edit first"}
                d["metadata"] = json.loads(body)["metadata"]
                return 200, self._view(d)
            return 405, {"message": "method"}
        if rest == ["files"] and method == "GET":
            return 200, [self._fview(f) for f in d["files"]]
        if len(rest) == 2 and rest[0] == "files" and method == "DELETE":
            if d["submitted"]:
                return 403, {"message": "record is published"}
            before = len(d["files"])
            d["files"] = [f for f in d["files"] if f["id"] != rest[1]]
            return (204, None) if len(d["files"]) < before else (404, {"message": "no file"})
        if rest == ["actions", "newversion"] and method == "POST":
            if not d["submitted"]:
                return 400, {"message": "not published"}
            if any(x["conceptrecid"] == d["conceptrecid"] and not x["submitted"]
                   for x in self.deps.values()):
                return 400, {"message": "an unpublished draft already exists"}
            new = self._new_dep(d["conceptrecid"])
            new["metadata"] = dict(d["metadata"])
            new["files"] = [dict(f, id=uuid.uuid4().hex) for f in d["files"]]
            out = self._view(d)
            out["links"]["latest_draft"] = f"{self.base}/deposit/depositions/{new['id']}"
            return 201, out
        if rest == ["actions", "publish"] and method == "POST":
            md = d["metadata"]
            missing = [k for k in ("title", "creators", "upload_type", "description") if not md.get(k)]
            if missing or not d["files"]:
                return 400, {"message": f"missing {missing or 'files'}"}
            d["submitted"], d["state"] = True, "done"
            d["doi"] = f"10.5072/zenodo.{d['id']}"
            d["conceptdoi"] = f"10.5072/zenodo.{d['conceptrecid']}"
            return 202, self._view(d)
        return 404, {"message": "not found"}

    def published(self):
        return [d for d in self.deps.values() if d["submitted"]]
