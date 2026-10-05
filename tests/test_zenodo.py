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
from map_project import header
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


def site_config(proj, **cfg):
    (proj / "config").mkdir(exist_ok=True)
    (proj / "config" / "site.local.yaml").write_text(yaml.safe_dump(cfg))


def test_tar_follows_symlinked_group_root(tmp_path, proj):
    # data/<task> may be a symlink to scratch; the tar holds the content, not the link
    scratch = tmp_path / "scratch" / "t01"
    shutil.copytree(proj / "data" / "t01", scratch)
    a = sha_of_tar(proj, ["data/t01"])
    shutil.rmtree(proj / "data" / "t01")
    (proj / "data" / "t01").symlink_to(scratch)
    # refused while the target is under no allowed root
    with pytest.raises(Z.ZenodoError, match="not under the project, the site's scratch"):
        sha_of_tar(proj, ["data/t01"])
    site_config(proj, scratch=str(tmp_path / "scratch"))
    assert sha_of_tar(proj, ["data/t01"]) == a
    site_config(proj, scratch="<path to your scratch>", data_roots=[str(tmp_path / "scratch" / "t01")])
    assert sha_of_tar(proj, ["data/t01"]) == a


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("not a real key\n")
    (home / ".aws").mkdir()
    (home / "runs" / "t09").mkdir(parents=True)
    (home / "runs" / "t09" / "x.txt").write_text("x\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


@pytest.mark.parametrize("target", [".ssh", ".aws", ".config/opsci", ".config", "", "runs/../.ssh"])
def test_symlinked_group_root_into_hidden_home_dir_refused(proj, fake_home, target):
    # Even with the whole home directory allowed as a data root, a link to ~/.ssh, the
    # token directory, or the home directory itself (which holds ~/.ssh) is refused.
    (fake_home / ".config" / "opsci").mkdir(parents=True, exist_ok=True)
    site_config(proj, data_roots=[str(fake_home)])
    (proj / "data" / "t03").symlink_to(fake_home / target if target else fake_home)
    with pytest.raises(Z.ZenodoError, match="not archived, it"):
        sha_of_tar(proj, ["data/t03"])
    plan = Z.make_plan(proj, "sandbox", Z.SANDBOX_API, build_dir=None)
    assert any("data/t03 resolves to" in e for e in plan.errors) and not plan.files


def test_symlinked_group_root_into_xdg_config_refused(proj, tmp_path, fake_home, monkeypatch):
    cfg = tmp_path / "xdg"
    (cfg / "opsci").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    site_config(proj, data_roots=[str(tmp_path)])
    (proj / "data" / "t03").symlink_to(cfg / "opsci")
    with pytest.raises(Z.ZenodoError, match="config directory"):
        sha_of_tar(proj, ["data/t03"])


def test_symlinked_group_root_into_allowed_home_dir_passes(proj, fake_home):
    site_config(proj, data_roots=[str(fake_home / "runs")])
    (proj / "data" / "t09").symlink_to(fake_home / "runs" / "t09")
    assert sha_of_tar(proj, ["data/t09"])


def tar_members(proj, paths):
    import io
    import tarfile
    buf = io.BytesIO()
    Z.write_tar(proj, paths, buf)
    buf.seek(0)
    with tarfile.open(fileobj=buf) as t:
        return {m.name: m for m in t.getmembers()}


def test_inner_absolute_symlink_refused(proj, fake_home):
    # the audit's case: data/t02/inner -> ~/.ssh/id_rsa would be stored with its absolute target
    (proj / "data" / "t02" / "inner").symlink_to(fake_home / ".ssh" / "id_rsa")
    with pytest.raises(Z.ZenodoError, match="symlink to an absolute path"):
        sha_of_tar(proj, ["data/t02"])
    plan = Z.make_plan(proj, "sandbox", Z.SANDBOX_API, build_dir=None)
    assert any("data/t02/inner is a symlink to an absolute path" in e for e in plan.errors)


def test_inner_relative_symlink_leaving_the_group_refused(proj):
    (proj / "data" / "t02" / "sub" / "up").symlink_to("../../t01/a.txt")
    with pytest.raises(Z.ZenodoError, match="outside data/t02"):
        sha_of_tar(proj, ["data/t02"])


def test_inner_relative_symlink_inside_the_group_stored(proj):
    (proj / "data" / "t02" / "sub" / "link").symlink_to("../a.txt")
    (proj / "data" / "t02" / "dirlink").symlink_to("sub")
    m = tar_members(proj, ["data/t02"])
    assert m["t02/sub/link"].issym() and m["t02/sub/link"].linkname == "../a.txt"
    assert m["t02/dirlink"].issym() and m["t02/dirlink"].linkname == "sub"


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


# ------------------------------------------------------------------ links to the project

def add_nodes(root: Path):
    """A task t01 with a result on data/t01/sub; no task for t02."""
    (root / "tasks" / "t01" / "results").mkdir(parents=True)
    (root / "tasks" / "t01" / "context.md").write_text(header(
        id="t01", title="Setup", type="task", status="active", summary="Setup."))
    (root / "tasks" / "t01" / "results" / "r-b.md").write_text(header(
        id="r-b", title="B", type="result", status="done", summary="B.", artifacts=["data/t01/sub"]))
    (root / "tasks" / "t01" / "results" / "r-b-file.md").write_text(header(
        id="r-b-file", title="B file", type="result", status="done", summary="B.",
        artifacts=["data/t01/sub/b.bin"]))


def test_release_records_group_contents(proj, mock, token):
    m = manifest(proj)
    m["zenodo"] = {"groups": [{"name": "all", "paths": ["data/t01", "data/t02/sub"]}]}
    Z.write_manifest(proj, m)
    assert release(proj, mock, token, "1.0").returncode == 0
    [entry] = manifest(proj)["zenodo"]["sandbox"]["releases"]
    assert entry["groups"] == {"all.tar.gz": ["data/t01", "data/t02/sub"]}


def test_files_list_names_task_and_results(proj, mock, token):
    add_nodes(proj)
    assert release(proj, mock, token, "1.0").returncode == 0
    [dep] = mock.published()
    rows = [l.split("\t") for l in next(f for f in dep["files"] if f["filename"] == "FILES.tsv")["data"]
            .decode().splitlines()]
    assert rows[0] == ["# path", "size", "sha256", "tar", "task", "results"]
    by = {r[0]: r for r in rows[1:]}
    assert by["data/t01/sub/b.bin"][3:] == ["t01.tar.gz", "t01", "r-b,r-b-file"]
    assert by["data/t01/a.txt"][3:] == ["t01.tar.gz", "t01", ""]
    assert by["data/t02/sub/b.bin"][3:] == ["t02.tar.gz", "", ""]  # no task t02: empty
    # unchanged data and unchanged headers: everything reused, the release refused
    server, base = Z.resolve_server(False, mock.base)
    plan = Z.make_plan(proj, server, base, build_dir=None)
    assert {f.decision for f in plan.files} == {"reuse"}
    r = release(proj, mock, token, "1.1")
    assert r.returncode == 1 and "nothing changed" in r.stderr


def test_metadata_links_the_project(proj):
    add_nodes(proj)
    groups, _ = Z.groups_from_manifest(proj, manifest(proj))
    # control: no publish manifest, no links
    md = Z.build_metadata(proj, manifest(proj), "1.0", Z.dt.date(2026, 1, 2), groups)
    assert "related_identifiers" not in md and "github" not in md["description"]
    assert "<li>t01.tar.gz: data/t01 (task t01)</li>" in md["description"]
    assert "<li>t02.tar.gz: data/t02</li>" in md["description"]
    (proj / "publish").mkdir()
    (proj / "publish" / "manifest.yaml").write_text("public_repo: git@github.com:Owner/demo.git\n")
    md = Z.build_metadata(proj, manifest(proj), "1.0", Z.dt.date(2026, 1, 2), groups)
    assert md["related_identifiers"] == [{"identifier": "https://github.com/Owner/demo",
                                          "relation": "isSupplementTo", "resource_type": "software"}]
    assert "https://github.com/Owner/demo" in md["description"]
    assert "https://owner.github.io/demo/" in md["description"]
    (proj / "publish" / "manifest.yaml").write_text(
        "public_repo: https://github.com/Owner/demo\nsite_url: https://example.org/demo/\n")
    m = manifest(proj)
    m["zenodo"] = {"metadata": {"description": "Mine."}}
    md = Z.build_metadata(proj, m, "1.0", Z.dt.date(2026, 1, 2), groups)
    assert md["description"] == "Mine."  # overrides win
    md = Z.build_metadata(proj, manifest(proj), "1.0", Z.dt.date(2026, 1, 2), groups)
    assert "https://example.org/demo/" in md["description"] and "github.io" not in md["description"]


def test_production_location():
    rel = lambda doi, **k: {"version": "1", "doi": doi, "files": {"t01.tar.gz": {}, "FILES.tsv": {}}, **k}
    # sandbox only: nothing (its DOIs do not resolve)
    m = {"zenodo": {"sandbox": {"releases": [rel("10.5072/zenodo.1")]}}}
    assert Z.production_location("data/t01/x.npz", m) is None
    assert Z.production_location("data/t01/x.npz", None) is None
    # an old entry without groups: the default rule
    m["zenodo"]["production"] = {"releases": [rel("10.5281/zenodo.5")]}
    assert Z.production_location("data/t01/x.npz", m) == ("10.5281/zenodo.5", ["t01.tar.gz"])
    assert Z.production_location("data/t02/x.npz", m) is None
    assert Z.production_location("tasks/t01/x.png", m) is None
    # the current zenodo.groups
    m["zenodo"]["groups"] = [{"name": "t01", "paths": ["data/t01/keep"]}]
    assert Z.production_location("data/t01/x.npz", m) is None
    assert Z.production_location("data/t01", m) == ("10.5281/zenodo.5", ["t01.tar.gz"])  # a directory above
    # recorded groups win, and the newest release that holds the path is used
    m["zenodo"]["production"]["releases"].append(
        rel("10.5281/zenodo.9", groups={"t01.tar.gz": ["data/t01/keep"], "b.tar.gz": ["data/t01/other"]}))
    assert Z.production_location("data/t01/keep/a", m) == ("10.5281/zenodo.9", ["t01.tar.gz"])
    assert Z.production_location("data/t01", m) == ("10.5281/zenodo.9", ["b.tar.gz", "t01.tar.gz"])
    assert Z.production_location("data/t01/x.npz", m) is None


# ------------------------------------------------------------------ manifest comments

def test_manifest_comments_survive_a_release(proj, mock, token):
    p = proj / "data" / "MANIFEST.yaml"
    text = p.read_text()
    text = text.replace("sha256: x\n", "sha256: x  # tar checksum of t01\n")
    text = text.replace("- path: data/elsewhere", "# the external set\n- path: data/elsewhere")
    text = text.replace("  zenodo: none\n", "  zenodo: none  # set by the tool\n", 1)
    p.write_text(text)
    assert manifest(proj)["datasets"][0]["sha256"] == "x"
    assert release(proj, mock, token, "1.0", "--write-citation").returncode == 0
    (proj / "data" / "t01" / "a.txt").write_text("v2\n")
    assert release(proj, mock, token, "1.1", "--write-citation").returncode == 0
    after = p.read_text()
    assert "sha256: x  # tar checksum of t01\n" in after
    assert "# the external set\n- path: data/elsewhere" in after
    v2 = max(mock.published(), key=lambda d: d["id"])
    assert f"  zenodo: {v2['doi']}  # set by the tool\n" in after
    assert after.startswith("# Every dataset the project keeps")
    m = manifest(proj)
    assert m["datasets"][0]["zenodo"] == v2["doi"] and m["datasets"][1]["zenodo"] == "none"
    assert [r["version"] for r in m["zenodo"]["sandbox"]["releases"]] == ["1.0", "1.1"]


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


# ------------------------------------------------------------------ privacy (audit finding C1, M2)

def add_task(root: Path, tid: str, privacy: str | None, where="tasks"):
    (root / where / tid).mkdir(parents=True, exist_ok=True)
    h = dict(id=tid, title=tid, type="task", status="active", summary="S.")
    if privacy:
        h["privacy"] = privacy
    (root / where / tid / "context.md").write_text(header(**h))


def files_tsv(mock):
    [dep] = mock.published()
    return next(f for f in dep["files"] if f["filename"] == "FILES.tsv")["data"].decode()


@pytest.mark.parametrize("tier", ["soft-private", "hard-private"])
def test_private_task_data_left_out_by_default(proj, mock, token, tier):
    add_task(proj, "t01", tier)
    add_task(proj, "t02", "public")
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 0, r.stderr + r.stdout
    assert f"data/t01 ({tier}, task t01) is private and will not be released" in r.stdout
    [dep] = mock.published()
    assert sorted(f["filename"] for f in dep["files"]) == ["FILES.tsv", "t02.tar.gz"]
    listing = files_tsv(mock)
    assert "data/t01" not in listing and "\tt01\t" not in listing  # finding M2
    assert "t01" not in dep["metadata"]["description"]


def test_private_task_in_explicit_group_refused_then_included(proj, mock, token):
    add_task(proj, "t01", "soft-private")
    m = manifest(proj)
    m["zenodo"] = {"groups": [{"name": "all", "paths": ["data/t01", "data/t02"]}]}
    Z.write_manifest(proj, m)
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 1 and "data/t01 is soft-private (task t01)" in r.stdout + r.stderr
    assert mock.requests == []
    m = manifest(proj)
    m["zenodo"]["include_private"] = ["t01"]
    Z.write_manifest(proj, m)
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "data/t01/a.txt" in files_tsv(mock)


def test_private_task_default_privacy_and_nesting(proj):
    # no privacy field: the publish manifest's default decides; a verification task inside a
    # soft-private task is at least soft-private; a task directory without a valid header is
    # hard-private
    (proj / "publish").mkdir()
    (proj / "publish" / "manifest.yaml").write_text(
        "policy: {default_privacy: soft-private}\nhard_private: [data/secret-*]\n")
    add_task(proj, "t01", None)
    add_task(proj, "t02", "soft-private")
    add_task(proj, "v01", "public", where="tasks/t02/verifications")
    add_task(proj, "t05", "public")
    (proj / "tasks" / "t06").mkdir()
    (proj / "tasks" / "t06" / "context.md").write_text("no header\n")
    own = Z.file_owners(proj)
    assert own.tier("data/t01/a.txt") == ("soft-private", "t01")
    assert own.tier("data/v01") == ("soft-private", "v01")
    assert own.tier("data/t05/x") == ("public", "")
    assert own.tier("data/t06") == ("hard-private", "t06")
    assert own.tier("data/secret-run/x") == ("hard-private", "hard_private")
    assert own.tier("data/other") == ("public", "")


# ------------------------------------------------------------------ secret and leak scans (C1)

def dry_plan(proj):
    return Z.make_plan(proj, "sandbox", Z.SANDBOX_API, build_dir=None)


def test_secret_in_data_refused_before_any_upload(proj, mock, token):
    tok = "ghp_" + "A1b2C3d4" * 5
    (proj / "data" / "t02" / "notes.txt").write_text(f"token = {tok}\n")
    r = release(proj, mock, token, "1.0")
    out = r.stdout + r.stderr
    assert r.returncode == 1 and "github-token" in out and "data/t02/notes.txt" in out
    assert tok not in out  # redacted
    assert mock.requests == []


def test_secret_in_binary_data_refused(proj):
    (proj / "data" / "t02" / "blob.bin").write_bytes(
        b"\0\1\2-----BEGIN OPENSSH " + b"PRIVATE KEY-----\n" + bytes(range(256)))
    assert any("private-key" in e for e in dry_plan(proj).errors)


def test_leaks_refused(proj):
    (proj / "data" / "t02" / "log.txt").write_text("written to " + abs_path() + "\n")
    assert any("absolute-path" in e and "data/t02/log.txt" in e for e in dry_plan(proj).errors)
    (proj / "data" / "t02" / "log.txt").unlink()
    # a site identifier inside binary data, and in a file name
    site_config(proj, identifiers=["clustername-xyz"])
    (proj / "data" / "t02" / "h.bin").write_bytes(b"\0\0attr=clustername-xyz\0" + bytes(range(256)))
    assert any("site-identifier" in e and "data/t02/h.bin" in e for e in dry_plan(proj).errors)
    (proj / "data" / "t02" / "h.bin").unlink()
    (proj / "data" / "t02" / "clustername-xyz.txt").write_text("x\n")
    assert any("[filename]" in e for e in dry_plan(proj).errors)
    (proj / "data" / "t02" / "clustername-xyz.txt").unlink()
    assert dry_plan(proj).errors == []


def test_absolute_path_chance_match_in_binary_not_refused(proj):
    # a slash-separated path occurs by chance in compressed data; binary content is not scanned for it
    (proj / "data" / "t02" / "c.bin").write_bytes(b"\0\x9f/ab/cd/ef\x01" + bytes(range(256)))
    assert dry_plan(proj).errors == []


def abs_path():
    return "/".join(["", "home", "someone", "runs", "out.h5"])


@pytest.mark.parametrize("whole", [True, False])
def test_absolute_path_in_binary_string_refused(proj, monkeypatch, whole):
    # a path stored as a string attribute (HDF5, pickle) is a long printable run: refused,
    # whether the file is scanned whole or in chunks
    if not whole:
        monkeypatch.setattr(Z, "SCAN_WHOLE", 100)
        monkeypatch.setattr(Z, "SCAN_CHUNK", 512)
    (proj / "data" / "t02" / "h.bin").write_bytes(b"\0\x9f" * 300 + b"src=" + abs_path().encode()
                                                  + b"\0" + bytes(range(256)))
    assert any("absolute-path" in e and "data/t02/h.bin" in e for e in dry_plan(proj).errors)


def test_secret_inside_archive_in_data_refused(proj):
    import zipfile
    tok = "ghp_" + "A1b2C3d4" * 5
    with zipfile.ZipFile(proj / "data" / "t02" / "arr.npz", "w") as z:
        z.writestr("notes.txt", f"token = {tok}\n")
    errs = dry_plan(proj).errors
    assert any("github-token" in e and "arr.npz" in e for e in errs) and not any(tok in e for e in errs)


def test_large_file_scanned_in_chunks(proj, monkeypatch):
    monkeypatch.setattr(Z, "SCAN_WHOLE", 100)
    monkeypatch.setattr(Z, "SCAN_CHUNK", 1024)
    monkeypatch.setattr(Z, "SCAN_OVERLAP", 64)
    tok = "ghp_" + "A1b2C3d4" * 5
    # the secret straddles a chunk boundary
    (proj / "data" / "t02" / "big.txt").write_text("a" * 1000 + f" {tok} " + "b" * 3000 + "\n")
    errs = dry_plan(proj).errors
    assert any("github-token" in e for e in errs) and not any(tok in e for e in errs)


def test_slurm_job_id_needs_an_override(proj, mock, token):
    (proj / "data" / "t02" / "run.txt").write_text("SLURM job " + "jobid=" + "1234567 finished\n")
    assert any("slurm-job-id" in e for e in dry_plan(proj).errors)
    m = manifest(proj)
    m["zenodo"] = {"overrides": [{"check": "leak", "kind": "slurm-job-id", "paths": ["data/t02"],
                                  "reason": "Job numbers name no person.", "date": "2026-10-05"}]}
    Z.write_manifest(proj, m)
    plan = dry_plan(proj)
    assert plan.errors == [] and len(plan.overridden) == 1
    assert "accepted by zenodo.overrides: data/t02/run.txt slurm-job-id" in Z.format_plan(plan, "1.0")
    r = release(proj, mock, token, "1.0")
    assert r.returncode == 0, r.stderr + r.stdout


@pytest.mark.parametrize("kind", [("secret", "github-token"), ("leak", "absolute-path"),
                                  ("leak", "email")])
def test_override_of_other_kinds_refused(proj, kind):
    m = manifest(proj)
    m["zenodo"] = {"overrides": [{"check": kind[0], "kind": kind[1], "reason": "r", "date": "2026-10-05"}]}
    Z.write_manifest(proj, m)
    with pytest.raises(Z.ZenodoError, match="cannot be overridden"):
        dry_plan(proj)


# ------------------------------------------------------------------ where the token goes (Low)

@pytest.mark.parametrize("url", ["http://zenodo.example.org/api", "ftp://127.0.0.1/api",
                                 "http://" + ".".join(["10", "0", "0", "5"]) + "/api", "https:///api"])
def test_api_url_without_tls_refused(url):
    with pytest.raises(Z.ZenodoError, match="must be an https URL"):
        Z.resolve_server(False, url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/api", "http://localhost:1/api",
                                 "https://zenodo.example.org/api"])
