# Releasing data to Zenodo

`opsci zenodo` publishes a project's `data/` to Zenodo as a versioned record. The
`open-science-publish:zenodo-release` skill calls it after a person confirms the release. Every
published version is permanent and public, so release at publication points, not on every
data change.

## Commands

```bash
opsci zenodo release [ROOT] --dry-run                 # print the plan; no network, nothing written
opsci zenodo release [ROOT] --version 1.0             # release to the sandbox (the default)
opsci zenodo release [ROOT] --version 1.0 --production   # release to zenodo.org
opsci zenodo checksum data/<task-id> [--root ROOT]    # sha256 of the reproducible tar of a path
opsci zenodo check-token [--production]              # check the token file and that Zenodo accepts it
```

`check-token` refuses a missing token file, a token file that gives any permission to group
or others (use mode 600), and a file that holds anything but one token. Then it makes one
read-only request (a list of at most one deposition) to see whether Zenodo accepts the token.
It creates nothing. It takes `--api-url` and `--token-file` as `release` does.

Options of `release`:

- `--dry-run`: builds each tar in memory to get its checksum and size, then prints the groups,
  checksums, reuse decisions and limit checks. It makes no network call and writes no file.
- `--production`: required for zenodo.org. Without it the tool uses sandbox.zenodo.org, and an
  `--api-url` that points at zenodo.org is refused.
- `--write-citation`: write the DOIs to `CITATION.cff` and to the datasets in
  `data/MANIFEST.yaml` also for a sandbox release. Production releases always do this.
  Sandbox DOIs do not resolve, so by default they are recorded only in the manifest's
  `zenodo.sandbox` section.
- `--token-file`, `--api-url`, `--build-dir`: override the defaults below.

## Token

Create a personal access token with the scopes `deposit:write` and `deposit:actions`:
on sandbox.zenodo.org for testing, on zenodo.org for real releases. These are separate
accounts with separate tokens. Store each in its own file, readable only by you:

```bash
mkdir -p ~/.config/opsci
( umask 077; cat > ~/.config/opsci/zenodo-sandbox.token )   # paste, then Ctrl-D
( umask 077; cat > ~/.config/opsci/zenodo.token )           # production
```

(`$XDG_CONFIG_HOME` replaces `~/.config` if it is set.) The tool refuses a token file that
group or others can read. It never takes the token from the command line or from an
environment variable, and never prints it. The token is sent only in the `Authorization`
header.

## What a release does

1. **Tar groups.** Data is packed into tar groups, one `<group>.tar.gz` each. By default each
   entry of `data/` (normally one directory per task) is one group. To group differently, for
   example to put finished data apart from data that still changes, list groups in
   `data/MANIFEST.yaml`:

   ```yaml
   zenodo:
     groups:
       - name: finished
         paths: [data/t01-setup, data/t02-first-fit]
       - name: t05-sweep
         paths: [data/t05-sweep]
   ```

   Groups may not overlap. Entries of `data/` that no group covers are reported and not
   released.
2. **File list.** `FILES.tsv` is uploaded next to the tars. It lists every file: path, size,
   sha256 and the tar that holds it.
3. **Limits.** A Zenodo record holds at most 100 files and 50 GB. The tool counts the groups
   plus `FILES.tsv` before it builds anything, and adds up the sizes after the tars are built.
   If either limit is exceeded it stops before any network call.
4. **Reproducible tars.** Members are sorted by name, with mtime 2000-01-01, owner and group 0,
   no user names, and modes normalised to 644/755 (777 for symlinks). The gzip header has no
   file name and no timestamp. The same input gives a byte-identical tar and the same checksum,
   wherever and whenever it is built. A group root that is a symlink (for example
   `data/<task-id>` pointing to scratch) is followed; symlinks inside it are stored as links.
   The tars are built with Python's `tarfile`, so the result does not depend on the installed
   `tar`.
5. **Versions.** The first release creates a deposition. Each later release asks Zenodo for a
   new version of the previous record. Zenodo copies the previous version's files into the new
   draft. The tool keeps every file whose checksum is unchanged, deletes groups that no longer
   exist, and uploads only groups whose checksum changed, plus `FILES.tsv`. If nothing changed,
   it refuses to make a new version.
6. **Checks and publish.** Each upload's md5 and size must match the local file, and the draft
   must hold exactly the planned files. Then the metadata is set and the draft is published.
   If a run stops before publishing, the draft id is kept in the manifest and the next run
   continues with that draft (Zenodo allows only one unpublished draft per record).
7. **Write-back.** The version DOI, the concept DOI (which always resolves to the newest
   version) and the checksum of every uploaded file are written to `data/MANIFEST.yaml`, under
   `zenodo.sandbox` or `zenodo.production`. For production (or with `--write-citation`), each
   dataset inside a released group gets `zenodo: <version DOI>`, and `CITATION.cff` gets two
   `identifiers` entries: the concept DOI and the version DOI.

## Metadata

The title and creators come from `CITATION.cff`. Defaults: `upload_type: dataset`,
`access_right: open`, `license: cc-by-4.0`. Override any field under `zenodo.metadata` in
`data/MANIFEST.yaml`. The project ships no `.zenodo.json`: Zenodo ignores `CITATION.cff` when
one exists, so metadata is sent through the API instead.

## Tests

`tests/test_zenodo.py` runs against a local mock of the deposit API (`tests/zenodo_mock.py`).
The real sandbox test is marked `manual`:

```bash
tests/run_all --run-manual -k real_sandbox
```

It needs `~/.config/opsci/zenodo-sandbox.token`.
