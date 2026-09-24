"""`opsci zenodo` tests (report, Tests table, row "zenodo-release").

Run against a local mock of the Zenodo deposit API (tests/zenodo_mock.py). The real sandbox
run is marked manual. Production is refused throughout: OPSCI_ZENODO_FORBID_PRODUCTION is set.
Each check has a case it must refuse next to one it must pass.
"""
import hashlib
import os
import shutil
import urllib.request
from pathlib import Path

import pytest
import yaml

from conftest import TEMPLATE, run_opsci
from opsci import zenodo as Z
from zenodo_mock import MockZenodo

CFF = """# How to cite this project.
cff-version: 1.2.0
message: "If you use this work, please cite it as below."
title: "Demo project"
authors:
  - name: "A. Person"
date-released: "2026-09-23"
license: MIT
type: software
"""


@pytest.fixture(autouse=True)
def no_production(monkeypatch):
    monkeypatch.setenv(Z.FORBID_PRODUCTION_ENV, "1")


@pytest.fixture
def mock():
    with MockZenodo() as m:
        yield m


@pytest.fixture
def token(tmp_path, mock):
    p = tmp_path / "tok"
    p.write_text(mock.token + "\n")
    p.chmod(0o600)
    return p


def make_project(root: Path, n_groups=2):
    (root / "data").mkdir(parents=True)
    shutil.copy(TEMPLATE / "data" / "MANIFEST.yaml", root / "data" / "MANIFEST.yaml")
    (root / "CITATION.cff").write_text(CFF)
    for i in range(1, n_groups + 1):
        d = root / "data" / f"t{i:02d}"
        (d / "sub").mkdir(parents=True)
        (d / "a.txt").write_text(f"group {i}\n")
        (d / "sub" / "b.bin").write_bytes(bytes(range(256)) * 4)
    m = yaml.safe_load((root / "data" / "MANIFEST.yaml").read_text())
    m["datasets"] = [{"path": "data/t01", "size_bytes": 1, "sha256": "x", "source": "task t01",
                      "used_by": [], "zenodo": "none"},
                     {"path": "data/elsewhere", "size_bytes": 1, "sha256": "y", "source": "external",
                      "used_by": [], "zenodo": "none"}]
    Z.write_manifest(root, m)
    return root


@pytest.fixture
def proj(tmp_path):
    return make_project(tmp_path / "proj")


def release(proj, mock, token, version, *extra):
    return run_opsci("zenodo", "release", proj, "--version", version, "--api-url", mock.base,
                     "--token-file", token, *extra)


def manifest(proj):
    return yaml.safe_load((proj / "data" / "MANIFEST.yaml").read_text())


def forbid_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


# ------------------------------------------------------------------ reproducible tar

def sha_of_tar(root, paths):
    return Z.tar_checksum(root, paths)[0]


def test_reproducible_tar_same_input_same_checksum(tmp_path, proj):
    a = sha_of_tar(proj, ["data/t01"])
    assert a == sha_of_tar(proj, ["data/t01"])
    # a copy elsewhere, with other mtimes and permission bits, gives the same bytes
    other = tmp_path / "copy"
    shutil.copytree(proj, other)
    os.utime(other / "data" / "t01" / "a.txt", (0, 12345))
    (other / "data" / "t01" / "a.txt").chmod(0o600)
    assert sha_of_tar(other, ["data/t01"]) == a
    # written to disk, the file has that checksum too
    out = tmp_path / "t01.tar.gz"
    with open(out, "wb") as f:
        Z.write_tar(proj, ["data/t01"], f)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == a


def test_reproducible_tar_one_byte_changes_checksum(proj):
    a = sha_of_tar(proj, ["data/t01"])
    p = proj / "data" / "t01" / "sub" / "b.bin"
    b = bytearray(p.read_bytes())
    b[100] ^= 1
    p.write_bytes(bytes(b))
    assert sha_of_tar(proj, ["data/t01"]) != a


def test_tar_follows_symlinked_group_root(tmp_path, proj):
    # data/<task> may be a symlink to scratch; the tar holds the content, not the link
    scratch = tmp_path / "scratch" / "t01"
    shutil.copytree(proj / "data" / "t01", scratch)
    a = sha_of_tar(proj, ["data/t01"])
    shutil.rmtree(proj / "data" / "t01")
    (proj / "data" / "t01").symlink_to(scratch)
    assert sha_of_tar(proj, ["data/t01"]) == a


def test_checksum_command(proj):
    r = run_opsci("zenodo", "checksum", "data/t01", "--root", proj)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == sha_of_tar(proj, ["data/t01"])
    assert run_opsci("zenodo", "checksum", "data/nope", "--root", proj).returncode == 1


# ------------------------------------------------------------------ versions and reuse

