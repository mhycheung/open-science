---
name: zenodo-release
description: Release a project's data to Zenodo as a new versioned record - dry run, sandbox release, then a production release only after the owner explicitly confirms, with the DOI written back to data/MANIFEST.yaml and CITATION.cff. Use when the user asks to release, archive or publish data on Zenodo or to get a DOI for a dataset.
---

# Zenodo release

A production release is **public and permanent**: Zenodo never deletes a published version.
Release at publication points, not on every data change. The tool is `opsci zenodo`; its
full reference is `docs/zenodo.md` in the framework repo.

## Procedure

1. **Check what would be released.** Every dataset in `data/MANIFEST.yaml` that is meant to
   be public, and nothing else: no data from a node marked `privacy: soft-private` or
   `hard-private`, no third-party data the project may not redistribute. Tar groups are set under
   `zenodo.groups` in the manifest (default: one group per `data/<task-id>/`).

2. **Dry run** (no network, nothing written):

   ```bash
   opsci zenodo release --dry-run
   ```

   It prints the groups, their checksums, which groups are reused unchanged, and the
   100-file and 50 GB checks. Fix any refusal before going on.

3. **Sandbox release** (the default server; needs `~/.config/opsci/zenodo-sandbox.token`,
   mode 600):

   ```bash
   opsci zenodo release --version <label>
   ```

   Show the owner the sandbox record link and the file list it printed.

4. **Production: only after the owner confirms, in this conversation, this version label
   and this file list.** A general "go ahead" given earlier does not cover it. Then:

   ```bash
   opsci zenodo release --version <label> --production
   ```

   Needs `~/.config/opsci/zenodo.token` (mode 600). The tool writes the version DOI and the
   concept DOI to `data/MANIFEST.yaml` and `CITATION.cff`.

5. **Record it:** commit the manifest and `CITATION.cff`, add one line to the log, update
   the dataset node's header if it has one, and report the DOIs to the owner.

## Rules

- Never pass a token on the command line or print one; the tool reads it from the file.
- If a run stops before publishing, run the same command again: the tool resumes the draft
  recorded in the manifest.
- If nothing changed since the last version, the tool refuses a new one. That is correct.
