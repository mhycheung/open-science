---
name: zenodo-release
description: Release a project's data to Zenodo as a new versioned record - dry run, sandbox release, then a production release only after the user explicitly confirms, with the DOI written back to data/MANIFEST.yaml and CITATION.cff. Use when the user asks to release, archive or publish data on Zenodo or to get a DOI for a dataset.
---

# Zenodo release

A production release is **public and permanent**: Zenodo never deletes a published version.
Release at publication points, not on every data change. The tool is `opsci zenodo`; its
full reference is `docs/zenodo.md` in the framework repo.

Start a release only when the user asks for one or says yes when you ask, never because data
changed or a task ended. The sandbox release in step 3 is not public, but it too runs only
within a release the user asked for.

## Procedure

1. **Check what would be released.** Every dataset in `data/MANIFEST.yaml` that is meant to
   be public, and nothing else: no third-party data the project may not redistribute. Tar
   groups are set under `zenodo.groups` in the manifest (default: one group per
   `data/<task-id>/`). The tool leaves out the data of `soft-private` and `hard-private` tasks
   by default and refuses an explicit group that holds it.

2. **Dry run** (no network, nothing written):

   ```bash
   opsci zenodo release --dry-run
   ```

   It prints the groups, their checksums, which groups are reused unchanged, the private data
   left out, and the 100-file and 50 GB checks. It also refuses, and names:

   - a `data/` symlink whose target is not under the project, the site's `scratch` or a
     `data_roots` entry of `config/site.local.yaml`, or lies in a hidden directory of the home
     directory or the config directory;
   - a symlink inside the data that is absolute or leaves its group;
   - a secret or a leak (absolute path, email, IP address, SLURM job number, user, host or
     site name, private-policy pattern) in a file, a file name or `FILES.tsv`.

   Fix any refusal before going on: change or remove the file, replace the link, or leave the
   path out of the tar groups. Ask the user before you do any of the following; never do them on your
   own judgement:

   - add a directory to `data_roots`;
   - release a private task's data (`zenodo.include_private: [<task-id>]` in
     `data/MANIFEST.yaml`);
   - accept a SLURM job number finding (`zenodo.overrides`, the same entry as in the publish
     skill's `reference/check-overrides.md`, which says what to ask).

   A secret and every other leak kind cannot be accepted. The full rules are in
   `docs/zenodo.md` under "Symlinks", "Private data" and "Scans".

3. **Sandbox release** (the default server; needs `~/.config/opsci/zenodo-sandbox.token`,
   mode 600):

   ```bash
   opsci zenodo release --version <label>
   ```

   Show the user the sandbox record link and the file list it printed.

4. **Production: only after the user confirms, in this conversation, this version label
   and this file list.** Never pass `--production` before that confirmation: the tool asks
   nothing and publishes permanently. A general "go ahead" given earlier does not cover it.
   Then:

   ```bash
   opsci zenodo release --version <label> --production
   ```

   Needs `~/.config/opsci/zenodo.token` (mode 600). The tool writes the version DOI and the
   concept DOI to `data/MANIFEST.yaml` and `CITATION.cff`.

5. **Record it:** after a production release, run `opsci map build`: the results pages and
   `map/claims.md` then show the DOI and tar beside every result artifact under `data/` that
   the release holds. Commit the manifest, `CITATION.cff` and the rebuilt pages, run
   `opsci notion sync` if the project is mirrored to Notion, add one line to the log, update
   the dataset node's header if it has one, and report the DOIs to the user.

## Rules

- Never pass a token on the command line or print one; the tool reads it from the file.
  The tool sends the token only over https (plain http only to a loopback test server) and
  only to the API host; do not work around a refusal of `--api-url`.
- If a run stops before publishing, run the same command again: the tool resumes the draft
  recorded in the manifest.
- If nothing changed since the last version, the tool refuses a new one. That is correct.
