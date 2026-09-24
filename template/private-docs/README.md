# Private notes

Notes that are never shared with the public: meeting notes, correspondence, drafts of
things not yet agreed with collaborators, remarks about people, reviews, anything under a
confidentiality agreement.

These files are committed to the private repo, so they are versioned and backed up with the
project, but they are never exported: `publish/manifest.yaml` lists `private-docs` under
`never`, so the export leaves them out even if an `include` entry matches.

This directory is soft-private (`AGENTS.md` §6). So that the public project has no broken
links, **nothing outside `private-docs/` links to a file here or quotes from here.** A
mention of a file in passing, in backticks, is allowed. If a published document needs
something from these notes, restate it there in public form.

A note that must not appear in the release even by name (proprietary data, collaborators'
unpublished work, private information about people) is hard-private: list its path under
`hard_private:` in `publish/manifest.yaml`, so that the publish check refuses its path and
copies of its text.

Secrets (tokens, passwords, keys) never go here either: they never go in the repo at all
(`AGENTS.md` §0).
