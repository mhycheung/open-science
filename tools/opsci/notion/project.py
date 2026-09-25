"""A project's Notion setup: config, state file, and the Notion client for it.

- User config (not secret): the `notify.notion` section of ~/.config/opsci/config.yaml or the
  project's config/site.local.yaml: `parent_page` (link or id of the page under which each
  project gets its own page), `owner` (the owner's Notion user id, @mentioned in messages that
  need them), optionally `credentials_file`.
- Project state (not secret, git-ignored): config/notion.local.yaml in the main checkout, with
  the ids of the project's Notion pages, the hashes of what was last written, and the upload
  cache.
- Feed ledger: messages/notion-feed.jsonl (git-ignored with the rest of messages/).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ..notify import find_roots, load_config
from .client import Client, NotionError

STATE_FILE = "config/notion.local.yaml"
LEDGER_FILE = "messages/notion-feed.jsonl"


class Project:
    def __init__(self, project_root: str | None = None):
        self.root, self.main = find_roots(project_root)
        self.cfg = load_config(self.root, self.main)
        section = self.cfg.get("notion") or {}
        if not isinstance(section, dict):
            raise NotionError("config: notify.notion must be a mapping")
        self.section = section
        self._client = None

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = Client.from_config(self.section, (self.root, self.main))
        return self._client

    # ------------------------------------------------------------ state

    @property
    def state_path(self) -> Path:
        return self.main / STATE_FILE

    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        return yaml.safe_load(self.state_path.read_text(encoding="utf-8")) or {}

    def save_state(self, st: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text("# Notion page ids and sync state for this checkout (opsci notion). Not secret;\n"
                       "# git-ignored. Delete it only together with the project's Notion pages.\n"
                       + yaml.safe_dump(st, sort_keys=True, allow_unicode=True), encoding="utf-8")
        tmp.replace(self.state_path)

    def require_state(self) -> dict:
        st = self.load_state()
        if not st.get("root_page"):
            raise NotionError(f"this project has no Notion pages yet ({STATE_FILE} missing); run: opsci notion init")
        return st

    # ------------------------------------------------------------ uploads

    def upload(self, st: dict, path: Path, digest: str) -> str:
        """Upload once per (path, content); the id can be attached again later."""
        cache = st.setdefault("uploads", {})
        try:
            rel = str(path.resolve().relative_to(self.root))
        except ValueError:
            rel = str(path.resolve())
        key = f"{rel}@{digest}"
        if key not in cache:
            cache[key] = self.client.upload(path)
        return cache[key]

    # ------------------------------------------------------------ ledger

    @property
    def ledger_path(self) -> Path:
        return self.main / LEDGER_FILE

    def ledger(self) -> list[dict]:
        p = self.ledger_path
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []

    def save_ledger(self, msgs: list[dict]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs), encoding="utf-8")