def test_first_release_creates_deposition(proj, mock, token):
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 0, r.stderr + r.stdout
    assert any(m == "POST" and p.endswith("api/deposit/depositions") for m, p in mock.requests)
    assert not any(p.endswith("newversion") for _, p in mock.requests)
    [dep] = mock.published()
    assert sorted(f["filename"] for f in dep["files"]) == ["FILES.tsv", "t01.tar.gz", "t02.tar.gz"]
    sec = manifest(proj)["zenodo"]["sandbox"]
    assert sec["releases"][0]["doi"] == dep["doi"] and sec["concept_doi"] == dep["conceptdoi"]
    assert "draft_id" not in sec
    # the uploaded tar is the reproducible one
    t01 = next(f for f in dep["files"] if f["filename"] == "t01.tar.gz")
    assert hashlib.sha256(t01["data"]).hexdigest() == sha_of_tar(proj, ["data/t01"])
    # FILES.tsv lists every file with its tar
    listing = next(f for f in dep["files"] if f["filename"] == "FILES.tsv")["data"].decode()
    assert "data/t02/sub/b.bin\t1024\t" in listing and listing.count("\n") == 5
    assert not (proj / "data" / ".zenodo-build" / "sandbox" / "t01.tar.gz").exists()


def test_new_version_reuses_unchanged_groups(proj, mock, token):
    assert release(proj, mock, token, "1.0").returncode == 0
    first_uploads = list(mock.uploads)
    assert sorted(n for _, n in first_uploads) == ["FILES.tsv", "t01.tar.gz", "t02.tar.gz"]
    (proj / "data" / "t02" / "a.txt").write_text("changed\n")
    mock.uploads.clear()
    r = release(proj, mock, token, "1.1")
    assert r.returncode == 0, r.stderr + r.stdout
    assert any(p.endswith("actions/newversion") for _, p in mock.requests)
    # only the changed group and the file list were uploaded; t01 came from the old version
    assert sorted(n for _, n in mock.uploads) == ["FILES.tsv", "t02.tar.gz"]
    assert "kept t01.tar.gz (unchanged)" in r.stdout
    v1, v2 = sorted(mock.published(), key=lambda d: d["id"])
    assert v1["conceptrecid"] == v2["conceptrecid"] and v1["doi"] != v2["doi"]
    assert sorted(f["filename"] for f in v2["files"]) == ["FILES.tsv", "t01.tar.gz", "t02.tar.gz"]
    rel = manifest(proj)["zenodo"]["sandbox"]["releases"]
    assert [x["version"] for x in rel] == ["1.0", "1.1"]
    assert rel[0]["files"]["t01.tar.gz"] == rel[1]["files"]["t01.tar.gz"]
    assert rel[0]["files"]["t02.tar.gz"] != rel[1]["files"]["t02.tar.gz"]


def test_removed_group_is_deleted_from_new_version(proj, mock, token):
    assert release(proj, mock, token, "1.0").returncode == 0
    shutil.rmtree(proj / "data" / "t02")
    r = release(proj, mock, token, "1.1")
    assert r.returncode == 0, r.stderr + r.stdout
    v2 = max(mock.published(), key=lambda d: d["id"])
    assert sorted(f["filename"] for f in v2["files"]) == ["FILES.tsv", "t01.tar.gz"]


def test_unchanged_data_refused_without_network(proj, mock, token):
    assert release(proj, mock, token, "1.0").returncode == 0
    n = len(mock.requests)
    r = release(proj, mock, token, "1.1")
    assert r.returncode == 1 and "nothing changed" in r.stderr
    assert len(mock.requests) == n


def test_version_label_reuse_refused(proj, mock, token):
    assert release(proj, mock, token, "1.0").returncode == 0
    (proj / "data" / "t01" / "a.txt").write_text("new\n")
    n = len(mock.requests)
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 1 and "already released" in r.stderr and len(mock.requests) == n


def test_bad_upload_not_published_then_resumed(proj, mock, token):
    mock.corrupt_uploads = True
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 1 and "does not match" in r.stderr
    assert mock.published() == []
    draft = manifest(proj)["zenodo"]["sandbox"]["draft_id"]
    mock.corrupt_uploads = False
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 0, r.stderr + r.stdout
    assert f"resuming unpublished draft {draft}" in r.stdout
    assert [d["id"] for d in mock.published()] == [draft]


# ------------------------------------------------------------------ limits, before any upload

def test_101_files_refused_before_upload(tmp_path, mock, token):
    proj = make_project(tmp_path / "p100", n_groups=100)  # 100 tars + FILES.tsv = 101
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 1 and "101 files" in r.stderr and "allows 100" in r.stderr
    assert mock.requests == []
    assert not (proj / "data" / ".zenodo-build").exists()  # refused before building tars


