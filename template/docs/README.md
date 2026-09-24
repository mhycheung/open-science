# Documentation

Documentation of the project for its readers and users: how to run the code, the
conventions and methods the tasks share, derivations too long for a task directory, and
notes for collaborators.

This directory is published: `publish/manifest.yaml` lists it under `include`. Write every
file here for the public (`AGENTS.md` §0). Give each document a `status:` line in its front
matter; the publish check requires one on every Markdown file that `status_exempt` in the
manifest does not exempt. For example:

```yaml
---
status: active
---
```

Private notes do not go here; they go in `private-docs/`.
