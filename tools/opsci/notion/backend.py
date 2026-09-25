"""The `notion` back end of `opsci notify`: post the message to the project's Notion Feed.

Config: `notify: {backend: notion, notion: {parent_page, user, kind, mention, author}}`.
`kind` (default note) and `mention` (default true: the user is @mentioned, so Notion notifies
them) apply to every message sent through `opsci notify`; `opsci notion post` sets them per
message. The author is $OPSCI_AUTHOR, else notify.notion.author, else "agent".
"""

from __future__ import annotations

import os
from pathlib import Path

from ..notify import Backend, DeliveryError, NotifyError, register


@register
class NotionBackend(Backend):
    name = "notion"

    def cfg_file(self) -> dict:
        from ..notify import load_config
        section = load_config(self.root, self.main).get("file")
        return section if isinstance(section, dict) else {}

    def send(self, text: str, attachment: Path | None) -> str:
        from . import feed
        from .client import NotionError
        from .project import Project
        from ..notify import FileBackend
        try:
            proj = Project(str(self.root))
        except NotionError as exc:
            raise NotifyError(str(exc))
        from .client import credentials_path
        hint = None
        if not credentials_path(proj.section).exists():
            hint = "Notion is not set up yet (no token file); run the open-science:onboard skill"
        elif not proj.load_state().get("root_page"):
            hint = "this project has no Notion pages; run `opsci notion init` to mirror it"
        if hint:
            # Chosen but not set up yet (onboarding allows deferring it), or a project that is
            # not mirrored: keep the message locally, and say what to do.
            where = FileBackend(self.cfg_file(), self.root, self.main).send(text, attachment)
            return f"{where} ({hint})"
        try:
            proj.client          # reads and checks the token file
        except NotionError as exc:
            raise NotifyError(str(exc))
        mention = self.section.get("mention", True)
        try:
            feed.post(proj, text, author=os.environ.get("OPSCI_AUTHOR") or self.section.get("author") or "agent",
                      kind=self.section.get("kind", "note"), mention=bool(mention) and bool(proj.section.get("user")),
                      files=[attachment] if attachment else ())
        except NotionError as exc:
            raise DeliveryError(str(exc))
        return "posted to the project's Notion Feed" + (" with file" if attachment else "")