def test_100_files_pass(tmp_path, mock, token):
    proj = make_project(tmp_path / "p99", n_groups=99)  # 99 tars + FILES.tsv = 100
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 0, r.stderr + r.stdout
    [dep] = mock.published()
    assert len(dep["files"]) == 100


def test_size_limit_checked_at_the_boundary():
    assert Z.check_limits({"a": Z.MAX_BYTES - 10, "b": 10}) == []
    [err] = Z.check_limits({"a": Z.MAX_BYTES - 10, "b": 11})
    assert "50 GB" in err


def test_size_limit_refused_before_upload(proj, mock, token, monkeypatch):
    # pretend each built tar is 26 GB: two of them exceed 50 GB
    monkeypatch.setattr(Z, "_file_size", lambda p: 26 * 10**9 if p.name.endswith(".tar.gz") else 10)
    with pytest.raises(Z.ZenodoError, match="refused before any upload.*50 GB"):
        Z.release(proj, "1.0", api_url=mock.base, token_file=str(token), log=lambda *a: None)
    assert mock.requests == []


def test_size_below_limit_passes(proj, mock, token, monkeypatch):
    monkeypatch.setattr(Z, "_file_size", lambda p: os.path.getsize(p))
    Z.release(proj, "1.0", api_url=mock.base, token_file=str(token), log=lambda *a: None)
    assert len(mock.published()) == 1


# ------------------------------------------------------------------ DOI write-back

def test_doi_written_to_manifest_and_citation(proj, mock, token):
    r = release(proj, mock, token, "1.0", "--write-citation")
    assert r.returncode == 0, r.stderr + r.stdout
    [dep] = mock.published()
    m = manifest(proj)
    by_path = {d["path"]: d for d in m["datasets"]}
    assert by_path["data/t01"]["zenodo"] == dep["doi"]
    assert by_path["data/elsewhere"]["zenodo"] == "none"  # in no released group
    text = (proj / "CITATION.cff").read_text()
    assert text.startswith("# How to cite this project.")  # comments kept
    cff = yaml.safe_load(text)
    assert {i["value"] for i in cff["identifiers"]} == {dep["doi"], dep["conceptdoi"]}
    assert cff["title"] == "Demo project" and cff["authors"] == [{"name": "A. Person"}]
    # a second version replaces the version DOI and keeps one concept DOI
    (proj / "data" / "t01" / "a.txt").write_text("v2\n")
    assert release(proj, mock, token, "1.1", "--write-citation").returncode == 0
    v2 = max(mock.published(), key=lambda d: d["id"])
    cff = yaml.safe_load((proj / "CITATION.cff").read_text())
    assert [i["value"] for i in cff["identifiers"]] == [dep["conceptdoi"], v2["doi"]]
    assert manifest(proj)["datasets"][0]["zenodo"] == v2["doi"]


def test_sandbox_doi_not_written_to_citation_by_default(proj, mock, token):
    before = (proj / "CITATION.cff").read_text()
    assert release(proj, mock, token, "1.0").returncode == 0
    assert (proj / "CITATION.cff").read_text() == before
    assert manifest(proj)["datasets"][0]["zenodo"] == "none"
    assert manifest(proj)["zenodo"]["sandbox"]["releases"][0]["doi"].startswith("10.5072/")


# ------------------------------------------------------------------ dry run

def test_dry_run_prints_plan_without_network(proj, mock, token, monkeypatch):
    assert release(proj, mock, token, "1.0").returncode == 0
    (proj / "data" / "t02" / "a.txt").write_text("changed\n")
    before = (proj / "data" / "MANIFEST.yaml").read_text()
    forbid_network(monkeypatch)
    server, base = Z.resolve_server(False, mock.base)
    plan = Z.make_plan(proj, server, base, build_dir=None)
    assert {f.filename: f.decision for f in plan.files} == \
        {"t01.tar.gz": "reuse", "t02.tar.gz": "upload", "FILES.tsv": "upload"}
    n = len(mock.requests)
    r = run_opsci("zenodo", "release", proj, "--dry-run", "--api-url", mock.base)
    assert r.returncode == 0, r.stderr
    assert "t01.tar.gz" in r.stdout and "reuse (unchanged" in r.stdout
    assert "upload (changed)" in r.stdout and "3 files (limit 100)" in r.stdout
    assert sha_of_tar(proj, ["data/t02"])[:16] in r.stdout
    assert len(mock.requests) == n
    assert (proj / "data" / "MANIFEST.yaml").read_text() == before
    assert not (proj / "data" / ".zenodo-build" / "sandbox" / "t01.tar.gz").exists()


def test_dry_run_reports_limit_refusal(tmp_path):
    proj = make_project(tmp_path / "p", n_groups=100)
    r = run_opsci("zenodo", "release", proj, "--dry-run")
    assert r.returncode == 1 and "REFUSED" in r.stdout


