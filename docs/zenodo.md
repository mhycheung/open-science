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
  `--api-url` that points at zenodo.org is refused. With it the tool publishes a permanent,
  public record with no confirmation prompt. The `zenodo-release` skill's instructions are what
  require the user's explicit confirmation first: an agent must not pass `--production` until
  the user has confirmed the version and file list in the conversation.
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
header, and only to the host of the API URL: a link in a server response (the new-version
draft, the upload bucket) that points to another host, port or scheme is refused. An
`--api-url` must use https; plain http is accepted only for a loopback test server
(`127.0.0.1`, `localhost`, `::1`).

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
   Data of private tasks is left out; see [Private data](#private-data). Before any tar is
   built, every file to be released is scanned for secrets and leaks; see [Scans](#scans).
2. **File list.** `FILES.tsv` is uploaded next to the tars. It lists every file, one per line,
   in six tab-separated columns: `path`, `size`, `sha256`, `tar` (the tar that holds it),
   `task` (the task id when the file lies under `data/<id>/` and `<id>` is a task of the
   project, in `tasks/`, `brainstorm/tasks/` or a `verifications/` directory; else empty) and
   `results` (the ids of the result nodes whose `artifacts` name the file or a directory above
   it, comma-separated). The tasks and results come from the node headers (`opsci map build`
   reads the same ones), so `FILES.tsv` also changes when a header changes. A change to
   `FILES.tsv` alone does not make a new version: with no tar changed, the release is
   still refused as "nothing changed".
3. **Limits.** A Zenodo record holds at most 100 files and 50 GB. The tool counts the groups
   plus `FILES.tsv` before it builds anything, and adds up the sizes after the tars are built.
   If either limit is exceeded it stops before any network call.
4. **Reproducible tars.** Members are sorted by name, with mtime 2000-01-01, owner and group 0,
   no user names, and modes normalised to 644/755 (777 for symlinks). The gzip header has no
   file name and no timestamp. The same input gives a byte-identical tar and the same checksum,
   wherever and whenever it is built. Symlinks are handled as described under
   [Symlinks](#symlinks). The tars are built with Python's `tarfile`, so the result does not
   depend on the installed `tar`.
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
   version), the checksum of every uploaded file and the paths each tar holds are written to
   `data/MANIFEST.yaml`, under `zenodo.sandbox` or `zenodo.production`:

   ```yaml
   zenodo:
     production:
       releases:
         - version: '1.0'
           date: '2026-09-28'
           record_id: 1234567
           doi: 10.5281/zenodo.1234567
           files: {t04.tar.gz: {sha256: ..., md5: ..., size: ...}, FILES.tsv: {...}}
           groups: {t04.tar.gz: [data/t04]}
       concept_doi: 10.5281/zenodo.1234566
   ```

   For production (or with `--write-citation`), each dataset inside a released group gets
   `zenodo: <version DOI>`, and `CITATION.cff` gets two `identifiers` entries: the concept DOI
   and the version DOI.

   The tool rewrites only the `zenodo:` section and the `zenodo:` field of the datasets, in
   place, so comments elsewhere in the manifest (a note after a `sha256:`, a comment line
   between datasets) are kept. Comments inside the `zenodo:` section are not kept: the tool
   writes that section. If a manifest cannot be edited in place (the rewrite would not read
   back as the intended data), it is written whole and only its leading comment block is kept.

## Symlinks

A group root (an entry `data/<name>`, or a path in `zenodo.groups`) may be a symlink, for
example `data/<task-id>` pointing to scratch. The tool follows it and archives what it points
to, if the target is allowed:

- The target must lie inside the project, inside the site's `scratch` directory, or inside a
  directory listed under `data_roots` in `config/site.local.yaml`:

  ```yaml
  scratch: <your scratch directory>
  data_roots: [<a directory>]   # more directories that data/ symlinks may point into
  ```

- Whatever those settings say, the tool refuses a target that is or holds the home directory
  or the project, one inside a hidden directory of the home directory (`~/.ssh`, `~/.config`,
  `~/.aws`, `~/.gnupg`, ...), one that is, holds or lies inside the config directory
  (`$XDG_CONFIG_HOME` or `~/.config`, which holds the token files in `opsci/`), and one inside
  the project's `.git`.

A symlink below a group root is stored as a symlink, not followed. It must be relative and
point to a place inside the same group root (`sub/link -> ../a.txt` is stored; `link ->
~/file` and `link -> ../../other-task/file` are refused). Replace a refused link with a
relative one or with the file itself.

`opsci zenodo checksum` applies the same rules.

## Private data

Data of a task whose privacy is `soft-private` or `hard-private` is not released. The task's
tier is its `privacy` field, else `policy.default_privacy` of `publish/manifest.yaml`; a
verification task inside a private task is at least as private; a task directory whose
`context.md` has no valid node header counts as hard-private, as in `opsci publish`. A path
under `data/` that the `hard_private` list of `publish/manifest.yaml` names is hard-private
too.

- With the default groups (one per entry of `data/`), the entry of a private task is left out
  and the plan says so.
- A group in `zenodo.groups` that holds a private path is refused.

`FILES.tsv` and the record description therefore never name a private task's files. To
release a private task's data, the user decides it and names the task in the data manifest:

```yaml
zenodo:
  include_private: [t05-sweep]   # private tasks whose data the user has decided to release
```

## Scans

Before any tar is built, every file to be released, every name and every symlink target is
scanned with the secret patterns of `opsci publish` (`tools/opsci/secretscan.py`) and its leak
patterns (`tools/opsci/leakscan.py`: absolute paths, emails, IP addresses, SLURM job numbers,
the user, host and `config/site.local.yaml` values, and the project's
`publish/PRIVATE_POLICY.md` patterns). The generated `FILES.tsv` is scanned too. Any finding
refuses the release, in the dry run as well, before any network call. Secrets are shown
redacted.

- Text files are scanned with every pattern. PNG images and PDFs are scanned as `opsci
  publish` scans them (image text chunks, PDF strings).
- Binary data is scanned with every pattern except `absolute-path`: a slash followed by a
  letter, another slash and more letters
  occurs by chance about once per megabyte of compressed data. A path that names this site
  still matches the user, host and scratch patterns.
- Big files are read in chunks, so a release of many gigabytes takes a while to scan; the
  scan reads every byte once.
- `gitleaks` is not run on data (it is run by `opsci publish`).

The kinds that `opsci publish` lets the user accept (SLURM job numbers: `slurm-job-id`,
`slurm-array-id`, `slurm-out-file`) may be accepted here too, in the data manifest, with the
same fields as in `publish/manifest.yaml` (see the publish skill's `check-overrides.md`):

```yaml
zenodo:
  overrides:
    - check: leak
      kind: slurm-job-id
      paths: [data/t05-sweep/logs]   # optional
      reason: Job numbers in run logs name no person or machine.
      date: 2026-10-05
```

The plan lists every accepted finding. A secret, and every other leak kind, is never
overridden: change or remove the file, or leave it out of the tar groups.

## Results pages

After a production release, run `opsci map build`. Every result `artifacts` path under
`data/` that a production release holds is then shown with that release, in the result's
"stored in" row on the results pages and in the "stored in" column of `map/claims.md`:

```markdown
`data/t04/x.npz` (Zenodo [10.5281/zenodo.1234567](https://doi.org/10.5281/zenodo.1234567), `t04.tar.gz`)
```

The release shown is the newest production release with a tar whose group path is the
artifact path, lies above it, or lies under it (an artifact that is a directory holding a
group). Sandbox releases are never shown: their DOIs (10.5072/...) do not resolve. Release
entries written before the tool recorded `groups` are matched with the current
`zenodo.groups`, or with the default one-group-per-`data/` entry rule. The public pages that
`opsci publish` exports show the same release, in place of "(not published)".

## Metadata

The title and creators come from `CITATION.cff`. Defaults: `upload_type: dataset`,
`access_right: open`, `license: cc-by-4.0`. The description names the project's public repo
and site when `publish/manifest.yaml` gives them (`public_repo`; `site_url`, else the GitHub
Pages URL of a github.com repo, as `opsci publish` derives it), and lists each tar with the
paths it holds and the task(s) they come from. With a public repo, the record also gets
`related_identifiers: [{identifier: <repo URL>, relation: isSupplementTo, resource_type:
software}]`, which links the record back to the project. Override any field under `zenodo.metadata` in
`data/MANIFEST.yaml`. The project ships no `.zenodo.json`: Zenodo ignores `CITATION.cff` when
one exists, so metadata is sent through the API instead.

## Tests

`tests/test_zenodo.py` runs against a local mock of the deposit API (`tests/zenodo_mock.py`).
The real sandbox test is marked `manual`:

```bash
tests/run_all --run-manual -k real_sandbox
```

It needs `~/.config/opsci/zenodo-sandbox.token`.