def test_api_url_https_or_loopback_accepted(url):
    assert Z.resolve_server(False, url) == ("custom", url)


@pytest.mark.parametrize("url", ["https://evil.example/api/deposit/depositions/1",
                                 "http://sandbox.zenodo.org/api/files/x",
                                 "https://sandbox.zenodo.org:8443/api/files/x"])
def test_token_not_sent_to_other_hosts(monkeypatch, url):
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, **k: sent.append(req))
    api = Z.API(Z.SANDBOX_API, "tok-secret")
    with pytest.raises(Z.ZenodoError, match="token is sent only to the configured API host"):
        api._call("GET", url)
    assert sent == []


def test_new_version_link_to_other_host_refused(proj, mock, token):
    assert release(proj, mock, token, "1.0").returncode == 0
    (proj / "data" / "t01" / "a.txt").write_text("changed\n")
    real = mock.route

    def route(method, path, body):
        code, obj = real(method, path, body)
        if path.endswith("actions/newversion") and isinstance(obj, dict):
            obj["links"]["latest_draft"] = obj["links"]["latest_draft"].replace("127.0.0.1", "localhost")
        return code, obj
    mock.route = route
    r = release(proj, mock, token, "1.1")
    assert r.returncode == 1 and "only to the configured API host" in r.stderr