# ------------------------------------------------------------------ server selection and token

def test_sandbox_is_default():
    assert Z.resolve_server(False, None) == ("sandbox", Z.SANDBOX_API)


def test_production_refused_without_flag(proj, mock, token):
    with pytest.raises(Z.ZenodoError, match="pass --production"):
        Z.resolve_server(False, "https://zenodo.org/api")
    r = run_opsci("zenodo", "release", proj, "--version", "1", "--api-url", "https://zenodo.org/api",
                  "--token-file", token)
    assert r.returncode == 1 and "pass --production" in r.stderr


def test_production_refused_in_tests_even_with_flag(proj, token):
    with pytest.raises(Z.ZenodoError, match=Z.FORBID_PRODUCTION_ENV):
        Z.resolve_server(True, None)
    r = run_opsci("zenodo", "release", proj, "--version", "1", "--production", "--token-file", token)
    assert r.returncode == 1 and Z.FORBID_PRODUCTION_ENV in r.stderr


def test_production_flag_accepted_when_not_forbidden(monkeypatch):
    # resolution only; no request is made
    monkeypatch.delenv(Z.FORBID_PRODUCTION_ENV)
    assert Z.resolve_server(True, None) == ("production", Z.PRODUCTION_API)
    with pytest.raises(Z.ZenodoError, match="not the production"):
        Z.resolve_server(True, "https://sandbox.zenodo.org/api")


def test_token_file_must_be_private(proj, mock, token):
    token.chmod(0o640)
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 1 and "readable by others" in r.stderr and mock.requests == []
    token.chmod(0o600)
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 0
    assert mock.token not in r.stdout + r.stderr


def test_wrong_token_fails_and_is_not_printed(proj, mock, tmp_path):
    bad = tmp_path / "bad"
    bad.write_text("wrong-secret-xyz\n")
    bad.chmod(0o600)
    r = release(proj, mock, bad, "1.0")
    assert r.returncode == 1 and "HTTP 401" in r.stderr
    assert "wrong-secret-xyz" not in r.stdout + r.stderr


# ------------------------------------------------------------------ groups

def test_explicit_groups_and_overlap_refused(proj):
    m = manifest(proj)
    m["zenodo"] = {"groups": [{"name": "all", "paths": ["data/t01", "data/t02"]}]}
    Z.write_manifest(proj, m)
    groups, uncovered = Z.groups_from_manifest(proj, manifest(proj))
    assert [g.name for g in groups] == ["all"] and uncovered == []
    m["zenodo"]["groups"].append({"name": "sub", "paths": ["data/t01/sub"]})
    Z.write_manifest(proj, m)
    with pytest.raises(Z.ZenodoError, match="overlap"):
        Z.groups_from_manifest(proj, manifest(proj))
    m["zenodo"]["groups"] = [{"name": "x", "paths": ["../etc"]}]
    Z.write_manifest(proj, m)
    with pytest.raises(Z.ZenodoError, match="inside data/"):
        Z.groups_from_manifest(proj, manifest(proj))


# ------------------------------------------------------------------ real sandbox

@pytest.mark.manual
def test_real_sandbox_release(tmp_path):
    """Two versions on sandbox.zenodo.org. Needs ~/.config/opsci/zenodo-sandbox.token."""
    proj = make_project(tmp_path / "proj")
    r = run_opsci("zenodo", "release", proj, "--version", "0.1")
    assert r.returncode == 0, r.stderr + r.stdout
    (proj / "data" / "t02" / "a.txt").write_text("changed\n")
    r = run_opsci("zenodo", "release", proj, "--version", "0.2", "--write-citation")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "kept t01.tar.gz (unchanged)" in r.stdout
    rel = manifest(proj)["zenodo"]["sandbox"]["releases"]
    assert len(rel) == 2 and rel[1]["doi"].startswith("10.5072/")
    print(r.stdout)


def test_check_token_accepts_good_token_and_creates_nothing(mock, token):
    r = run_opsci("zenodo", "check-token", "--api-url", mock.base, "--token-file", token)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("ok: ") and mock.token not in r.stdout + r.stderr
    assert [m for m, _ in mock.requests] == ["GET"] and not mock.deps


def test_check_token_refuses_wrong_token_and_open_mode(tmp_path, mock, token):
    bad = tmp_path / "bad.token"
    bad.write_text("not-the-token\n")
    bad.chmod(0o600)
    r = run_opsci("zenodo", "check-token", "--api-url", mock.base, "--token-file", bad)
    assert r.returncode == 1 and "HTTP 401" in r.stderr and "not-the-token" not in r.stderr
    Path(token).chmod(0o644)
    r = run_opsci("zenodo", "check-token", "--api-url", mock.base, "--token-file", token)
    assert r.returncode == 1 and "readable by others" in r.stderr
